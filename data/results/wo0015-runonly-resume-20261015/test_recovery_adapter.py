import ast
import copy
import hashlib
import importlib.util
from pathlib import Path
import sys
import types
import pytest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import recovery_adapter as rec
import run_only_model as model

@pytest.fixture
def live(tmp_path,monkeypatch):
    monkeypatch.setattr(rec,'OUT',tmp_path)
    monkeypatch.setattr(rec,'exclusive_bytes',lambda p,data,**kw:p.write_bytes(data))
    api=types.SimpleNamespace(read32=lambda b,o:0x10002,writes=[])
    api.write32=lambda b,o,v:api.writes.append((b,o,v))
    pre=types.SimpleNamespace(DMA_BASE=model.DMA,read_boot_id=lambda:model.BOOT)
    obj=rec.LiveRunOnly(api,pre,None,{})
    obj.claimed=True
    return obj

def test_adapter_allows_exact_rx_then_tx_control_writes(live):
    live.write32(0x30,0x10003); live.write32(0,0x10003)
    assert live.api.writes==[(model.DMA,0x30,0x10003),(model.DMA,0,0x10003)]
    with pytest.raises(model.ProposalRejected):live.write32(0,0x10003)
    assert len(live.api.writes)==2

@pytest.mark.parametrize('offset,value',[(0,0x10003),(0x30,1),(0x30,0x10007),(0x48,0x1000000),(0x58,76800),(0x18,4),(0x28,230432),(0x34,0x1000)])
def test_adapter_rejects_other_writes_before_mmio(live,offset,value):
    with pytest.raises(model.ProposalRejected):live.write32(offset,value)
    assert not live.api.writes

def test_guard_claim_required(live):
    live.claimed=False
    with pytest.raises(model.ProposalRejected):live.write32(0x30,0x10003)
    assert not live.api.writes

def test_boot_or_control_drift_rejected_before_write(live):
    live.pre.read_boot_id=lambda:'changed'
    with pytest.raises(model.ProposalRejected):live.write32(0x30,0x10003)
    live.pre.read_boot_id=lambda:model.BOOT
    live.api.read32=lambda b,o:0x10003
    with pytest.raises(model.ProposalRejected):live.write32(0x30,0x10003)
    assert not live.api.writes

def test_write_exception_consumes_intent_and_cannot_be_retried(live):
    def fail(*args):raise OSError('uncertain write')
    live.api.write32=fail
    with pytest.raises(OSError):live.write32(0x30,0x10003)
    assert len(live.writes)==1
    with pytest.raises(model.ProposalRejected):live.write32(0x30,0x10003)

@pytest.fixture
def archive_paths(tmp_path,monkeypatch):
    guard=tmp_path/'guard';guard.mkdir()
    source=guard/'terminal-failure.json';source.write_bytes(b'original\n')
    archive=guard/'history/original.json'
    monkeypatch.setattr(rec,'GUARD',guard);monkeypatch.setattr(rec,'ARCHIVE',archive)
    monkeypatch.setattr(rec,'sync_dir',lambda p:None)
    # Exercise actual O_EXCL byte copy on Windows; POSIX metadata is tested via spies.
    monkeypatch.setattr(rec.os,'O_NOFOLLOW',getattr(rec.os,'O_NOFOLLOW',0),raising=False)
    monkeypatch.setattr(rec.os,'fchown',lambda *a:None,raising=False)
    monkeypatch.setattr(rec.os,'fchmod',lambda *a:None,raising=False)
    return source,archive

def test_archive_preserves_exact_bytes_before_removing_canonical(archive_paths):
    source,archive=archive_paths;raw=source.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    result=rec.archive_terminal(raw,digest)
    assert not source.exists() and archive.read_bytes()==raw and result['sha256']==digest

def test_archive_existing_destination_never_overwritten(archive_paths):
    source,archive=archive_paths;archive.parent.mkdir();archive.write_bytes(b'older')
    raw=source.read_bytes()
    with pytest.raises(FileExistsError):rec.archive_terminal(raw,hashlib.sha256(raw).hexdigest())
    assert source.read_bytes()==raw and archive.read_bytes()==b'older'

def test_archive_hash_change_keeps_canonical(archive_paths):
    source,archive=archive_paths
    with pytest.raises(model.ProposalRejected):rec.archive_terminal(b'original\n','0'*64)
    assert source.exists() and not archive.exists()

def test_archive_copy_failure_keeps_canonical(archive_paths,monkeypatch):
    source,archive=archive_paths;raw=source.read_bytes()
    def fail(*args,**kw):raise OSError('disk failure')
    monkeypatch.setattr(rec,'exclusive_bytes',fail)
    with pytest.raises(OSError):rec.archive_terminal(raw,hashlib.sha256(raw).hexdigest())
    assert source.read_bytes()==raw

def test_no_allocation_load_reset_or_frame_methods_in_adapter():
    tree=ast.parse((HERE/'recovery_adapter.py').read_text())
    calls=[n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
    assert not set(calls)&{'allocate','load_overlay','download','run_frames','reset','transfer'}
    writes=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='write32']
    assert len(writes)==1
