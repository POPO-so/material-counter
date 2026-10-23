"""Host-only preparation of a hash-pinned continuation package and commands."""
import base64
import hashlib
import json
from pathlib import Path
import shlex
import zipfile

OUT=Path(__file__).resolve().parent
REMOTE='/var/lib/wo0015-s2mm/continuations/watermark-retry2-20261015'
STAGING='/home/xilinx/wo0015_runonly_resume_retry2_20261015'
WO='/home/xilinx/wo0015_first_board_retry3_20261015'
PY='/usr/local/share/pynq-venv/bin/python'
BOOT='822a9c29-37c0-438c-81c4-da998d57073b'
def sha(raw):return hashlib.sha256(raw).hexdigest()
locks=json.loads((OUT/'locked-evidence.json').read_bytes())
config={'schema':1,'status':'AUTHORIZED_CMA_WATERMARK_CONTINUATION','boot_id':BOOT,
        'base_manifest_sha256':locks['deployed_sha256']['run-manifest.json'],
        'base_deployment_sha256':locks['deployed_sha256'],
        'executor_sha256':sha((OUT/'board_once_watermark.py').read_bytes()),
        'executor_origin':'byte-identical to updated scripts/wo0015/board_once.py',
        'changed_logic':['CMA deltas/exact restoration become telemetry','16 MiB allocation/release watermarks',
                         'nonzero integer PYNQ physical/device addresses','Mock CMA baseline'],
        'legacy_overlay_receipt_unchanged':True,'legacy_single_consumed_retained':True,
        'single_guard':'/var/lib/wo0015-s2mm/dma-single-watermark-retry2-consumed.json',
        'three_guard':'/var/lib/wo0015-s2mm/dma-three-watermark-retry2-consumed.json',
        'single_receipt':'/var/lib/wo0015-s2mm/dma-single-watermark-retry2-pass.json'}
(OUT/'continuation-execution.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
names=('board_once_watermark.py','transition.py','execute_stage.py','collect_exit.py','locked-evidence.json','continuation-execution.json')
execution={n:sha((OUT/n).read_bytes()) for n in names}
(OUT/'execution-lock.json').write_text(json.dumps(execution,indent=2),encoding='utf-8')
launch_lock={n:sha((OUT/n).read_bytes()) for n in names+('execution-lock.json',)}
archive=OUT/'recovery-package.zip'
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
    for n in launch_lock:z.write(OUT/n,n)
archive_sha=sha(archive.read_bytes())
def command(source,pynq=False):
    program='import base64;exec(compile(base64.b64decode('+repr(base64.b64encode(source.encode()).decode())+'),"wo0015-watermark-continuation","exec"))'
    return 'sudo -S -p "" '+('env XILINX_XRT=/usr ' if pynq else '')+PY+(' -B -u -c ' if pynq else ' -I -S -B -u -c ')+shlex.quote(program)
deploy='''from pathlib import Path
import hashlib,zipfile,os,json,io
assert Path('/proc/sys/kernel/random/boot_id').read_text().strip()==BOOT
out=Path(REMOTE)
assert all(not p.is_symlink() for p in (out,*out.parents))
assert not out.exists()
staging=Path(STAGING)
assert all(not p.is_symlink() for p in (staging,*staging.parents))
assert sorted(p.name for p in staging.iterdir())==['recovery.zip']
z=staging/'recovery.zip'
archive_bytes=z.read_bytes()
assert hashlib.sha256(archive_bytes).hexdigest()==ARCHIVE_SHA
with zipfile.ZipFile(io.BytesIO(archive_bytes)) as a:
 assert set(a.namelist())==set(LOCK)
 payload={n:a.read(n) for n in LOCK}
assert all(hashlib.sha256(payload[n]).hexdigest()==h for n,h in LOCK.items())
out.parent.mkdir(mode=0o755,exist_ok=True)
assert all(p.stat().st_uid==0 and not p.stat().st_mode & 0o022 for p in (out.parent,*out.parent.parents))
out.mkdir(mode=0o755)
os.chown(out,0,0);os.chmod(out,0o755)
for n,data in payload.items():
 assert Path(n).name==n
 fd=os.open(str(out/n),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
 with os.fdopen(fd,'wb') as f:
  f.write(data);f.flush();os.fsync(f.fileno())
 assert hashlib.sha256((out/n).read_bytes()).hexdigest()==LOCK[n]
print(json.dumps({'status':'RECOVERY_DEPLOYED_NO_HARDWARE_ACCESS','hashes':LOCK}),flush=True)
'''
deploy='BOOT='+repr(BOOT)+'\nREMOTE='+repr(REMOTE)+'\nSTAGING='+repr(STAGING)+'\nLOCK='+repr(launch_lock)+'\nARCHIVE_SHA='+repr(archive_sha)+'\n'+deploy
def launch(filename,args):
    source='''from pathlib import Path
import hashlib,sys,os,fcntl
out=Path(REMOTE)
assert all(not p.is_symlink() for p in (out,*out.parents))
assert all(p.stat().st_uid==0 and not p.stat().st_mode & 0o022 for p in (out,*out.parents))
payload={}
for name,digest in LOCK.items():
 assert not (out/name).is_symlink()
 data=(out/name).read_bytes()
 assert hashlib.sha256(data).hexdigest()==digest, 'continuation launch digest differs: '+name
 payload[name]=data
lock_fd=os.open(str(out/'execution.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
sys.path.insert(0,WO)
sys.argv=[str(out/FILE)]+ARGS
# Execute the same verified bytes, with __main__ globals registered for dataclasses.
__file__=str(out/FILE)
exec(compile(payload[FILE],__file__,'exec'),globals())
'''
    return command('REMOTE='+repr(REMOTE)+'\nWO='+repr(WO)+'\nLOCK='+repr(launch_lock)+'\nFILE='+repr(filename)+'\nARGS='+repr(args)+'\n'+source,True)
templates={'deploy':{'op':'upload-recovery','path':str(archive),'sha256':archive_sha,'command':command(deploy),'timeout':120}}
for n,f,args in [('transition','transition.py',[]),('dma-single','execute_stage.py',['dma-single']),
                 ('dma-three','execute_stage.py',['dma-three']),('exit-pass','collect_exit.py',[]),
                 ('exit-failure','collect_exit.py',['--failure'])]:
    templates[n]={'op':'command','command':launch(f,args),'timeout':120}
(OUT/'command-templates.json').write_text(json.dumps(templates,indent=2),encoding='utf-8')
(OUT/'launch-lock.json').write_text(json.dumps(launch_lock,indent=2),encoding='utf-8')
print(json.dumps({'archive_sha256':archive_sha,'launch_lock':launch_lock,'commands':list(templates)},indent=2))
