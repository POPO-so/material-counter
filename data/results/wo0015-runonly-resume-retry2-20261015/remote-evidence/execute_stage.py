"""Pinned continuation wrapper; fresh fixed once tokens, legacy tokens preserved."""
import hashlib
import json
from pathlib import Path
import sys
import types

WO=Path('/home/xilinx/wo0015_first_board_retry3_20261015')
OUT=Path('/var/lib/wo0015-s2mm/continuations/watermark-retry2-20261015')
GUARD=Path('/var/lib/wo0015-s2mm')
BOOT='822a9c29-37c0-438c-81c4-da998d57073b'
def sha(raw):return hashlib.sha256(raw).hexdigest()

def main(mode):
    if mode not in ('dma-single','dma-three'):raise ValueError('unsupported stage')
    config_raw=(OUT/'continuation-execution.json').read_bytes()
    if sha(config_raw)!=LOCK['continuation-execution.json']:
        raise ValueError('continuation config differs from pinned launcher')
    config=json.loads(config_raw)
    locks_raw=(OUT/'locked-evidence.json').read_bytes()
    if sha(locks_raw)!=LOCK['locked-evidence.json']:raise ValueError('base lock differs from launcher')
    locks=json.loads(locks_raw)
    for n,h in locks['deployed_sha256'].items():
        if sha((WO/n).read_bytes())!=h:raise ValueError('base deployment changed')
    source=(OUT/'board_once_watermark.py').read_bytes()
    if sha(source)!=LOCK['board_once_watermark.py']:raise ValueError('executor differs from pinned launcher')
    if sha(source)!=config['executor_sha256']:raise ValueError('patched executor changed')
    if config['base_manifest_sha256']!=locks['deployed_sha256']['run-manifest.json']:
        raise ValueError('continuation base identity differs')
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=BOOT:raise ValueError('boot differs')
    acceptance=json.loads((OUT/'step1-acceptance.json').read_bytes())
    if acceptance['status']!='PASS' or acceptance['continuation_execution_sha256']!=sha(config_raw):
        raise ValueError('continuation precheck acceptance differs')
    if mode=='dma-three':
        prior=json.loads((OUT/'single-continuation-pass.json').read_bytes())
        if (prior['status']!='PASS' or prior['executor_sha256']!=sha(source) or
                prior['continuation_sha256']!=sha(config_raw) or prior['boot_id']!=BOOT or
                prior['result_sha256']!=sha((OUT/'dma-single-result.json').read_bytes())):
            raise ValueError('single continuation PASS binding differs')
    sys.path.insert(0,str(WO))
    module=types.ModuleType('wo0015_watermark_executor')
    module.__file__=str(OUT/'board_once_watermark.py')
    sys.modules[module.__name__]=module
    exec(compile(source,module.__file__,'exec'),module.__dict__)
    if Path(module.preflight.__file__).resolve()!=WO/'board_preflight.py':
        raise ValueError('unexpected base preflight import path')
    if sha(Path(module.preflight.__file__).read_bytes())!=locks['deployed_sha256']['board_preflight.py']:
        raise ValueError('base preflight import bytes differ')
    # Fixed names for this explicitly authorized generation. Old single token
    # stays at its canonical path and continues blocking the old executor.
    module.DMA_SINGLE_GUARD=GUARD/'dma-single-watermark-retry2-consumed.json'
    module.DMA_THREE_GUARD=GUARD/'dma-three-watermark-retry2-consumed.json'
    module.DMA_SINGLE_PASS_RECEIPT=GUARD/'dma-single-watermark-retry2-pass.json'
    evidence=OUT/(mode+'-result.json')
    rc=module.main(['--mode',mode,'--manifest',str(WO/'run-manifest.json'),
                    '--manifest-sha256',config['base_manifest_sha256'],'--execute',
                    '--authorize-stage',mode,'--approve-once','--timeout-s','5',
                    '--evidence-json',str(evidence)])
    plan=module.preflight.validate_manifest(WO/'run-manifest.json',config['base_manifest_sha256'])
    if rc:
        module._record_terminal_failure('watermark-'+mode,plan,'Continuation stage rejected/failed; see independent result.')
        return rc
    result=json.loads(evidence.read_bytes())
    expected='DMA_SINGLE_PASS' if mode=='dma-single' else 'DMA_THREE_PASS'
    if result['status']!=expected:raise ValueError('unexpected stage status')
    receipt={'status':'PASS','stage':mode,'boot_id':BOOT,'executor_sha256':sha(source),
             'continuation_sha256':sha(config_raw),'base_manifest_sha256':config['base_manifest_sha256'],
             'result_sha256':sha(evidence.read_bytes()),'guard':str(module._guard_path(mode))}
    target=OUT/('single-continuation-pass.json' if mode=='dma-single' else 'three-continuation-pass.json')
    with target.open('xb') as f:
        import os
        f.write((json.dumps(receipt,indent=2)+'\n').encode());f.flush();os.fsync(f.fileno())
    print(json.dumps({'continuation_receipt':receipt}),flush=True)
    return 0

if __name__=='__main__':
    try:raise SystemExit(main(sys.argv[1]))
    except (OSError,ValueError,KeyError) as exc:
        # The launcher has already verified the immutable base sources.
        sys.path.insert(0,str(WO))
        import board_once as original
        import board_preflight as pre
        plan=pre.validate_manifest(WO/'run-manifest.json',json.loads((OUT/'locked-evidence.json').read_bytes())['deployed_sha256']['run-manifest.json'])
        original._record_terminal_failure('watermark-continuation',plan,str(exc))
        print(json.dumps({'status':'FAIL_STOPPED','error':str(exc)}),flush=True)
        raise SystemExit(2)
