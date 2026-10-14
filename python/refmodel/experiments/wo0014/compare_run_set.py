"""Aggregate directed, partial-TKEEP, and negative-recovery XSim results."""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from compare_output import compare_frames


def compare_run_set(results_root: Path, vectors_root: Path) -> dict:
    manifest = json.loads((vectors_root / "manifest.json").read_text(encoding="utf-8"))
    directed = manifest["directed"]
    width, height = directed["width"], directed["height"]
    comparisons = {"thresholds": {}, "partial_keep": {}, "negative_recovery": {}}
    failures = []

    def record(target: dict, key: str, actual: Path, expected: Path,
               frame_width: int, frame_height: int) -> None:
        try:
            result = compare_frames(actual, expected, frame_width, frame_height)
            result["actual_file"] = actual.name
            result["expected_file"] = expected.name
            target[key] = result
        except Exception as exc:
            target[key] = {"status": "FAIL", "error": str(exc),
                           "actual_file": actual.name, "expected_file": expected.name}
            failures.append(f"{key}: {exc}")

    for threshold in (0, 73, 128, 200, 255):
        actual_name = "directed-reviewed-actual.bin" if threshold == 128 else f"th{threshold}-actual.bin"
        record(comparisons["thresholds"], str(threshold),
               results_root / actual_name,
               vectors_root / f"directed_expected_th{threshold}.bin", width, height)

    for name, fixture in manifest["partial_keep_fixtures"].items():
        record(comparisons["partial_keep"], name,
               results_root / f"{name}-actual.bin",
               vectors_root / fixture["expected_file"],
               fixture["width"], fixture["height"])

    negative_manifest = json.loads(
        (vectors_root / manifest["negative_manifest"]).read_text(encoding="utf-8"))
    for name, case in negative_manifest["cases"].items():
        actual = results_root / f"{name}-actual.bin"
        expected = vectors_root / "directed_expected_th128.bin"
        frame_bytes = 2 * width * height
        try:
            raw = actual.read_bytes()
            if len(raw) % 2 or len(raw) < frame_bytes:
                raise AssertionError("raw malformed/recovery output is not a whole number of pixels")
            with tempfile.TemporaryDirectory(prefix="wo0014-recovery-") as temp_dir:
                tail = Path(temp_dir) / "recovery_tail.bin"
                tail.write_bytes(raw[-frame_bytes:])
                result = compare_frames(tail, expected, width, height)
            result.update({
                "actual_file": actual.name,
                "expected_file": expected.name,
                "raw_output_pixels": len(raw) // 2,
                "recovery_tail_pixels": frame_bytes // 2,
                "raw_output_sha256": hashlib.sha256(raw).hexdigest(),
                "bad_packet_error_code": case["error_code"],
                "bad_records": case["bad_records"],
                "drain_records": case["drain_records"],
                "recovery_records": case["recovery_records"],
            })
            comparisons["negative_recovery"][name] = result
        except Exception as exc:
            comparisons["negative_recovery"][name] = {
                "status": "FAIL", "error": str(exc), "actual_file": actual.name,
            }
            failures.append(f"negative recovery {name}: {exc}")

    for group in comparisons.values():
        for name, result in group.items():
            if result.get("status") != "PASS":
                failures.append(f"{name}: status is {result.get('status')}")
    return {
        "status": "FAIL" if failures else "PASS",
        "directed_geometry": {"width": width, "height": height,
                              "pixels_per_frame": width * height},
        "thresholds_tested": [0, 73, 128, 200, 255],
        "partial_keep_masks_tested": {
            name: item["final_payload_tkeep"]
            for name, item in manifest["partial_keep_fixtures"].items()
        },
        "negative_cases_tested": sorted(negative_manifest["cases"]),
        "failures": failures,
        "comparisons": comparisons,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, required=True,
                        help="directory containing XSim actual output files")
    parser.add_argument("--vectors-root", type=Path, required=True,
                        help="directory containing generated vectors and manifests")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = compare_run_set(args.results_root.resolve(), args.vectors_root.resolve())
    args.report.resolve().write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "threshold_cases": len(report["comparisons"]["thresholds"]),
        "partial_keep_cases": len(report["comparisons"]["partial_keep"]),
        "negative_recovery_cases": len(report["comparisons"]["negative_recovery"]),
        "mismatches": sum(
            item.get("word_mismatches", -1)
            for group in report["comparisons"].values()
            for item in group.values()
        ),
        "report": str(args.report.resolve()),
        "failures": report["failures"],
    }, indent=2))
