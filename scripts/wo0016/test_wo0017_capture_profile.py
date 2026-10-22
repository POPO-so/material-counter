import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from wo0017_capture_profile import (  # noqa: E402
    profile_identity,
    validate_stage1_receipt,
)


BOOT_ID = "35983d66-bb1d-4a8a-85e5-802170825515"
MANIFEST_SHA256 = "a" * 64
BIT_SHA256 = "b" * 64
HWH_SHA256 = "c" * 64
STAGE1_SHA256 = "d" * 64


def _plan(**changes):
    values = {
        "boot_id": BOOT_ID,
        "bit_sha256": BIT_SHA256,
        "hwh_sha256": HWH_SHA256,
        "manifest": {"clock_mhz": 50},
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _stage1_receipt(**changes):
    receipt = {
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
    receipt.update(changes)
    return receipt


def _write_receipt(path, receipt):
    raw = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(raw)
    if os.name == "posix":
        path.chmod(0o644)
    return raw


@pytest.mark.parametrize(
    ("profile", "frames", "count", "label", "stage"),
    [
        ("b1", 115, 0, "empty_belt", "wo0017-b1-empty"),
        ("b2", 220, 5, "M6_nut_no_washer", "wo0017-b2-nuts"),
    ],
)
def test_profile_identity_returns_stage1_bound_stage_specific_contract(
    profile, frames, count, label, stage
):
    identity = profile_identity(
        profile,
        boot_id=BOOT_ID,
        manifest_sha256=MANIFEST_SHA256,
        bit_sha256=BIT_SHA256,
        hwh_sha256=HWH_SHA256,
        max_frames=frames,
        expected_count=count,
        material_label=label,
        stabilization_delay_s=3.0,
        stage1_receipt_sha256=STAGE1_SHA256,
    )

    assert identity == {
        "schema": 1,
        "work_order": "WO-0017",
        "stage": stage,
        "boot_id": BOOT_ID,
        "manifest_sha256": MANIFEST_SHA256,
        "bit_sha256": BIT_SHA256,
        "hwh_sha256": HWH_SHA256,
        "max_frames": frames,
        "expected_count": count,
        "material_label": label,
        "stabilization_delay_s": 3.0,
        "threshold": 128,
        "stage1_receipt_sha256": STAGE1_SHA256,
    }


@pytest.mark.parametrize(
    ("profile", "changes"),
    [
        ("b1", {"max_frames": 116}),
        ("b1", {"expected_count": 1}),
        ("b1", {"material_label": "M6_nut_no_washer"}),
        ("b1", {"stabilization_delay_s": 2.0}),
        ("b2", {"max_frames": 165}),
        ("b2", {"expected_count": 4}),
        ("b2", {"material_label": "other"}),
        ("b2", {"stabilization_delay_s": 3.1}),
    ],
)
def test_profile_identity_rejects_contract_drift(profile, changes):
    values = {
        "boot_id": BOOT_ID,
        "manifest_sha256": MANIFEST_SHA256,
        "bit_sha256": BIT_SHA256,
        "hwh_sha256": HWH_SHA256,
        "max_frames": 115 if profile == "b1" else 220,
        "expected_count": 0 if profile == "b1" else 5,
        "material_label": "empty_belt" if profile == "b1" else "M6_nut_no_washer",
        "stabilization_delay_s": 3.0,
        "stage1_receipt_sha256": STAGE1_SHA256,
    }
    values.update(changes)

    with pytest.raises(ValueError):
        profile_identity(profile, **values)


def test_profile_identity_rejects_unknown_profile_and_invalid_receipt_hash():
    common = {
        "boot_id": BOOT_ID,
        "manifest_sha256": MANIFEST_SHA256,
        "bit_sha256": BIT_SHA256,
        "hwh_sha256": HWH_SHA256,
        "max_frames": 115,
        "expected_count": 0,
        "material_label": "empty_belt",
        "stabilization_delay_s": 3.0,
        "stage1_receipt_sha256": "not-a-sha256",
    }
    with pytest.raises(ValueError, match="profile"):
        profile_identity("b3", **common)
    with pytest.raises(ValueError, match="SHA-256"):
        profile_identity("b1", **common)


def test_stage1_receipt_accepts_exact_hash_and_current_plan_identity(tmp_path):
    path = tmp_path / "stage1-pass.json"
    raw = _write_receipt(path, _stage1_receipt())
    expected_uid = path.stat().st_uid if os.name == "posix" else 0

    result = validate_stage1_receipt(
        path, hashlib.sha256(raw).hexdigest(), _plan(), expected_uid=expected_uid
    )

    assert result["status"] == "OVERLAY_STAGE1_PASS"
    assert result["attempt"] == "root-02"


def test_stage1_receipt_rejects_hash_mismatch_before_accepting_json(tmp_path):
    path = tmp_path / "stage1-pass.json"
    _write_receipt(path, _stage1_receipt())
    expected_uid = path.stat().st_uid if os.name == "posix" else 0

    with pytest.raises(RuntimeError, match="SHA-256"):
        validate_stage1_receipt(path, "e" * 64, _plan(), expected_uid=expected_uid)


@pytest.mark.parametrize(
    ("field", "value", "plan_changes"),
    [
        ("boot_id", "different-boot", {}),
        ("bit_sha256", "e" * 64, {}),
        ("hwh_sha256", "f" * 64, {}),
        ("clock_mhz", 100, {}),
        ("boot_id", BOOT_ID, {"boot_id": "different-boot"}),
        ("bit_sha256", BIT_SHA256, {"bit_sha256": "e" * 64}),
        ("hwh_sha256", HWH_SHA256, {"hwh_sha256": "f" * 64}),
        ("clock_mhz", 50, {"manifest": {"clock_mhz": 100}}),
    ],
)
def test_stage1_receipt_rejects_identity_mismatch(tmp_path, field, value, plan_changes):
    path = tmp_path / "stage1-pass.json"
    raw = _write_receipt(path, _stage1_receipt(**{field: value}))
    expected_uid = path.stat().st_uid if os.name == "posix" else 0

    with pytest.raises(RuntimeError, match="does not match current plan|clock_mhz"):
        validate_stage1_receipt(
            path, hashlib.sha256(raw).hexdigest(), _plan(**plan_changes),
            expected_uid=expected_uid,
        )


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"overlay_download_returned": False}, "overlay_download_returned"),
        ({"dma_transfer_started": True}, "dma_transfer_started"),
        ({"dma_register_write_performed": True}, "dma_register_write_performed"),
        ({"effective_uid": 1000}, "effective_uid"),
        ({"schema": True}, "schema"),
        ({"attempt": "root-03"}, "attempt"),
        ({"fpga_manager_after": {"state": "failed", "flags": "0"}}, "fpga_manager_after"),
        ({"dma_registers": {"mm2s_dmasr": {"offset": "0x4", "value": "0x1002"}}}, "dma_registers"),
    ],
)
def test_stage1_receipt_rejects_unsafe_hardware_evidence(tmp_path, changes, match):
    path = tmp_path / "stage1-pass.json"
    raw = _write_receipt(path, _stage1_receipt(**changes))
    expected_uid = path.stat().st_uid if os.name == "posix" else 0

    with pytest.raises(RuntimeError, match=match):
        validate_stage1_receipt(
            path, hashlib.sha256(raw).hexdigest(), _plan(), expected_uid=expected_uid
        )


