"""Issue the single 100-frame M8 memory-probe permit for the current Boot.

Run on the board as root before the probe. This tool writes one root-owned
permit file; the runner consumes a separate one-shot marker, so the probe can
run at most once per Boot. It never opens the camera, touches DMA or any
formal WO-0016/WO-0017 guard.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import m8_probe_profile as probe_profile
import run_stream_counter as runner

# The runner publishes SCRIPT_DIR and the WO-0015 bundle on sys.path.
import board_once
import board_preflight as preflight


def _sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_permit_once(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--expected-boot-id", required=True)
    parser.add_argument("--stage1-receipt", default=str(board_once.OVERLAY_PASS_RECEIPT))
    parser.add_argument("--stage1-receipt-sha256", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--capture-dir", required=True)
    parser.add_argument("--evidence-json", required=True)
    parser.add_argument("--capture-max-bytes", type=int, required=True)
    args = parser.parse_args(argv)

    if os.name == "posix" and os.geteuid() != 0:
        raise SystemExit("probe permit issuance requires root")

    plan = preflight.validate_manifest(args.manifest, args.manifest_sha256,
                                       approved_boot_id=args.expected_boot_id)
    if not plan.ready_for_board:
        raise SystemExit("manifest is not marked ready_for_board")
    if plan.manifest.get("clock_mhz") != 50:
        raise SystemExit("probe requires the WO-0015 50 MHz image")
    if preflight.read_boot_id() != plan.boot_id:
        raise SystemExit("current Boot does not match the approved Boot ID")

    receipt_path = Path(args.stage1_receipt)
    if receipt_path.resolve(strict=False) != Path(board_once.OVERLAY_PASS_RECEIPT).resolve(strict=False):
        raise SystemExit(f"Stage 1 receipt must be {board_once.OVERLAY_PASS_RECEIPT}")
    board_once.require_overlay_pass_receipt(receipt_path, plan)
    if _sha256_file(receipt_path) != args.stage1_receipt_sha256:
        raise SystemExit("Stage 1 receipt SHA-256 does not match")

    capture_path = Path(args.capture_dir)
    if not capture_path.is_absolute():
        raise SystemExit("capture directory must be an absolute path")
    if capture_path.exists() or capture_path.is_symlink():
        raise SystemExit("capture directory already exists; choose a new attempt path")
    evidence_path = Path(args.evidence_json)
    if (not evidence_path.is_absolute() or evidence_path.exists() or evidence_path.is_symlink()
            or evidence_path.with_suffix('.memory-samples.jsonl').exists()
            or evidence_path.with_suffix('.memory-samples.jsonl').is_symlink()):
        raise SystemExit("probe evidence and sampler paths must be absolute, new and non-symlink")

    paths = probe_profile.probe_guard_paths(plan.boot_id)
    if paths["permit"].exists() or paths["permit"].is_symlink():
        raise SystemExit("a probe permit already exists for this Boot; it is never replaced")
    if paths["consumed"].exists() or paths["consumed"].is_symlink():
        raise SystemExit("the one-shot probe for this Boot is already consumed")

    runtime = runner.formal_runtime_sha256()
    import m8_probe_runtime
    m8_probe_runtime.require_probe_environment()
    ram_admission = m8_probe_runtime.retained_admission(m8_probe_runtime.read_meminfo())
    options = runner.canonical_probe_options(capture_max_bytes=args.capture_max_bytes)
    identity = probe_profile.probe_permit_identity(
        boot_id=plan.boot_id,
        manifest_sha256=plan.manifest_sha256,
        bit_sha256=plan.bit_sha256,
        hwh_sha256=plan.hwh_sha256,
        stage1_receipt_sha256=args.stage1_receipt_sha256,
        attempt_id=args.attempt_id,
        counting_profile=probe_profile.PROBE_COUNTING_PROFILE,
        area_filter=probe_profile.PROBE_AREA_FILTER,
        reference_area=probe_profile.PROBE_REFERENCE_AREA,
        lighting_threshold=probe_profile.PROBE_LIGHTING_THRESHOLD,
        capture_path=str(capture_path.resolve(strict=False)),
        evidence_path=str(evidence_path.resolve(strict=False)),
        capture_max_bytes=args.capture_max_bytes,
        runtime_sha256=runtime,
        counter_sha256=runtime["stream_counter.py"],
        runner_sha256=runtime["run_stream_counter.py"],
        probe_module_sha256=_sha256_file(Path(__file__).with_name("m8_probe_profile.py")),
        gate_launcher_sha256=_sha256_file(Path(__file__).with_name("operator_gate_launcher.py")),
        execution_contract=runner.probe_execution_contract(options),
        diagnostic_runtime_sha256={name: _sha256_file(Path(__file__).with_name(name))
                                   for name in probe_profile.PROBE_RUNTIME_FILES},
    )
    m8_probe_runtime.retained_admission(m8_probe_runtime.read_meminfo())
    _write_permit_once(paths["permit"], {**identity, "permit_status": "AUTHORIZED",
                                         "consumed": False})
    print(json.dumps({"status": "PERMIT_ISSUED", "boot_id": plan.boot_id,
                      "attempt_id": args.attempt_id, "permit": str(paths["permit"]),
                      "permit_sha256": _sha256_file(paths["permit"]),
                      "diagnostic_ram_admission": ram_admission}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except preflight.PreflightError as exc:
        raise SystemExit(f"probe permit refused: {exc}")
