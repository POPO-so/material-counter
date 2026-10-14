"""Read-only OS and existing evidence collection after the outer CMA refusal."""
import base64
import importlib.util
import json
import zlib
from pathlib import Path
OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
spec = importlib.util.spec_from_file_location('prior_collect',ROOT/'data/results/wo0014-pg021-ready-20261015/controller.py')
prior = importlib.util.module_from_spec(spec); spec.loader.exec_module(prior)
prior.OUT = OUT; prior.native.OUT = OUT
cfg = json.loads((OUT/'package-config.json').read_text())
previous = json.loads((OUT/'deploy-result.json').read_text())['post']
previous = {k:previous[k] for k in ('boot_id','kernel_lines')}
previous['kernel_lines'] = previous['kernel_lines'][-8:]
variables = {'deployment':cfg['deployment'],'previous_os':previous,
             'snapshot_source':(ROOT/'data/results/wo0014-pg021-ready-20261015/snapshot_source.py').read_text(),
             'target_path':cfg['target'],'manifest_sha':cfg['manifest_sha256']}
program = '\n'.join(k+'='+repr(v) for k,v in variables.items())+'\n'
program += (ROOT/'data/results/wo0014-pg021-ready-20261015/remote_support.py').read_text()
program += '''
cfg=deployment['new_config']
current=snapshot(cfg,previous_os)
os_gate(current,cfg)
target=Path(target_path)
evidence=target/'evidence-multi50'
status=json.loads((evidence/'status.json').read_text())
receipt=json.loads((GUARD/'closeout-20261015/mode-multi50-pass.json').read_text())
if status.get('status')!='PASS' or status.get('send_attempts')!=3 or receipt.get('manifest_sha256')!=manifest_sha:
    raise RuntimeError('Successful three-frame evidence not established; no claim')
payload={str(p.relative_to(target)):base64.b64encode(zlib.compress(p.read_bytes())).decode()
         for p in evidence.rglob('*') if p.is_file()}
payload.update({str(p.relative_to(GUARD)):base64.b64encode(zlib.compress(p.read_bytes())).decode()
                for p in (GUARD/'closeout-20261015').rglob('*') if p.is_file()})
current_cma=cma()
# Persist the outer refusal so a different controller cannot continue to100.
failure={'mode':'wo0014-closeout:multi50','stage':'OUTER_POST_CMA',
         'error':'Outer wrapper observed CMA accounting changed after successful DMA/free; no retry',
         'boot_id':BOOT,'manifest_sha256':manifest_sha,'current_cma':current_cma}
for folder in (GUARD,GUARD/'closeout-20261015'):
    path=folder/'terminal-fail.json'
    if path.exists():
        raise RuntimeError('Unexpected prior failure marker; preserve it')
    with path.open('x') as stream:
        json.dump(failure,stream);stream.flush();os.fsync(stream.fileno())
    fd=os.open(str(folder),os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
print('ACTION_JSON '+json.dumps({'status':'PASS','action':'read-only-evidence-collection',
                               'current':current,'current_cma':current_cma,'multi50_supervisor':status,
                               'multi50_receipt':receipt,'evidence_bytes':payload,
                               'outer_failure_marker':failure,
                               'hardware_accesses':0,'overlay100_attempted':False}),flush=True)
'''
if __name__ == '__main__':
    try:
        prior.native.ping('collect-pre')
        done=prior.action(program,'collect-failure',hardware=False)
        prior.native.ping('collect-post')
        for name,encoded in done['evidence_bytes'].items():
            dest=OUT/'board-evidence/multi50'/name
            assert dest.resolve().is_relative_to((OUT/'board-evidence/multi50').resolve())
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(zlib.decompress(base64.b64decode(encoded)))
        (OUT/'collection-status.json').write_text(json.dumps({'status':'PASS','hardware_accesses':0,'multi50_dma_checker':'PASS','outer_cma':'FAIL_CLOSED','100mhz':'NOT_ATTEMPTED'},indent=2)+'\n')
        print('READ_ONLY_EVIDENCE_COLLECTED; DMA_CHECKER_3FRAMES_PASS; OUTER_CMA_REFUSED; NO100',flush=True)
    except BaseException as exc:
        (OUT/'collection-status.json').write_text(json.dumps({'status':'FAIL','error':type(exc).__name__+': '+str(exc)},indent=2)+'\n')
        print(type(exc).__name__+': '+str(exc),flush=True)
    finally:
        input('Press Enter to close. ')
