"""Offline CMA provenance and pre-write buffer-safety regressions; no PYNQ import."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board_preflight as pre
import board_once as run
from test_board_preflight import make_manifest

RAM = '00000000-1fffffff : System RAM\n  00008000-00efffff : Kernel code\n  01000000-010845cf : Kernel data\n'
CMA = ((0x18000000, 0x20000000),)
MEM = {'CmaTotal': 131072, 'CmaFree': 73944}
LOG = '[    0.01] cma: Reserved 128 MiB at 0x0000000018000000\n'


def resolve(**kwargs):
    args = dict(dt_ranges=(), dmesg_text='', debugfs_ranges=(), meminfo=MEM,
                cmdline='', bootargs='', iomem_text=RAM)
    args.update(kwargs)
    return pre.resolve_cma_sources(**args)


@pytest.mark.parametrize('kind', ['device_tree', 'dmesg', 'debugfs'])
def test_exact_source_returns_half_open_cma_and_provenance(kind):
    args = {'device_tree': {'dt_ranges': CMA}, 'dmesg': {'dmesg_text': LOG},
            'debugfs': {'debugfs_ranges': CMA}}[kind]
    result = resolve(**args)
    assert result.ranges == CMA
    assert result.source == kind and result.exact is True
    assert result.as_evidence()['physical_cma_range_known'] is True


def test_dt_has_priority_when_precise_sources_agree():
    assert resolve(dt_ranges=CMA, dmesg_text=LOG, debugfs_ranges=CMA).source == 'device_tree'


@pytest.mark.parametrize('unit,value', [('KiB', 131072), ('MiB', 128), ('GiB', 1)])
def test_kernel_reservation_units(unit, value):
    mem = {'CmaTotal': value * {'KiB': 1, 'MiB': 1024, 'GiB': 1048576}[unit], 'CmaFree': 1024}
    if unit == 'GiB':
        with pytest.raises(pre.PreflightError):
            resolve(dmesg_text=f'cma: Reserved {value} {unit} at 0x18000000', meminfo=mem)
    else:
        assert resolve(dmesg_text=f'cma: Reserved {value} {unit} at 0x18000000', meminfo=mem).ranges == CMA


@pytest.mark.parametrize('line', ['cma: Reserved 0 MiB at 0x18000000',
                                 'cma: Reserved 128 MiB at not_an_address'])
def test_malformed_cma_reservation_never_falls_back(line):
    with pytest.raises(pre.PreflightError):
        resolve(dmesg_text=line, cmdline='cma=128M')


@pytest.mark.parametrize('ranges', [((0x18000000, 0x1fffffff),),
                                   ((0x20000000, 0x28000000),),
                                   ((0x18000000, 0x18000000),),
                                   ((0x18000001, 0x20000001),)])
def test_invalid_or_mismatched_exact_cma_is_rejected(ranges):
    with pytest.raises(pre.PreflightError):
        resolve(dt_ranges=ranges, cmdline='cma=128M')


def test_conflicting_precise_sources_are_rejected():
    with pytest.raises(pre.PreflightError, match='conflict'):
        resolve(dmesg_text=LOG, debugfs_ranges=((0x10000000, 0x18000000),))


@pytest.mark.parametrize('source', ['dmesg', 'device_tree'])
def test_exact_cma_cannot_overlap_kernel_or_reserved_iomem(source):
    args = {'dmesg_text': LOG} if source == 'dmesg' else {'dt_ranges': CMA}
    with pytest.raises(pre.PreflightError, match='iomem'):
        resolve(iomem_text=RAM + '  19000000-19000fff : Kernel data\n', **args)


@pytest.mark.parametrize('location', ['cmdline', 'bootargs'])
def test_coarse_ram_high_fallback_is_explicitly_not_a_cma_measurement(location):
    result = resolve(**{location: 'root=/dev/mmcblk0p2 cma=128M'})
    assert result.source == 'MEMINFO_IOMEM_VERIFIED'
    assert result.exact is False and result.ranges == ((0x00100000, 0x20000000),)
    assert result.as_evidence()['physical_cma_range_known'] is False
    assert 'envelope' in result.as_evidence()['limitation']


@pytest.mark.parametrize('cmdline', ['cma=0', 'cma=64M', 'cma=129M',
                                    'cma=bogus', 'cma=128M cma=64M', 'cma=128M@0x10000000'])
def test_coarse_fallback_requires_unambiguous_matching_size_without_guessing(cmdline):
    with pytest.raises(pre.PreflightError):
        resolve(cmdline=cmdline)


@pytest.mark.parametrize('iomem', ['', '00000000-0fffffff : System RAM\n',
                                 '00000000-3fffffff : System RAM\n'])
def test_coarse_fallback_rejects_missing_wrong_or_reserved_ram(iomem):
    with pytest.raises(pre.PreflightError):
        resolve(cmdline='cma=128M', iomem_text=iomem)


@pytest.mark.parametrize('meminfo', [{'CmaTotal': 0, 'CmaFree': 0},
                                   {'CmaTotal': 131072, 'CmaFree': 1},
                                   {'CmaTotal': 131072, 'CmaFree': 131073}])
def test_all_sources_require_valid_sufficient_cma_accounting(meminfo):
    with pytest.raises(pre.PreflightError):
        resolve(dmesg_text=LOG, meminfo=meminfo)


def test_debugfs_pfn_count_reader_uses_bytes_not_pfn_as_address(tmp_path):
    area = tmp_path/'cma-0'
    area.mkdir()
    (area/'base_pfn').write_text('98304\n')
    (area/'count').write_text('32768\n')
    (area/'used').write_text('42\n')
    assert pre.read_debugfs_cma_ranges(tmp_path, page_size=4096) == CMA


@pytest.mark.parametrize('count,used', [('0', '0'), ('-1', '0'), ('32768', '32769')])
def test_invalid_debugfs_accounting_is_not_ignored(tmp_path, count, used):
    area = tmp_path/'cma-0'; area.mkdir()
    (area/'base_pfn').write_text('98304')
    (area/'count').write_text(count)
    (area/'used').write_text(used)
    with pytest.raises(pre.PreflightError):
        pre.read_debugfs_cma_ranges(tmp_path, page_size=4096)


def test_live_reader_falls_back_when_dt_is_absent(tmp_path):
    cmd = tmp_path/'cmdline'; cmd.write_text('cma=128M')
    bootargs = tmp_path/'bootargs'; bootargs.write_bytes(b'cma=128M\0')
    iomem = tmp_path/'iomem'; iomem.write_text(RAM)
    r = pre.read_cma_admission(tmp_path/'missing-dt', meminfo=MEM, dmesg_text=LOG,
                               debugfs_root=tmp_path/'missing-debug', cmdline_path=cmd,
                               bootargs_path=bootargs, iomem_path=iomem)
    assert r.source == 'dmesg' and r.ranges == CMA


def gate(**kwargs):
    args = dict(tx_address=0x18000000, tx_size=pre.TX_BYTES,
                rx_address=0x18040000, rx_size=pre.RX_BYTES,
                cma_ranges=CMA, hp0_range=pre.EXPECTED_DDR_RANGE,
                hp1_range=pre.EXPECTED_DDR_RANGE)
    args.update(kwargs)
    return pre.validate_dma_buffer_pair(**args)


def test_valid_exact_buffers_are_inside_cma_and_nonoverlapping():
    assert gate()['status'] == 'POST_ALLOC_PHYSICAL_GATE_PASS'


@pytest.mark.parametrize('case', [
    {'tx_address': 0}, {'tx_address': -8}, {'tx_address': 0x18000004},
    {'rx_address': 0x18040001}, {'tx_address': 0x20000000},
    {'rx_address': 0xfffffff0}, {'rx_address': 0x1ffff000},
    {'rx_address': 0x18000008}, {'tx_address': 0x17000000},
    {'tx_size': pre.TX_BYTES - 1}, {'rx_size': pre.RX_BYTES + 1},
    {'cma_ranges': None}, {'cma_ranges': ()}, {'tx_address': 0xf8000000},
    {'rx_address': 0xfffc0000}])
def test_buffer_gate_rejects_zero_peripherals_ocm_bad_size_overlap_and_alignment(case):
    with pytest.raises(pre.PreflightError):
        gate(**case)


def test_pynq_device_address_must_match_physical_address_without_importing_pynq():
    api = object.__new__(run.PynqBoardAPI)
    with pytest.raises(pre.PreflightError, match='physical'):
        api.device_address(SimpleNamespace(device_address=0x18000000, physical_address=0x17000000))


def test_pynq_matching_physical_and_device_address_is_accepted_without_import():
    api = object.__new__(run.PynqBoardAPI)
    assert api.device_address(SimpleNamespace(device_address=0x18000000,
                                              physical_address=0x18000000)) == 0x18000000


@pytest.mark.parametrize('buffer', [SimpleNamespace(device_address=0x18000000),
                                    SimpleNamespace(physical_address=0x18000000)])
def test_pynq_missing_physical_or_device_address_is_rejected(buffer):
    with pytest.raises(pre.PreflightError, match='physical'):
        object.__new__(run.PynqBoardAPI).device_address(buffer)


@pytest.mark.parametrize('kind', ['zero', 'short_rx', 'missing_ranges'])
def test_invalid_allocations_fail_before_any_register_write_and_release_mock_buffers(tmp_path, kind):
    manifest, _ = make_manifest(tmp_path, frame_count=1)
    plan = pre.validate_manifest(manifest)
    class InvalidAPI(run.MockBoardAPI):
        def allocate(self, size):
            b = super().allocate(size)
            if kind == 'zero' and self._run_allocation_index == 1:
                b.device_address = 0
            if kind == 'short_rx' and self._run_allocation_index == 2:
                del b[-1:]
            return b
    api = InvalidAPI(plan)
    if kind == 'missing_ranges':api.cma_ranges = None
    with pytest.raises(run.ExecutionFailure):
        run.run_frames(plan, api, timeout_s=1)
    assert api.write_trace == [] and api.free_count == 2


def test_actual_probe_satisfies_authorized_kernel_accounting_admission():
    probe = Path(__file__).resolve().parents[2]/'data/results/wo0015-cma-compat-20261015/board-cma-probe.json'
    if not probe.exists():pytest.skip('live metadata artifact not present in this checkout')
    r = json.loads(probe.read_text(encoding='utf-8'))
    if r['debug_cma']['areas']:pytest.skip('this regression is the no-debugfs observed case')
    mem = {line.split(':')[0]: int(line.split(':')[1].split()[0]) for line in r['meminfo']['stdout'].splitlines()}
    result = resolve(meminfo=mem, cmdline=r['cmdline']['stdout'], bootargs=r['chosen_bootargs']['stdout'],
                     dmesg_text=r['dmesg_filtered']['stdout'], iomem_text=r['iomem']['stdout'])
    assert result.source == 'MEMINFO_IOMEM_VERIFIED' and not result.exact
