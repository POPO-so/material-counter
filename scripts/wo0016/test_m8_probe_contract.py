"""Probe contract v2; all checks are local and hardware-free."""
import copy
import hashlib
from pathlib import Path

import pytest

import run_stream_counter as runner


@pytest.mark.parametrize('field,value', [('camera', '/dev/video1'), ('timeout_s', 1.0),
    ('count_observability', False), ('preview_overlay', 'standard'),
    ('measure_preview_client', True), ('host', '0.0.0.0'), ('min_area', 999)])
def test_probe_execution_rejects_workload_drift(field, value):
    args = runner.canonical_probe_options()
    baseline = runner.probe_execution_contract(args)
    assert baseline['startup_policy'] == 'consume_before_hardware_click_starts_sampling'
    setattr(args, field, value)
    with pytest.raises(ValueError, match='diagnostic execution'):
        runner.probe_execution_contract(args)


def test_execution_identity_contains_effective_config_sampler_and_abort_policy():
    contract = runner.probe_execution_contract(runner.canonical_probe_options())
    assert contract['effective_configuration']['counter_config']['observability'] is True
    assert contract['memory_model']['metadata_kib_per_frame'] == 128
    assert contract['sampler_interval_s'] == 1.0
    assert contract['abort_policy'] == 'checkpoint_exception_dma_safe_cleanup_else_fail_hold_no_kill_reset_retry'
    assert contract['execution_arguments']['camera'] == '/dev/video0'
    assert contract['execution_arguments']['preview_overlay'] == 'compact'


def test_full_admission_and_sampler_ready_precede_consumption(monkeypatch, tmp_path):
    import m8_probe_runtime as runtime
    calls = []
    class Session:
        def __init__(self):
            self.last_event = {'sample': {'meminfo_kib': {'MemAvailable': 500000, 'CmaFree': 50000}}}
        def checkpoint(self, label):
            calls.append(label)
            return self.last_event
        def abandon(self):
            calls.append('abandon')
    monkeypatch.setattr(runtime, 'read_meminfo', lambda: {'MemAvailable': 500000, 'CmaFree': 50000})
    monkeypatch.setattr(runtime, 'start_for_current_process', lambda *a, **k: (calls.append('ready') or Session()))
    monkeypatch.setattr(runtime, 'require_probe_environment', lambda: calls.append('environment'))
    session, admission = runtime.prepare_probe(tmp_path / 'samples', boot_id='boot', timeout_s=5)
    assert calls == ['environment', 'ready', 'before_consume']
    assert admission['status'] == 'PASS'
    assert session is not None


def test_failed_full_budget_never_starts_sampler(monkeypatch, tmp_path):
    import m8_probe_runtime as runtime
    monkeypatch.setattr(runtime, 'require_probe_environment', lambda: None)
    monkeypatch.setattr(runtime, 'read_meminfo', lambda: {'MemAvailable': 122880, 'CmaFree': 50000})
    monkeypatch.setattr(runtime, 'start_for_current_process', lambda *a, **k: pytest.fail('sampler started'))
    with pytest.raises(RuntimeError, match='MemAvailable'):
        runtime.prepare_probe(tmp_path / 'samples', boot_id='boot', timeout_s=5)


