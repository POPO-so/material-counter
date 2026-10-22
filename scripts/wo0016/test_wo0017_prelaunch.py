"""Offline integration checks for the read-only WO-0017 prelaunch gate."""

from __future__ import annotations

import builtins
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_stream_counter as runner  # noqa: E402
import wo0017_capture_profile as profile_module  # noqa: E402


BOOT_ID = "35983d66-bb1d-4a8a-85e5-802170825515"
MANIFEST_SHA256 = "a" * 64
BIT_SHA256 = "b" * 64
HWH_SHA256 = "c" * 64
LAUNCHER_SHA256 = "d" * 64
STAGE1_SHA256 = ""


def _stage1_receipt():
    return {
        "attempt": "root-02",
        "schema": 1,
        "work_order": "WO-0017",
        "stage": "phase1b-stage1-overlay",
        "status": "OVERLAY_STAGE1_PASS",
        "boot_id": BOOT_ID,
        "bit_sha256": BIT_SHA256,
        "hwh_sha256": HWH_SHA256,
        "clock_mhz": 50,
        "axi_dma_phys_addr": "0x40400000",
        "overlay_download_returned": True,
        "dma_transfer_started": False,
        "dma_register_write_performed": False,
        "effective_uid": 0,
        "fpga_manager_after": {"state": "operating", "flags": "0"},
        "dma_registers": {
            "mm2s_dmasr": {"offset": "0x4", "value": "0x1"},
            "s2mm_dmasr": {"offset": "0x34", "value": "0x1"},
            "mm2s_dmacr": {"offset": "0x0", "value": "0x10002"},
            "s2mm_dmacr": {"offset": "0x30", "value": "0x10002"},
        },
    }


def _write_json(path: Path, value: dict) -> bytes:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(raw)
    path.chmod(0o600)
    return raw


def _set_private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        path.chmod(0o700)


def _write_permit(path: Path, identity: dict) -> None:
    _set_private(path.parent)
    _write_json(path, identity | {"permit_status": "AUTHORIZED", "consumed": False})


