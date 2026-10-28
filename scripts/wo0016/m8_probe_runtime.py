"""Probe-only RAM admission and independent sampler supervision.

No PYNQ imports, hardware access, signals, termination, or target-process writes.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from typing import Any, Mapping

from m8_probe_profile import PROBE_CAPTURE_RING_DEPTH, PROBE_MAX_FRAMES

SYSTEM_RESERVE_KIB = 122880
RSS_PROXY_KIB = 144208
CMA_PREFLIGHT_MIN_KIB = 36864
CMA_RUNTIME_MIN_KIB = 32768
METADATA_KIB_PER_FRAME = 128
FRAME_PAIR_KIB = 150
CAPTURE_RING_DEPTH = PROBE_CAPTURE_RING_DEPTH


def _kib(values: Mapping[str, Any], key: str) -> int:
    value = values.get(key)
    if type(value) is not int or value < 0:
        raise ValueError(f"missing or invalid {key} reading")
    return value


def retained_admission(meminfo: Mapping[str, Any]) -> dict[str, Any]:
    """Reserve the fixed capture pool plus the existing per-frame metadata budget."""
    available = _kib(meminfo, 'MemAvailable')
    cma = _kib(meminfo, 'CmaFree')
    capture_pool_kib = CAPTURE_RING_DEPTH * FRAME_PAIR_KIB
    required = (SYSTEM_RESERVE_KIB + RSS_PROXY_KIB + cma + capture_pool_kib
                + PROBE_MAX_FRAMES * METADATA_KIB_PER_FRAME)
    if cma < CMA_PREFLIGHT_MIN_KIB:
        raise RuntimeError('diagnostic CmaFree is below 36864 KiB preflight minimum')
    if available < required:
        raise RuntimeError(f'diagnostic MemAvailable {available} KiB is below retained requirement {required} KiB')
    return {'status': 'PASS', 'mem_available_kib': available, 'cma_free_kib': cma,
            'required_kib': required, 'margin_kib': available - required,
            'frames': PROBE_MAX_FRAMES, 'metadata_kib_per_frame': METADATA_KIB_PER_FRAME,
            'ring_depth': CAPTURE_RING_DEPTH, 'capture_pool_kib': capture_pool_kib,
            'scope': 'fresh diagnostic pre-consumption arithmetic; not board memory acceptance'}


def read_meminfo(path: str | Path = '/proc/meminfo') -> dict[str, int]:
    result = {}
    for line in Path(path).read_text(encoding='ascii').splitlines():
        name, _, value = line.partition(':')
        if name in {'MemAvailable', 'CmaFree'}:
            parts = value.split()
            if len(parts) != 2 or parts[1] != 'kB' or name in result:
                raise ValueError(f'invalid {name} reading')
            result[name] = int(parts[0])
    for name in ('MemAvailable', 'CmaFree'):
        _kib(result, name)
    return result


def require_probe_environment() -> None:
    from calibration_runtime_gate import DEFAULT_WORKER_PYTHON
    if (sys.platform != 'linux' or os.geteuid() != 0
            or os.path.realpath(sys.executable) != os.path.realpath(DEFAULT_WORKER_PYTHON)
            or os.environ.get('XILINX_XRT') != '/usr'):
        raise RuntimeError('diagnostic requires root, pinned PYNQ interpreter and XILINX_XRT=/usr')


def prepare_probe(output: Path, *, boot_id: str, timeout_s: float):
    """Hardware-free handshake and fresh full admission before one-shot consume."""
    require_probe_environment()
    retained_admission(read_meminfo())
    session = start_for_current_process(output, boot_id=boot_id, timeout_s=timeout_s)
    try:
        # Includes the independent sampler's own memory and PID/Boot validation.
        event = session.checkpoint('before_consume')
        admission = retained_admission(event['sample']['meminfo_kib'])
        retained_admission(read_meminfo())
        return session, admission
    except BaseException:
        session.abandon()
        raise


def validate_sample(sample: Mapping[str, Any], *, pid: int, starttime: int,
                    boot_id: str, timeout_s: float) -> None:
    try:
        if (sample.get('status') != 'OK' or sample.get('pid') != pid
                or sample.get('starttime_ticks') != starttime or sample.get('boot_id') != boot_id):
            raise ValueError('sampler failed or process/Boot identity changed')
        at = sample.get('monotonic_ns')
        if type(at) is not int or not 0 <= time.monotonic_ns() - at <= timeout_s * 1e9:
            raise ValueError('sampler reading is stale or has invalid monotonic time')
        rss = _kib(sample.get('process_kib', {}), 'VmRSS')
        hwm = _kib(sample.get('process_kib', {}), 'VmHWM')
        if hwm < rss:
            raise ValueError('invalid VmHWM reading')
        mem = sample.get('meminfo_kib', {})
        if _kib(mem, 'MemAvailable') < SYSTEM_RESERVE_KIB or _kib(mem, 'CmaFree') < CMA_RUNTIME_MIN_KIB:
            raise ValueError('diagnostic runtime memory abort threshold crossed')
    except (ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError(f'diagnostic memory sample rejected: {exc}') from exc


def validate_summary(summary: Mapping[str, Any], *, sampler_pid: int, pid: int,
                     starttime: int, boot_id: str, first_ns: int,
                     last_sample: Mapping[str, Any]) -> None:
    try:
        if (summary.get('status') != 'OK' or summary.get('final_valid') is not True
                or summary.get('claims_exact_process_peak') is not False
                or summary.get('sampler_pid') != sampler_pid
                or summary.get('target_pid') != pid
                or summary.get('target_starttime_ticks') != starttime
                or summary.get('boot_id') != boot_id
                or _kib(summary, 'error_count') != 0 or summary.get('errors') != []
                or _kib(summary, 'sample_count') < 3
                or summary.get('sampling_interval_s') != 1.0
                or type(summary.get('sampling_interval_s')) not in (int, float)
                or summary.get('observed_start_monotonic_ns') != first_ns
                or summary.get('observed_end_monotonic_ns') != last_sample['monotonic_ns']
                or _kib(summary, 'observed_end_monotonic_ns') < _kib(summary, 'observed_start_monotonic_ns')):
            raise ValueError('identity/count/time or completeness mismatch')
        rss = _kib(summary, 'peak_observed_vmrss_kib')
        hwm = _kib(summary, 'max_observed_vmhwm_kib')
        available = _kib(summary, 'minimum_observed_memavailable_kib')
        cma = _kib(summary, 'minimum_observed_cmafree_kib')
        if (rss < last_sample['process_kib']['VmRSS'] or hwm < rss
                or hwm < last_sample['process_kib']['VmHWM']
                or not SYSTEM_RESERVE_KIB <= available <= last_sample['meminfo_kib']['MemAvailable']
                or not CMA_RUNTIME_MIN_KIB <= cma <= last_sample['meminfo_kib']['CmaFree']):
            raise ValueError('invalid observed extrema')
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise RuntimeError(f'diagnostic sampler final summary is incomplete: {exc}') from exc


class SamplerSession:
    """JSON-line handshake; failures never kill either process.

    The request deadline reuses the pinned runner timeout. It bounds waiting for
    sampler replies, not filesystem writes, DMA cleanup, or the entire close.
    """

    def __init__(self, output: Path, *, pid: int, starttime: int, boot_id: str,
                 timeout_s: float, popen_factory=subprocess.Popen):
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError('sampler timeout must be finite and positive')
        self.pid, self.starttime, self.boot_id = pid, starttime, boot_id
        self.timeout_s = timeout_s
        self.output = str(output)
        self.last_event = None
        self.closed = False
        self.failed = False
        self._events: queue.Queue = queue.Queue()
        path = Path(__file__).with_name('m8_memory_sampler.py')
        self.process = popen_factory([sys.executable, '-B', str(path), '--pid', str(pid),
            '--starttime', str(starttime), '--boot-id', boot_id, '--output', self.output],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', bufsize=1)
        threading.Thread(target=self._read_events, daemon=True, name='m8-sampler-replies').start()
        try:
            ready = self._receive('READY')
            self.first_ns = ready['sample']['monotonic_ns']
        except BaseException:
            self.abandon()
            raise

    def _read_events(self):
        try:
            while True:
                line = self.process.stdout.readline(65537)
                if not line:
                    self._events.put(RuntimeError('diagnostic sampler exited without reply'))
                    return
                if len(line) > 65536:
                    raise RuntimeError('diagnostic sampler reply is too large')
                self._events.put(json.loads(line))
        except BaseException as exc:
            self._events.put(exc)

    def _receive(self, event: str, label: str | None = None):
        try:
            value = self._events.get(timeout=self.timeout_s)
            if isinstance(value, BaseException):
                raise RuntimeError(str(value))
            self.last_event = value
            if (value.get('event') != event or value.get('status') != 'OK'
                    or (label is not None and value.get('label') != label)):
                raise RuntimeError('diagnostic sampler protocol/health failure')
            validate_sample(value.get('sample', {}), pid=self.pid, starttime=self.starttime,
                            boot_id=self.boot_id, timeout_s=self.timeout_s)
            if self.failed:
                raise RuntimeError('diagnostic sampler previously failed')
            return value
        except (Exception,):
            self.failed = True
            raise

    def checkpoint(self, label: str):
        try:
            if self.closed or self.failed:
                raise RuntimeError('diagnostic sampler closed or previously failed')
            self.process.stdin.write(json.dumps({'command': 'checkpoint', 'label': label}) + '\n')
            self.process.stdin.flush()
            return self._receive('CHECKPOINT', label)
        except BaseException:
            self.failed = True
            raise

    def finish(self, label: str = 'final'):
        if self.closed:
            raise RuntimeError('diagnostic sampler already closed')
        try:
            self.process.stdin.write(json.dumps({'command': 'stop', 'label': label}) + '\n')
            self.process.stdin.flush()
            value = self._receive('FINAL', label)
            summary = value.get('summary', {})
            validate_summary(summary, sampler_pid=self.process.pid, pid=self.pid,
                             starttime=self.starttime, boot_id=self.boot_id,
                             first_ns=self.first_ns, last_sample=value['sample'])
            if self.process.wait(timeout=self.timeout_s) != 0:
                raise RuntimeError('diagnostic sampler exited with failure')
            self.closed = True
            return value
        except BaseException:
            self.failed = True
            raise
        finally:
            self.abandon()

    def abandon(self, *, wait: bool = True):
        """Request EOF; never terminate/kill a blocked writer or target."""
        self.closed = True
        try:
            self.process.stdin.close()
        except (OSError, ValueError):
            pass
        if wait:
            try:
                self.process.wait(timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                self.failed = True
        return {'sampler_pid': self.process.pid, 'output': self.output,
                'status': ('ERROR' if self.failed else 'STOP_REQUESTED_NOT_VERIFIED'
                           if self.process.returncode is None else 'STOPPED'),
                'exit_verified': self.process.returncode is not None,
                'last_event': self.last_event,
                'exit_code': self.process.returncode}


def start_for_current_process(output: Path, *, boot_id: str, timeout_s: float) -> SamplerSession:
    from m8_memory_sampler import parse_starttime
    starttime = parse_starttime(Path(f'/proc/{os.getpid()}/stat').read_text())
    return SamplerSession(output, pid=os.getpid(), starttime=starttime,
                          boot_id=boot_id, timeout_s=timeout_s)