def test_stage1_receipt_rejects_missing_required_fields(tmp_path):
    receipt = _stage1_receipt()
    del receipt["dma_registers"]
    path = tmp_path / "stage1-pass.json"
    raw = _write_receipt(path, receipt)
    expected_uid = path.stat().st_uid if os.name == "posix" else 0

    with pytest.raises(RuntimeError, match="dma_registers"):
        validate_stage1_receipt(
            path, hashlib.sha256(raw).hexdigest(), _plan(), expected_uid=expected_uid
        )


def test_stage1_receipt_requires_integer_50mhz_plan(tmp_path):
    path = tmp_path / "stage1-pass.json"
    raw = _write_receipt(path, _stage1_receipt())
    expected_uid = path.stat().st_uid if os.name == "posix" else 0

    with pytest.raises(RuntimeError, match="clock_mhz"):
        validate_stage1_receipt(
            path, hashlib.sha256(raw).hexdigest(),
            _plan(manifest={"clock_mhz": 50.0}), expected_uid=expected_uid,
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits are required")
def test_stage1_receipt_rejects_group_or_world_writable_file(tmp_path):
    path = tmp_path / "stage1-pass.json"
    raw = _write_receipt(path, _stage1_receipt())
    path.chmod(0o664)

    with pytest.raises(RuntimeError, match="permissions"):
        validate_stage1_receipt(
            path, hashlib.sha256(raw).hexdigest(), _plan(),
            expected_uid=path.stat().st_uid,
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership is required")
def test_stage1_receipt_requires_expected_owner(tmp_path):
    path = tmp_path / "stage1-pass.json"
    raw = _write_receipt(path, _stage1_receipt())
    wrong_uid = path.stat().st_uid + 1

    with pytest.raises(RuntimeError, match="expected owner"):
        validate_stage1_receipt(
            path, hashlib.sha256(raw).hexdigest(), _plan(), expected_uid=wrong_uid
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits are required")
def test_stage1_receipt_rejects_group_or_world_writable_parent(tmp_path):
    path = tmp_path / "stage1-pass.json"
    raw = _write_receipt(path, _stage1_receipt())
    path.parent.chmod(0o777)

    with pytest.raises(RuntimeError, match="permissions"):
        validate_stage1_receipt(
            path, hashlib.sha256(raw).hexdigest(), _plan(),
            expected_uid=path.stat().st_uid,
        )
