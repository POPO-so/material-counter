"""OS/files only after DMA admission failure; no PYNQ import, MMIO or cleanup."""
import base64
import importlib.util
import json
import shlex
import zlib
from pathlib import Path
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
spec=importlib.util.spec_from_file_location('native',ROOT/'data/results/wo0014-coldstate-fix-20261015/controller.py')
native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)
native.OUT=OUT
config=json.loads((OUT/'deployment-config.json').read_text())['new_config']
config['remote_evidence_directory']='/home/xilinx/wo0014_pg021_dma_once_20261015'
baseline=json.loads((OUT/'mmio-read-only-result.json').read_text())['post']
baseline={k:baseline[k] for k in ('boot_id','kernel_lines')}
baseline['kernel_lines']=baseline['kernel_lines'][-8:]
source=(OUT/'snapshot_source.py').read_text()
extra='''
import base64
evidence=Path(config['remote_evidence_directory'])
output['evidence_bytes']={str(p.relative_to(evidence)):base64.b64encode(p.read_bytes()).decode()
                        for p in evidence.rglob('*') if p.is_file()}
output['hardware_process_records']={str(p['pid']):{
    'cmdline':(Path('/proc')/str(p['pid'])/'cmdline').read_bytes().decode(errors='replace'),
    'stat':(Path('/proc')/str(p['pid'])/'stat').read_text(),
    'status':(Path('/proc')/str(p['pid'])/'status').read_text()}
    for p in output['process_audit']['conflicts']}
print('FAILURE_JSON '+json.dumps(output),flush=True)
'''
try:
    native.ping('failure-post')
    program='config='+repr(config)+'\nbaseline='+repr(baseline)+'\nphase="post"\n'+source+extra
    encoded=base64.b64encode(zlib.compress(program.encode())).decode()
    outer="import base64,zlib;print('VERIFICATION_STARTED',flush=True);exec(zlib.decompress(base64.b64decode("+repr(encoded)+")))"
    remote="sudo -p '[FAILURE READONLY sudo] Password: ' /usr/local/share/pynq-venv/bin/python -I -S -B -c "+shlex.quote(outer)
    stdout=native.run_native(['ssh','-tt','-v','-E',str(OUT/'failure-post-ssh-debug.log'),*native.options(),'xilinx@192.168.2.99',remote],'failure-post',sudo=True)
    native.verified_auth('failure-post')
    rows=[json.loads(s[13:]) for s in stdout.splitlines() if s.startswith('FAILURE_JSON ')]
    if len(rows)!=1:raise RuntimeError('Complete failure snapshot missing')
    data=rows[0]
    (OUT/'dma-failure-snapshot.json').write_text(json.dumps(data,indent=2)+'\n')
    folder=OUT/'dma-failure-board';folder.mkdir()
    for name,encoded in data['evidence_bytes'].items():
        path=folder/name
        if not path.resolve().is_relative_to(folder.resolve()):raise RuntimeError('Invalid evidence path')
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(base64.b64decode(encoded))
    for name,entry in data['guard'].items():
        path=folder/'guard'/name;path.parent.mkdir(exist_ok=True)
        path.write_bytes(entry['text'].encode())
        if native.sha(path)!=entry['sha256']:raise RuntimeError('Guard byte capture differs '+name)
    (OUT/'dma-failure-capture-sha256.json').write_text(json.dumps({str(p.relative_to(folder)):native.sha(p) for p in folder.rglob('*') if p.is_file()},indent=2))
    print('FAILURE OS/EVIDENCE CAPTURED. Retained hardware process/buffer untouched.',flush=True)
except BaseException as exc:
    (OUT/'dma-failure-capture-error.json').write_text(json.dumps({'error':str(exc)}))
    print('Read-only capture stopped: '+str(exc),flush=True)
finally:input('Press Enter to close. ')
