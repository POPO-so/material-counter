"""Create new private B1 records only. No camera, PYNQ API or DMA access."""
import hashlib
import json
import os
from pathlib import Path
import sys

BOOT = "35983d66-bb1d-4a8a-85e5-802170825515"
ROOT = Path('/home/xilinx/wo0017/formal-b1-20261022-01')
STAGE1 = Path('/home/xilinx/wo0017/stage1-attempt-root-02-' + BOOT + '/receipt.json')
STAGE1_SHA = 'b3dfa4788dac8ac3b1b867846fe7f654cc41ef1e49de29f5facd161994fa7d74'
APP = ROOT / 'app'
assert os.geteuid() == 0
sys.path.insert(0, str(APP))
import run_stream_counter as runner

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write_new(path, value):
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())

assert runner.preflight.read_boot_id() == BOOT
assert digest(STAGE1) == STAGE1_SHA
mem = runner.preflight.read_cma_meminfo()
if mem['CmaFree'] <= runner.CMA_WATERMARK_KIB:
    raise RuntimeError('PREPARATION_BLOCKED_CMA: no manifest/READY/permit written; '
                       + json.dumps(mem, sort_keys=True))
original_path = Path('/home/xilinx/wo0015/run-manifest.json')
manifest = json.loads(original_path.read_text())
manifest['boot_id'] = BOOT
manifest['build_id'] += '-wo0017-formal-currentboot'
for key in ('bit_path', 'hwh_path'):
    manifest[key] = str(original_path.parent / manifest[key])
for frame in manifest['frames']:
    for key in ('packet_path', 'binary_path'):
        frame[key] = str(original_path.parent / frame[key])
manifest['executor_sha256'] = {
    name: digest(APP / 'wo0015' / name)
    for name in ('board_once.py', 'board_preflight.py')}
manifest_path = ROOT / 'currentboot-manifest.json'
write_new(manifest_path, manifest)
manifest_sha = digest(manifest_path)
evidence = ROOT / 'b1-result.json'
capture = ROOT / 'capture'
argv = [
    '--mode', 'board', '--wo0017-profile', 'b1', '--expected-boot-id', BOOT,
    '--stage1-receipt', str(STAGE1), '--stage1-receipt-sha256', STAGE1_SHA,
    '--execute', '--authorize-stage', 'wo0016-stream', '--approve-once',
    '--no-consume-guard', '--attempt-id', 'b1-empty-01',
    '--manifest', str(manifest_path), '--manifest-sha256', manifest_sha,
    '--max-frames', '115', '--expected-count', '0', '--material-label', 'empty_belt',
    '--stabilization-delay-s', '3', '--operator-prearm-delay-s', '15',
    '--count-axis', 'x', '--line-x', '160', '--line-y', '120', '--hysteresis', '15',
    '--max-step', '60', '--max-lateral-step', '10', '--min-area', '12',
    '--max-area', '900', '--max-bbox-width', '85', '--max-bbox-height', '85',
    '--capture-dir', str(capture), '--capture-max-bytes', '100663296',
    '--host', '127.0.0.1', '--port', '8080', '--evidence-json', str(evidence),
]
args = runner._parser().parse_args(argv)
plan = runner.preflight.validate_manifest(manifest_path, manifest_sha, approved_boot_id=BOOT)
formal = runner.formal_capture_context(args, plan)
ready = formal['ready_path']
assert not ready.parent.exists(), 'new B1 guard directory must be absent'
ready.parent.mkdir(parents=True, mode=0o700)
os.chmod(ready.parent.parent.parent, 0o700)
os.chmod(ready.parent.parent, 0o700)
write_new(ready, {**formal['identity'], 'guard_status': 'READY', 'consumed': False})
attempt = formal['attempt_root'] / args.attempt_id
attempt.mkdir(parents=True, mode=0o700)
os.chmod(attempt.parent, 0o700)
identity = runner.stageb2_attempt_permit_identity(
    ready_identity=formal['identity'], ready_guard_sha256=digest(ready),
    attempt_id=args.attempt_id, operator_prearm_delay_s=args.operator_prearm_delay_s,
    runner_sha256=formal['runtime_sha256']['run_stream_counter.py'],
    gate_launcher_sha256=formal['runtime_sha256']['operator_gate_launcher.py'],
    counting_config=runner.counting_configuration(args),
    counter_sha256=formal['runtime_sha256']['stream_counter.py'],
    capture_config={'enabled': True, 'output_directory': str(capture),
                    'max_bytes': args.capture_max_bytes, 'queue_depth': 2, 'schema_version': 1})
identity.update({'stage': 'wo0017-b1-attempt', 'runtime_sha256': formal['runtime_sha256']})
permit_path = attempt / 'attempt-permit.json'
write_new(permit_path, {**identity, 'permit_status': 'AUTHORIZED', 'consumed': False})
write_new(ROOT / 'runner-argv.json', argv)
admission = runner.validate_formal_prelaunch(args, digest(APP / 'operator_gate_launcher.py'))
assert admission['sha256'] == digest(permit_path)
assert 'pynq' not in sys.modules
result = {'status': 'PREPARED_NOT_STARTED', 'boot_id': BOOT,
          'manifest_sha256': manifest_sha, 'stage1_receipt_sha256': STAGE1_SHA,
          'ready_path': str(ready), 'ready_sha256': digest(ready),
          'permit_path': str(permit_path), 'permit_sha256': digest(permit_path),
          'attempt_consumed': (attempt / 'consumed.json').exists(),
          'capture_exists': capture.exists(), 'runtime_sha256': formal['runtime_sha256'],
          'camera_opened': False, 'pynq_imported': False, 'dma_started': False,
          'b2_status': 'NOT_AUTHORIZED_NOT_ISSUED', 'b2_frames_proposed': 220}
write_new(ROOT / 'preparation-receipt.json', result)
print(json.dumps(result, indent=2))
