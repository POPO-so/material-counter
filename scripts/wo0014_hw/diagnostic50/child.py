"""Board-resident persistent child; deploy later only with separate board authorization."""
import argparse
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

import core
from core import Journal, Worker, base, validate_50, verify_bundle

# Fixed across process restarts and deployment directories. No reset/delete CLI.
GUARD = Path('/var/lib/wo0014-diagnostic50')
HERE = Path(__file__).resolve().parent
stdout_lock = threading.Lock()
transport_lost = threading.Event()


def emit(row):
    with stdout_lock:
        try:
            print(json.dumps(row, sort_keys=True), file=sys.__stdout__, flush=True)
        except (BrokenPipeError, OSError):
            transport_lost.set()
            raise RuntimeError('Supervisor transport lost; no further hardware access')


def process_audit():
    findings, denied = [], []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            cmd = (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
            maps = (proc / 'maps').read_text()
            reasons = []
            if any(word in cmd for word in ('ipykernel_launcher', 'fpgautil', 'openocd',
                                            'xsdb', 'remote-send-once', 'run_replay')):
                reasons.append('notebook_or_hardware_command')
            if any(dev in maps for dev in ('/dev/mem', '/dev/xdevcfg', '/dev/fpga')):
                reasons.append('hardware_mapping')
            for fd in (proc / 'fd').iterdir():
                try:
                    if os.readlink(fd) in ('/dev/mem', '/dev/xdevcfg', '/dev/fpga0'):
                        reasons.append('hardware_fd')
                except FileNotFoundError:
                    pass
            if reasons:
                findings.append({'pid': int(proc.name), 'reasons': reasons})
        except FileNotFoundError:
            pass
        except PermissionError:
            denied.append(int(proc.name))
    if findings or denied:
        raise base.PreflightError('FPGA ownership scan failed: ' + json.dumps(
            {'findings': findings, 'denied': denied}))
    return {'findings': findings, 'denied': denied}


def install_signal_guard():
    def disconnected(signum, frame):
        transport_lost.set()
    for name in ('SIGTERM', 'SIGINT', 'SIGHUP'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), disconnected)


