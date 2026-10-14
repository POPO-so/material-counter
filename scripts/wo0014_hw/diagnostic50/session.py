"""Board-local controller for a manually authenticated SSH terminal. Offline by default."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from core import Journal, base, validate_50, verify_bundle
from supervisor import ProcessLink, Supervisor, REMOTE_PYTHON
from deadline import run_bounded

HERE = Path(__file__).resolve().parent
GUARD = Path('/var/lib/wo0014-diagnostic50')
MODES = ('health-check-only', 'deploy-verify-only', 'overlay-only', 'mmio-read-only', 'dma-once', 'full')


def authorize(mode, execute, approval):
    if not execute or (mode not in MODES[:2] and approval != mode):
        raise RuntimeError('Explicit matching stage authorization required')
    if mode in MODES[:2] and approval is not None:
        raise RuntimeError('Read-only entry does not accept hardware authorization')


def require_secure_session():
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        raise RuntimeError('Run through native interactive sudo in the authenticated SSH terminal')
    # Read sshd identity from ancestry; sudo may remove SSH_CONNECTION.
    pid = os.getpid()
    for _ in range(32):
        proc = Path('/proc') / str(pid)
        status = proc.joinpath('status').read_text()
        cmd = proc.joinpath('comm').read_text().strip()
        if cmd == 'sshd':
            return
        pid = int(next(line.split()[1] for line in status.splitlines() if line.startswith('PPid:')))
        if pid == 0:
            break
    raise RuntimeError('Authenticated sshd terminal ancestry cannot be established')


def verify_local():
    manifest_sha = verify_bundle(HERE)
    plan = base.validate_inputs(HERE / 'wo0014_image.bit', HERE / 'wo0014_image.hwh',
                                HERE / 'rgb_packet.bin', HERE / 'pair-manifest.json')
    validate_50(plan)
    return manifest_sha


def kernel_increment(before, after, anchor_lines=8):
    """Locate the exact previous tail once; head eviction is normal for dmesg.

    A missing or ambiguous anchor cannot establish continuity and fails closed.
    At least the last eight lines must survive (all lines for short snapshots).
    """
    if not before or not after:
        raise RuntimeError('kernel continuity lost: empty snapshot')
    anchor = before[-anchor_lines:]
    matches = [i for i in range(len(after) - len(anchor) + 1)
               if after[i:i + len(anchor)] == anchor]
    if len(matches) != 1:
        raise RuntimeError('kernel continuity lost: tail anchor missing or ambiguous')
    return after[matches[0] + len(anchor):]


class LocalHealth:
    """Independent OS process checks; no PYNQ import or memory mapping.

    SSH means an authenticated, live sshd ancestor. Ping is board loopback;
    it is explicitly not a measurement of workstation-to-board reachability.
    That external reachability check remains a separate manual prerequisite.
    """
    def __init__(self):
        self.kernel = None

    def __call__(self):
        require_secure_session()
        ping = subprocess.run(['ping', '-c', '1', '-W', '1', '127.0.0.1'],
                              capture_output=True, text=True, timeout=2)
        dmesg = subprocess.run(['dmesg', '--color=never'], capture_output=True, text=True,
                               timeout=2, check=True)
        lines = dmesg.stdout.splitlines()
        if self.kernel is None:
            self.kernel = lines
        try:
            new = kernel_increment(self.kernel, lines)
        except RuntimeError as exc:
            new = [str(exc)]
        else:
            # Advance only after continuity was established; warnings still fail
            # the current gate, and a failed session never dispatches again.
            self.kernel = lines
        bad = ('warn', 'error', 'oops', 'panic', 'fault', 'hung', 'timeout', 'continuity lost')
        state = Path('/sys/class/fpga_manager/fpga0/state').read_text().strip()
        return {'ping': ping.returncode == 0, 'ping_scope': 'board-loopback',
                'ssh': True, 'ssh_scope': 'authenticated-sshd-ancestry', 'linux': state == 'operating',
                'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'fpga_state': state, 'new_kernel_warnings': [s for s in new if any(w in s.lower() for w in bad)],
                'kernel_line_count': len(lines), 'new_kernel_lines': new}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=MODES, required=True)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--authorize-stage', choices=MODES[2:])
    p.add_argument('--evidence-dir', required=True)
    p.add_argument('--executor-manifest-sha', required=False)
    args = p.parse_args(argv)
    if not args.execute:
        print(json.dumps({'status': 'OFFLINE_ONLY', 'hardware_attempted': False, 'mode': args.mode}))
        return 0
    authorize(args.mode, args.execute, args.authorize_stage)
    if args.mode == 'full':
        raise RuntimeError('Full execution remains disabled: board-local loopback/sshd checks cannot '
                           'replace the original independent workstation Ping/SSH/Linux gates. '
                           'Use separately authorized partial stages and manual external checks.')
    require_secure_session()
    if args.mode in MODES[:2]:
        # Read-only branches never create guards, import PYNQ or start a child.
        report = LocalHealth()() if args.mode == 'health-check-only' else {'manifest_sha256': verify_local()}
        if args.mode == 'health-check-only' and (any(report.get(k) is not True for k in ('ping','ssh','linux'))
                                                or report.get('new_kernel_warnings')):
            raise RuntimeError('Read-only health gate failed')
        print(json.dumps({'status': 'PASS', 'mode': args.mode, 'hardware_attempted': False, **report}, indent=2))
        return 0
    gate = run_bounded(lambda ctx: verify_local(), 35, phase='DEPLOY_VERIFY')
    if gate.status != 'PASS':
        raise RuntimeError('Deployment verification ' + gate.status)
    digest = gate.value
    if not args.executor_manifest_sha or args.executor_manifest_sha != digest:
        raise RuntimeError('Explicit reviewed executor manifest SHA required')
    def claim_supervision(ctx):
        journal = Journal(GUARD)
        for mode in MODES[2:]:
            if journal.exists('supervision-' + mode + '-consumed'):
                expected = {'mode': mode, 'manifest_sha256': digest}
                if (journal.read_json('supervision-' + mode + '-consumed') != expected
                        or journal.read_json('supervision-' + mode + '-pass') != expected):
                    raise RuntimeError('Previous incomplete or changed supervision is terminal; no retry or next stage')
        ctx.check_active()
        journal.claim('supervision-' + args.mode + '-consumed', {'mode': args.mode, 'manifest_sha256': digest})
        ctx.check_active()
        return journal
    gate = run_bounded(claim_supervision, 35, phase='SUPERVISION_CLAIM')
    if gate.status != 'PASS':
        raise RuntimeError('Supervision claim ' + gate.status)
    journal = gate.value
    health = LocalHealth()
    # ProcessLink covers startup I/O; Supervisor covers health and result collection.
    link = ProcessLink([REMOTE_PYTHON, '-u', str(HERE / 'child.py'), '--execute', '--approve-once',
                        '--mode', args.mode, '--authorize-stage', args.mode,
                        '--executor-manifest-sha', digest], args.evidence_dir, timeout_s=35)
    result = Supervisor(link, health, mode=args.mode).run()
    if result.get('status') == 'PASS':
        gate = run_bounded(lambda ctx: journal.claim('supervision-' + args.mode + '-pass',
                           {'mode': args.mode, 'manifest_sha256': digest}), 35, phase='SUPERVISION_RECEIPT')
        if gate.status != 'PASS':
            result = link.stop('SUPERVISION_RECEIPT_' + gate.status)
    print(json.dumps(result, indent=2), flush=True)
    if result.get('status') != 'PASS':
        # Retain child pipes/objects and buffers. No reset/kill/retry on failure.
        while True:
            time.sleep(1)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
