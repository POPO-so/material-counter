"""Fail-closed, single-frame state machine. Importing this module touches no board."""
import hashlib
import json
import math
import os
import time
from pathlib import Path

import baseline_pynq_image_once as base

BIT_SHA = '56c1af5f043148c1c19a4ad182622ea3d5400344cb701da43da7a48755e03d1d'
HWH_SHA = '9e2e09f0cb79ca678d24c3c3d826fbb393832bec6a45a43e2c49f4276dcc4b57'
PACKET_SHA = 'cacf35237e6b4e79de2940a55ad414565fb9eadd581dfe8251ef09f4909f9dae'
BASELINE_SHA = 'b1d2a56fd3d87c5e3331ae2b45a14e867fbccebe00df8a5d44f2ce93940375e2'
STAGES = ('ENV', 'LOAD', 'HEALTH', 'MIN_READ', 'INIT', 'SEND', 'VERIFY', 'RELEASE', 'POST')
MODE_STAGES = {
    'full': STAGES,
    'overlay-only': ('ENV', 'LOAD', 'HEALTH'),
    'mmio-read-only': ('ENV', 'HEALTH', 'MIN_READ'),
    'dma-once': ('ENV', 'HEALTH', 'MIN_READ', 'INIT', 'SEND', 'VERIFY', 'RELEASE', 'POST'),
}
PARTIAL_MODES = frozenset(('overlay-only', 'mmio-read-only', 'dma-once'))
BUNDLE_FILES = ('baseline_pynq_image_once.py', 'core.py', 'child.py', 'supervisor.py',
                'deadline.py', 'session.py', 'authentication.py')


def verify_bundle(directory, expected_manifest_sha=None):
    directory = Path(directory)
    path = directory / 'executor-manifest.json'
    if expected_manifest_sha is not None and base._sha256_file(path) != expected_manifest_sha:
        raise base.PreflightError('Transferred executor manifest differs from workstation')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('schema') != 1 or set(manifest.get('files', {})) != set(BUNDLE_FILES):
        raise base.PreflightError('Executor bundle schema/files differ')
    for name, digest in manifest['files'].items():
        if base._sha256_file(directory / name) != digest:
            raise base.PreflightError('Reviewed executor source changed: ' + name)
    return base._sha256_file(path)


def validate_50(plan):
    if (plan.clock_mhz != 50 or plan.build_id != 'wo0014-hw-50mhz-20261014'
            or plan.bit_sha256 != BIT_SHA or plan.hwh_sha256 != HWH_SHA
            or plan.packet_sha256 != PACKET_SHA):
        raise base.PreflightError('Only the reviewed 50 MHz build and legal first frame are allowed')
    path = Path(base.__file__)
    if base._sha256_file(path) != BASELINE_SHA:
        raise base.PreflightError('Preserved baseline helper hash changed')
    root = base.ET.parse(plan.hwh_path).getroot()
    for node in root.iter():
        if base._local_tag(node) == 'MODULE' and 'shutdown' in str(node.attrib).lower():
            raise base.PreflightError('Unexpected shutdown manager may perform implicit MMIO during load')
        if base._local_tag(node) == 'MEMRANGE' and node.attrib.get('INSTANCE') in ('axi_dma_0', 'image_pipeline'):
            expected = plan.dma_base if node.attrib['INSTANCE'] == 'axi_dma_0' else plan.checker_base
            if int(node.attrib.get('HIGHVALUE', '0'), 0) != expected + 0xffff:
                raise base.PreflightError('Control address aperture differs from reviewed HWH')


