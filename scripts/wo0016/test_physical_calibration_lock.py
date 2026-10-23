from __future__ import annotations

import json
import builtins
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import physical_calibration_lock as lock  # noqa: E402
import run_stream_counter as runner  # noqa: E402
import wo0017_capture_profile as profile_module  # noqa: E402


BOOT_ID = "35983d66-bb1d-4a8a-85e5-802170825515"


def _identity():
    return {
        "schema": 1,
        "work_order": "WO-0017",
        "stage": "physical-calibration",
        "calibration_id": "physical-calibration-20261023-01",
        "boot_id": BOOT_ID,
        "manifest_sha256": "a" * 64,
        "bit_sha256": "b" * 64,
        "hwh_sha256": "c" * 64,
        "stage1_receipt_sha256": "d" * 64,
        "max_frames": 300,
        "counting_profile": "full-width-mixed",
        "counting_config": {"line_x": 160, "roi_mode": "full"},
        "preprocessing_config": {"active_output_roi_xyxy": [0, 20, 320, 220]},
        "capture_config": {"enabled": True, "dual_arrays": ["gray8", "binary8"]},
        "runtime_sha256": {"run_stream_counter.py": "e" * 64},
    }


def _calibration_args(tmp_path):
    return runner._parser().parse_args([
        "--mode", "board", "--physical-calibration", "--execute", "--approve-once",
        "--authorize-stage", runner.WO17_CALIBRATION_STAGE,
        "--calibration-id", "physical-calibration-20261023-01",
        "--calibration-lock", str(tmp_path / BOOT_ID / "physical-calibration-20261023-01"
                                    / "calibration-lock.json"),
        "--counting-profile", "full-width-mixed", "--max-frames", "300",
        "--stabilization-delay-s", "3", "--operator-prearm-delay-s", "0",
        "--manifest", str(tmp_path / "manifest.json"), "--manifest-sha256", "a" * 64,
        "--expected-boot-id", BOOT_ID,
        "--stage1-receipt", str(tmp_path / "stage1.json"),
        "--stage1-receipt-sha256", "d" * 64,
        "--capture-dir", str(tmp_path / "capture"),
        "--evidence-json", str(tmp_path / "evidence.json"),
    ])


def _prepare_mock_board(monkeypatch, tmp_path, *, with_lock=True):
    args = _calibration_args(tmp_path)
    plan = SimpleNamespace(boot_id=BOOT_ID, manifest_sha256="a" * 64,
                           bit_sha256="b" * 64, hwh_sha256="c" * 64,
                           ready_for_board=True, manifest={"clock_mhz": 50})
    lock_path = Path(args.calibration_lock)
    monkeypatch.setattr(runner.calibration_lock, "calibration_lock_path",
                        lambda _boot, _calibration, root=runner.calibration_lock.CALIBRATION_ROOT: lock_path)
    monkeypatch.setattr(runner.preflight, "validate_manifest", lambda *a, **k: plan)
    monkeypatch.setattr(runner.preflight, "read_boot_id", lambda: BOOT_ID)
    monkeypatch.setattr(runner.preflight, "read_cma_meminfo",
                        lambda: {"CmaTotal": 131072, "CmaFree": 100000})
    monkeypatch.setattr(runner.preflight, "read_cma_admission",
                        lambda **k: SimpleNamespace(as_evidence=lambda: {"ranges": []}))
    monkeypatch.setattr(runner.preflight, "audit_process_ownership",
                        lambda: {"findings": [], "denied": []})
    monkeypatch.setattr(runner.preflight, "read_fpga_manager",
                        lambda: {"state": "operating", "flags": "0"})
    monkeypatch.setattr(runner, "_require_camera_device_unowned",
                        lambda _device: {"owners": [], "read_only": True})
    monkeypatch.setattr(runner, "_read_standard_camera_profile", lambda _device: {
        "resolution": [640, 400], "pixel_format": "MJPG", "fps": 60.0,
        "auto_exposure": 1, "exposure_time_absolute": 50, "brightness": 0,
    })
    monkeypatch.setattr(runner.board_once, "read_phys32_readonly", lambda _address: 1)
    monkeypatch.setattr(profile_module, "validate_stage1_receipt",
                        lambda *a, **k: {"status": "OVERLAY_STAGE1_PASS"})
    context = runner.physical_calibration_context(args, plan)
    if with_lock:
        lock.write_ready_calibration_lock(context["lock_path"], context["identity"])
    return args, plan, context


