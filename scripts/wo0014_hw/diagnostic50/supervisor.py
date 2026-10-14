"""Workstation supervisor. Default CLI is offline validation, never SSH or Ping."""
import argparse
import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

from core import Journal, STAGES, base, validate_50, verify_bundle
from deadline import run_bounded

REMOTE_PYTHON = '/usr/local/share/pynq-venv/bin/python'


def verify_remote_files(target, remote_dir, plan, executor_dir, *, runner=None):
    """Legacy hash parser for injected offline transport tests. No live default."""
    if runner is None:
        raise RuntimeError('Automatic SSH authentication disabled; use manual deploy-verify-only')
    manifest = json.loads((Path(executor_dir) / 'executor-manifest.json').read_text())
    expected = dict(manifest['files'])
    expected.update({'executor-manifest.json': base._sha256_file(Path(executor_dir) / 'executor-manifest.json'),
                     'wo0014_image.bit': plan.bit_sha256, 'wo0014_image.hwh': plan.hwh_sha256,
                     'rgb_packet.bin': plan.packet_sha256, 'pair-manifest.json': plan.manifest_sha256})
    paths = {remote_dir.rstrip('/') + '/' + name: digest for name, digest in expected.items()}
    command = 'sha256sum -- ' + ' '.join(shlex.quote(path) for path in paths)
    done = runner(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=3',
                           '-o', 'StrictHostKeyChecking=yes', target, command],
                          capture_output=True, text=True, encoding='utf-8', timeout=10)
    if done.returncode != 0:
        raise RuntimeError('Remote file hash verification failed: ' + done.stderr)
    actual = {}
    for line in done.stdout.splitlines():
        digest, name = line.split(maxsplit=1)
        name = name.lstrip('* ')
        if name in actual:
            raise RuntimeError('Duplicate remote hash record')
        actual[name] = digest
    if actual != paths:
        raise RuntimeError('Transferred executor/artifact hashes differ from local review')
    return {'stdout': done.stdout, 'stderr': done.stderr, 'hashes': actual}