def test_claim_does_not_consume_if_resource_or_sampler_preparation_fails(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import m8_probe_runtime as runtime
    args = runner.canonical_probe_options()
    args.evidence_json = str(tmp_path / 'result.json')
    paths = {'permit': tmp_path / 'permit', 'consumed': tmp_path / 'consumed'}
    monkeypatch.setattr(runner, 'probe_run_identity', lambda *a, **k: {'execution_contract_sha256': 'a' * 64})
    monkeypatch.setattr(runner, 'validate_probe_permit', lambda *a, **k: {'sha256': 'b' * 64})
    def reject(*a, **k):
        raise RuntimeError('sampler failed before consumption')
    monkeypatch.setattr(runtime, 'prepare_probe', reject)
    monkeypatch.setattr(runner, 'claim_once_guard', lambda *a, **k: pytest.fail('consumed early'))
    with pytest.raises(RuntimeError, match='sampler failed'):
        runner.claim_probe_with_diagnostics(args, SimpleNamespace(boot_id='boot'), paths,
            gate_launcher_sha256='a' * 64, capture_path=str(tmp_path / 'capture'))
    import json
    result = json.loads(Path(args.evidence_json).read_text())
    assert result['board_accessed'] is False and result['consumed_marker_exists'] is False


def test_claim_failure_preserves_marker_and_closes_sampler(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import m8_probe_runtime as runtime
    args = runner.canonical_probe_options()
    args.evidence_json = str(tmp_path / 'result.json')
    args.attempt_id = 'offline-test'
    paths = {'permit': tmp_path / 'permit', 'consumed': tmp_path / 'consumed'}
    calls = []
    session = SimpleNamespace(process=SimpleNamespace(pid=456),
                              abandon=lambda: calls.append('abandon'))
    monkeypatch.setattr(runner, 'probe_run_identity', lambda *a, **k: {'execution_contract_sha256': 'a' * 64})
    monkeypatch.setattr(runner, 'validate_probe_permit', lambda *a, **k: {'sha256': 'b' * 64})
    monkeypatch.setattr(runtime, 'prepare_probe', lambda *a, **k: (session, {'status': 'PASS'}))
    def partial_claim(path, payload):
        path.write_text('partial-marker')
        raise OSError('durable marker write failed')
    monkeypatch.setattr(runner, 'claim_once_guard', partial_claim)
    with pytest.raises(OSError):
        runner.claim_probe_with_diagnostics(args, SimpleNamespace(boot_id='boot'), paths,
            gate_launcher_sha256='a' * 64, capture_path=str(tmp_path / 'capture'))
    assert paths['consumed'].read_text() == 'partial-marker'
    assert calls == ['abandon']


def test_existing_evidence_rejected_before_sampler_and_consume(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import m8_probe_runtime as runtime
    args = runner.canonical_probe_options()
    args.evidence_json = str(tmp_path / 'result.json')
    Path(args.evidence_json).write_text('existing evidence')
    monkeypatch.setattr(runner, 'probe_run_identity', lambda *a, **k: {})
    monkeypatch.setattr(runner, 'validate_probe_permit', lambda *a, **k: {'sha256': 'a' * 64})
    monkeypatch.setattr(runtime, 'prepare_probe', lambda *a, **k: pytest.fail('sampler started'))
    monkeypatch.setattr(runner, 'claim_once_guard', lambda *a, **k: pytest.fail('consumed'))
    with pytest.raises(FileExistsError):
        runner.claim_probe_with_diagnostics(args, SimpleNamespace(boot_id='boot'),
            {'permit': tmp_path / 'permit', 'consumed': tmp_path / 'consumed'},
            gate_launcher_sha256='a' * 64, capture_path=str(tmp_path / 'capture'))
    assert Path(args.evidence_json).read_text() == 'existing evidence'


def test_api_constructor_failure_after_consumption_has_failure_evidence(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import json
    args = runner.canonical_probe_options()
    args.evidence_json = str(tmp_path / 'result.json')
    marker = tmp_path / 'consumed'
    marker.write_text('preserved marker')
    def reject(*a, **k):
        raise RuntimeError('API construction failed')
    monkeypatch.setattr(runner.board_once, 'PynqBoardAPI', reject)
    sampler = SimpleNamespace(abandon=lambda: {'status': 'ERROR', 'exit_code': 1})
    with pytest.raises(RuntimeError, match='API construction failed'):
        runner.probe_board_api(args, SimpleNamespace(boot_id='boot'), SimpleNamespace(ranges=[]),
                               sampler, marker)
    result = json.loads(Path(args.evidence_json).read_text())
    assert result['consumed_marker_exists'] is True
    assert result['status'] == 'M8_PROBE_POSTCONSUMPTION_REJECTED'
    assert result['board_accessed'] is False
    assert marker.read_text() == 'preserved marker'


def test_probe_uses_the_fifteen_second_prearm_that_its_permit_binds():
    args = runner.canonical_probe_options()
    assert args.no_consume_guard is False
    assert runner.effective_operator_prearm_delay(args) == 15.0
    args.diagnostic_probe = False
    assert runner.effective_operator_prearm_delay(args) == 0.0
    args.no_consume_guard = True
    assert runner.effective_operator_prearm_delay(args) == 15.0


def test_probe_initialization_memory_error_stops_sampler_and_records_consumption(tmp_path):
    from types import SimpleNamespace
    import json
    args = runner.canonical_probe_options()
    args.evidence_json = str(tmp_path / 'result.json')
    marker = tmp_path / 'consumed'
    marker.write_text('preserved')
    calls = []
    sampler = SimpleNamespace(abandon=lambda: calls.append('abandon') or {'status': 'ERROR'})
    with pytest.raises(MemoryError):
        with runner.probe_preparation_guard(args, SimpleNamespace(boot_id='boot'), sampler, marker):
            raise MemoryError('dashboard allocation failed')
    assert calls == ['abandon']
    assert json.loads(Path(args.evidence_json).read_text())['consumed_marker_exists'] is True
