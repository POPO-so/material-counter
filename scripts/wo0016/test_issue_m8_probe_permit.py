"""Issuer tests replace every board read and never write an actual permit."""
from pathlib import Path
from types import SimpleNamespace

import pytest

import issue_m8_probe_permit as issuer
import m8_probe_runtime as runtime


@pytest.mark.parametrize('fresh_available,issued', [(500000, True), (122880, False)])
def test_issuer_rechecks_full_ram_before_exclusive_write(monkeypatch, tmp_path, fresh_available, issued):
    boot = '9552f3cf-5fb6-4b97-b5c3-5640e708a598'
    plan = SimpleNamespace(ready_for_board=True, manifest={'clock_mhz': 50}, boot_id=boot,
                           manifest_sha256='a' * 64, bit_sha256='a' * 64, hwh_sha256='a' * 64)
    receipt = tmp_path / 'receipt.json'
    monkeypatch.setattr(issuer.preflight, 'validate_manifest', lambda *a, **k: plan)
    monkeypatch.setattr(issuer.preflight, 'read_boot_id', lambda: boot)
    monkeypatch.setattr(issuer.board_once, 'OVERLAY_PASS_RECEIPT', receipt)
    monkeypatch.setattr(issuer.board_once, 'require_overlay_pass_receipt', lambda *a, **k: None)
    monkeypatch.setattr(issuer, '_sha256_file', lambda *a: 'a' * 64)
    monkeypatch.setattr(issuer.probe_profile, 'probe_guard_paths', lambda *a: {
        'permit': tmp_path / 'not-created-permit', 'consumed': tmp_path / 'not-created-consumed'})
    monkeypatch.setattr(issuer.runner, 'formal_runtime_sha256', lambda: {
        'stream_counter.py': 'b' * 64, 'run_stream_counter.py': 'c' * 64})
    monkeypatch.setattr(runtime, 'require_probe_environment', lambda: None)
    readings = iter([500000, fresh_available])
    monkeypatch.setattr(runtime, 'read_meminfo', lambda: {'MemAvailable': next(readings), 'CmaFree': 50000})
    writes = []
    monkeypatch.setattr(issuer, '_write_permit_once', lambda path, payload: writes.append(payload))
    args = ['--manifest', str(tmp_path / 'manifest'), '--manifest-sha256', 'a' * 64,
            '--expected-boot-id', boot, '--stage1-receipt', str(receipt),
            '--stage1-receipt-sha256', 'a' * 64, '--attempt-id', 'offline-test',
            '--capture-dir', str(tmp_path / 'capture'), '--evidence-json', str(tmp_path / 'result.json'),
            '--capture-max-bytes', str(96 * 1024 * 1024)]
    if issued:
        assert issuer.main(args) == 0
        assert writes[0]['schema'] == 2
        assert writes[0]['execution_contract']['execution_arguments']['camera'] == '/dev/video0'
    else:
        with pytest.raises(RuntimeError, match='MemAvailable'):
            issuer.main(args)
        assert writes == []
    assert not (tmp_path / 'not-created-permit').exists()