def test_calibration_lock_path_is_boot_and_attempt_scoped(tmp_path):
    assert hasattr(lock, "calibration_lock_path")
    path = lock.calibration_lock_path(
        BOOT_ID, "physical-calibration-20261023-01", root=tmp_path
    )
    assert path == (tmp_path / BOOT_ID / "physical-calibration-20261023-01"
                    / "calibration-lock.json")
    with pytest.raises(ValueError, match="calibration id"):
        lock.calibration_lock_path(BOOT_ID, "../old-attempt", root=tmp_path)


def test_ready_calibration_lock_is_hash_pinned_and_consumed_once(tmp_path):
    identity = _identity()
    path = lock.calibration_lock_path(BOOT_ID, identity["calibration_id"], root=tmp_path)
    ready = lock.write_ready_calibration_lock(path, identity)
    validated = lock.validate_ready_calibration_lock(path, identity, expected_uid=None)
    assert validated["sha256"] == ready["sha256"]
    receipt = lock.claim_calibration_lock_once(path, identity, expected_uid=None)
    assert receipt["consumed"] is True
    marker = path.parent / "consumed.json"
    assert json.loads(marker.read_text()) == receipt
    with pytest.raises(FileExistsError):
        lock.claim_calibration_lock_once(path, identity, expected_uid=None)


def test_ready_calibration_lock_rejects_config_tampering_without_rewriting(tmp_path):
    identity = _identity()
    path = lock.calibration_lock_path(BOOT_ID, identity["calibration_id"], root=tmp_path)
    lock.write_ready_calibration_lock(path, identity)
    raw_before = path.read_bytes()
    changed = _identity()
    changed["counting_config"]["line_x"] = 159
    with pytest.raises(RuntimeError, match="identity/status"):
        lock.validate_ready_calibration_lock(path, changed, expected_uid=None)
    assert path.read_bytes() == raw_before


def test_runner_calibration_mode_is_fixed_to_300_full_width_raw_capture(tmp_path):
    args = _calibration_args(tmp_path)
    assert runner.validate_calibration_options(args) is None
    args.max_frames = 299
    with pytest.raises(ValueError, match="exactly 300"):
        runner.validate_calibration_options(args)


def test_calibration_lock_binds_deferred_memory_capture_and_idle_baseline(monkeypatch, tmp_path):
    args, plan, _ = _prepare_mock_board(monkeypatch, tmp_path, with_lock=False)
    args.calibration_initial_dmasr = 0x1002
    identity = runner.physical_calibration_context(args, plan)["identity"]
    assert identity["max_frames"] == 300
    assert identity["initial_dmasr"] == "0x00001002"
    assert identity["capture_config"]["write_mode"] == "deferred_memory"
    assert identity["capture_config"]["queue_depth"] == 0
    assert identity["capture_config"]["flush_timeout_s"] == 300.0


def test_standby_admission_is_read_only_and_binds_only_the_calibration_lock(
        monkeypatch, tmp_path):
    monkeypatch.setenv("XILINX_XRT", "/usr")
    args, _plan, context = _prepare_mock_board(monkeypatch, tmp_path)
    gate = SimpleNamespace()
    gate.bind_control_sha256 = lambda digest, *, label: setattr(
        gate, "binding", (digest, label))
    launcher_sha = runner.formal_runtime_sha256()["operator_gate_launcher.py"]
    imported = builtins.__import__

    def reject_pynq(name, *args, **kwargs):
        if name == "pynq":
            raise AssertionError("prelaunch must not import PYNQ")
        return imported(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_pynq)
    result = runner.validate_calibration_prelaunch(args, launcher_sha, gate)
    assert result["status"] == "CALIBRATION_STANDBY_READY"
    assert result["pynq_imported"] is False
    assert result["camera_opened"] is False
    assert result["dma_transfer_started"] is False
    assert gate.binding == (result["lock_sha256"], "calibration lock")
    assert not context["consumed_path"].exists()


def test_operator_click_claim_is_once_only_and_does_not_create_formal_permit(
        monkeypatch, tmp_path):
    args, _plan, context = _prepare_mock_board(monkeypatch, tmp_path)
    claim = runner.claim_calibration_after_operator_click(args)
    assert claim["consumed"] is True
    assert claim["ready_lock_sha256"] == lock.validate_ready_calibration_lock(
        context["lock_path"], context["identity"], expected_uid=None)["sha256"]
    assert context["consumed_path"].is_file()
    assert not (context["lock_path"].parent / "attempt-permit.json").exists()
    with pytest.raises(RuntimeError, match="already been consumed"):
        runner.claim_calibration_after_operator_click(args)
