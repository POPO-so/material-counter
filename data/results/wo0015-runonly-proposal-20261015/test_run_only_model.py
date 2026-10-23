import copy
import hashlib
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_only_model as model

BASE = {'identity': dict(model.IDENTITY), 'checker_signature': 0x574f3134,
        'packer_signature': 0x574f3135, 'counters': {'checker': 0, 'packer_pixels': 0, 'packer_beats': 0},
        'dma': {0x00: 0x10002, 0x04: 1, 0x18: 0, 0x28: 0, 0x30: 0x10002, 0x34: 1, 0x48: 0, 0x58: 0},
        'CmaTotal': 131072, 'CmaFree': 72772, 'system_ram': [0, 0x20000000], 'ownership_clean': True}

class RegisterModel:
    def __init__(self, snapshot=None, stuck=None, error=None):
        self.state = copy.deepcopy(snapshot or BASE)
        self.stuck = stuck; self.error = error
        self.writes = []; self.consumed = False; self.now = 0
    def snapshot(self):return copy.deepcopy(self.state)
    def boot_id(self):return self.state['identity']['boot_id']
    def claim_once(self):
        if self.consumed:raise model.ProposalRejected('consumed')
        self.consumed = True
    def monotonic(self):return self.now
    def sleep(self, duration):self.now += duration
    def read32(self, offset):return self.state['dma'][offset]
    def write32(self, offset, value):
        assert offset in (0x30, 0x00), 'model prohibits all other writes'
        assert value == 0x10003 and not value & 4
        self.writes.append((offset, value))
        self.state['dma'][offset] = value
        sr = 0x34 if offset == 0x30 else 0x04
        if offset == self.error:self.state['dma'][sr] = 0x10
        elif offset != self.stuck:self.state['dma'][sr] = 0

def test_exact_two_rmw_run_writes_rx_first_no_length_reset_or_address_changes():
    api = RegisterModel()
    result = model.exercise_proposed_sequence(api)
    assert result['status'] == 'RUN_ONLY_PASS'
    assert api.writes == [(0x30, 0x10003), (0x00, 0x10003)]
    assert result['length_writes'] == result['cma_allocations'] == result['overlay_loads'] == 0
    assert result['dma_transfer_started'] is False

@pytest.mark.parametrize('offset,value', [(0x00, 0x10003), (0x30, 0x10006), (0x04, 0x11),
                                         (0x34, 0x8), (0x18, 0x10000000), (0x48, 4),
                                         (0x28, 230432), (0x58, 76800)])
def test_changed_dma_baseline_rejected_before_any_write_or_guard_claim(offset, value):
    snapshot = copy.deepcopy(BASE); snapshot['dma'][offset] = value
    api = RegisterModel(snapshot)
    with pytest.raises(model.ProposalRejected):model.exercise_proposed_sequence(api)
    assert not api.writes and not api.consumed

@pytest.mark.parametrize('field', ['boot_id', 'manifest_sha256', 'bit_sha256', 'hwh_sha256'])
def test_identity_change_is_not_a_reason_to_try_the_writes(field):
    snapshot = copy.deepcopy(BASE); snapshot['identity'][field] = 'changed'
    api = RegisterModel(snapshot)
    with pytest.raises(model.ProposalRejected):model.exercise_proposed_sequence(api)
    assert not api.writes

@pytest.mark.parametrize('field,value', [('CmaFree',32767), ('CmaTotal',65536),
                                       ('ownership_clean',False), ('checker_signature',0)])
def test_resource_or_identity_preflight_failure_has_no_writes(field, value):
    snapshot = copy.deepcopy(BASE); snapshot[field] = value
    api = RegisterModel(snapshot)
    with pytest.raises(model.ProposalRejected):model.exercise_proposed_sequence(api)
    assert not api.writes

@pytest.mark.parametrize('offset', [0x30, 0])
def test_stuck_halted_fuses_without_retries_or_compensating_writes(offset):
    api = RegisterModel(stuck=offset)
    result = model.exercise_proposed_sequence(api, timeout_s=.003)
    assert result['status'] == 'FAIL_STOPPED'
    assert len(api.writes) == (1 if offset == 0x30 else 2)
    assert len({o for o,v in api.writes}) == len(api.writes)

@pytest.mark.parametrize('offset', [0x30, 0])
def test_dma_error_fuses_without_retry_or_reset(offset):
    api = RegisterModel(error=offset)
    assert model.exercise_proposed_sequence(api)['status'] == 'FAIL_STOPPED'
    assert len(api.writes) == (1 if offset == 0x30 else 2)

def terminal():
    return json.dumps({**model.IDENTITY,'status':'TERMINAL_FAILURE','stage':'initial-state',
                       'reason':'MM2S Halted != 0; S2MM Halted != 0'}).encode()

