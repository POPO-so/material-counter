"""One authorized Attempt4 dispatch; no retries; browser click gates frame capture."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

root = Path('/home/xilinx/board-runtime-currentboot-v2')
payload = json.loads(Path('/home/xilinx/attempt4-offline-prep-20261020/preparation-payload.json').read_text())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
assert boot == payload['boot_id']
attempt = Path('/var/lib/wo0016/attempts')/boot/'attempt-4'
ready = Path('/var/lib/wo0016')/('stageb2-ready-'+boot+'.json')
assert sha(ready) == payload['ready_guard_sha256']
assert json.loads(ready.read_text())['consumed'] is False
permit = attempt/'attempt-permit.json'
assert sha(permit) == '7272a5ebe90d92d1b9ddb7ff80abe8108de1e1715ed8af2913b684f58910a4b3'
assert json.loads(permit.read_text()) == payload['permit']
assert not (attempt/'consumed.json').exists()
assert not (root/'stageb2-attempt-4-result.json').exists()
assert not (root/'stageb2-attempt-4-result.milestones.jsonl').exists()
package = json.loads((root/'PACKAGE_SHA256.json').read_text())
for relative, digest in package['files'].items():
    assert sha(root/relative) == digest, relative
for relative,digest in payload['new_source_hashes'].items():
    assert sha(root/relative) == digest
handles = subprocess.run(['fuser','/dev/video0','/dev/video1','/dev/mem'],capture_output=True)
assert handles.returncode == 1, 'device handles occupied'
listeners = subprocess.check_output(['ss','-ltnp'],text=True)
assert ':8080 ' not in listeners and ':8081 ' not in listeners
assert Path('/sys/class/fpga_manager/fpga0/state').read_text().strip() == 'operating'
assert Path('/sys/class/fpga_manager/fpga0/flags').read_text().strip() == '0'
def cma():
    return int(next(s.split()[1] for s in Path('/proc/meminfo').read_text().splitlines() if s.startswith('CmaFree:')))
before = cma()
assert before >= 32768
# Prior measured CV/WO import pressure warrants cache headroom, once, idle only.
reclaimed = before < 100000
if reclaimed:
    os.sync()
    Path('/proc/sys/vm/drop_caches').write_text('3\n')
after = cma()
assert after >= 70000, 'insufficient initialization headroom; do not retry'
request = {'authorization':'User explicitly requested Stage B-2 Attempt4 now; physical environment ready; browser click triggers countdown',
    'boot_id':boot,'ready_guard_sha256':sha(ready),'permit_sha256':sha(permit),
    'command':payload['prepared_command'],'cma_before_reclaim_kib':before,
    'cma_after_reclaim_kib':after,'controlled_drop_caches':reclaimed,
    'dispatch_monotonic':time.monotonic(),'source_hashes':payload['new_source_hashes']}
with (attempt/'dispatch-request.json').open('x') as stream:
    json.dump(request,stream,indent=2);stream.flush();os.fsync(stream.fileno())
env = os.environ.copy()
env['XILINX_XRT']='/usr'
env['PYTHONUNBUFFERED']='1'
with (attempt/'stdout.log').open('x') as log:
    process = subprocess.Popen(payload['prepared_command'],cwd=str(root),env=env,
        stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
with (attempt/'launch.json').open('x') as stream:
    json.dump({**request,'pid':process.pid},stream,indent=2);stream.flush();os.fsync(stream.fileno())
print(json.dumps({'status':'DISPATCHED_ONCE_WAIT_BROWSER','pid':process.pid,
    'cma_before_kib':before,'cma_after_kib':after,'control_url':'http://192.168.2.99:8081/'}),flush=True)