class BoardAPI:
    def __init__(self):
        # Runtime import happens only in the explicitly authorized board child.
        import numpy as np
        import pynq
        from pynq import MMIO, Overlay, allocate
        self.np, self.pynq = np, pynq
        self.MMIO, self.Overlay, self._allocate = MMIO, Overlay, allocate

    def allocate(self, **kwargs):
        from pynq.buffer import PynqBuffer
        buffer = self._allocate(**kwargs)
        # No custom allocator, arbitrary numpy array or unverified address alias.
        if not isinstance(buffer, PynqBuffer):
            raise base.PreflightError('Supported PYNQ allocation did not return PynqBuffer')
        if buffer.device_address != buffer.physical_address or getattr(buffer, 'offset', 0) != 0:
            buffer.freebuffer()  # No DMA has been programmed at this point.
            raise base.PreflightError('PYNQ buffer address aliases/offset disagree')
        return buffer

    def environment(self):
        if os.geteuid() != 0:
            raise base.PreflightError('Root is required for a complete /proc audit')
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        state = Path('/sys/class/fpga_manager/fpga0/state').read_text().strip()
        flags = Path('/sys/class/fpga_manager/fpga0/flags').read_text().strip()
        if state != 'operating' or flags != '0':
            raise base.PreflightError('FPGA manager is not operating with flags=0')
        audit = process_audit()
        pl_path = Path(self.pynq.__file__).parent / 'pl_server/global_pl_state_.json'
        if pl_path.is_symlink():
            raise base.PreflightError('PYNQ global state must not be a symlink')
        try:
            state_text = pl_path.read_text(encoding='utf-8')
        except FileNotFoundError:
            # No old state means PYNQ shutdown has no old IP list to access.
            # Keep loaded identity absent: later MMIO/DMA and post-load HEALTH
            # still require a real GlobalState and matching Overlay receipt.
            identity = {'pl_state_status': 'COLD_BOOT_NO_GLOBAL_STATE',
                        'effective_shutdown_ips': {}, 'loaded_bit_path': None,
                        'loaded_bit_sha256': None, 'loaded_bit_sha1': None,
                        'loaded_ip_dict': {}}
        except (OSError, UnicodeError) as exc:
            raise base.PreflightError('Cannot read PYNQ global state') from exc
        else:
            try:
                pl = json.loads(state_text)
                if not isinstance(pl, dict) or not isinstance(pl.get('shutdown_ips'), dict):
                    raise base.PreflightError('PYNQ shutdown_ips must be an explicit dictionary')
                if pl['shutdown_ips']:
                    raise base.PreflightError('Existing shutdown_ips could cause implicit MMIO during download')
                ip_dict = pl.get('ip_dict', {})
                if not isinstance(ip_dict, dict):
                    raise base.PreflightError('PYNQ ip_dict must be a dictionary')
                if any('shutdown' in (name + str(info)).lower() for name, info in ip_dict.items()):
                    raise base.PreflightError('Existing Overlay shutdown IP could cause implicit MMIO during download')
                bit_path = Path(pl['bitfile_name'])
                if base.hashlib.sha1(bit_path.read_bytes()).hexdigest() != pl['bitfile_hash']:
                    raise base.PreflightError('Loaded Overlay file and PYNQ identity disagree')
                identity = {'pl_state_status': 'GLOBAL_STATE_PRESENT',
                            'effective_shutdown_ips': {}, 'loaded_bit_path': str(bit_path),
                            'loaded_bit_sha256': base._sha256_file(bit_path),
                            'loaded_bit_sha1': pl['bitfile_hash'], 'loaded_ip_dict': ip_dict}
            except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
                raise base.PreflightError('Invalid PYNQ global state or recorded bitfile') from exc
        # RAM evidence from the OS, not the synthetic PYNQ PSDDR topology size.
        ranges = []
        for line in Path('/proc/iomem').read_text().splitlines():
            if line.strip().endswith(': System RAM'):
                low, high = line.strip().split()[0].split('-')
                ranges.append((int(low, 16), int(high, 16) + 1))
        if (0, 0x20000000) not in ranges:
            raise base.PreflightError('Linux physical RAM window cannot be verified')
        return {'boot_id': boot, 'exclusive': True, 'fpga_state': state,
                'fpga_flags': flags, 'pynq': self.pynq.__version__,
                'linux_ram': ranges, 'process_audit': audit, **identity}


def hold():
    # Intentionally retain lock/buffer/process. No recheck, prompt, MMIO or reset.
    while True:
        time.sleep(1)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--approve-once', action='store_true')
    p.add_argument('--mode', choices=tuple(core.MODE_STAGES), default='full')
    p.add_argument('--authorize-stage', choices=tuple(core.MODE_STAGES))
    p.add_argument('--executor-manifest-sha', required=True)
    args = p.parse_args(argv)
    if not args.execute or not args.approve_once:
        p.error('Execution requires --execute and --approve-once')
    if args.mode != 'full' and args.authorize_stage != args.mode:
        p.error('Partial stage mode requires a matching --authorize-stage value')
    if args.authorize_stage is not None and args.authorize_stage != args.mode:
        p.error('--authorize-stage does not match --mode')
    return args


