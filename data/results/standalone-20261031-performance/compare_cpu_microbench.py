"""Validate and compare serial v7/v8 pure-CPU microbenchmark reports."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import statistics


ROOT = Path(__file__).resolve().parent
COMMIT = "e2fb5952bfc4f36c3eacd53a8bdb6bdeec6c4fe2"
BRANCH = "perf/board-performance-20261031"


def _read(version: str) -> tuple[dict, str]:
    path = ROOT / f"{version}-microbench.json"
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _metric_summary(old: dict, new: dict) -> dict:
    before, after = old["values_ms"], new["values_ms"]
    old_p50, new_p50 = statistics.median(before), statistics.median(after)
    return {
        "v7_p50_ms": old_p50,
        "v8_p50_ms": new_p50,
        "p50_delta_ms": new_p50 - old_p50,
        "p50_change_percent": (new_p50 / old_p50 - 1.0) * 100.0 if old_p50 else None,
        "v7_p95_ms": old["p95_ms"],
        "v8_p95_ms": new["p95_ms"],
        "v7_max_ms": old["max_ms"],
        "v8_max_ms": new["max_ms"],
        "samples_each": [old["warmups"], old["samples"]],
    }


def main() -> None:
    reports, hashes = {}, {}
    for version in ("v7", "v8"):
        reports[version], hashes[version] = _read(version)
        report = reports[version]
        if report.get("schema") != "material-counter.pure-cpu-microbench.v1":
            raise ValueError(f"invalid microbenchmark schema for {version}")
        if report.get("mode") != "cpu-only-no-camera-no-pynq-no-mmio-no-dma":
            raise ValueError(f"unexpected execution mode for {version}")
        if report["snapshot_writer"] != {
            "events": 0, "saved": 0, "write_errors": 0, "closed_cleanly": True
        }:
            raise ValueError(f"snapshot writer was not idle/clean for {version}")

    a, b = reports["v7"], reports["v8"]
    if a["inputs"] != b["inputs"]:
        raise ValueError("v7/v8 benchmark input hashes differ")
    native_a = a["source"]["native_library"]["sha256"]
    native_b = b["source"]["native_library"]["sha256"]
    if not native_a or native_a != native_b:
        raise ValueError("v7/v8 native library hashes differ")

    measurements = {}
    for name in ("gray8_pack_into_640x400", "crossing_snapshots_observe_empty_frame"):
        left, right = a["measurements"][name], b["measurements"][name]
        if (left["warmups"], left["samples"]) != (5, 30):
            raise ValueError(f"unexpected sample plan in v7 {name}")
        if (right["warmups"], right["samples"]) != (5, 30):
            raise ValueError(f"unexpected sample plan in v8 {name}")
        measurements[name] = _metric_summary(left, right)
    measurements["rle_decode"] = {}
    if set(a["measurements"]["rle_decode"]) != set(b["measurements"]["rle_decode"]):
        raise ValueError("RLE case sets differ")
    for case in sorted(a["measurements"]["rle_decode"]):
        left, right = a["measurements"]["rle_decode"][case], b["measurements"]["rle_decode"][case]
        if (left["runs"], left["total_foreground"], left["packet_bytes"]) != (
            right["runs"], right["total_foreground"], right["packet_bytes"]
        ):
            raise ValueError(f"decoded case metadata differs: {case}")
        measurements["rle_decode"][case] = {
            "runs": left["runs"],
            "total_foreground": left["total_foreground"],
            "packet_bytes": left["packet_bytes"],
            **_metric_summary(left["decode"], right["decode"]),
        }

    source_path_sets_equal = (
        set(a["source"]["sha256_by_path"]) == set(b["source"]["sha256_by_path"])
    )
    if not source_path_sets_equal:
        raise ValueError("v7/v8 source identity path sets differ")
    source_hash_comparison = {
        path: {
            "v7_sha256": a["source"]["sha256_by_path"][path],
            "v8_sha256": b["source"]["sha256_by_path"][path],
            "same": a["source"]["sha256_by_path"][path] == b["source"]["sha256_by_path"][path],
        }
        for path in sorted(a["source"]["sha256_by_path"])
    }
    result = {
        "schema": "material-counter.pure-cpu-microbench-comparison.v1",
        "claim_scope": "serial measurements of the actual v7/v8 temporary CPU copies; not full-system FPS",
        "validation": {
            "inputs_identical": True,
            "native_library_sha256_identical": True,
            "all_groups_five_warmups_thirty_samples": True,
            "snapshot_writer_zero_events_and_errors": True,
            "source_path_sets_identical": source_path_sets_equal,
        },
        "reports": {
            version: {
                "sha256": hashes[version],
                "source": report["source"],
                "snapshot_writer": report["snapshot_writer"],
            }
            for version, report in reports.items()
        },
        "source_hash_comparison": source_hash_comparison,
        "measurements": measurements,
        "runtime_claims": {
            "full_system_fps_comparison": False,
            "camera_or_hardware_access": False,
            "PYNQ_MMIO_or_DMA": False,
            "CPU_copy_matches_fixed_commit_byte_for_byte": False,
            "note": "CPU microbenchmarks characterize the reported temporary copies only; fixed-commit release bytes are verified separately.",
        },
        "planned_release_source": {"commit": COMMIT, "branch": BRANCH},
    }
    (ROOT / "cpu-microbench-comparison.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "inputs_identical": True,
        "native_sha256": native_a,
        "source_different_paths": [path for path, item in source_hash_comparison.items() if not item["same"]],
        "p50": {name: value["v8_p50_ms"] for name, value in measurements.items()
                if name != "rle_decode"},
    }, sort_keys=True))


if __name__ == "__main__":
    main()
