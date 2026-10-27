"""Offline resource and sampler protocol checks; no board APIs are used."""
import io
import json
import time

import pytest

import m8_probe_runtime as runtime


def test_retained_admission_exact_ram_and_cma_boundaries():
    required = 122880 + 144208 + 36864 + 100 * (150 + 128)
    result = runtime.retained_admission({'MemAvailable': required, 'CmaFree': 36864})
    assert result['required_kib'] == required and result['margin_kib'] == 0
    with pytest.raises(RuntimeError, match='MemAvailable'):
        runtime.retained_admission({'MemAvailable': required - 1, 'CmaFree': 36864})
    with pytest.raises(RuntimeError, match='CmaFree'):
        runtime.retained_admission({'MemAvailable': required + 10000, 'CmaFree': 36863})


@pytest.mark.parametrize('mem', [{}, {'MemAvailable': True, 'CmaFree': 40000},
                                {'MemAvailable': 500000, 'CmaFree': -1}])
def test_missing_or_invalid_resource_readings_are_not_admitted(mem):
    with pytest.raises((ValueError, RuntimeError)):
        runtime.retained_admission(mem)


def sample(**changes):
    value = {'status': 'OK', 'pid': 123, 'starttime_ticks': 42, 'boot_id': 'boot',
             'monotonic_ns': time.monotonic_ns(),
             'process_kib': {'VmRSS': 100, 'VmHWM': 200},
             'meminfo_kib': {'MemAvailable': 400000, 'CmaFree': 50000}}
    value.update(changes)
    return value


def test_checkpoint_rejects_missing_failed_stale_or_reused_pid_samples():
    runtime.validate_sample(sample(), pid=123, starttime=42, boot_id='boot', timeout_s=5)
    for bad in [sample(status='ERROR'), sample(pid=124), sample(starttime_ticks=43),
                sample(boot_id='other'), sample(monotonic_ns=1),
                sample(process_kib={'VmRSS': None, 'VmHWM': None}),
                sample(meminfo_kib={'MemAvailable': 122879, 'CmaFree': 50000})]:
        with pytest.raises(RuntimeError):
            runtime.validate_sample(bad, pid=123, starttime=42, boot_id='boot', timeout_s=5)


class FakeProcess:
    def __init__(self, events):
        self.stdout = io.StringIO(''.join(json.dumps(x) + '\n' for x in events))
        self.stdin = io.StringIO()
        self.pid = 456
        self.returncode = None

    def wait(self, timeout):
        self.returncode = 0
        return 0


def test_sampler_requires_ready_and_echoed_checkpoint_label(tmp_path):
    def factory(*args, **kwargs):
        return FakeProcess([{'event': 'READY', 'status': 'OK', 'sample': sample()},
                            {'event': 'CHECKPOINT', 'status': 'OK', 'label': 'wrong', 'sample': sample()}])
    session = runtime.SamplerSession(tmp_path / 'samples.jsonl', pid=123, starttime=42,
        boot_id='boot', timeout_s=5, popen_factory=factory)
    with pytest.raises(RuntimeError, match='protocol'):
        session.checkpoint('expected')
    session.abandon()


def test_sampler_bad_ready_never_admits(tmp_path):
    def factory(*args, **kwargs):
        return FakeProcess([{'event': 'ERROR', 'status': 'ERROR', 'sample': sample(status='ERROR')}])
    with pytest.raises(RuntimeError):
        runtime.SamplerSession(tmp_path / 'samples.jsonl', pid=123, starttime=42,
            boot_id='boot', timeout_s=5, popen_factory=factory)


def test_fail_hold_can_detach_without_waiting_for_sampler_io(tmp_path):
    def factory(*args, **kwargs):
        return FakeProcess([{'event': 'READY', 'status': 'OK', 'sample': sample()}])
    session = runtime.SamplerSession(tmp_path / 'samples', pid=123, starttime=42,
        boot_id='boot', timeout_s=5, popen_factory=factory)
    session.process.wait = lambda *a, **k: pytest.fail('wait delayed DMA fail-hold')
    assert session.abandon(wait=False)['status'] == 'STOP_REQUESTED_NOT_VERIFIED'


