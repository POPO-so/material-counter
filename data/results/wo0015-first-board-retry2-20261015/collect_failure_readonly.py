"""Read-only exit evidence: no MMIO, Overlay construction, DMA, or buffer allocation."""
import json,sys,subprocess
from pathlib import Path
WO=Path('/home/xilinx/wo0015_first_board_retry1_20261015')
sys.path.insert(0,str(WO))
import board_preflight as pre
def attempt(fn):
    try:return fn()
    except Exception as e:return {'error':type(e).__name__+': '+str(e)}
r={'boot_id':pre.read_boot_id(),'cma':pre.read_cma_meminfo(),
'ownership':attempt(pre.audit_process_ownership),'fpga_manager':attempt(pre.read_fpga_manager),
'kernel_log':attempt(pre.read_kernel_log),
'guards':{str(p):p.read_text() for p in Path('/var/lib/wo0015-s2mm').glob('*') if p.is_file()},
'ps':subprocess.run(['ps','-eo','pid,ppid,stat,comm,args'],capture_output=True,text=True).stdout}

import hashlib,os
def read_text(p):
    try:return Path(p).read_text()
    except OSError as e:return {'error':str(e)}
r['cmdline']=read_text('/proc/cmdline')
r['iomem']=read_text('/proc/iomem')
r['kernel_release']=os.uname().release
r['device_tree_paths']={}
for name in ('/proc/device-tree','/proc/device-tree/reserved-memory','/sys/firmware/devicetree/base','/sys/firmware/devicetree/base/reserved-memory'):
    p=Path(name)
    r['device_tree_paths'][name]={'exists':p.exists(),'is_dir':p.is_dir(),'resolved':str(p.resolve()),'children':sorted(q.name for q in p.iterdir()) if p.is_dir() else []}
r['deployed_sha256']={n:hashlib.sha256((WO/n).read_bytes()).hexdigest() for n in ('wo15.bit','wo15.hwh','board_once.py','board_preflight.py','run-manifest.json','SHA256SUMS.json')}
r['meminfo_raw']=read_text('/proc/meminfo')
r['hardware_stage_result']=json.loads((WO/'retry2-overlay-only-result.json').read_text())

(WO/'exit-os-result.json').write_text(json.dumps(r,indent=2))
print(json.dumps(r,indent=2),flush=True)
