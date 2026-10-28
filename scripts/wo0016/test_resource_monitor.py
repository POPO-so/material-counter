import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from resource_monitor import ResourceMonitor


def test_resource_monitor_starts_disabled_without_sampling():
    calls = []
    monitor = ResourceMonitor(mode='normal', source='board', reader=lambda path: calls.append(path) or '')
    assert monitor.snapshot() == {'enabled': False, 'mode': 'normal', 'metrics': {}}
    monitor.close()
    assert calls == []


def test_mock_monitor_never_claims_hardware_metrics():
    calls = []
    monitor = ResourceMonitor(source='mock', reader=lambda path: calls.append(path) or '')
    assert monitor.control(enabled=True, mode='detail') == {
        'enabled': True, 'mode': 'detail', 'metrics': {'source': 'mock'}}
    monitor.control(enabled=False)
    assert calls == []


def test_disable_joins_sampler_and_clears_metrics_and_baselines():
    calls = []
    sampled = threading.Event()
    def reader(path):
        calls.append(path)
        sampled.set()
        return 'cpu 10 0 10 80 0 0 0 0\n' if path == '/proc/stat' else ''
    monitor = ResourceMonitor(source='board', interval=0.01, reader=reader)
    monitor.control(enabled=True)
    assert sampled.wait(1)
    result = monitor.control(enabled=False)
    stopped_call_count = len(calls)
    time.sleep(0.04)
    assert result == {'enabled': False, 'mode': 'normal', 'metrics': {}}
    assert not monitor._thread
    assert len(calls) == stopped_call_count
    assert monitor._cpu_baseline is monitor._network_baseline is monitor._sd_baseline is None
    monitor.close()


def test_reenable_resets_cpu_baseline_and_emits_none_for_first_sample():
    samples = iter(['cpu 10 0 10 80 0 0 0 0\n', 'cpu 20 0 20 160 0 0 0 0\n'])
    gate = threading.Event()
    def reader(path):
        if path == '/proc/stat':
            value = next(samples)
            gate.set()
            return value
        return ''
    monitor = ResourceMonitor(source='board', interval=10, reader=reader)
    monitor.control(enabled=True)
    assert gate.wait(1)
    deadline = time.monotonic() + 1
    while 'cpu_percent' not in monitor.snapshot()['metrics'] and time.monotonic() < deadline:
        time.sleep(0.001)
    assert monitor.snapshot()['metrics']['cpu_percent'] is None
    monitor.control(enabled=False)
    gate.clear()
    monitor.control(enabled=True)
    assert gate.wait(1)
    deadline = time.monotonic() + 1
    while 'cpu_percent' not in monitor.snapshot()['metrics'] and time.monotonic() < deadline:
        time.sleep(0.001)
    assert monitor.snapshot()['metrics']['cpu_percent'] is None
    monitor.close()


def test_cpu_delta_and_memory_ranges_are_sane():
    stat = iter(['cpu 100 0 100 800 0 0 0 0\n', 'cpu 150 0 150 900 0 0 0 0\n'])
    mem = 'MemTotal: 1000 kB\nMemAvailable: 400 kB\nCmaTotal: 200 kB\nCmaFree: 50 kB\n'
    def reader(path):
        return next(stat) if path == '/proc/stat' else mem if path == '/proc/meminfo' else (_ for _ in ()).throw(OSError())
    monitor = ResourceMonitor(source='board', reader=reader)
    assert monitor._sample_once()['cpu_percent'] is None
    metrics = monitor._sample_once()
    assert metrics['cpu_percent'] == 50.0
    assert metrics['memory_total_kib'] == 1000
    assert metrics['memory_available_kib'] == 400
    assert metrics['cma_total_kib'] == 200
    assert metrics['cma_free_kib'] == 50


def test_rootfs_is_only_reported_for_mmc_root_and_valid_capacity():
    statvfs = lambda path: type('Vfs', (), {'f_blocks': 100, 'f_frsize': 4096, 'f_bavail': 25})()
    monitor = ResourceMonitor(source='board', reader=lambda path: '1 0 8:1 / / rw - ext4 /dev/mmcblk0p2 rw\n', statvfs=statvfs)
    assert monitor._root_filesystem() == {'rootfs_total_bytes': 409600, 'rootfs_used_bytes': 307200}
    monitor._reader = lambda path: '1 0 8:1 / / rw - ext4 /dev/sda2 rw\n'
    assert monitor._root_filesystem() == {'rootfs_total_bytes': None, 'rootfs_used_bytes': None}
    monitor._reader = lambda path: '1 0 8:1 / / rw - ext4 /dev/mmcblk0p2 rw\n'
    monitor._statvfs = lambda path: type('Vfs', (), {'f_blocks': 0, 'f_frsize': 4096, 'f_bavail': 0})()
    assert monitor._root_filesystem() == {'rootfs_total_bytes': None, 'rootfs_used_bytes': None}


def test_invalid_control_values_are_rejected():
    monitor = ResourceMonitor(source='mock')
    with pytest.raises(ValueError):
        monitor.control(enabled=1)
    with pytest.raises(ValueError):
        monitor.control(enabled=True, mode='fast')


def test_network_uses_default_route_and_reports_separate_rx_tx_rates():
    now = [10.0]
    data = {
        '/proc/net/route': 'Iface Destination Gateway Flags RefCnt Use Metric Mask\neth0 00000000 0100000A 0003 0 0 100 00000000\n',
        '/sys/class/net/eth0/statistics/rx_bytes': '1000',
        '/sys/class/net/eth0/statistics/tx_bytes': '2000',
        '/sys/class/net/eth0/speed': '1000',
    }
    monitor = ResourceMonitor(source='board', reader=lambda path: data[path], monotonic=lambda: now[0])
    first = monitor._network()
    assert first['network_interface'] == 'eth0'
    assert first['network_rx_bytes_per_second'] is None
    data['/sys/class/net/eth0/statistics/rx_bytes'] = '1500'
    data['/sys/class/net/eth0/statistics/tx_bytes'] = '2400'
    now[0] = 12.0
    second = monitor._network()
    assert second['network_rx_bytes_per_second'] == 250.0
    assert second['network_tx_bytes_per_second'] == 200.0
    assert second['network_link_mbps'] == 1000