def test_final_without_complete_measurement_is_rejected(tmp_path):
    def factory(*args, **kwargs):
        return FakeProcess([{'event': 'READY', 'status': 'OK', 'sample': sample()},
                            {'event': 'FINAL', 'status': 'OK', 'label': 'final',
                             'sample': sample(), 'summary': {'final_valid': False}}])
    session = runtime.SamplerSession(tmp_path / 'samples', pid=123, starttime=42,
        boot_id='boot', timeout_s=5, popen_factory=factory)
    with pytest.raises(RuntimeError, match='summary'):
        session.finish()


def test_supervisor_and_real_sampler_protocol_use_synthetic_proc_only(tmp_path):
    import threading
    import queue
    import m8_memory_sampler as sampler
    boot = '9552f3cf-5fb6-4b97-b5c3-5640e708a598'
    proc = tmp_path / 'proc'
    (proc / '123').mkdir(parents=True)
    (proc / 'sys/kernel/random').mkdir(parents=True)
    (proc / 'sys/kernel/random/boot_id').write_text(boot)
    (proc / '123/stat').write_text('123 (fake runner) ' + ' '.join(['S'] + ['0'] * 18 + ['42']))
    (proc / '123/status').write_text('VmRSS: 100 kB\nVmHWM: 200 kB\n')
    (proc / 'meminfo').write_text('MemAvailable: 500000 kB\nMemFree: 300000 kB\nCmaFree: 50000 kB\nCmaTotal: 131072 kB\n')
    class Pipe(io.StringIO):
        def __init__(self):
            super().__init__()
            self.lines = queue.Queue()
        def write(self, value):
            self.lines.put(value)
            return len(value)
        def flush(self):
            pass
        def readline(self, size=-1):
            return self.lines.get(timeout=5)
        def close(self):
            self.lines.put('')
    class Process:
        def __init__(self, *args, **kwargs):
            self.pid = 456
            self.returncode = None
            self.stdin, self.stdout = Pipe(), Pipe()
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()
        def run(self):
            self.returncode = sampler.run_protocol(pid=123, starttime=42, boot_id=boot,
                output=tmp_path / 'samples.jsonl', proc_root=proc, sampler_pid=456,
                input_stream=self.stdin, output_stream=self.stdout)
            self.stdout.lines.put('')
        def wait(self, timeout):
            self.thread.join(timeout)
            if self.thread.is_alive():
                raise runtime.subprocess.TimeoutExpired('synthetic-sampler', timeout)
            return self.returncode
    session = runtime.SamplerSession(tmp_path / 'samples.jsonl', pid=123, starttime=42,
        boot_id=boot, timeout_s=5, popen_factory=Process)
    assert session.checkpoint('before_close')['sample']['process_kib']['VmRSS'] == 100
    final = session.finish('post_close')
    assert final['summary']['sample_count'] == 3
    assert final['summary']['final_valid'] is True
    assert session.closed and not session.failed


def test_final_summary_requires_sampler_identity_counts_times_and_observed_extrema():
    final_sample = sample()
    first = final_sample['monotonic_ns'] - 100
    summary = {'status': 'OK', 'final_valid': True, 'claims_exact_process_peak': False,
        'sampler_pid': 456, 'target_pid': 123, 'target_starttime_ticks': 42, 'boot_id': 'boot',
        'sample_count': 3, 'error_count': 0, 'errors': [], 'sampling_interval_s': 1.0,
        'observed_start_monotonic_ns': first, 'observed_end_monotonic_ns': final_sample['monotonic_ns'],
        'peak_observed_vmrss_kib': 100, 'max_observed_vmhwm_kib': 200,
        'minimum_observed_memavailable_kib': 400000, 'minimum_observed_cmafree_kib': 50000}
    kwargs = dict(sampler_pid=456, pid=123, starttime=42, boot_id='boot',
                  first_ns=first, last_sample=final_sample)
    runtime.validate_summary(summary, **kwargs)
    for field in summary:
        bad = {k: v for k, v in summary.items() if k != field}
        with pytest.raises(RuntimeError, match='summary'):
            runtime.validate_summary(bad, **kwargs)
    for field, value in [('sampler_pid', 999), ('sample_count', 2),
                          ('observed_end_monotonic_ns', first - 1),
                          ('minimum_observed_cmafree_kib', 32767),
                          ('peak_observed_vmrss_kib', 99)]:
        with pytest.raises(RuntimeError, match='summary'):
            runtime.validate_summary({**summary, field: value}, **kwargs)
