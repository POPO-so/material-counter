"""Only collect OS/filesystem evidence after pre-MMIO CMA topology rejection."""
import base64,json,shlex
from pathlib import Path
OUT=Path(__file__).resolve().parent
WO='/home/xilinx/wo0015_first_board_retry1_20261015'
source=(OUT/'read_exit_os.py').read_text(encoding='utf-8')
needle="(WO/'exit-os-result.json').write_text(json.dumps(r,indent=2))"
extra='''
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
'''
assert needle in source
source=source.replace(needle,extra+'\n'+needle)
(OUT/'collect_failure_readonly.py').write_text(source,encoding='utf-8')
encoded=base64.b64encode(source.encode()).decode()
program='import base64;exec(compile(base64.b64decode('+repr(encoded)+'),"retry2-failure-readonly","exec"))'
command='sudo -S -p "" env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -u -c '+shlex.quote(program)
files={WO+'/retry2-offline-result.json':'board-offline-result.json',WO+'/retry2-overlay-only-result.json':'overlay-only-result.json',WO+'/exit-os-result.json':'exit-os-result.json'}
with (OUT/'failure-evidence-request.json').open('x',encoding='utf-8') as f:json.dump({'command':command,'files':files},f)
print('READONLY_FAILURE_COLLECTION_SUBMITTED_ONCE')
