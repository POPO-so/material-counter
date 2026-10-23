import sys,json,hashlib
from pathlib import Path
p=Path('/home/xilinx/wo0015_first_board_retry3_20261015')
sys.path.insert(0,str(p))
import board_preflight as pre
plan=pre.validate_manifest(p/'run-manifest.json','84c27012ec038d48267128af08deb9685eb1bb9a5fdb1a79e5da064083788312')
assert pre.AUTHORIZED_BOOT_ID==plan.boot_id==pre.read_boot_id()=='822a9c29-37c0-438c-81c4-da998d57073b'
sums=json.loads((p/'SHA256SUMS.json').read_text())
assert all(hashlib.sha256((p/n).read_bytes()).hexdigest()==h for n,h in sums.items())
mem=pre.read_cma_meminfo()
admission=pre.read_cma_admission(meminfo=mem)
pre.validate_runtime_cma(mem,admission.ranges,(plan.hp0_range,plan.hp1_range))
owners=pre.audit_process_ownership()
r={'status':'STATIC_TIER1_PASS','boot_id':pre.read_boot_id(),'meminfo':mem,'cma_admission':admission.as_evidence(),'ownership':owners,'manager':pre.read_fpga_manager(),'sha256':{n:hashlib.sha256((p/n).read_bytes()).hexdigest() for n in ('wo15.bit','wo15.hwh','run-manifest.json','board_once.py','board_preflight.py')},'file_owner_uids':{n:(p/n).stat().st_uid for n in ('board_once.py','board_preflight.py','run-manifest.json')}}
assert all(uid==1000 for uid in r['file_owner_uids'].values())
print(json.dumps(r,indent=2),flush=True)
