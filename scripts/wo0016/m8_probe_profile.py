"""One-shot 100-frame empty-belt memory probe identity and guard paths.

This is a diagnostic measurement contract, deliberately separate from the
WO-0017 B1/B2 acceptance profiles and from the global WO-0016 consume-once
guard. It authorizes exactly one probe per Boot, records its own audit trail,
and never claims counting or acceptance results.
"""

from __future__ import annotations

import re
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

PROBE_STAGE = "m8-probe100-memory"
PROBE_MAX_FRAMES = 100
PROBE_EXPECTED_COUNT = 0
PROBE_MATERIAL_LABEL = "empty_belt_memory_probe"
PROBE_STABILIZATION_DELAY_S = 3.0
PROBE_OPERATOR_PREARM_DELAY_S = 15.0
PROBE_GUARD_DIR = Path("/var/lib/m8probe")
PROBE_COUNTING_PROFILE = "lighting-calibrated"
PROBE_AREA_FILTER = "output-roi"
PROBE_REFERENCE_AREA = 111.0
PROBE_LIGHTING_THRESHOLD = 100
PROBE_STARTUP_POLICY = "consume_before_hardware_click_starts_sampling"
PROBE_ABORT_POLICY = "checkpoint_exception_dma_safe_cleanup_else_fail_hold_no_kill_reset_retry"
PROBE_RUNTIME_FILES = ("m8_probe_profile.py", "m8_probe_runtime.py", "m8_memory_sampler.py")

_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_ATTEMPT_ID = re.compile(r"[A-Za-z0-9_-]{1,32}", re.ASCII)


def probe_guard_paths(boot_id: str) -> dict[str, Path]:
    """Return the single permit and consume-once marker for this Boot."""
    if not isinstance(boot_id, str) or not boot_id:
        raise ValueError("probe guard requires a non-empty Boot ID")
    base = PROBE_GUARD_DIR / boot_id
    return {"base": base, "permit": base / "permit.json", "consumed": base / "consumed.json"}


def validate_pinned_arguments(*, max_frames: int, expected_count: int,
                              material_label: str, stabilization_delay_s: float,
                              operator_prearm_delay_s: float,
                              counting_profile: str, area_filter: str,
                              reference_area: float, lighting_threshold: int,
                              capture_write_mode: str) -> None:
    """Reject any deviation from the single reviewed probe contract."""
    if max_frames != PROBE_MAX_FRAMES:
        raise ValueError(f"diagnostic probe requires exactly {PROBE_MAX_FRAMES} frames")
    if expected_count != PROBE_EXPECTED_COUNT:
        raise ValueError(f"diagnostic probe requires expected count {PROBE_EXPECTED_COUNT}")
    if material_label != PROBE_MATERIAL_LABEL:
        raise ValueError(f"diagnostic probe requires material label {PROBE_MATERIAL_LABEL!r}")
    if float(stabilization_delay_s) != PROBE_STABILIZATION_DELAY_S:
        raise ValueError(
            f"diagnostic probe requires a {PROBE_STABILIZATION_DELAY_S:g}-second stabilization")
    if float(operator_prearm_delay_s) != PROBE_OPERATOR_PREARM_DELAY_S:
        raise ValueError(
            f"diagnostic probe requires a {PROBE_OPERATOR_PREARM_DELAY_S:g}-second operator pre-arm")
    if counting_profile != PROBE_COUNTING_PROFILE:
        raise ValueError(f"diagnostic probe requires --counting-profile {PROBE_COUNTING_PROFILE}")
    if area_filter != PROBE_AREA_FILTER:
        raise ValueError(f"diagnostic probe requires --lighting-area-filter {PROBE_AREA_FILTER}")
    if float(reference_area) != PROBE_REFERENCE_AREA:
        raise ValueError(f"diagnostic probe requires --reference-area {PROBE_REFERENCE_AREA:g}")
    if lighting_threshold != PROBE_LIGHTING_THRESHOLD:
        raise ValueError(f"diagnostic probe requires --lighting-threshold {PROBE_LIGHTING_THRESHOLD}")
    if capture_write_mode != "deferred":
        raise ValueError("diagnostic probe requires --capture-write-mode deferred")


