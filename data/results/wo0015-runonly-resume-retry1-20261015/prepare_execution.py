"""Prepare fixed commands only. Running this host script never contacts the board."""
import base64
import hashlib
import json
from pathlib import Path
import shlex

OUT=Path(__file__).resolve().parent
REMOTE='/home/xilinx/wo0015_runonly_resume_retry1_20261015'
WO='/home/xilinx/wo0015_first_board_retry3_20261015'
PY='/usr/local/share/pynq-venv/bin/python'
MANIFEST='84c27012ec038d48267128af08deb9685eb1bb9a5fdb1a79e5da064083788312'
def sha(b):return hashlib.sha256(b).hexdigest()
names=('recovery_adapter.py','run_only_model.py','locked-evidence.json','collect_exit.py')
execution={n:sha((OUT/n).read_bytes()) for n in names}
(OUT/'execution-lock.json').write_text(json.dumps(execution,sort_keys=True,indent=2),encoding='utf-8')
files={n:base64.b64encode((OUT/n).read_bytes()).decode('ascii') for n in names+('execution-lock.json',)}
launch_lock={n:sha((OUT/n).read_bytes()) for n in files}
def command(source,*,pynq=False):
    program='import base64;exec(compile(base64.b64decode('+repr(base64.b64encode(source.encode()).decode())+'),"wo0015-authorized-recovery","exec"))'
    prefix='sudo -S -p "" '
    if pynq:prefix+='env XILINX_XRT=/usr '
    return prefix+PY+(' -B -u -c ' if pynq else ' -I -S -B -u -c ')+shlex.quote(program)
deploy='''from pathlib import Path
import base64,hashlib,json,os
assert Path('/proc/sys/kernel/random/boot_id').read_text().strip()=='822a9c29-37c0-438c-81c4-da998d57073b'
out=Path(REMOTE)
assert not out.is_symlink() and not out.exists()
assert all(not p.is_symlink() for p in out.parents)
out.mkdir(mode=0o755)
for name,encoded in FILES.items():
 data=base64.b64decode(encoded)
 assert hashlib.sha256(data).hexdigest()==LOCK[name]
 fd=os.open(str(out/name),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
 with os.fdopen(fd,'wb') as f:
  f.write(data);f.flush();os.fsync(f.fileno())
 assert hashlib.sha256((out/name).read_bytes()).hexdigest()==LOCK[name]
print(json.dumps({'status':'RECOVERY_DEPLOYED_NO_HARDWARE_ACCESS','hashes':LOCK}),flush=True)
'''
deploy='REMOTE='+repr(REMOTE)+'\nFILES='+repr(files)+'\nLOCK='+repr(launch_lock)+'\n'+deploy
def launch(filename,args):
    source='''from pathlib import Path
import hashlib,sys,runpy
out=Path(REMOTE)
assert all(not p.is_symlink() for p in (out,*out.parents))
for name,digest in LOCK.items():
 assert not (out/name).is_symlink()
 assert hashlib.sha256((out/name).read_bytes()).hexdigest()==digest, 'recovery launch hash mismatch: '+name
sys.path.insert(0,str(out))
sys.argv=[str(out/FILE)]+ARGS
runpy.run_path(str(out/FILE),run_name='__main__')
'''
    return command('REMOTE='+repr(REMOTE)+'\nLOCK='+repr(launch_lock)+'\nFILE='+repr(filename)+'\nARGS='+repr(args)+'\n'+source,pynq=True)
templates={n:{'op':'command','command':c,'timeout':120} for n,c in [
    ('deploy',command(deploy)),('run-only',launch('recovery_adapter.py',['run-only'])),
    ('archive',launch('recovery_adapter.py',['archive'])),
    ('exit-pass',launch('collect_exit.py',[])),('exit-failure',launch('collect_exit.py',['--failure']))]}
for mode in ('dma-single','dma-three'):
    # Original unchanged executor/manifest; only the evidence destination is new.
    c='sudo -S -p "" env XILINX_XRT=/usr '+PY+' -B -u '+WO+'/board_once.py --mode '+mode+' --manifest '+WO+'/run-manifest.json --manifest-sha256 '+MANIFEST+' --execute --authorize-stage '+mode+' --approve-once --timeout-s 5 --evidence-json '+REMOTE+'/'+mode+'-result.json'
    templates[mode]={'op':'command','command':c,'timeout':120}
(OUT/'command-templates.json').write_text(json.dumps(templates,indent=2),encoding='utf-8')
(OUT/'launch-lock.json').write_text(json.dumps(launch_lock,indent=2),encoding='utf-8')
print(json.dumps({'commands':list(templates),'launch_lock':launch_lock},indent=2))
