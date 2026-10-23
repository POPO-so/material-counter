"""Materialize individual requests; this file never dispatches board operations."""
import base64,json,shlex
from pathlib import Path
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
WO='/home/xilinx/wo0015_first_board_retry3_20261015'
PY='/usr/local/share/pynq-venv/bin/python'
receipt=json.loads((OUT/'updated-bundle-receipt.json').read_text())
H=receipt['manifest_sha256']
def command(source,site=False):
    payload=base64.b64encode(source.encode()).decode()
    program='import base64;exec(compile(base64.b64decode('+repr(payload)+'),"retry3-controlled-audit","exec"))'
    return 'sudo -S -p "" '+('env XILINX_XRT=/usr ' if site else '')+PY+(' -u' if site else ' -I -S -B')+' -c '+shlex.quote(program)
templates={'upload':{'op':'upload','path':receipt['archive'],'sha256':receipt['archive_sha256']}}
for mode in ('offline','overlay-only','dma-single','dma-three'):
    cmd=f'sudo -S -p "" env XILINX_XRT=/usr {PY} -u {WO}/board_once.py --mode {mode} --manifest {WO}/run-manifest.json --manifest-sha256 {H}'
    if mode!='offline':cmd+=f' --execute --authorize-stage {mode} --approve-once --timeout-s 5'
    cmd+=f' --evidence-json {WO}/{mode}-result.json'
    templates[mode]={'op':'command','command':cmd,'timeout':60}
initial=(OUT.parent/'wo0015-first-board-retry1-20261015/read_initial_state.py').read_text()
initial=initial.replace('/home/xilinx/wo0015_first_board_retry1_20261015',WO)
initial=initial.replace('ceac16e28b968bc28d3e4f30828402447c9dbe8c566f9800a789478a435a3d8b',H)
initial=initial.replace("sys.exit(0 if not errors else 2)",
    "if errors:\n    run._record_terminal_failure('initial-state',plan,'; '.join(errors))\nsys.exit(0 if not errors else 2)")
(OUT/'read_initial_state.py').write_text(initial,encoding='utf-8')
templates['initial-state']={'op':'command','command':command(initial,True),'timeout':45}
exit_source=(OUT.parent/'wo0015-first-board-retry1-20261015/read_exit_os.py').read_text()
exit_source=exit_source.replace('/home/xilinx/wo0015_first_board_retry1_20261015',WO)
needle="(WO/'exit-os-result.json').write_text(json.dumps(r,indent=2))"
extra="""
import hashlib
r['deployed_sha256']={n:hashlib.sha256((WO/n).read_bytes()).hexdigest() for n in ('wo15.bit','wo15.hwh','board_once.py','board_preflight.py','run-manifest.json','SHA256SUMS.json')}
r['stage_results']={n:json.loads((WO/n).read_text()) for n in ('offline-result.json','overlay-only-result.json','initial-state-result.json','dma-single-result.json','dma-three-result.json') if (WO/n).exists()}
"""
exit_source=exit_source.replace(needle,extra+'\n'+needle)
(OUT/'read_exit_os.py').write_text(exit_source,encoding='utf-8')
templates['exit-os']={'op':'command','command':command(exit_source),'timeout':45}
static=f'''import sys,json,hashlib
from pathlib import Path
p=Path({WO!r})
sys.path.insert(0,str(p))
import board_preflight as pre
plan=pre.validate_manifest(p/'run-manifest.json',{H!r})
assert pre.AUTHORIZED_BOOT_ID==plan.boot_id==pre.read_boot_id()=='822a9c29-37c0-438c-81c4-da998d57073b'
sums=json.loads((p/'SHA256SUMS.json').read_text())
assert all(hashlib.sha256((p/n).read_bytes()).hexdigest()==h for n,h in sums.items())
mem=pre.read_cma_meminfo()
admission=pre.read_cma_admission(meminfo=mem)
pre.validate_runtime_cma(mem,admission.ranges,(plan.hp0_range,plan.hp1_range))
owners=pre.audit_process_ownership()
r={{'status':'STATIC_TIER1_PASS','boot_id':pre.read_boot_id(),'meminfo':mem,'cma_admission':admission.as_evidence(),'ownership':owners,'manager':pre.read_fpga_manager(),'sha256':{{n:hashlib.sha256((p/n).read_bytes()).hexdigest() for n in ('wo15.bit','wo15.hwh','run-manifest.json','board_once.py','board_preflight.py')}},'file_owner_uids':{{n:(p/n).stat().st_uid for n in ('board_once.py','board_preflight.py','run-manifest.json')}}}}
assert all(uid==1000 for uid in r['file_owner_uids'].values())
print(json.dumps(r,indent=2),flush=True)
'''
(OUT/'static_check.py').write_text(static,encoding='utf-8')
templates['static']={'op':'command','command':command(static),'timeout':45}
templates['download-exit']={'op':'download','files':{WO+'/exit-os-result.json':'exit-os-result.json'}}
(OUT/'command-templates.json').write_text(json.dumps(templates,indent=2),encoding='utf-8')
print('COMMANDS_READY_NO_DISPATCH')
