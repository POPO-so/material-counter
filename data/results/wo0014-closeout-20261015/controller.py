"""Native credential prompts; explicit one-shot, gated sequential closeout."""
import base64
import importlib.util
import json
import subprocess
import time
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
spec = importlib.util.spec_from_file_location('prior_action', ROOT/'data/results/wo0014-pg021-ready-20261015/controller.py')
prior = importlib.util.module_from_spec(spec); spec.loader.exec_module(prior)
prior.OUT = OUT; prior.native.OUT = OUT
result = {'status':'NOT_STARTED','actions':[],'baseline_commit':'5a153c4c91bc985882853c39c24b52c3130bd7a2'}

def main():
    approved = json.loads((OUT/'reviewed-ready.json').read_text())
    if prior.native.sha(OUT/'package-config.json') != approved['package_config_sha256']:
        raise RuntimeError('Reviewed package configuration changed')
    cfg = json.loads((OUT/'package-config.json').read_text())
    if approved['status'] != 'READY' or approved['manifest_sha256'] != cfg['manifest_sha256']:
        raise RuntimeError('Reviewed readiness missing or stale')
    for name, digest in approved['manager_source_sha256'].items():
        if prior.native.sha(OUT / name) != digest:
            raise RuntimeError('Reviewed controller source changed: ' + name)
    if prior.native.sha(OUT/'wo0014-closeout-20261015.zip') != cfg['zip_sha256']:
        raise RuntimeError('Reviewed package changed')
    if cfg['zip_sha256'] != approved['zip_sha256']:
        raise RuntimeError('ZIP is not the reviewed package')
    with (OUT/'dispatch-consumed.json').open('x') as stream:
        json.dump({'zip_sha256':cfg['zip_sha256'],'authorized_modes':['multi50','overlay100','single100','multi100']},stream)
    previous = cfg['previous_os']
    def invoke(mode, previous):
        previous = {k:previous[k] for k in ('boot_id','kernel_lines')}
        previous['kernel_lines'] = previous['kernel_lines'][-8:]
        manifest = json.loads((OUT/'closeout-manifest.json').read_text())
        variables = {'deployment':cfg['deployment'],'previous_os':previous,
                     'snapshot_source':(ROOT/'data/results/wo0014-pg021-ready-20261015/snapshot_source.py').read_text(),
                     'mode':mode,'expected_50':manifest['profiles']['50']['bit']['sha256'],
                     'expected_100':manifest['profiles']['100']['bit']['sha256'],
                     'package_files':cfg['package_files'],'zip_sha':cfg['zip_sha256'],
                     'manifest_sha':cfg['manifest_sha256']}
        program = '\n'.join(k+'='+repr(v) for k,v in variables.items())+'\n'
        program += (ROOT/'data/results/wo0014-pg021-ready-20261015/remote_support.py').read_text()
        program += '\ntarget=Path('+repr(cfg['target'])+')\n'+(OUT/'remote_driver.py').read_text()
        return prior.action(program,mode,hardware=mode not in ('preflight','deploy'))
    prior.native.ping('preflight-pre')
    preflight = invoke('preflight', previous)
    prior.native.ping('preflight-post')
    previous = preflight['post']
    result['actions'].append('preflight')
    prior.native.ping('upload-pre')
    print('Native SCP password prompt: enter directly in this terminal; do not send credentials to chat.',flush=True)
    argv = ['scp','-v',*prior.native.options(),str(OUT/'wo0014-closeout-20261015.zip'),
            'xilinx@192.168.2.99:/home/xilinx/wo0014-closeout-20261015.zip']
    prior.native.run_native(argv,'upload')
    log = (OUT/'upload-stderr.bin').read_text(encoding='utf-8',errors='replace')
    if prior.native.FINGERPRINT not in log or 'Authenticated to 192.168.2.99' not in log:
        raise RuntimeError('SCP strict fingerprint/authentication evidence missing')
    for mode in ('deploy','multi50','overlay100','single100','multi100'):
        if prior.native.sha(OUT/'package-config.json') != approved['package_config_sha256']:
            raise RuntimeError('Reviewed package config changed before ' + mode)
        for name, digest in approved['manager_source_sha256'].items():
            if prior.native.sha(OUT / name) != digest:
                raise RuntimeError('Controller source changed before ' + mode)
        prior.native.ping(mode+'-pre')
        done = invoke(mode, previous)
        prior.native.ping(mode+'-post')
        for name,encoded in done.get('evidence_bytes',{}).items():
            dest = OUT/'board-evidence'/mode/name
            if not dest.resolve().is_relative_to((OUT/'board-evidence'/mode).resolve()):
                raise RuntimeError('Unsafe returned path')
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(zlib.decompress(base64.b64decode(encoded)))
        previous = done['post']
        result['actions'].append(mode)
        result['status'] = mode+'_PASS'
        (OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print('VERIFIED '+mode+' PASS',flush=True)
    result['status'] = 'WO0014_CLOSEOUT_BOARD_PASS'

if __name__ == '__main__':
    try: main()
    except BaseException as exc:
        result.update(status='FAIL_STOPPED',error=type(exc).__name__+': '+str(exc))
        print(json.dumps(result,indent=2),flush=True)
    finally:
        (OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2),flush=True)
        if any(p.poll() is None for p in prior.retained):
            print('Remote child/session retained. No further operations.',flush=True)
            while True: time.sleep(1)
        else: input('Press Enter to close. ')