def _make_case(tmp_path, monkeypatch, profile, ready_id=None):
    monkeypatch.setenv("XILINX_XRT", "/usr")
    frames, count, label = (115, 0, "empty_belt") if profile == "b1" else (
        220, 5, "M6_nut_no_washer"
    )
    receipt_path = tmp_path / "stage1-root-02.json"
    raw_receipt = _write_json(receipt_path, _stage1_receipt())
    receipt_path.chmod(0o644)
    receipt_sha256 = hashlib.sha256(raw_receipt).hexdigest()
    global STAGE1_SHA256
    STAGE1_SHA256 = receipt_sha256

    # POSIX CI may run as a non-root user. The source validator still checks
    # the exact file and receipt identity while this offline test uses its owner.
    validate_receipt = profile_module.validate_stage1_receipt

    def validate_receipt_for_temp_file(path, sha256, plan):
        expected_uid = path.stat().st_uid if os.name == "posix" else 0
        return validate_receipt(path, sha256, plan, expected_uid=expected_uid)

    monkeypatch.setattr(profile_module, "validate_stage1_receipt", validate_receipt_for_temp_file)
    plan = SimpleNamespace(
        boot_id=BOOT_ID,
        manifest_sha256=MANIFEST_SHA256,
        bit_sha256=BIT_SHA256,
        hwh_sha256=HWH_SHA256,
        manifest={"clock_mhz": 50},
    )
    monkeypatch.setattr(runner.preflight, "validate_manifest", lambda *a, **k: plan)
    monkeypatch.setattr(runner.preflight, "read_boot_id", lambda: BOOT_ID)
    monkeypatch.setattr(
        runner.preflight, "read_cma_meminfo",
        lambda: {"CmaFree": runner.CMA_WATERMARK_KIB + 1},
    )

    receipt_hash_path = tmp_path / "evidence.json"
    capture_parent = tmp_path / "captures"
    capture_parent.mkdir()
    capture_path = capture_parent / f"{profile}-raw"
    args = runner._parser().parse_args([
        "--mode", "board",
        "--wo0017-profile", profile,
        "--expected-boot-id", BOOT_ID,
        "--stage1-receipt", str(receipt_path),
        "--stage1-receipt-sha256", receipt_sha256,
        "--max-frames", str(frames),
        "--stabilization-delay-s", "3",
        "--ground-truth-count", str(count),
        "--material-label", label,
        "--capture-dir", str(capture_path),
        "--capture-max-bytes", "400000000",
        "--attempt-id", f"formal-{profile}-01",
        "--operator-prearm-delay-s", "15",
        "--no-consume-guard",
        "--execute", "--authorize-stage", "wo0016-stream", "--approve-once",
        "--manifest", str(tmp_path / "reviewed-manifest.json"),
        "--manifest-sha256", MANIFEST_SHA256,
        "--evidence-json", str(receipt_hash_path),
    ] + ([] if ready_id is None else ["--wo0017-ready-id", ready_id]))

    # formal_capture_context deliberately uses the board's fixed /var/lib path.
    # Redirect just the returned guard/attempt locations into this test's private
    # temporary tree, retaining its real identity, Stage 1 validation and hashes.
    real_context = runner.formal_capture_context
    guard_path = tmp_path / "private" / "ready.json"
    attempt_root = tmp_path / "private" / "attempts"

    def temporary_context(current_args, current_plan):
        context = real_context(current_args, current_plan)
        context["ready_path"] = guard_path
        context["attempt_root"] = attempt_root
        return context

    monkeypatch.setattr(runner, "formal_capture_context", temporary_context)

    context = runner.formal_capture_context(args, plan)
    _set_private(guard_path.parent)
    _write_json(guard_path, context["identity"] | {
        "guard_status": "READY", "consumed": False,
    })
    owner_uid = guard_path.stat().st_uid if os.name == "posix" else 0
    real_ready_validator = runner.validate_non_consuming_guard
    monkeypatch.setattr(
        runner, "validate_non_consuming_guard",
        lambda path, identity: real_ready_validator(
            path, identity, expected_uid=path.stat().st_uid if os.name == "posix" else 0
        ),
    )
    ready = runner.validate_non_consuming_guard(guard_path, context["identity"])

    capture = runner.preflight_capture_target(
        args.capture_dir, expected_frames=args.max_frames,
        max_bytes=args.capture_max_bytes,
    )
    identity = runner.stageb2_attempt_permit_identity(
        ready_identity=context["identity"],
        ready_guard_sha256=ready["sha256"],
        attempt_id=args.attempt_id,
        operator_prearm_delay_s=args.operator_prearm_delay_s,
        runner_sha256=context["runtime_sha256"]["run_stream_counter.py"],
        gate_launcher_sha256=LAUNCHER_SHA256,
        counting_config=runner.counting_configuration(args),
        counter_sha256=context["runtime_sha256"]["stream_counter.py"],
        capture_config={
            "enabled": True,
            "output_directory": capture["path"],
            "max_bytes": args.capture_max_bytes,
            "queue_depth": 2,
            "schema_version": 1,
        },
    )
    identity.update({
        "stage": f"wo0017-{profile}-attempt",
        "runtime_sha256": context["runtime_sha256"],
    })
    permit_path = attempt_root / args.attempt_id / "attempt-permit.json"
    _write_permit(permit_path, identity)
    real_permit_validator = runner.validate_stageb2_attempt_permit
    monkeypatch.setattr(
        runner, "validate_stageb2_attempt_permit",
        lambda path, expected: real_permit_validator(
            path, expected, expected_uid=path.stat().st_uid if os.name == "posix" else 0
        ),
    )
    return args, context, identity, permit_path, capture_path, attempt_root


@pytest.mark.parametrize(
    ("profile", "frames", "count", "label", "stage"),
    [
        ("b1", 115, 0, "empty_belt", "wo0017-b1-empty"),
        ("b2", 220, 5, "M6_nut_no_washer", "wo0017-b2-nuts"),
    ],
)
def test_formal_prelaunch_accepts_exact_b1_b2_permits_without_board_side_effects(
    tmp_path, monkeypatch, profile, frames, count, label, stage
):
    args, context, identity, permit_path, capture_path, attempt_root = _make_case(
        tmp_path, monkeypatch, profile
    )
    real_import = builtins.__import__

    def forbid_pynq_import(name, *args, **kwargs):
        if name == "pynq" or name.startswith("pynq."):
            raise AssertionError("formal prelaunch imported PYNQ")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", forbid_pynq_import)
    ready_before = context["ready_path"].read_bytes()
    permit_before = permit_path.read_bytes()

    result = runner.validate_formal_prelaunch(args, LAUNCHER_SHA256)

    assert result["permit"] == identity | {"permit_status": "AUTHORIZED", "consumed": False}
    assert result["permit"]["stage"] == f"wo0017-{profile}-attempt"
    assert result["permit"]["work_order"] == "WO-0017"
    assert result["permit"]["max_frames"] == frames
    assert result["permit"]["expected_count"] == count
    assert result["permit"]["material_label"] == label
    assert result["permit"]["threshold"] == 128
    assert result["permit"]["stage1_receipt_sha256"] == STAGE1_SHA256
    assert result["permit"]["runtime_sha256"] == context["runtime_sha256"]
    assert context["identity"]["stage"] == stage
    assert context["identity"]["capture_config"]["output_directory"] == str(capture_path.resolve())
    assert context["identity"]["capture_config"]["schema_version"] == 1
    assert context["identity"]["capture_config"]["queue_depth"] == 2
    assert result["permit"]["capture_config"]["output_directory"] == str(capture_path.resolve())
    assert not (attempt_root / args.attempt_id / "consumed.json").exists()
    assert not capture_path.exists()
    assert context["ready_path"].read_bytes() == ready_before
    assert permit_path.read_bytes() == permit_before