def check_start_guards(mode, journal, manifest_sha=None):
    if manifest_sha is not None:
        expected = {'mode': mode, 'manifest_sha256': manifest_sha}
        if (journal.read_json('supervision-' + mode + '-consumed') != expected
                or journal.exists('supervision-' + mode + '-pass')):
            raise base.PreflightError('A fresh consumed supervisor authorization is required')
    for prior in core.MODE_STAGES:
        if prior != mode and journal.exists('supervision-' + prior + '-consumed'):
            consumed = journal.read_json('supervision-' + prior + '-consumed')
            passed = journal.read_json('supervision-' + prior + '-pass')
            if (passed != consumed or (manifest_sha is not None and
                    consumed != {'mode': prior, 'manifest_sha256': manifest_sha})):
                raise base.PreflightError('Previous incomplete supervision forbids the next stage')
    if journal.exists('terminal-fail'):
        raise base.PreflightError('A previous terminal failure permanently blocks execution')
    if mode == 'full' and journal.exists('trial-consumed'):
        raise base.PreflightError('Full-trial guard is already consumed')
    if mode in core.PARTIAL_MODES and journal.exists('trial-consumed'):
        raise base.PreflightError('Full-trial guard is consumed; partial execution is forbidden')
    consumed = {
        'overlay-only': 'overlay-attempt',
        'mmio-read-only': 'mmio-read-only-consumed',
        'dma-once': 'dma-once-consumed',
    }.get(mode)
    if consumed and journal.exists(consumed):
        raise base.PreflightError('Requested stage entry has already been consumed: ' + consumed)
    if mode == 'dma-once' and journal.exists('send-attempt'):
        raise base.PreflightError('Persistent SEND guard is already consumed')


def main(argv=None):
    args = parse_args(argv)
    verify_bundle(HERE, args.executor_manifest_sha)
    install_signal_guard()
    import fcntl
    journal = Journal(GUARD)
    lock_file = (GUARD / 'exclusive.lock').open('a')
    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    # Keep references at module scope even if logging/transport throws.
    global retained_worker, retained_lock
    retained_lock = lock_file
    check_start_guards(args.mode, journal, args.executor_manifest_sha)
    # PYNQ/library prints are diagnostic text, never JSON command responses.
    sys.stdout = (GUARD / 'library-stdout.log').open('a', buffering=1, encoding='utf-8')
    plan = base.validate_inputs(HERE / 'wo0014_image.bit', HERE / 'wo0014_image.hwh',
                                HERE / 'rgb_packet.bin', HERE / 'pair-manifest.json')
    validate_50(plan)
    api = BoardAPI()
    retained_worker = Worker(plan, api, journal, api.environment,
                             mode=args.mode,
                             emit=lambda row: emit({'kind': 'event', **row}),
                             may_progress=lambda: not transport_lost.is_set())
    def heartbeat():
        while True:
            time.sleep(0.5)
            try:
                emit({'kind': 'heartbeat', 'state': retained_worker.state,
                      'pid': os.getpid(), 'monotonic_s': time.monotonic()})
            except BaseException:
                transport_lost.set()
                return
    threading.Thread(target=heartbeat, daemon=True).start()
    try:
        for line in sys.stdin:
            command = json.loads(line)
            if (not isinstance(command, dict)
                    or set(command) != {'stage', 'report', 'deadline_monotonic_s'}):
                raise ValueError('Invalid supervisor command schema')
            stage = command['stage']
            retained_worker.timeout_s = 25 if stage == 'LOAD' else 5
            result = retained_worker.command(
                stage, command['report'],
                deadline_monotonic_s=command.get('deadline_monotonic_s'))
            if stage == 'ENV' and result['ok']:
                result['boot_id'] = retained_worker.env['boot_id']
            emit({'kind': 'result', **result})
            if not result['ok']:
                hold()
            if retained_worker.state == 'PASS':
                return 0
        # A disconnected supervisor never authorizes buffer release.
        retained_worker.state = 'FAIL'
        hold()
    except BaseException as exc:
        retained_worker.state = 'FAIL'
        retained_worker._persist_terminal_failure(
            'CHILD', type(exc).__name__ + ': ' + str(exc))
        try:
            journal.event(event='CHILD_TERMINAL', error=type(exc).__name__ + ': ' + str(exc),
                          buffer_retained=retained_worker.buffer is not None)
        finally:
            hold()


if __name__ == '__main__':
    raise SystemExit(main())