class Journal:
    """Consumed claims survive crashes. Never remove or overwrite a claim here."""
    def __init__(self, root):
        self.root = Path(root)
        existed = self.root.exists()
        self.root.mkdir(parents=True, exist_ok=True)
        if not existed and os.name == 'posix':
            fd = os.open(str(self.root.parent), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def sync_directory(self):
        if os.name == 'posix':
            fd = os.open(str(self.root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def claim(self, name, data):
        # An empty/partial marker also consumes the attempt if power fails.
        with (self.root / (name + '.json')).open('x', encoding='utf-8') as f:
            json.dump(data, f, sort_keys=True); f.write('\n')
            f.flush(); os.fsync(f.fileno())
        self.sync_directory()

    def exists(self, name):
        return (self.root / (name + '.json')).exists()

    def read_json(self, name):
        path = self.root / (name + '.json')
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise base.PreflightError('Persistent guard receipt is unreadable: ' + name) from exc
        if not isinstance(value, dict):
            raise base.PreflightError('Persistent guard receipt schema is invalid: ' + name)
        return value

    def event(self, **data):
        row = {'monotonic_s': time.monotonic(), **data}
        with (self.root / 'events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, sort_keys=True) + '\n')
            f.flush(); os.fsync(f.fileno())
        return row


class Worker:
    def __init__(self, plan, api, journal, environment, *, fixed_identity=True,
                 timeout_s=5.0, emit=None, may_progress=None, mode='full'):
        if not 0 < timeout_s <= 30:
            raise ValueError('stage timeout must be in (0,30]')
        if mode not in MODE_STAGES:
            raise ValueError('unknown execution mode')
        self.plan, self.api, self.journal = plan, api, journal
        self.environment, self.fixed_identity = environment, fixed_identity
        self.timeout_s, self.emit = timeout_s, emit or (lambda row: None)
        self.may_progress = may_progress or (lambda: True)
        self.mode = mode
        self.stages = MODE_STAGES[mode]
        self.state, self.index = 'NEW', 0
        self.buffer = self.dma = self.checker = None
        self.send_attempts = 0
        self.raw = {}
        self.deadline = 0
        self.env = None

    def record(self, event, **data):
        row = self.journal.event(event=event, state=self.state, **data)
        self.emit(row)

    def remaining(self):
        if not self.may_progress():
            raise RuntimeError('Supervisor transport lost; further hardware access forbidden')
        value = self.deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError('Python stage deadline expired; hardware timeout is not established')
        return value

    def access(self, name, fn, *args, **kwargs):
        self.remaining()
        self.record('ACCESS_BEGIN', operation=name)
        self.remaining()
        value = fn(*args, **kwargs)
        if isinstance(value, int):
            self.raw[name] = value
        self.record('ACCESS_RETURN', operation=name,
                    value=value if isinstance(value, (int, str, bool)) else None)
        # A call returning after its deadline cannot unlock a later operation.
        self.remaining()
        return value

    def read(self, device, offset, label):
        return int(self.access(label, device.read, offset)) & 0xffffffff

    def checker_values(self):
        return {name: self.read(self.checker, offset, 'checker.' + name)
                for name, offset in base.CHECKER_REGS.items()}

    def health(self, report):
        if (not isinstance(report, dict)
                or any(report.get(k) is not True for k in ('ping', 'ssh', 'linux'))
                or report.get('boot_id') != self.env['boot_id']
                or report.get('new_kernel_warnings', [])):
            raise base.PreflightError('External Ping/SSH/Linux/boot/kernel health gate failed')
        self.record('HEALTH_ACCEPTED', report=report)

    def _validate_receipt(self, name, expected_mode, environment):
        receipt = self.journal.read_json(name)
        expected = {
            'schema': 1,
            'mode': expected_mode,
            'boot_id': environment['boot_id'],
            'bit_sha256': self.plan.bit_sha256,
        }
        if receipt != expected:
            raise base.PreflightError('Required successful stage receipt is missing or stale: ' + name)

    def _validate_current_overlay(self, *, require_receipts):
        current = self.environment()
        if (not current.get('exclusive')
                or current.get('boot_id') != self.env.get('boot_id')
                or current.get('loaded_bit_sha256') != self.plan.bit_sha256):
            raise base.PreflightError('Current environment does not match the successful Overlay receipt')
        if require_receipts:
            self._validate_receipt('overlay-receipt', 'overlay-only', current)
        return current

    def _check_start_guards(self):
        if self.journal.exists('terminal-fail'):
            raise base.PreflightError('A previous terminal failure permanently blocks execution')
        if self.mode == 'full' and self.journal.exists('trial-consumed'):
            raise base.PreflightError('Full-trial guard is already consumed')
        if self.mode in PARTIAL_MODES and self.journal.exists('trial-consumed'):
            raise base.PreflightError('Full-trial guard is consumed; partial execution is forbidden')
        consumed = {
            'overlay-only': 'overlay-attempt',
            'mmio-read-only': 'mmio-read-only-consumed',
            'dma-once': 'dma-once-consumed',
        }.get(self.mode)
        if consumed and self.journal.exists(consumed):
            raise base.PreflightError('Requested stage entry has already been consumed: ' + consumed)
        if self.mode == 'dma-once' and self.journal.exists('send-attempt'):
            raise base.PreflightError('Persistent SEND guard is already consumed')

    def _persist_terminal_failure(self, stage, error):
        try:
            self.journal.claim('terminal-fail', {
                'mode': self.mode,
                'stage': stage,
                'error': error,
                'monotonic_s': time.monotonic(),
            })
        except FileExistsError:
            pass  # The existing marker is already a permanent denial.
        except BaseException:
            # Preserve the first failure result; the child remains locked/held.
            pass

    def command(self, stage, report=None, *, deadline_monotonic_s=None):
        if self.state in ('FAIL', 'PASS'):
            return {'ok': False, 'state': self.state, 'error': 'terminal state; no operation allowed'}
        try:
            if self.index >= len(self.stages) or stage != self.stages[self.index]:
                raise base.PreflightError('Invalid stage order; no recovery or skipped gate allowed')
            self.state = stage
            local_deadline = time.monotonic() + self.timeout_s
            if deadline_monotonic_s is not None:
                if (isinstance(deadline_monotonic_s, bool)
                        or not isinstance(deadline_monotonic_s, (int, float))
                        or not math.isfinite(float(deadline_monotonic_s))):
                    raise base.PreflightError('Invalid supervisor stage deadline')
                local_deadline = min(local_deadline, float(deadline_monotonic_s))
            self.deadline = local_deadline
            self.record('STAGE_BEGIN')
            self.remaining()
            getattr(self, 'stage_' + stage)(report)
            self.remaining()
            self.index += 1
            if self.index == len(self.stages):
                self.state = 'PASS'
            self.record('STAGE_DONE', completed_stage=stage)
            return {'ok': True, 'state': self.state, 'stage': stage,
                    'send_attempts': self.send_attempts, 'raw': dict(self.raw)}
        except BaseException as exc:
            self.state = 'FAIL'
            result = {'ok': False, 'state': 'FAIL', 'stage': stage,
                      'error': type(exc).__name__ + ': ' + str(exc),
                      'send_attempts': self.send_attempts,
                      'buffer_retained': self.buffer is not None, 'raw': dict(self.raw)}
            self._persist_terminal_failure(stage, result['error'])
            # Logging only. No MMIO, no status reread, no free, no reset.
            self.record('TERMINAL_FAIL', **{k: v for k, v in result.items() if k != 'state'})
            return result

    def stage_ENV(self, _):
        self._check_start_guards()
        if self.fixed_identity:
            validate_50(self.plan)
        self.env = self.environment()
        if not self.env.get('exclusive') or not self.env.get('boot_id'):
            raise base.PreflightError('Environment or exclusive ownership cannot be established')
        if self.mode in ('mmio-read-only', 'dma-once'):
            if self.env.get('loaded_bit_sha256') != self.plan.bit_sha256:
                raise base.PreflightError('Current loaded Overlay identity is missing or mismatched')
            self._validate_receipt('overlay-receipt', 'overlay-only', self.env)
        if self.mode == 'dma-once':
            self._validate_receipt('mmio-receipt', 'mmio-read-only', self.env)
        if self.mode == 'full':
            self.journal.claim('trial-consumed', {'boot_id': self.env['boot_id'],
                                                 'bit_sha256': self.plan.bit_sha256})
        self.record('ENVIRONMENT', report=self.env)

    def stage_LOAD(self, _):
        current = self.environment()
        if not current.get('exclusive') or current['boot_id'] != self.env['boot_id']:
            raise base.PreflightError('Environment changed before Overlay')
        if (base._sha256_file(self.plan.bit_path) != self.plan.bit_sha256
                or base._sha256_file(self.plan.hwh_path) != self.plan.hwh_sha256):
            raise base.PreflightError('Artifacts changed before Overlay')
        self.journal.claim('overlay-attempt', {'count': 1, 'clock_mhz': 50})
        overlay = self.access('Overlay', self.api.Overlay, str(self.plan.bit_path), download=True)
        bases = base._resolve_overlay_bases(overlay.ip_dict, self.plan.dma_base, self.plan.checker_base)
        self.record('OVERLAY_RETURNED', bases=bases)

    def stage_HEALTH(self, report):
        self.health(report)
        if self.mode == 'overlay-only':
            current = self.environment()
            if (not current.get('exclusive')
                    or current.get('boot_id') != self.env['boot_id']
                    or current.get('loaded_bit_sha256') != self.plan.bit_sha256):
                raise base.PreflightError('Post-Overlay environment identity is missing or mismatched')
            self.journal.claim('overlay-receipt', {
                'schema': 1,
                'mode': 'overlay-only',
                'boot_id': current['boot_id'],
                'bit_sha256': self.plan.bit_sha256,
            })

    def stage_MIN_READ(self, _):
        if self.mode in ('mmio-read-only', 'dma-once'):
            current = self._validate_current_overlay(require_receipts=True)
            if self.mode == 'dma-once':
                self._validate_receipt('mmio-receipt', 'mmio-read-only', current)
            marker = 'mmio-read-only-consumed' if self.mode == 'mmio-read-only' else 'dma-once-consumed'
            self.journal.claim(marker, {
                'mode': self.mode,
                'boot_id': current['boot_id'],
                'bit_sha256': self.plan.bit_sha256,
            })
        self.checker = self.access('MMIO.checker', self.api.MMIO, self.plan.checker_base, length=0x1000)
        magic = self.read(self.checker, 0x04, 'checker.magic')
        if magic != base.PACKET_MAGIC:
            raise base.PreflightError('Checker minimal read magic mismatch')
        if self.mode == 'mmio-read-only':
            self.journal.claim('mmio-receipt', {
                'schema': 1,
                'mode': 'mmio-read-only',
                'boot_id': self.env['boot_id'],
                'bit_sha256': self.plan.bit_sha256,
            })

    def stage_INIT(self, _):
        self.dma = self.access('MMIO.dma', self.api.MMIO, self.plan.dma_base, length=0x1000)
        sr = self.read(self.dma, 4, 'initial.dmasr')
        cr = self.read(self.dma, 0, 'initial.dmacr')
        state = base._check_dma_quiescent_status(sr, 'initial')
        if cr & base.DMA_CR_RESET or (state == base.DMA_SR_HALTED and cr & 1):
            raise base.PreflightError('DMA initial control is not clean')
        if self.read(self.dma, 0x28, 'initial.length') != 0:
            raise base.PreflightError('DMA length is not zero after fresh Overlay')
        base._check_checker_ready(self.checker_values())

    def idle(self):
        """Require a live, error-free channel before LENGTH; Idle may be clear on frame one."""
        sr = self.read(self.dma, 4, 'before_length.dmasr')
        if sr & base.DMA_SR_ERROR_MASK:
            raise base.PreflightError(f'DMA has error status before LENGTH: 0x{sr:08x}')
        if (sr & base.DMA_SR_HALTED) != 0:
            raise base.PreflightError(f'DMA is HALTED before LENGTH: 0x{sr:08x}')
        cr = self.read(self.dma, 0, 'before_length.dmacr')
        if ((cr & base.DMA_CR_RESET) != 0
                or (cr & base.DMA_CR_RUNSTOP) == 0):
            raise base.PreflightError('DMA is not running cleanly before LENGTH')

    def stage_SEND(self, _):
        if self.journal.exists('send-attempt'):
            raise base.PreflightError('Persistent SEND guard is already consumed')
        # Check again before allocation/programming; no cached readiness claims.
        current = self.environment()
        loaded_hash = current.get('loaded_bit_sha256')
        if self.mode == 'full' and loaded_hash is None:
            loaded_hash = self.plan.bit_sha256
        if (not current.get('exclusive') or current.get('boot_id') != self.env['boot_id']
                or loaded_hash != self.plan.bit_sha256):
            raise base.PreflightError('Ownership, boot or loaded Overlay changed before SEND')
        if self.mode == 'dma-once':
            self._validate_receipt('overlay-receipt', 'overlay-only', current)
            self._validate_receipt('mmio-receipt', 'mmio-read-only', current)
        self.record('PRE_SEND_ENVIRONMENT', report=current)
        sr = self.read(self.dma, 4, 'send_initial.dmasr')
        state = base._check_dma_quiescent_status(sr, 'before RUNSTOP')
        cr = self.read(self.dma, 0, 'send_initial.dmacr')
        if cr & 4 or (state == base.DMA_SR_HALTED and cr & 1):
            raise base.PreflightError('DMA initial control changed')
        base._check_checker_ready(self.checker_values())
        self.buffer = self.access('allocate', self.api.allocate,
                                  shape=(base.PACKET_BYTES,), dtype=self.api.np.uint8)
        flags = getattr(self.buffer, 'flags', None)
        if flags is None or not getattr(flags, 'c_contiguous', False):
            raise base.PreflightError('Buffer is not contiguous')
        address = base._buffer_address(self.buffer)
        base._validate_dma_address(address, base.PACKET_BYTES, len(self.buffer))
        self.buffer[:base.PACKET_BYTES] = self.api.np.frombuffer(self.plan.packet, dtype=self.api.np.uint8)
        if hashlib.sha256(bytes(self.buffer[:base.PACKET_BYTES])).hexdigest() != self.plan.packet_sha256:
            raise base.PreflightError('Allocated CPU buffer hash differs')
        sync = self.access('sync_to_device', base._sync_to_device, self.buffer)
        self.record('BUFFER_READY', address=address, bytes=base.PACKET_BYTES, sync=sync)
        self.access('write.RUNSTOP', self.dma.write, 0, cr | 1)
        start = min(self.deadline, time.monotonic() + 1)
        while True:
            sr = self.read(self.dma, 4, 'runstop.dmasr')
            if sr & base.DMA_SR_ERROR_MASK:
                raise base.PreflightError('DMA start error')
            if (sr & base.DMA_SR_HALTED) == 0:
                break
            if time.monotonic() >= start:
                raise TimeoutError('Python RUNSTOP polling deadline')
            time.sleep(min(0.005, self.remaining()))
        self.access('write.SA', self.dma.write, 0x18, address)
        self.idle()
        self.journal.claim('send-attempt', {'count': 1, 'bytes': base.PACKET_BYTES,
                                           'address': address, 'boot_id': self.env['boot_id']})
        self.send_attempts = 1
        self.access('write.LENGTH', self.dma.write, 0x28, base.PACKET_BYTES)
        while True:
            sr = self.read(self.dma, 4, 'poll.dmasr')
            status = self.read(self.checker, 8, 'poll.checker_status')
            if sr & base.DMA_SR_ERROR_MASK:
                raise base.PreflightError('DMA status error after send')
            if status & 6:
                raise base.PreflightError('Checker reported error after send')
            if sr & 2 and not sr & 1 and status & 9 == 9:
                checker = self.checker_values()
                self.result = checker
                self.record('DMA_OUTPUT_COMPLETE', dmasr=sr, checker=checker)
                return
            time.sleep(min(0.005, self.remaining()))

    def stage_VERIFY(self, _):
        # These reads belong only to the normal path. Any exception immediately
        # makes the worker terminal; no exception-handler register scan exists.
        dma = {name: self.read(self.dma, offset, 'final.' + name)
               for name, offset in [('dmacr', 0), ('dmasr', 4), ('sa', 0x18), ('length', 0x28)]}
        if (dma['dmasr'] & base.DMA_SR_ERROR_MASK or dma['dmasr'] & 3 != 2
                or not dma['dmasr'] & 0x1000 or not dma['dmacr'] & 1 or dma['dmacr'] & 4
                or dma['sa'] != base._buffer_address(self.buffer)
                or dma['length'] != base.PACKET_BYTES):
            raise base.PreflightError('Final DMA Idle/IOC/control/address/length evidence differs')
        base._check_checker_result(self.plan, self.result)
        self.record('RESULT_MATCH', checker=self.result, dma=dma)

    def stage_RELEASE(self, _):
        self.access('freebuffer', base._release_buffer, self.buffer)
        self.buffer = None

    def stage_POST(self, report):
        self.health(report)