def test_formal_prelaunch_binds_explicit_ready_id_and_rejects_drift(tmp_path, monkeypatch):
    args, context, _, _, _, _ = _make_case(tmp_path, monkeypatch, "b1", "b1-empty-04")
    assert context["identity"]["ready_id"] == "b1-empty-04"
    assert runner.validate_formal_prelaunch(args, LAUNCHER_SHA256)["permit"]["ready_id"] == "b1-empty-04"
    args.wo0017_ready_id = "b1-empty-05"
    with pytest.raises(RuntimeError, match="READY guard.*does not match"):
        runner.validate_formal_prelaunch(args, LAUNCHER_SHA256)


def test_formal_prelaunch_rejects_missing_xrt_environment_before_manifest_or_consumption(tmp_path, monkeypatch):
    args, context, _, permit, capture, attempts = _make_case(tmp_path, monkeypatch, "b1")
    monkeypatch.delenv("XILINX_XRT", raising=False)
    monkeypatch.setattr(runner.preflight, "validate_manifest", lambda *a, **k: pytest.fail("manifest read before XRT environment gate"))
    ready_before, permit_before = context["ready_path"].read_bytes(), permit.read_bytes()
    with pytest.raises(ValueError, match="XILINX_XRT=/usr"):
        runner.validate_formal_prelaunch(args, LAUNCHER_SHA256)
    assert not (attempts / args.attempt_id / "consumed.json").exists() and not capture.exists()
    assert context["ready_path"].read_bytes() == ready_before and permit.read_bytes() == permit_before


def test_formal_execution_rechecks_xrt_environment_before_manifest_or_consumption(tmp_path, monkeypatch):
    from operator_gate_launcher import OperatorGate, make_server
    args, _, _, _, capture, attempts = _make_case(tmp_path, monkeypatch, "b1")
    monkeypatch.delenv("XILINX_XRT", raising=False)
    monkeypatch.setattr(runner.preflight, "validate_manifest", lambda *a, **k: pytest.fail("manifest read before XRT environment gate"))
    gate = OperatorGate()
    server = make_server(gate, port=0)
    try:
        digest = hashlib.sha256((runner.SCRIPT_DIR / "operator_gate_launcher.py").read_bytes()).hexdigest()
        with pytest.raises(ValueError, match="XILINX_XRT=/usr"):
            runner.execute_board_stream(args, operator_gate_sha256=digest, operator_gate=gate)
    finally:
        server.server_close()
    assert not (attempts / args.attempt_id / "consumed.json").exists() and not capture.exists()


def test_formal_prelaunch_rejects_runtime_hash_drift(tmp_path, monkeypatch):
    args, _, _, _, _, _ = _make_case(tmp_path, monkeypatch, "b1")
    original = runner.formal_runtime_sha256
    monkeypatch.setattr(runner, "formal_runtime_sha256", lambda: {
        **original(), "stream_counter.py": "e" * 64,
    })

    with pytest.raises(RuntimeError, match="READY guard.*does not match"):
        runner.validate_formal_prelaunch(args, LAUNCHER_SHA256)


def test_formal_prelaunch_rejects_capture_configuration_drift(tmp_path, monkeypatch):
    args, _, _, _, _, _ = _make_case(tmp_path, monkeypatch, "b1")
    args.capture_max_bytes += 1

    with pytest.raises(RuntimeError, match="READY guard.*does not match"):
        runner.validate_formal_prelaunch(args, LAUNCHER_SHA256)


def test_formal_prelaunch_rejects_consumed_attempt(tmp_path, monkeypatch):
    args, _, _, _, _, attempt_root = _make_case(tmp_path, monkeypatch, "b1")
    attempt = attempt_root / args.attempt_id
    marker = attempt / "consumed.json"
    _write_json(marker, {"consumed": True})

    with pytest.raises(ValueError, match="already consumed"):
        runner.validate_formal_prelaunch(args, LAUNCHER_SHA256)


def test_formal_prelaunch_rejects_permit_marked_consumed(tmp_path, monkeypatch):
    args, _, identity, permit_path, _, _ = _make_case(tmp_path, monkeypatch, "b2")
    _write_json(permit_path, identity | {"permit_status": "AUTHORIZED", "consumed": True})

    with pytest.raises(RuntimeError, match="attempt permit.*does not match"):
        runner.validate_formal_prelaunch(args, LAUNCHER_SHA256)
