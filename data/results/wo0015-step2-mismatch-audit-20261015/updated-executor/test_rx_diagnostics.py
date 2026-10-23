"""Offline tests for completed-DMA mismatch capture before buffer release."""
import hashlib
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent))
import board_once as run
import board_preflight as pre
from test_board_preflight import make_manifest

def test_parity_analysis_distinguishes_zero_and_poison_words():
    golden=bytes([0,0,0,0,255,0,0,0,255,255,0,0,0,0,0,0])*4
    rx=(b'\0'*4+b'\xa5'*4)*8
    d=run.analyze_rx_mismatch(rx,golden)
    assert d['byte_count']==64 and d['first_mismatch']==4
    assert len(d['first_64_bytes_words'])==16
    assert d['parity']['even']['word_count']==8 and d['parity']['even']['zero_words']==8
    assert d['parity']['even']['poison_words']==0 and d['parity']['even']['mismatch_bytes']==8
    assert d['parity']['odd']['poison_words']==8 and d['parity']['odd']['mismatch_bytes']==32
    assert d['first_mismatches'][0]=={'offset':4,'expected':255,'actual':165}
    assert d['first_64_bytes_words'][1]['rx_hex']=='a5a5a5a5'

@pytest.mark.parametrize('lengths',[(0,0),(8,4),(3,3)])
def test_analysis_rejects_empty_inexact_or_partial_words(lengths):
    with pytest.raises(ValueError):run.analyze_rx_mismatch(bytes(lengths[0]),bytes(lengths[1]))

def test_single_capture_is_durable_and_exact_before_either_free(tmp_path):
    manifest,_=make_manifest(tmp_path,frame_count=1);plan=pre.validate_manifest(manifest)
    directory=tmp_path/'rx-capture';observed=[]
    class CheckedFree(run.MockBoardAPI):
        def free(self,b):
            raw=next(directory.glob('*_rx_raw.bin')).read_bytes()
            record=json.loads(next(directory.glob('*_diagnostics.json')).read_bytes())
            assert len(raw)==76800 and record['rx_sha256']==hashlib.sha256(raw).hexdigest()
            assert record['captured_before_release'] is True
            assert Path(record['golden_path']).read_bytes()==plan.frames[0].binary
            assert record['golden_sha256']==hashlib.sha256(plan.frames[0].binary).hexdigest()
            assert record['first_64_bytes_words'][0]['rx_hex']==raw[:4].hex()
            observed.append(raw)
            super().free(b)
    api=CheckedFree(plan,corrupt_s2mm_frame=1)
    with pytest.raises(run.ExecutionFailure,match='differs from golden') as failure:
        run.run_frames(plan,api,diagnostic_dir=directory)
    d=failure.value.evidence['rx_diagnostics']
    assert len(observed)==2 and api.free_count==2
    assert d['mismatch_bytes']==1 and d['capture_status']=='SAVED'
    assert failure.value.evidence['failed_frame_completion']['packer_status']==2
    assert not failure.value.dma_may_be_active

def test_three_frame_failure_preserves_only_failed_frame_with_distinct_name(tmp_path):
    manifest,_=make_manifest(tmp_path,frame_count=3);plan=pre.validate_manifest(manifest)
    api=run.MockBoardAPI(plan,corrupt_s2mm_frame=2)
    with pytest.raises(run.ExecutionFailure) as failure:
        run.run_frames(plan,api,diagnostic_dir=tmp_path/'diag')
    assert len(failure.value.evidence['frames'])==1
    raw=list((tmp_path/'diag').glob('*_rx_raw.bin'))
    assert len(raw)==1 and raw[0].name.startswith('step3_frame01_')
    assert api.free_count==2

def test_capture_failure_retains_completed_buffers_without_claiming_active_dma(tmp_path,monkeypatch):
    manifest,_=make_manifest(tmp_path,frame_count=1);plan=pre.validate_manifest(manifest)
    api=run.MockBoardAPI(plan,corrupt_s2mm_frame=1)
    def fail(*a,**kw):raise OSError('disk full')
    monkeypatch.setattr(run,'persist_rx_diagnostics',fail)
    with pytest.raises(run.ExecutionFailure) as failure:
        run.run_frames(plan,api,diagnostic_dir=tmp_path/'diag')
    assert api.free_count==0 and not failure.value.dma_may_be_active
    assert failure.value.evidence['buffers_retained_for_evidence'] is True
    assert 'disk full' in failure.value.evidence['rx_diagnostics']['save_error']
    assert run.QUARANTINED_BUFFERS

