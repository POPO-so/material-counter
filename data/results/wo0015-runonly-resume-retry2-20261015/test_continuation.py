import ast
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wo15_transition_test',HERE/'transition.py')
t=importlib.util.module_from_spec(spec);spec.loader.exec_module(t)

@pytest.fixture
def archive(tmp_path,monkeypatch):
    guard=tmp_path/'guards';guard.mkdir();out=tmp_path/'out';out.mkdir()
    (out/'execution-lock.json').write_bytes(b'{}')
    raws={'terminal-failure.json':json.dumps({'stage':'dma-single','reason':'ExecutionFailure: CmaFree did not show the required allocated-page decrease before DMA'}).encode()+b'\n',
          'dma-single-consumed.json':json.dumps({'stage':'dma-single','boot_id':t.BOOT,'consumed':True}).encode()+b'\n'}
    for n,raw in raws.items():(guard/n).write_bytes(raw)
    locks={'guard_sha256':{n:hashlib.sha256(b).hexdigest() for n,b in raws.items()},
           'deployed_sha256':{'run-manifest.json':'1'*64}}
    monkeypatch.setattr(t,'GUARD',guard);monkeypatch.setattr(t,'OUT',out)
    monkeypatch.setattr(t,'HISTORY',guard/'history');monkeypatch.setattr(t,'ONCE',guard/'once.json')
    monkeypatch.setattr(t,'sync_dir',lambda p:None)
    monkeypatch.setattr(t.os,'O_NOFOLLOW',getattr(t.os,'O_NOFOLLOW',0),raising=False)
    monkeypatch.setattr(t.os,'fchown',lambda *a:None,raising=False)
    monkeypatch.setattr(t.os,'fchmod',lambda *a:None,raising=False)
    pre=SimpleNamespace(read_boot_id=lambda:t.BOOT,DMA_BASE=0x40400000)
    api=SimpleNamespace(read32=lambda base,offset:t.EXPECTED_DMA[offset])
    return locks,pre,api,raws

def test_archive_keeps_legacy_consumed_and_exact_original_bytes(archive):
    locks,pre,api,raws=archive
    result=t.archive_originals(locks,pre,api)
    assert not (t.GUARD/'terminal-failure.json').exists()
    assert (t.GUARD/'dma-single-consumed.json').read_bytes()==raws['dma-single-consumed.json']
    assert all((t.HISTORY/n).read_bytes()==raw for n,raw in raws.items())
    assert t.ONCE.exists() and len(result)==2

def test_archive_existing_target_or_changed_guard_never_unlinks_canonical(archive):
    locks,pre,api,raws=archive
    t.HISTORY.mkdir()
    with pytest.raises(ValueError):t.archive_originals(locks,pre,api)
    assert (t.GUARD/'terminal-failure.json').read_bytes()==raws['terminal-failure.json']

def test_dma_drift_before_unlink_retains_terminal_and_consumed(archive):
    locks,pre,api,raws=archive
    api.read32=lambda base,offset:0xdead
    with pytest.raises(ValueError):t.archive_originals(locks,pre,api)
    assert all((t.GUARD/n).read_bytes()==raw for n,raw in raws.items())

def test_transition_has_no_register_write_allocate_reset_or_load_calls():
    tree=ast.parse((HERE/'transition.py').read_text())
    calls={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
    assert not calls&{'write32','allocate','load_overlay','download','reset','transfer'}

def test_fixed_retry2_names_and_version_receipt_required_before_step3():
    tree=ast.parse((HERE/'execute_stage.py').read_text())
    strings={n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str)}
    assert {'dma-single-watermark-retry2-consumed.json','dma-three-watermark-retry2-consumed.json',
            'dma-single-watermark-retry2-pass.json','single-continuation-pass.json'}<=strings
    assert {'executor_sha256','continuation_sha256','result_sha256'}<=strings

def test_packaged_executor_matches_reviewed_source_and_scope():
    root=HERE.parents[2]
    assert (HERE/'board_once_watermark.py').read_bytes()==(root/'scripts/wo0015/board_once.py').read_bytes()
    before=ast.parse((HERE/'before-board_once.py').read_bytes())
    after=ast.parse((HERE/'board_once_watermark.py').read_bytes())
    def defs(tree):
        d={}
        for n in tree.body:
            if isinstance(n,ast.FunctionDef):d[n.name]=ast.dump(n)
            elif isinstance(n,ast.ClassDef):
                for m in n.body:
                    if isinstance(m,ast.FunctionDef):d[n.name+'.'+m.name]=ast.dump(m)
        return d
    a,b=defs(before),defs(after)
    assert {k for k in a if a[k]!=b.get(k)}=={'run_frames','PynqBoardAPI.device_address','MockBoardAPI.__init__'}