class ProcessLink:
    def __init__(self, argv, root, timeout_s=8.0):
        if not 0 < timeout_s <= 60:
            raise ValueError('external timeout must be in (0,60]')
        self.root, self.timeout_s = Path(root), timeout_s
        self.queue = queue.Queue()
        self.stopped, self.sent, self.heartbeats = False, [], 0
        self.process = None
        self.deadline_results = []
        self.last_status = None
        self.command_lock = threading.Lock()
        self.active_contexts = []
        self.raw_pending = []
        def start(ctx):
            self.journal = Journal(self.root)
            self.journal.claim('supervisor-consumed', {'argv': argv})
            ctx.check_active()
            self._start(argv, ctx)
        outcome = run_bounded(start, self.timeout_s, phase='STARTUP')
        self.deadline_results.append(outcome)
        if outcome.status != 'PASS':
            if outcome.exception_type == 'FileExistsError':
                self.stopped = True
                raise FileExistsError(outcome.error)
            self.stop('STARTUP_' + outcome.status, deadline=outcome.as_record())

    def _start(self, argv, ctx):
        ctx.check_active()
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                        bufsize=1)
        ctx.check_active()
        self.record('CHILD_STARTED', pid=self.process.pid)
        threading.Thread(target=self._reader, args=(self.process.stdout, 'stdout'), daemon=True).start()
        threading.Thread(target=self._reader, args=(self.process.stderr, 'stderr'), daemon=True).start()

    def record(self, event, **data):
        row = {'event': event, 'monotonic_s': time.monotonic(), **data}
        with (self.root / 'supervisor-events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(row) + '\n'); f.flush(); os.fsync(f.fileno())

    def _reader(self, stream, kind):
        try:
            for line in stream:
                # Keep the line in memory if persistence stalls. A blocked reader
                # cannot authorize progression; request expires independently.
                pending = (kind, line)
                self.raw_pending.append(pending)
                with (self.root / ('child-' + kind + '.log')).open('a', encoding='utf-8') as f:
                    f.write(line); f.flush(); os.fsync(f.fileno())
                self.raw_pending.remove(pending)
                self.queue.put((kind, line))
            self.queue.put((kind, None))
        except BaseException as exc:
            self.queue.put(('reader_error', repr(exc)))

    def request(self, stage, report=None):
        if self.stopped:
            raise RuntimeError('supervisor terminal; command dispatch is disabled')
        if not self.command_lock.acquire(blocking=False):
            return self.stop('CONCURRENT_DISPATCH')
        try:
            outcome = run_bounded(lambda ctx: self._request(ctx, stage, report),
                                  self.timeout_s, phase=stage)
            self.deadline_results.append(outcome)
            if outcome.status != 'PASS':
                return self.stop('SUPERVISOR_TIMEOUT' if outcome.timed_out else 'DISPATCH_ERROR',
                                 stage=stage, deadline=outcome.as_record())
            if self.stopped:
                return self.last_status
            return outcome.value
        finally:
            self.command_lock.release()

    def _request(self, ctx, stage, report):
        self.active_contexts.append(ctx)
        ctx.check_active()
        self.sent.append(stage)
        self.record('DISPATCH', stage=stage)
        ctx.check_active()
        if self.stopped:
            raise RuntimeError('Terminal state before command write')
        try:
            self.process.stdin.write(json.dumps({'stage': stage, 'report': report,
                                                'deadline_monotonic_s': ctx.deadline_monotonic_s}) + '\n')
            ctx.check_active()
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            return self.stop('CHILD_EXIT', exit_code=self.process.poll())
        deadline = ctx.deadline_monotonic_s
        while True:
            ctx.check_active()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self.stop('SUPERVISOR_TIMEOUT', stage=stage, exit_code=self.process.poll())
            try:
                kind, line = self.queue.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                continue
            if kind == 'reader_error':
                return self.stop('CHILD_LOG_IO_ERROR', stage=stage, error=line)
            if kind == 'stdout' and line is None:
                code = self.process.wait(timeout=min(remaining, 1))
                return self.stop('CHILD_EXIT', stage=stage, exit_code=code)
            if kind != 'stdout':
                continue
            try:
                row = json.loads(line)
            except ValueError:
                return self.stop('INVALID_CHILD_PROTOCOL', stage=stage)
            if row.get('kind') == 'heartbeat':
                self.heartbeats += 1
                self.record('HEARTBEAT', child_state=row.get('state'))
                continue
            self.record('CHILD_EVENT', row=row)
            if row.get('kind') == 'result':
                if row.get('stage') != stage:
                    return self.stop('INVALID_CHILD_STAGE', stage=stage)
                if not row.get('ok'):
                    return self.stop('CHILD_FAIL', stage=stage, child_result=row)
                return row

    def stop(self, reason, **data):
        self.stopped = True
        for ctx in self.active_contexts:
            ctx.revoked.set()
        result = {'ok': False, 'status': 'FAIL', 'reason': reason,
                  'child_pid': self.process.pid if self.process else None, 'child_killed': False,
                  'axi_bus_recovered': False, 'commands': list(self.sent), **data}
        self.last_status = result
        # Terminal in memory first. Storage failure cannot delay stop forever.
        def persist(ctx):
            self.record('SUPERVISOR_STOP', **result)
            ctx.check_active()
            self.save_status(result)
        outcome = run_bounded(persist, min(self.timeout_s, 0.3), phase='TERMINAL_EVIDENCE')
        self.deadline_results.append(outcome)
        if outcome.status != 'PASS':
            result['evidence_storage_status'] = outcome.status
        # Do not kill, send EOF, reset, reconnect, or try to inspect MMIO here.
        return result

    def save_status(self, result):
        with (self.root / 'status.json').open('w', encoding='utf-8') as f:
            json.dump(result, f, indent=2); f.flush(); os.fsync(f.fileno())


class Supervisor:
    def __init__(self, link, health, mode='full'):
        self.link, self.health = link, health
        self.boot = None
        self.mode = mode

    def check(self):
        outcome = run_bounded(lambda ctx: self._check(ctx), self.link.timeout_s, phase='HEALTH_GATE')
        self.link.deadline_results.append(outcome)
        if outcome.status != 'PASS':
            raise RuntimeError('Health gate ' + outcome.status)
        return outcome.value

    def _check(self, ctx):
        report = self.health()
        ctx.check_active()
        if not isinstance(report, dict) or any(report.get(k) is not True for k in ('ping', 'ssh', 'linux')):
            raise RuntimeError('Ping/SSH/Linux health failed')
        if self.boot is None:
            self.boot = report.get('boot_id')
        if not self.boot or report.get('boot_id') != self.boot or report.get('new_kernel_warnings', []):
            raise RuntimeError('Boot identity or kernel warning health failed')
        self.link.record('EXTERNAL_HEALTH', report=report)
        return report

    def run(self):
        try:
            if self.link.stopped:
                return self.link.last_status
            self.check()
            from core import MODE_STAGES
            for stage in MODE_STAGES[self.mode]:
                report = self.check() if stage in ('HEALTH', 'POST') else None
                row = self.link.request(stage, report)
                if not row.get('ok'):
                    return row
                if stage == 'ENV' and row.get('boot_id', self.boot) != self.boot:
                    raise RuntimeError('Child boot differs from external SSH health')
            self.link.stopped = True
            outcome = run_bounded(lambda ctx: self._finish(ctx, row), self.link.timeout_s, phase='FINAL_COLLECTION')
            self.link.deadline_results.append(outcome)
            if outcome.status != 'PASS':
                return self.link.stop('FINAL_COLLECTION_' + outcome.status, deadline=outcome.as_record())
            return outcome.value
        except BaseException as exc:
            return self.link.stop('EXTERNAL_GATE_ERROR', error=type(exc).__name__ + ': ' + str(exc))

    def _finish(self, ctx, row):
        code = self.link.process.wait(timeout=ctx.remaining_s())
        ctx.check_active()
        if code != 0:
            return self.link.stop('CHILD_EXIT_AFTER_RESULT', exit_code=code)
        result = {'ok': True, 'status': 'PASS', 'commands': self.link.sent,
                  'child_killed': False, 'axi_bus_recovered': False,
                  'send_attempts': row.get('send_attempts'), 'child_pid': self.link.process.pid,
                  'exit_code': code}
        self.link.record('SUPERVISOR_PASS', **result)
        ctx.check_active()
        self.link.save_status(result)
        return result

class SSHHealth:
    """Compatibility name; live automatic authentication is deliberately disabled."""
    def __init__(self, target, host):
        self.target, self.host = target, host

    def __call__(self):
        raise RuntimeError('Automatic SSH/sudo health disabled; use native secure terminal checks')


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('bit', 'hwh', 'packet', 'manifest'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--approve-once', action='store_true')
    p.add_argument('--host', default='192.168.2.99')
    p.add_argument('--target', default='xilinx@192.168.2.99')
    p.add_argument('--remote-dir')
    p.add_argument('--evidence-dir')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    plan = base.validate_inputs(args.bit, args.hwh, args.packet, args.manifest)
    validate_50(plan)
    manifest_sha = verify_bundle(Path(__file__).resolve().parent)
    if args.execute != args.approve_once:
        raise RuntimeError('--execute and --approve-once must be paired')
    if not args.execute:
        print(json.dumps({'status': 'OFFLINE_ARTIFACT_READY', 'hardware_attempted': False,
                          'clock_mhz': 50, 'bit_sha256': plan.bit_sha256,
                          'hwh_sha256': plan.hwh_sha256, 'packet_bytes': len(plan.packet)}, indent=2))
        return 0
    if not args.remote_dir or not args.evidence_dir:
        raise RuntimeError('Future board execution requires prepared remote-dir and evidence-dir')
    if args.host != '192.168.2.99' or args.target != 'xilinx@192.168.2.99':
        raise RuntimeError('Unexpected board target')
    raise RuntimeError('Automated SSH/sudo execution disabled. Use strict-key native SSH in a private '
                       'terminal, interactive sudo, then the separately authorized session.py entry. '
                       'No password capture or authentication fallback is supported.')


if __name__ == '__main__':
    raise SystemExit(main())
