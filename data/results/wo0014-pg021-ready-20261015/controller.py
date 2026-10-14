"""Reviewed PG021 recovery -> one Overlay -> MMIO -> one DMA, native auth."""
import ast
import base64
import difflib
import importlib.util
import json
import queue
import shlex
import subprocess
import sys
import threading
import time
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
CODE = ROOT/'scripts/wo0014_hw/diagnostic50'
OLD = ROOT/'data/results/wo0014-coldstate-fix-20261015'
spec = importlib.util.spec_from_file_location('native_previous', OLD/'controller.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
native.OUT = OUT
retained = []
result = {'status':'NOT_STARTED','hardware_test_pass':False,'actions':[]}

def action(program, name, hardware=False):
    encoded = base64.b64encode(zlib.compress(program.encode())).decode()
    outer = "import base64,zlib;print('ACTION_BEGIN',flush=True);exec(compile(zlib.decompress(base64.b64decode("+repr(encoded)+")),'authorized-recovery-action','exec'))"
    remote = "sudo -p '["+name+" sudo] Password: ' /usr/local/share/pynq-venv/bin/python -I -S -B -c "+shlex.quote(outer)
    argv=['ssh','-tt','-v','-E',str(OUT/(name+'-ssh-debug.log')),*native.options(),'xilinx@192.168.2.99',remote]
    if native.sha(native.KNOWN)!=native.KNOWN_SHA or len(subprocess.list2cmdline(argv))>30000:
        raise RuntimeError('Known hosts or command length gate failed')
    with (OUT/(name+'-dispatch.json')).open('x') as f:
        json.dump({'argv':argv,'hardware':hardware},f)
    native.event(name+'_AUTH_BEGIN')
    p = subprocess.Popen(argv,stdin=None,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    retained.append(p)
    messages=queue.Queue()
    def drain(pipe,kind):
        with (OUT/(name+'-'+kind+'.bin')).open('xb',buffering=0) as f:
            prefix=bytearray();persist=kind!='stdout'
            while True:
                chunk=pipe.read1(4096)
                if not chunk:break
                messages.put((kind,chunk))
                if persist:f.write(chunk)
                else:
                    prefix.extend(chunk)
                    index=prefix.find(b'ACTION_BEGIN')
                    if index>=0:
                        f.write(prefix[index:]);prefix.clear();persist=True
                    elif len(prefix)>8192:
                        # Authentication output is not execution evidence.
                        prefix=prefix[-64:]
        messages.put((kind,None))
    for pipe,kind in ((p.stdout,'stdout'),(p.stderr,'stderr')):
        threading.Thread(target=drain,args=(pipe,kind),daemon=True).start()
    deadline=time.monotonic()+150
    started=False;closed=set();stdout=bytearray();display=bytearray()
    while len(closed)<2:
        if time.monotonic()>=deadline:
            raise TimeoutError(name+' transport timeout; SSH retained, no retry')
        try:kind,data=messages.get(timeout=.1)
        except queue.Empty:continue
        if data is None:closed.add(kind);continue
        if kind=='stderr':
            sys.stdout.buffer.write(data);sys.stdout.buffer.flush()
            continue
        stdout.extend(data)
        display.extend(data)
        if not started and b'Password: ' in display:
            print('\n[Native sudo authentication] Password: ',end='',flush=True);display.clear()
        while b'\n' in display:
            line,_,remaining=display.partition(b'\n');display=bytearray(remaining)
            if (started or b'ACTION_BEGIN' in line) and not line.startswith((b'ENV_JSON ',b'ACTION_JSON ')):
                sys.stdout.buffer.write(line+b'\n');sys.stdout.buffer.flush()
        if not started and b'ACTION_BEGIN' in stdout:
            started=True;deadline=time.monotonic()+140
            stdout=stdout[stdout.index(b'ACTION_BEGIN'):]
            native.event(name+'_EXECUTION_BEGIN')
        if b'ACTION_FAILED_HOLD' in stdout:
            raise RuntimeError(name+' failed; remote session retained, no next stage')
    code=p.wait(timeout=3)
    native.event(name+'_EXIT',exit_code=code)
    native.verified_auth(name)
    if code:raise RuntimeError(name+' nonzero exit; no retry')
    rows=[json.loads(line[12:]) for line in stdout.decode(errors='replace').splitlines() if line.startswith('ACTION_JSON ')]
    if len(rows)!=1 or rows[0]['status']!='PASS':raise RuntimeError(name+' PASS evidence missing')
    (OUT/(name+'-result.json')).write_text(json.dumps(rows[0],indent=2)+'\n')
    return rows[0]

def main():
    reviewed=json.loads((OUT/'reviewed-local.json').read_text())
    if not reviewed['tests_pass']:raise RuntimeError('Local regression gate failed')
    for name,digest in reviewed['source_sha256'].items():
        if native.sha(CODE/name)!=digest:raise RuntimeError('Reviewed source changed '+name)
    deployment=json.loads((OUT/'deployment-config.json').read_text())
    previous=json.loads((OUT/'previous-failure.json').read_text())
    previous={k:previous[k] for k in ('boot_id','kernel_lines')}
    previous['kernel_lines']=previous['kernel_lines'][-8:]
    source=(OUT/'snapshot_source.py').read_text()
    support=(OUT/'remote_support.py').read_text()
    native.ping('cleanup-pre')
    reference=json.loads((OUT/'previous-failure-reference.json').read_text())
    program='deployment='+repr(deployment)+'\nprevious_os='+repr(previous)+'\nsnapshot_source='+repr(source)+'\nprevious_failure='+repr(reference)+'\n'+support+'\n'+(OUT/'remote_cleanup.py').read_text()
    recovered=action(program,'cleanup-deploy')
    archive=OUT/'archived-board-verified'
    archive.mkdir()
    for name,encoded in recovered['archive_bytes'].items():
        path=archive/name
        if not path.resolve().is_relative_to(archive.resolve()):raise RuntimeError('Invalid archive path')
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(zlib.decompress(base64.b64decode(encoded)))
    for name,digest in recovered['archive']['files'].items():
        if native.sha(archive/name)!=digest:raise RuntimeError('Archive SHA differs '+name)
    result['guard_archive_verified']=True
    result['actions'].append('cleanup-deploy')
    result['cleanup']=recovered['cleanup']
    previous={k:recovered['post'][k] for k in ('boot_id','kernel_lines')}
    previous['kernel_lines']=previous['kernel_lines'][-8:]
    native.ping('cleanup-post')
    for mode in ('overlay-only','mmio-read-only','dma-once'):
        native.ping(mode+'-pre')
        config=dict(deployment['new_config'])
        config['remote_evidence_directory']='/home/xilinx/wo0014_pg021_'+mode.replace('-','_')+'_20261015'
        stage_deployment={'new_config':config}
        program='deployment='+repr(stage_deployment)+'\nprevious_os='+repr(previous)+'\nsnapshot_source='+repr(source)+'\nmode='+repr(mode)+'\n'+support+'\n'+(OUT/'remote_stage.py').read_text()
        done=action(program,mode,hardware=True)
        native.ping(mode+'-post')
        previous={k:done['post'][k] for k in ('boot_id','kernel_lines')}
        previous['kernel_lines']=previous['kernel_lines'][-8:]
        result['actions'].append(mode)
        native.event(mode+'_PASS')
        (OUT/'progress-result.json').write_text(json.dumps(result,indent=2))
    result.update(status='OVERLAY_MMIO_SINGLE_FRAME_DMA_PASS',hardware_test_pass=True)

if __name__=='__main__':
    try:
        print('Authorized: archive PID6311 no-SEND failure and reclaim CMA -> Overlay ONCE -> MMIO -> DMA ONCE. Use native hidden SSH/sudo prompts.',flush=True)
        main()
    except BaseException as exc:
        result.update(status='FAIL_STOPPED',error=type(exc).__name__+': '+str(exc))
        native.event('FAIL_STOPPED',error=result['error'])
    finally:
        result['known_hosts_unchanged']=native.sha(native.KNOWN)==native.KNOWN_SHA
        (OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2),flush=True)
        if any(p.poll() is None for p in retained):
            print('Retaining SSH/session. No further hardware commands.',flush=True)
            while True:time.sleep(1)
        else:input('Press Enter to close. ')
