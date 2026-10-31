"""Validate and compare paired v7/v8 CPU replay reports, one frame at a time."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import statistics


ROOT = Path(__file__).resolve().parent
METRICS = ("wall_ms", "region_ms", "track_ms")


def _read(version: str) -> tuple[dict, str]:
    path = ROOT / f"{version}-cpu.json"
    raw = path.read_bytes()
    value = json.loads(raw)
    if value.get("schema") != "material-counter.cpu-performance-replay.v1":
        raise ValueError(f"unexpected replay schema in {path.name}")
    if value.get("execution", {}).get("mode") != "cpu-only":
        raise ValueError(f"not a CPU-only report: {path.name}")
    return value, hashlib.sha256(raw).hexdigest()


def _validate(report: dict, version: str) -> None:
    for group in report["inputs"]:
        repeats = group["repeats"]
        if len(repeats) != 5:
            raise ValueError(f"{version} {group['event_path']}: expected five repeats")
        if len({item["semantic_sha256"] for item in repeats}) != 1:
            raise ValueError(f"{version} {group['event_path']}: semantic hashes diverge")
        if len({item["repeat"] for item in repeats}) != 5:
            raise ValueError(f"{version} {group['event_path']}: duplicate repeat IDs")
        frame_sets = [tuple(frame["frame_id"] for frame in item["frames"])
                      for item in repeats]
        if len(set(frame_sets)) != 1 or len(frame_sets[0]) != group["frame_count"]:
            raise ValueError(f"{version} {group['event_path']}: frame lists differ")


def _frame_metrics(group: dict) -> dict[int, dict[str, list[float]]]:
    values: dict[int, dict[str, list[float]]] = {}
    for repeat in group["repeats"]:
        for frame in repeat["frames"]:
            slot = values.setdefault(frame["frame_id"], {metric: [] for metric in METRICS})
            for metric in METRICS:
                slot[metric].append(float(frame[metric]))
    return values


def main() -> None:
    reports = {}
    input_hashes = {}
    for version in ("v7", "v8"):
        reports[version], input_hashes[version] = _read(version)
        _validate(reports[version], version)

    native_hashes = {
        version: report["native_library"].get("sha256")
        for version, report in reports.items()
    }
    if not native_hashes["v7"] or native_hashes["v7"] != native_hashes["v8"]:
        raise ValueError(f"native library hashes differ: {native_hashes}")

    pairs = {}
    for version in ("v7", "v8"):
        for group in reports[version]["inputs"]:
            key = group["event_sha256"]
            if key in pairs.setdefault(version, {}):
                raise ValueError(f"duplicate event digest in {version}: {key}")
            pairs[version][key] = group
    if set(pairs["v7"]) != set(pairs["v8"]):
        raise ValueError("v7/v8 event input sets differ")

    comparisons = []
    for event_sha in sorted(pairs["v7"], key=lambda digest: pairs["v7"][digest]["event_path"]):
        old = pairs["v7"][event_sha]
        new = pairs["v8"][event_sha]
        if old["frame_count"] != new["frame_count"]:
            raise ValueError(f"frame count differs for input {event_sha}")
        old_frames, new_frames = _frame_metrics(old), _frame_metrics(new)
        if set(old_frames) != set(new_frames):
            raise ValueError(f"frame IDs differ for input {event_sha}")
        frame_rows = []
        for frame_id in sorted(old_frames):
            row = {"frame_id": frame_id, "metrics": {}}
            for metric in METRICS:
                before = statistics.median(old_frames[frame_id][metric])
                after = statistics.median(new_frames[frame_id][metric])
                row["metrics"][metric] = {
                    "v7_median_ms": before,
                    "v8_median_ms": after,
                    "delta_ms": after - before,
                    "change_percent": (after / before - 1.0) * 100.0 if before else None,
                    "v7_repeat_values_ms": old_frames[frame_id][metric],
                    "v8_repeat_values_ms": new_frames[frame_id][metric],
                }
            frame_rows.append(row)
        group_metrics = {}
        worst_frames = {}
        for metric in METRICS:
            old_values = [row["metrics"][metric]["v7_median_ms"] for row in frame_rows]
            new_values = [row["metrics"][metric]["v8_median_ms"] for row in frame_rows]
            worst = max(frame_rows, key=lambda row: row["metrics"][metric]["v8_median_ms"])
            group_metrics[metric] = {
                "v7_frame_median_p50_ms": statistics.median(old_values),
                "v8_frame_median_p50_ms": statistics.median(new_values),
                "v7_slowest_frame_ms": max(old_values),
                "v8_slowest_frame_ms": max(new_values),
                "v8_slowest_frame_id": worst["frame_id"],
            }
            worst_frames[metric] = worst["frame_id"]
        comparisons.append({
            "event_path": old["event_path"],
            "event_sha256": event_sha,
            "frame_count": old["frame_count"],
            "v7_deterministic": old["deterministic"],
            "v8_deterministic": new["deterministic"],
            "group_summary": group_metrics,
            "frames": frame_rows,
        })

    result = {
        "schema": "material-counter.paired-cpu-replay-comparison.v1",
        "scope": "same captured CPU replay inputs; not full-video or material-accuracy evidence",
        "validation": {
            "five_semantic_repeats_each_group": True,
            "all_within_group_semantic_sha256_identical": True,
            "native_library_sha256_identical": True,
            "paired_event_inputs": len(comparisons),
            "paired_frames": sum(item["frame_count"] for item in comparisons),
        },
        "reports": {
            version: {
                "sha256": input_hashes[version],
                "source_identity": report["application_source"],
                "native_library": report["native_library"],
            }
            for version, report in reports.items()
        },
        "comparisons": comparisons,
    }
    (ROOT / "cpu-replay-comparison.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["validation"], sort_keys=True))


if __name__ == "__main__":
    main()
