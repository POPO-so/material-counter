"""One exit evidence collection. No transfer, reset, Overlay, or CMA allocation."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

WO=Path('/home/xilinx/wo0015_first_board_retry3_20261015')
OUT=Path('/home/xilinx/wo0015_runonly_resume_retry1_20261015')
BOOT='822a9c29-37c0-438c-81c4-da998d57073b'
def attempt(fn):
    try:return fn()
    except BaseException as e:return {'error':type(e).__name__+': '+str(e)}
def sha(data):return hashlib.sha256(data).hexdigest()
locks=json.loads((OUT/'locked-evidence.json').read_bytes())
for n,h in locks['deployed_sha256'].items():
    if sha((WO/n).read_bytes())!=h:raise RuntimeError('deployed artifact differs: '+n)
sys.path.insert(0,str(WO))
import board_preflight as pre
import board_once as run
plan=pre.validate_manifest(WO/'run-manifest.json',locks['deployed_sha256']['run-manifest.json'])
if '--failure' in sys.argv:
    run._record_terminal_failure('run-only-recovery-stopped',plan,'Manager stopped at first failed recovery prerequisite/stage; see independent recovery evidence.')
r={'boot_id':pre.read_boot_id(),'cma':attempt(pre.read_cma_meminfo),
   'ownership':attempt(pre.audit_process_ownership),'fpga_manager':attempt(pre.read_fpga_manager),
   'kernel_log':attempt(pre.read_kernel_log),'iomem':Path('/proc/iomem').read_text(),
   'ps':subprocess.run(['ps','-eo','pid,ppid,stat,comm,args'],capture_output=True,text=True).stdout,
   'deployed_sha256':{n:sha((WO/n).read_bytes()) for n in locks['deployed_sha256']}}
if r['boot_id']==BOOT and 'error' not in r['ownership'] and 'error' not in r['fpga_manager']:
    def read_registers():
        api=run.PynqBoardAPI(plan,stage='dma-single')
        environment=api.audit_environment(require_empty_shutdown=False,require_loaded_bit=True)
        api.open_mmio()
        return {'environment':environment,
                'checker':{f'0x{o:02x}':api.read32(pre.CHECKER_BASE,o) for o in range(0,0x64,4)},
                'dma':{f'0x{o:02x}':api.read32(pre.DMA_BASE,o) for o in (0,4,0x18,0x28,0x30,0x34,0x48,0x58)}}
    r['final_registers']=attempt(read_registers)
r['guards']={str(p):{'sha256':sha(p.read_bytes()),'base64':base64.b64encode(p.read_bytes()).decode('ascii')}
             for p in Path('/var/lib/wo0015-s2mm').rglob('*.json') if p.is_file() and not p.is_symlink()}
r['result_files']={p.name:{'sha256':sha(p.read_bytes()),'base64':base64.b64encode(p.read_bytes()).decode('ascii')}
                   for p in OUT.iterdir() if p.is_file() and not p.is_symlink()}
print(json.dumps(r),flush=True)