def test_capture_never_overwrites_prior_raw_bytes(tmp_path):
    raw=b'\xa5'*64;golden=bytes(64)
    d=run.persist_rx_diagnostics(tmp_path,'fixed',raw,golden,context={})
    with pytest.raises(FileExistsError):run.persist_rx_diagnostics(tmp_path,'fixed',bytes(64),golden,context={})
    assert Path(d['rx_path']).read_bytes()==raw


def test_live_stage_capture_failure_enters_evidence_hold_even_when_dma_is_idle(tmp_path,monkeypatch):
    manifest,_=make_manifest(tmp_path,frame_count=3);plan=pre.validate_manifest(manifest)
    receipt=tmp_path/'overlay-pass.json';run.write_overlay_pass_receipt(receipt,plan)
    api=run.MockBoardAPI(plan,corrupt_s2mm_frame=1);api._overlay_loaded=True
    holds=[]
    class HoldReached(BaseException):pass
    def save_fail(*a,**kw):raise OSError('read-only evidence volume')
    monkeypatch.setattr(run,'persist_rx_diagnostics',save_fail)
    monkeypatch.setattr(run,'OVERLAY_PASS_RECEIPT',receipt)
    monkeypatch.setattr(run,'TERMINAL_FAILURE',tmp_path/'terminal.json')
    monkeypatch.setattr(run,'_guard_path',lambda stage:tmp_path/'once.json')
    monkeypatch.setattr(run,'PynqBoardAPI',lambda *a,**kw:api)
    monkeypatch.setattr(run,'_install_hold_signals',lambda:None)
    monkeypatch.setattr(run,'_hold_forever',lambda:(_ for _ in ()).throw(HoldReached()))
    api.fail_hold=lambda path,payload:holds.append(payload)
    with pytest.raises(HoldReached):run._dma_stage(plan,plan.boot_id,'dma-single',str(tmp_path/'result.json'),2)
    assert api.free_count==0 and len(holds)==1
    assert holds[0]['dma_may_be_active'] is False
    assert holds[0]['buffers_retained_for_evidence'] is True
    assert 'persistence failed' in holds[0]['hold_reason']


def test_real_pynq_hold_wrapper_preserves_stage_and_idle_dma_metadata(tmp_path,monkeypatch):
    manifest,_=make_manifest(tmp_path,frame_count=1);plan=pre.validate_manifest(manifest)
    api=run.PynqBoardAPI(plan,stage='dma-single');payloads=[]
    class HoldReached(BaseException):pass
    monkeypatch.setattr(run,'_install_hold_signals',lambda:None)
    monkeypatch.setattr(run,'_emit',lambda payload,path:payloads.append(payload))
    monkeypatch.setattr(run,'_hold_forever',lambda:(_ for _ in ()).throw(HoldReached()))
    with pytest.raises(HoldReached):
        api.fail_hold(None,{'status':'DMA_SINGLE_FAIL_HOLD','dma_may_be_active':False,
                           'buffers_retained_for_evidence':True,'hold_reason':'capture failed'})
    assert payloads[0]['status']=='DMA_SINGLE_FAIL_HOLD'
    assert payloads[0]['hardware_stage']=='dma-single' and payloads[0]['dma_may_be_active'] is False


def test_partial_capture_failure_reports_planned_paths_without_overwriting_partial_raw(tmp_path,monkeypatch):
    manifest,_=make_manifest(tmp_path,frame_count=1);plan=pre.validate_manifest(manifest)
    api=run.MockBoardAPI(plan,corrupt_s2mm_frame=1)
    def partial(directory,stem,rx,golden,**kw):
        Path(directory).mkdir();(Path(directory)/(stem+'_rx_raw.bin')).write_bytes(rx)
        raise OSError('golden write failed')
    monkeypatch.setattr(run,'persist_rx_diagnostics',partial)
    with pytest.raises(run.ExecutionFailure) as failure:
        run.run_frames(plan,api,diagnostic_dir=tmp_path/'diag')
    d=failure.value.evidence['rx_diagnostics']
    assert d['partial_files_may_exist'] is True
    assert len(Path(d['planned_paths']['rx_path']).read_bytes())==76800
    assert api.free_count==0
