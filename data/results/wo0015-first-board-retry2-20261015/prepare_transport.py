"""Prepare the explicitly authorized Retry 2 transport; no board operations here."""
import ast,base64,hashlib,json,shlex,subprocess
from pathlib import Path
OUT=Path(__file__).resolve().parent
PREV=OUT.parent/'wo0015-first-board-retry1-20261015'
ROOT=OUT.parents[2]
WO='/home/xilinx/wo0015_first_board_retry1_20261015'
PY='/usr/local/share/pynq-venv/bin/python'
rebind=json.loads((PREV/'authorization-rebind.json').read_text())
for folder in (PREV,OUT.parent/'wo0015-first-board-20261015'):
    index=json.loads((folder/'evidence-sha256.json').read_text())
    assert all(hashlib.sha256((folder/n).read_bytes()).hexdigest()==h for n,h in index.items())
for n,h in rebind['after'].items():
    assert hashlib.sha256((PREV/'authorized-deployment'/n).read_bytes()).hexdigest()==h
(OUT/'authorization-rebind-reference.json').write_text(json.dumps(rebind,indent=2),encoding='utf-8')
controller=(PREV/'controller.py').read_text(encoding='utf-8')
ast.parse(controller)
(OUT/'controller.py').write_text(controller,encoding='utf-8')
for name in ('read_initial_state.py','read_exit_os.py'):
    (OUT/name).write_bytes((PREV/name).read_bytes())
def pycommand(source,site=False):
    payload=base64.b64encode(source.encode()).decode()
    program='import base64;exec(compile(base64.b64decode('+repr(payload)+'),"retry2-controlled-preparation-or-readonly","exec"))'
    return 'sudo -S -p "" '+('env XILINX_XRT=/usr ' if site else '')+PY+(' -u' if site else ' -I -S -B')+' -c '+shlex.quote(program)
repair='''import os,pwd,json,hashlib,subprocess
from pathlib import Path
p=Path('/home/xilinx/wo0015_first_board_retry1_20261015')
assert Path('/proc/sys/kernel/random/boot_id').read_text().strip()=='822a9c29-37c0-438c-81c4-da998d57073b'
assert not p.is_symlink() and p.resolve()==p and p.is_dir()
entries=[p]+list(p.rglob('*'))
assert all(not q.is_symlink() and (q==p or p in q.resolve().parents) for q in entries)
assert not list(Path('/var/lib/wo0015-s2mm').glob('*'))
expected={'wo15.bit':'a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420','wo15.hwh':'bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6'}
def hashes():return {n:hashlib.sha256((p/n).read_bytes()).hexdigest() for n in expected}
assert hashes()==expected
before={str(q.relative_to(p)):{'uid':q.stat().st_uid,'gid':q.stat().st_gid,'mode':oct(q.stat().st_mode&0o777)} for q in entries}
account=pwd.getpwnam('xilinx')
subprocess.run(['chown','-R','--no-dereference','xilinx:xilinx',str(p)],check=True)
assert all(q.stat().st_uid==account.pw_uid and q.stat().st_gid==account.pw_gid for q in entries)
assert all((q.stat().st_mode&0o600)==0o600 for q in entries if q.is_file())
assert hashes()==expected
result={'status':'PERMISSION_REPAIR_PASS','path':str(p),'uid':account.pw_uid,'gid':account.pw_gid,'before':before,'sha256_after':hashes(),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
print(json.dumps(result,indent=2),flush=True)
'''
(OUT/'repair_permissions.py').write_text(repair,encoding='utf-8')
templates={'repair':{'op':'command','command':pycommand(repair),'timeout':45},
'configure':{'op':'upload-config','files':{n:str(PREV/'authorized-deployment'/n) for n in ('board_preflight.py','run-manifest.json','SHA256SUMS.json')}}}
H=rebind['manifest_sha256']
for mode in ('offline','overlay-only','dma-single','dma-three'):
    cmd=f'sudo -S -p "" env XILINX_XRT=/usr {PY} -u {WO}/board_once.py --mode {mode} --manifest {WO}/run-manifest.json --manifest-sha256 {H}'
    if mode!='offline':cmd+=f' --execute --authorize-stage {mode} --approve-once --timeout-s 5'
    cmd+=f' --evidence-json {WO}/retry2-{mode}-result.json'
    templates[mode]={'op':'command','command':cmd,'timeout':60}
for name,file in [('initial-state','read_initial_state.py'),('exit-os','read_exit_os.py')]:
    templates[name]={'op':'command','command':pycommand((OUT/file).read_text(),site=True),'timeout':45}
static='''import sys,json,hashlib
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
'''
(OUT/'static_check.py').write_text(static,encoding='utf-8')
templates['static']={'op':'command','command':pycommand(static),'timeout':45}
(OUT/'command-templates.json').write_text(json.dumps(templates,indent=2),encoding='utf-8')
for label,path in [('main',Path(r'C:\fpga\material-counter')),('worktree',ROOT)]:
    (OUT/(label+'-git-before.txt')).write_text(subprocess.check_output(['git','-C',str(path),'status','--porcelain=v1'],text=True),encoding='utf-8')
print('RETRY2_PREPARATION_PASS; no board commands dispatched')