def test_archive_is_a_plan_preserving_old_bytes_and_overlay_guards():
    raw = terminal(); result = model.exercise_proposed_sequence(RegisterModel())
    plan = model.terminal_archive_plan(raw,hashlib.sha256(raw).hexdigest(),result)
    assert plan['status'] == 'ARCHIVE_PROPOSED_NOT_EXECUTED'
    assert plan['overlay_consumed_guard_unchanged'] and plan['overlay_pass_receipt_unchanged']
    assert plan['must_not_overwrite_destination']

def test_historical_terminal_uses_manifest_binding_when_hwh_field_is_absent():
    marker = json.loads(terminal()); marker.pop('hwh_sha256')
    raw = json.dumps(marker).encode()
    result = model.exercise_proposed_sequence(RegisterModel())
    assert model.terminal_archive_plan(raw, hashlib.sha256(raw).hexdigest(), result)['status'] == 'ARCHIVE_PROPOSED_NOT_EXECUTED'

@pytest.mark.parametrize('case', ['failed', 'hash_changed', 'different_reason'])
def test_no_archive_plan_for_failed_recovery_changed_marker_or_other_failure(case):
    raw = terminal(); expected = hashlib.sha256(raw).hexdigest()
    result = model.exercise_proposed_sequence(RegisterModel())
    if case == 'failed':result['status'] = 'FAIL_STOPPED'
    elif case == 'hash_changed':expected = '0'*64
    else:
        raw = raw.replace(b'initial-state',b'dma-single'); expected = hashlib.sha256(raw).hexdigest()
    with pytest.raises(model.ProposalRejected):model.terminal_archive_plan(raw,expected,result)

@pytest.mark.parametrize('field,value', [('CmaFree',0), ('ownership_clean',False), ('packer_signature',0)])
def test_post_run_resource_or_signature_changes_keep_failure_marker(field, value):
    class ChangedAfterRun(RegisterModel):
        def snapshot(self):
            snapshot = super().snapshot()
            if self.writes:snapshot[field] = value
            return snapshot
    result = model.exercise_proposed_sequence(ChangedAfterRun())
    assert result['status'] == 'FAIL_STOPPED'
    raw = terminal()
    with pytest.raises(model.ProposalRejected):
        model.terminal_archive_plan(raw, hashlib.sha256(raw).hexdigest(), result)

def test_existing_executor_accepts_run_only_poststate_and_one_then_three_mock_frames(tmp_path):
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0,str(root/'scripts/wo0015'))
    import board_preflight as pre
    import board_once as run
    from test_board_preflight import make_manifest
    manifest,_ = make_manifest(tmp_path,frame_count=3)
    plan = pre.validate_manifest(manifest)
    assert model.exercise_proposed_sequence(RegisterModel())['status'] == 'RUN_ONLY_PASS'
    api = run.MockBoardAPI(plan,initial_idle=False)
    api._dma[0x00] = api._dma[0x30] = 0x10003
    single = run.run_frames(plan,api,timeout_s=2,frame_limit=1)
    three = run.run_frames(plan,api,timeout_s=2,frame_limit=3,expected_prior_frames=1)
    assert single['status'] == three['status'] == 'DMA_ONCE_PASS'
    assert single['frame_count'] == 1 and three['frame_count'] == 3
    assert all(f['byte_mismatches'] == 0 for f in single['frames']+three['frames'])
    assert api.overlay_load_count == 0 and api.free_count == 4

@pytest.mark.parametrize('timeout', [float('nan'), float('inf'), -float('inf'), 0, -1])
def test_nonfinite_or_nonpositive_timeout_rejected_before_claim(timeout):
    api = RegisterModel()
    with pytest.raises(model.ProposalRejected):model.exercise_proposed_sequence(api, timeout_s=timeout)
    assert not api.consumed and not api.writes

@pytest.mark.parametrize('case', ['minimal', 'missing_after', 'wrong_writes', 'transfer', 'allocation', 'poststate'])
def test_archive_rejects_incomplete_or_inconsistent_recovery(case):
    result = model.exercise_proposed_sequence(RegisterModel())
    if case == 'minimal':result = {'status':'RUN_ONLY_PASS','identity':dict(model.IDENTITY)}
    elif case == 'missing_after':result.pop('after')
    elif case == 'wrong_writes':result['writes'].reverse()
    elif case == 'transfer':result['dma_transfer_started'] = True
    elif case == 'allocation':result['cma_allocations'] = 1
    else:result['after']['dma'][0x58] = 76800
    raw = terminal()
    with pytest.raises(model.ProposalRejected):model.terminal_archive_plan(raw, hashlib.sha256(raw).hexdigest(), result)