def test_network_rate_is_unknown_when_default_route_or_counters_are_invalid():
    monitor = ResourceMonitor(source='board', reader=lambda path: 'bad data')
    metrics = monitor._network()
    assert metrics['network_interface'] is None
    assert metrics['network_rx_bytes_per_second'] is None
    assert metrics['network_tx_bytes_per_second'] is None


def test_concurrent_disable_enable_is_serialized_and_final_enable_wins():
    entered = threading.Event()
    release = threading.Event()
    first = [True]
    def reader(path):
        if path == '/proc/stat' and first[0]:
            first[0] = False
            entered.set()
            assert release.wait(2)
            return 'cpu 1 0 1 8 0 0 0 0\n'
        if path == '/proc/stat':
            return 'cpu 2 0 2 16 0 0 0 0\n'
        raise OSError('unavailable')
    monitor = ResourceMonitor(source='board', interval=10, reader=reader)
    monitor.control(enabled=True)
    assert entered.wait(1)
    disabled = threading.Thread(target=lambda: monitor.control(enabled=False))
    enabled = threading.Thread(target=lambda: monitor.control(enabled=True))
    disabled.start()
    deadline = time.monotonic() + 1
    while not monitor._stop.is_set() and time.monotonic() < deadline:
        time.sleep(0.001)
    enabled.start()
    time.sleep(0.02)
    assert enabled.is_alive()
    release.set()
    disabled.join(1)
    enabled.join(1)
    assert not disabled.is_alive() and not enabled.is_alive()
    assert monitor.snapshot()['enabled'] is True
    monitor.close()


def test_network_falls_back_to_only_connected_physical_ethernet_and_keeps_rates_without_speed():
    now = [10.0]
    data = {
        '/proc/net/route': 'Iface Destination Gateway Flags RefCnt Use Metric Mask\n',
        '/sys/class/net/eth0/type': '1',
        '/sys/class/net/eth0/carrier': '1',
        '/sys/class/net/eth0/operstate': 'up',
        '/sys/class/net/eth0/statistics/rx_bytes': '1000',
        '/sys/class/net/eth0/statistics/tx_bytes': '2000',
    }
    def read(path):
        if path not in data:
            raise OSError('not available')
        return data[path]
    monitor = ResourceMonitor(source='board', reader=read,
                              monotonic=lambda: now[0], interface_provider=lambda: ['eth0'],
                              device_checker=lambda iface: True)
    assert monitor._network()['network_interface'] == 'eth0'
    data['/sys/class/net/eth0/statistics/rx_bytes'] = '1200'
    data['/sys/class/net/eth0/statistics/tx_bytes'] = '2300'
    now[0] = 12.0
    second = monitor._network()
    assert second['network_rx_bytes_per_second'] == 100.0
    assert second['network_tx_bytes_per_second'] == 150.0
    assert second['network_link_mbps'] is None


def test_network_refuses_to_guess_between_multiple_connected_interfaces():
    data = {'/proc/net/route': 'Iface Destination Gateway Flags RefCnt Use Metric Mask\n'}
    def read(path):
        if path == '/proc/net/route':
            return data[path]
        if path.endswith('/type'):
            return '1'
        if path.endswith('/carrier'):
            return '1'
        if path.endswith('/operstate'):
            return 'up'
        return '0'
    monitor = ResourceMonitor(source='board', reader=read,
                              interface_provider=lambda: ['eth0', 'eth1'],
                              device_checker=lambda iface: True)
    metrics = monitor._network()
    assert metrics['network_interface'] is None
    assert metrics['network_rx_bytes_per_second'] is None


def test_rootfs_resolves_dev_root_only_to_verified_mmc_partition():
    statvfs = lambda path: type('Vfs', (), {'f_blocks': 100, 'f_frsize': 4096, 'f_bavail': 25})()
    monitor = ResourceMonitor(
        source='board',
        reader=lambda path: '1 0 8:1 / / rw - ext4 /dev/root rw\n',
        statvfs=statvfs,
        device_resolver=lambda path: '/dev/mmcblk0p2',
    )
    assert monitor._root_filesystem() == {
        'rootfs_total_bytes': 409600, 'rootfs_used_bytes': 307200}
    monitor._device_resolver = lambda path: '/dev/sda2'
    assert monitor._root_filesystem() == {
        'rootfs_total_bytes': None, 'rootfs_used_bytes': None}
    monitor._device_resolver = lambda path: (_ for _ in ()).throw(OSError('unresolved'))
    assert monitor._root_filesystem() == {
        'rootfs_total_bytes': None, 'rootfs_used_bytes': None}


def test_sd_root_uses_kernel_device_identity_when_dev_root_missing():
    from types import SimpleNamespace
    def resolver(path):
        if path == '/sys/dev/block/179:2':
            return '/sys/devices/platform/block/mmcblk0/mmcblk0p2'
        raise OSError('missing device')
    monitor = ResourceMonitor(reader=lambda path: '17 1 179:2 / / rw - ext4 /dev/root rw\n',
                              statvfs=lambda path: SimpleNamespace(f_blocks=100, f_frsize=4096, f_bavail=25),
                              device_resolver=resolver)
    assert monitor._root_filesystem() == {'rootfs_total_bytes': 409600, 'rootfs_used_bytes': 307200}
