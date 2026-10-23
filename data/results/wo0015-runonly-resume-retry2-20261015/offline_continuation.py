"""Replay the actual continuation wrapper against register Mock; no board API."""
import builtins
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

HERE=Path(__file__).resolve().parent
BASE=HERE.parent/'wo0015-retry3-two-tier-bundle-20261015'
sys.path.insert(0,str(BASE))
import board_preflight as pre
plan=pre.validate_manifest(BASE/'run-manifest.json','84c27012ec038d48267128af08deb9685eb1bb9a5fdb1a79e5da064083788312')
spec=importlib.util.spec_from_file_location('offline_wrapper',HERE/'execute_stage.py')
wrapper=importlib.util.module_from_spec(spec);spec.loader.exec_module(wrapper)
shared=[]
with tempfile.TemporaryDirectory() as temporary:
    out=Path(temporary);guards=out/'guards';guards.mkdir()
    for name in ('continuation-execution.json','locked-evidence.json','board_once_watermark.py'):
        (out/name).write_bytes((HERE/name).read_bytes())
    digest=hashlib.sha256((out/'continuation-execution.json').read_bytes()).hexdigest()
    (out/'step1-acceptance.json').write_text(json.dumps({'status':'PASS','continuation_execution_sha256':digest}))
    class FakePath(type(Path())):
        def read_text(self,*a,**kw):
            if self.as_posix()=='/proc/sys/kernel/random/boot_id':return plan.boot_id
            return super().read_text(*a,**kw)
    wrapper.Path=FakePath;wrapper.WO=BASE;wrapper.OUT=out;wrapper.GUARD=guards
    wrapper.LOCK=json.loads((HERE/'launch-lock.json').read_bytes())
    def inject_mock(code,namespace):
        builtins.exec(code,namespace)
        cls=namespace['MockBoardAPI']
        if not shared:shared.append(cls(plan,initial_idle=False))
        api=shared[0]
        api._overlay_loaded=True  # Model an already loaded Overlay; never call load_overlay.
        def factory(p,stage,cma_ranges=None):
            api.stage=stage
            return api
        namespace['PynqBoardAPI']=factory
        namespace['TERMINAL_FAILURE']=guards/'terminal.json'
        namespace['OVERLAY_PASS_RECEIPT']=guards/'overlay-pass.json'
        if not namespace['OVERLAY_PASS_RECEIPT'].exists():
            namespace['write_overlay_pass_receipt'](namespace['OVERLAY_PASS_RECEIPT'],plan,{})
        def runtime(p,**kw):
            api.stage=kw['stage']
            env=api.audit_environment(require_empty_shutdown=False,require_loaded_bit=True)
            return p.boot_id,{'CmaTotal':131072,'CmaFree':32768},((0x100000,0x20000000),),env
        namespace['_runtime_preflight']=runtime
    wrapper.exec=inject_mock
    assert wrapper.main('dma-single')==0
    assert wrapper.main('dma-three')==0
    results={n:json.loads((out/(n+'-result.json')).read_bytes()) for n in ('dma-single','dma-three')}
    assert all(f['byte_mismatches']==0 for r in results.values() for f in r['frames'])
    assert shared[0].overlay_load_count==0 and shared[0].free_count==4
    final={'status':'OFFLINE_CONTINUATION_MOCK_PASS','board_accessed':False,
           'pynq_imported':False,'frames':4,'overlay_load_count':0,'free_count':4,
           'single':results['dma-single'],'three':results['dma-three']}
    (HERE/'offline-continuation-result.json').write_text(json.dumps(final,indent=2),encoding='utf-8')
    print(json.dumps({'status':final['status'],'frames':4,'mismatches':0}))
