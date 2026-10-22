from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "baseline-replay.json"

ATTEMPT4 = ROOT / "data/results/wo0016-stageB2-attempt4-20261020-6f13f0f0"
ATTEMPT4_RESULT = ATTEMPT4 / "home/xilinx/board-runtime-currentboot-v2/stageb2-attempt-4-result.json"
ATTEMPT4_FRAMES = ATTEMPT4 / "var/lib/wo0016/attempts/6f13f0f0-4433-445f-a023-fa403685aeee/attempt-4/frame-results.json"
ATTEMPT4_SOURCE = ATTEMPT4 / "home/xilinx/board-runtime-currentboot-v2/scripts/wo0016/stream_counter.py"
ATTEMPT4_PACKAGE = ATTEMPT4 / "home/xilinx/board-runtime-currentboot-v2/PACKAGE_SHA256.json"

ATTEMPT5_ROOT = ROOT / "data/results/wo0016-stageB2-attempt5-prep-20261021"
ATTEMPT5 = ATTEMPT5_ROOT / "attempt-5-recovery-2-sample-20261021/attempt-5-recovery-2"
ATTEMPT5_RESULT = ATTEMPT5 / "attempt-5-recovery-2-result.json"
ATTEMPT5_FRAMES = ATTEMPT5 / "frame-results.json"
ATTEMPT5_SOURCE = ATTEMPT5_ROOT / "deployment-source/stream_counter.py"
ATTEMPT5_MANIFEST = ATTEMPT5_ROOT / "deployment-source/DEPLOYMENT-SOURCE-SHA256.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(source: Path, label: str):
    spec = importlib.util.spec_from_file_location(f"wo0017_counter_{label}", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load historical counter source: {source}")
    module = importlib.util.module_from_spec(spec)
    __import__('sys').modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def normalized_event(event: dict) -> dict:
    return {
        "track_id": int(event["track_id"]),
        "frame_id": int(event["frame_id"]),
        "centroid_x": float(event["centroid_x"]),
        "centroid_y": float(event["centroid_y"]),
        "inferred_gap": bool(event.get("inferred_gap", False)),
    }


def expected_source_hash(label: str, manifest_path: Path) -> str:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if label == "attempt4":
        return manifest["files"]["scripts/wo0016/stream_counter.py"]
    entry = next(x for x in manifest["source_files"] if x["path"] == "stream_counter.py")
    return entry["sha256"]


def active_runs(records: list[dict]) -> list[list[int]]:
    active = []
    for row in records:
        has_signal = bool(row.get("roi_foreground_pixels", 0)) or bool(
            row.get("raw_detections", row.get("detections", []))
        )
        if has_signal:
            frame = int(row["frame_id"])
            if not active or frame != active[-1][-1] + 1:
                active.append([frame, frame])
            else:
                active[-1][-1] = frame
    return active


def replay(label: str, source: Path, manifest: Path, result_path: Path,
           frames_path: Path) -> dict:
    result_record = json.loads(result_path.read_text(encoding="utf-8"))
    records = json.loads(frames_path.read_text(encoding="utf-8"))
    config = result_record["counting_config"]
    source_hash = sha256(source)
    source_hash_expected = expected_source_hash(label, manifest)
    if source_hash != source_hash_expected:
        raise AssertionError(f"{label}: historical source hash mismatch")

    module = load_module(source, label)
    counter = module.MaskCounter(**config)
    replayed_rows = []
    replayed_events = []
    recorded_events = []
    associations = []
    for row in records:
        component_rows = row.get("raw_detections", row.get("detections", []))
        components = [module.Detection(**item) for item in component_rows]
        frame_result = counter.update_detections(int(row["frame_id"]), components)
        events = [asdict(event) for event in frame_result.events]
        replayed_events.extend(normalized_event(event) for event in events)
        recorded_events.extend(normalized_event(event) for event in row.get("events", []))
        replayed_rows.append({
            "frame_id": frame_result.frame_id,
            "total_count": frame_result.total_count,
            "accepted_components": len(frame_result.detections),
            "events": [normalized_event(event) for event in events],
        })
        for association in counter.last_associations:
            associations.append({"frame_id": frame_result.frame_id, **association})

    recorded_counts = [int(row["total_count"]) for row in records]
    replayed_counts = [int(row["total_count"]) for row in replayed_rows]
    masks_present = any(any(key in row for key in ("mask", "binary8_mask", "frame", "image"))
                        for row in records)
    summary = {
        "attempt": label,
        "material_label": result_record.get("material_label"),
        "frames_recorded": len(records),
        "expected_count_configured": result_record.get("expected_count"),
        "ground_truth_count_field": result_record.get("ground_truth_count"),
        "board_total_count": result_record.get("total_count"),
        "counting_config": config,
        "historical_counter_sha256": source_hash,
        "historical_counter_sha256_manifest": source_hash_expected,
        "frame_results_sha256": sha256(frames_path),
        "replay_total_count": counter.total_count,
        "replay_events": replayed_events,
        "events_match_recorded": replayed_events == recorded_events,
        "per_frame_count_match": replayed_counts == recorded_counts,
        "no_raw_frame_or_mask_fields": not masks_present,
        "active_detection_runs": active_runs(records),
        "associations": associations,
        "frame_totals": replayed_rows,
    }
    if len(records) != int(result_record["frames_processed"]):
        raise AssertionError(f"{label}: recorded frame count differs from result JSON")
    if counter.total_count != int(result_record["total_count"]):
        raise AssertionError(f"{label}: replay count differs from board result")
    if replayed_events != recorded_events:
        raise AssertionError(f"{label}: event sequence differs from board sidecar")
    if replayed_counts != recorded_counts:
        raise AssertionError(f"{label}: per-frame totals differ from board sidecar")
    return summary


summaries = [
    replay("attempt4", ATTEMPT4_SOURCE, ATTEMPT4_PACKAGE, ATTEMPT4_RESULT, ATTEMPT4_FRAMES),
    replay("attempt5_recovery2", ATTEMPT5_SOURCE, ATTEMPT5_MANIFEST, ATTEMPT5_RESULT, ATTEMPT5_FRAMES),
]
output = {"scope": "offline replay of saved CCL records only; no masks or physical identities",
          "board_accessed": False, "attempts": summaries}
OUT.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
print(json.dumps({
    "output": str(OUT),
    "attempts": [{k: x[k] for k in (
        "attempt", "frames_recorded", "expected_count_configured", "board_total_count",
        "replay_total_count", "historical_counter_sha256", "frame_results_sha256",
        "events_match_recorded", "per_frame_count_match", "no_raw_frame_or_mask_fields",
        "active_detection_runs", "replay_events")}
        for x in summaries],
}, indent=2))
