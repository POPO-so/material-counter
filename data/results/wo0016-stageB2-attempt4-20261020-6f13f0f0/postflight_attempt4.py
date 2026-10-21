"""Read-only hardware postflight plus append-only operator audit records."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

boot = '6f13f0f0-4433-445f-a023-fa403685aeee'
assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() == boot
root = Path('/home/xilinx/board-runtime-currentboot-v2')
attempt = Path('/var/lib/wo0016/attempts')/boot/'attempt-4'
ready = Path('/var/lib/wo0016')/('stageb2-ready-'+boot+'.json')
sha = lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
result_path=root/'stageb2-attempt-4-result.json'
result=json.loads(result_path.read_text())
guard=json.loads(ready.read_text())
assert sha(ready)=='f15464025abeabc2e52681aaa000caf25da217cdf5505781c3d7fcba45cd5b9b'
assert guard['consumed'] is False
assert json.loads((attempt/'consumed.json').read_text())['consumed'] is True
exit_status=json.loads((attempt/'exit-status.json').read_text())
statuses=result['frame_dma_status']
assert len(statuses)==165 and [s['frame_id'] for s in statuses]==list(range(165))
mm2s=sum(s['mm2s']=='0x00001002' for s in statuses)
s2mm=sum(s['s2mm']=='0x00001002' for s in statuses)
handles=subprocess.run(['fuser','/dev/video0','/dev/video1','/dev/mem'],capture_output=True,text=True)
listeners=subprocess.check_output(['ss','-ltnp'],text=True)
package=json.loads((root/'PACKAGE_SHA256.json').read_text())
for relative,digest in package['files'].items():
    assert sha(root/relative)==digest, relative
record={
    'boot_id':boot,'monotonic':time.monotonic(),'result_sha256':sha(result_path),
    'frame_records_sha256':sha(attempt/'frame-results.json'),
    'frames_processed':result['frames_processed'],'frame_records':exit_status['frame_records'],
    'exit_code':exit_status['exit_code'],'mm2s_pass':mm2s,'s2mm_pass':s2mm,
    'frames_expected':165,'buffers_released':result['buffers_released'],
    'cma_free_after_release_kib':result['cma_free_after_release_kib'],
    'cma_free_after_process_exit_kib':int(next(s.split()[1] for s in Path('/proc/meminfo').read_text().splitlines() if s.startswith('CmaFree:'))),
    'ready_guard_sha256':sha(ready),'ready_guard_consumed':guard['consumed'],
    'attempt_marker_consumed':True,'video_and_mem_handles_free':handles.returncode==1,
    'fuser_stdout':handles.stdout,'fuser_stderr':handles.stderr,
    'port8080_listening':':8080 ' in listeners,'port8081_listening':':8081 ' in listeners,
    'fpga_manager':{'state':Path('/sys/class/fpga_manager/fpga0/state').read_text().strip(),
                    'flags':Path('/sys/class/fpga_manager/fpga0/flags').read_text().strip()},
    'deployed_package_hashes_verified':True,'automatic_retry':False,
    'stage_b2_acceptance':'FAIL_EXPECTED_FIVE_NOT_ESTABLISHED',
    'count':result['total_count'],'expected':5}
statement={'attempt_id':'attempt-4','source':'Direct human reply in current chat',
    'operator_reply':'Five nuts fed, but one may have passed after sampling ended',
    'five_in_window_confirmed':False,'late_fifth':'POSSIBLE_NOT_PROVEN',
    'classification':'FIVE_NUT_ACCEPTANCE_NOT_ESTABLISHED_POSSIBLE_WINDOW_MISS'}
for name,value in [('postflight.json',record),('operator-statement.json',statement)]:
    with (attempt/name).open('x',encoding='utf-8') as stream:
        json.dump(value,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    (attempt/name).chmod(0o600)
print(json.dumps(record),flush=True)
