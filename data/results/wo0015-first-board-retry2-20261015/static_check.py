import sys,json,hashlib
from pathlib import Path
p=Path('/home/xilinx/wo0015_first_board_retry1_20261015')
sys.path.insert(0,str(p))
import board_preflight as pre
plan=pre.validate_manifest(p/'run-manifest.json','ceac16e28b968bc28d3e4f30828402447c9dbe8c566f9800a789478a435a3d8b')
assert pre.AUTHORIZED_BOOT_ID==plan.boot_id==pre.read_boot_id()=='822a9c29-37c0-438c-81c4-da998d57073b'
sums=json.loads((p/'SHA256SUMS.json').read_text())
assert all(hashlib.sha256((p/n).read_bytes()).hexdigest()==h for n,h in sums.items())
owners=pre.audit_process_ownership()
r={'status':'STATIC_CONFIGURATION_PASS','manifest_boot_id':plan.boot_id,'executor_boot_id':pre.AUTHORIZED_BOOT_ID,'live_boot_id':pre.read_boot_id(),'meminfo':pre.read_cma_meminfo(),'ownership':owners,'manager':pre.read_fpga_manager(),'sha256':{n:hashlib.sha256((p/n).read_bytes()).hexdigest() for n in ('wo15.bit','wo15.hwh','run-manifest.json','board_once.py','board_preflight.py')},'config_owner_uids':{n:(p/n).stat().st_uid for n in ('board_preflight.py','run-manifest.json','SHA256SUMS.json')}}
print(json.dumps(r,indent=2),flush=True)
