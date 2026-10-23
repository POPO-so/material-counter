"""Fail-closed WO-0017 B1/B2 capture identities and Stage 1 receipt checks."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping


THRESHOLD = 128
STAGE1_RECEIPT_MAX_BYTES = 64 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_PROFILES = {
    "b1": {
        "stage": "wo0017-b1-empty",
        "max_frames": 115,
        "expected_count": 0,
        "material_label": "empty_belt",
    },
    "b2": {
        "stage": "wo0017-b2-nuts",
        "max_frames": 220,
        "expected_count": 5,
        "material_label": "M6_nut_no_washer",
    },
}


def _require_sha256(name: str, value: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def profile_identity(
    profile: str,
    *,
    boot_id: str,
    manifest_sha256: str,
    bit_sha256: str,
    hwh_sha256: str,
    max_frames: int,
    expected_count: int,
    material_label: str,
    stabilization_delay_s: float,
    stage1_receipt_sha256: str,
) -> dict[str, Any]:
    """Build the exact, boot-bound READY identity for a WO-0017 capture stage."""
    if not isinstance(profile, str) or profile not in _PROFILES:
        raise ValueError("profile must be 'b1' or 'b2'")
    contract = _PROFILES[profile]
    if (isinstance(max_frames, bool) or not isinstance(max_frames, int)
            or max_frames != contract["max_frames"]):
        raise ValueError(f"{profile} max_frames must be {contract['max_frames']}")
    if (isinstance(expected_count, bool) or not isinstance(expected_count, int)
            or expected_count != contract["expected_count"]):
        raise ValueError(
            f"{profile} expected_count must be {contract['expected_count']}"
        )
    if material_label != contract["material_label"]:
        raise ValueError(f"{profile} material_label must be {contract['material_label']!r}")
    if (isinstance(stabilization_delay_s, bool)
            or not isinstance(stabilization_delay_s, (int, float))
            or not math.isfinite(float(stabilization_delay_s))
            or float(stabilization_delay_s) != 3.0):
        raise ValueError("stabilization_delay_s must be exactly 3.0 seconds")
    if not isinstance(boot_id, str) or not boot_id:
        raise ValueError("boot_id must be a non-empty string")
    _require_sha256("manifest_sha256", manifest_sha256)
    _require_sha256("bit_sha256", bit_sha256)
    _require_sha256("hwh_sha256", hwh_sha256)
    _require_sha256("stage1_receipt_sha256", stage1_receipt_sha256)

    return {
        "schema": 1,
        "work_order": "WO-0017",
        "stage": contract["stage"],
        "boot_id": boot_id,
        "manifest_sha256": manifest_sha256,
        "bit_sha256": bit_sha256,
        "hwh_sha256": hwh_sha256,
        "max_frames": max_frames,
        "expected_count": expected_count,
        "material_label": material_label,
        "stabilization_delay_s": float(stabilization_delay_s),
        "threshold": THRESHOLD,
        "stage1_receipt_sha256": stage1_receipt_sha256,
    }


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _read_stage1_receipt(path: str | Path, *, expected_uid: int | None) -> bytes:
    target = Path(path)
    try:
        parent_stat = target.parent.lstat()
        before = target.lstat()
    except OSError as exc:
        raise RuntimeError(f"Stage 1 receipt is unavailable: {exc}") from exc

    if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode):
        raise RuntimeError("Stage 1 receipt parent must be a real directory")
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RuntimeError("Stage 1 receipt must be a regular, non-symlink file")

    if os.name == "posix":
        if expected_uid is not None and (
            parent_stat.st_uid != expected_uid or before.st_uid != expected_uid
        ):
            raise RuntimeError("Stage 1 receipt and parent must have the expected owner")
        if (stat.S_IMODE(parent_stat.st_mode) & 0o022
                or stat.S_IMODE(before.st_mode) & 0o022):
            raise RuntimeError(
                "Stage 1 receipt and parent permissions must forbid group/world writes"
            )
    elif expected_uid is not None and not isinstance(expected_uid, int):
        raise ValueError("expected_uid must be an integer or None")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(target), flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                    or not stat.S_ISREG(opened.st_mode)):
                raise RuntimeError("Stage 1 receipt changed while being opened")
            if os.name == "posix":
                if (expected_uid is not None and opened.st_uid != expected_uid):
                    raise RuntimeError("Stage 1 receipt owner changed while being opened")
                if stat.S_IMODE(opened.st_mode) & 0o022:
                    raise RuntimeError("Stage 1 receipt permissions changed while being opened")
            raw = stream.read(STAGE1_RECEIPT_MAX_BYTES + 1)
            if len(raw) > STAGE1_RECEIPT_MAX_BYTES:
                raise RuntimeError("Stage 1 receipt exceeds its size limit")
            return raw
    except OSError as exc:
        raise RuntimeError(f"Stage 1 receipt could not be read safely: {exc}") from exc


def _plan_identity(plan: Any) -> dict[str, Any]:
    try:
        boot_id = plan.boot_id
        bit_sha256 = plan.bit_sha256
        hwh_sha256 = plan.hwh_sha256
        manifest = plan.manifest
        clock_mhz = manifest.get("clock_mhz") if isinstance(manifest, Mapping) else None
    except (AttributeError, TypeError) as exc:
        raise RuntimeError("validated plan is missing Stage 1 identity fields") from exc

    if not isinstance(boot_id, str) or not boot_id:
        raise RuntimeError("validated plan boot_id is invalid")
    for field, value in (("bit_sha256", bit_sha256), ("hwh_sha256", hwh_sha256)):
        if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
            raise RuntimeError(f"validated plan {field} is invalid")
    if type(clock_mhz) is not int or clock_mhz != 50:
        raise RuntimeError("validated plan clock_mhz must be 50")
    return {
        "boot_id": boot_id,
        "bit_sha256": bit_sha256,
        "hwh_sha256": hwh_sha256,
        "clock_mhz": clock_mhz,
    }


def _validate_stage1_receipt_fields(receipt: Mapping[str, Any], plan: Any) -> None:
    expected = {
        "attempt": "root-02",
        "schema": 1,
        "work_order": "WO-0017",
        "stage": "phase1b-stage1-overlay",
        "status": "OVERLAY_STAGE1_PASS",
        **_plan_identity(plan),
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
    for field, expected_value in expected.items():
        if field not in receipt:
            raise RuntimeError(f"Stage 1 receipt is missing required field {field}")
        actual_value = receipt[field]
        if isinstance(expected_value, bool):
            matches = isinstance(actual_value, bool) and actual_value is expected_value
        elif isinstance(expected_value, int):
            matches = type(actual_value) is int and actual_value == expected_value
        else:
            matches = actual_value == expected_value
        if not matches:
            if field in {"boot_id", "bit_sha256", "hwh_sha256", "clock_mhz"}:
                raise RuntimeError(f"Stage 1 receipt {field} does not match current plan")
            raise RuntimeError(f"Stage 1 receipt {field} does not match the reviewed Stage 1 profile")
    if type(receipt["effective_uid"]) is not int or receipt["effective_uid"] != 0:
        raise RuntimeError("Stage 1 receipt effective_uid must be integer 0")


def validate_stage1_receipt(
    path: str | Path,
    sha256: str,
    plan: Any,
    *,
    expected_uid: int | None = 0,
) -> dict[str, Any]:
    """Read and verify the immutable Stage 1 PASS receipt for the live plan."""
    _require_sha256("stage1 receipt SHA-256", sha256)
    if expected_uid is not None and (
        isinstance(expected_uid, bool) or not isinstance(expected_uid, int)
    ):
        raise ValueError("expected_uid must be an integer or None")

    raw = _read_stage1_receipt(path, expected_uid=expected_uid)
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(actual_sha256, sha256):
        raise RuntimeError("Stage 1 receipt SHA-256 does not match the pinned digest")
    try:
        receipt = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"Stage 1 receipt is not valid unambiguous UTF-8 JSON: {exc}") from exc
    if not isinstance(receipt, dict):
        raise RuntimeError("Stage 1 receipt must contain a JSON object")
    _validate_stage1_receipt_fields(receipt, plan)
    return receipt