def probe_permit_identity(*, boot_id: str, manifest_sha256: str, bit_sha256: str,
                          hwh_sha256: str, stage1_receipt_sha256: str,
                          attempt_id: str, counting_profile: str, area_filter: str,
                          reference_area: float, lighting_threshold: int,
                          capture_path: str, capture_max_bytes: int, evidence_path: str,
                          runtime_sha256: Mapping[str, str], counter_sha256: str,
                          runner_sha256: str, probe_module_sha256: str,
                          gate_launcher_sha256: str,
                          execution_contract: Mapping[str, Any],
                          diagnostic_runtime_sha256: Mapping[str, str]) -> dict[str, Any]:
    """Build the exact permit identity that the runner must reproduce."""
    if not isinstance(boot_id, str) or not boot_id:
        raise ValueError("probe identity requires a non-empty Boot ID")
    for name, value in (("manifest_sha256", manifest_sha256), ("bit_sha256", bit_sha256),
                        ("hwh_sha256", hwh_sha256),
                        ("stage1_receipt_sha256", stage1_receipt_sha256),
                        ("counter_sha256", counter_sha256),
                        ("runner_sha256", runner_sha256),
                        ("probe_module_sha256", probe_module_sha256),
                        ("gate_launcher_sha256", gate_launcher_sha256)):
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    if not isinstance(attempt_id, str) or _ATTEMPT_ID.fullmatch(attempt_id) is None:
        raise ValueError("attempt id must contain 1-32 ASCII letters, digits, '_' or '-'")
    if not isinstance(capture_path, str) or not Path(capture_path).is_absolute():
        raise ValueError("probe capture directory must be an absolute path")
    if not isinstance(evidence_path, str) or not Path(evidence_path).is_absolute():
        raise ValueError("probe evidence path must be absolute")
    if Path(capture_path).resolve(strict=False) in Path(evidence_path).resolve(strict=False).parents:
        raise ValueError("probe evidence must be outside the new capture directory")
    if isinstance(capture_max_bytes, bool) or not isinstance(capture_max_bytes, int) \
            or capture_max_bytes < 1:
        raise ValueError("probe capture capacity must be a positive integer")
    if isinstance(lighting_threshold, bool) or not isinstance(lighting_threshold, int) \
            or lighting_threshold != PROBE_LIGHTING_THRESHOLD:
        raise ValueError(f"probe threshold must be {PROBE_LIGHTING_THRESHOLD}")
    if counting_profile != PROBE_COUNTING_PROFILE:
        raise ValueError(f"probe counting profile must be {PROBE_COUNTING_PROFILE}")
    if area_filter != PROBE_AREA_FILTER:
        raise ValueError(f"probe area filter must be {PROBE_AREA_FILTER}")
    if isinstance(reference_area, bool) or float(reference_area) != PROBE_REFERENCE_AREA:
        raise ValueError(f"probe reference area must be {PROBE_REFERENCE_AREA:g}")
    if not isinstance(runtime_sha256, Mapping) or not runtime_sha256:
        raise ValueError("probe runtime hashes must be a non-empty mapping")
    for name, value in runtime_sha256.items():
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise ValueError(f"runtime hash for {name} must be a lowercase SHA-256 digest")
    if (not isinstance(diagnostic_runtime_sha256, Mapping)
            or set(diagnostic_runtime_sha256) != set(PROBE_RUNTIME_FILES)
            or any(not isinstance(v, str) or _SHA256.fullmatch(v) is None
                   for v in diagnostic_runtime_sha256.values())
            or diagnostic_runtime_sha256["m8_probe_profile.py"] != probe_module_sha256):
        raise ValueError("diagnostic runtime hashes must bind the probe, resource helper and sampler")
    if (not isinstance(execution_contract, Mapping) or execution_contract.get("schema") != 2
            or execution_contract.get("startup_policy") != PROBE_STARTUP_POLICY
            or execution_contract.get("abort_policy") != PROBE_ABORT_POLICY
            or not execution_contract.get("effective_configuration")
            or not execution_contract.get("execution_arguments")):
        raise ValueError("probe requires a complete v2 execution contract")
    # JSON round trip snapshots caller-owned structures and rejects NaN/Infinity.
    execution_json = json.dumps(dict(execution_contract), sort_keys=True,
                               separators=(",", ":"), allow_nan=False)
    return {
        "schema": 2,
        "work_order": "M8-PROBE",
        "stage": PROBE_STAGE,
        "purpose": "bounded memory measurement; not a counting or acceptance run",
        "boot_id": boot_id,
        "manifest_sha256": manifest_sha256,
        "bit_sha256": bit_sha256,
        "hwh_sha256": hwh_sha256,
        "stage1_receipt_sha256": stage1_receipt_sha256,
        "max_frames": PROBE_MAX_FRAMES,
        "expected_count": PROBE_EXPECTED_COUNT,
        "material_label": PROBE_MATERIAL_LABEL,
        "stabilization_delay_s": PROBE_STABILIZATION_DELAY_S,
        "operator_prearm_delay_s": PROBE_OPERATOR_PREARM_DELAY_S,
        "counting_profile": counting_profile,
        "area_filter": area_filter,
        "reference_area": float(reference_area),
        "threshold": lighting_threshold,
        "capture_config": {
            "enabled": True,
            "output_directory": capture_path,
            "max_bytes": capture_max_bytes,
            "queue_depth": 2,
            "write_mode": "deferred_memory",
            "schema_version": 1,
        },
        "evidence_path": evidence_path,
        "memory_samples_path": str(Path(evidence_path).with_suffix('.memory-samples.jsonl')),
        "attempt_id": attempt_id,
        "runtime_sha256": dict(runtime_sha256),
        "counter_sha256": counter_sha256,
        "runner_sha256": runner_sha256,
        "probe_module_sha256": probe_module_sha256,
        "gate_launcher_sha256": gate_launcher_sha256,
        "diagnostic_runtime_sha256": dict(diagnostic_runtime_sha256),
        "execution_contract": json.loads(execution_json),
        "execution_contract_sha256": hashlib.sha256(execution_json.encode("utf-8")).hexdigest(),
    }
