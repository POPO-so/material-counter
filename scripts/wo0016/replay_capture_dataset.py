"""Offline replay of a verified WO-0017 raw Gray8/Binary8 capture dataset."""
from __future__ import annotations

import argparse
import inspect
import hashlib
import json
import platform
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture_dataset import evaluate_entities, load_dataset  # noqa: E402
from stream_counter import MaskCounter  # noqa: E402


def replay_capture_dataset(dataset_dir: str | Path, *,
                           counter_factory: Callable[..., Any] | None = None,
                           algorithm_name: str = "baseline") -> dict[str, Any]:
    """Replay the same stored Binary8 masks through a selectable counter factory."""
    manifest, frames = load_dataset(dataset_dir)
    factory = counter_factory or MaskCounter
    config = manifest.get("config", {})
    nested = config.get("counter_config")
    if isinstance(nested, dict):
        requested_config = nested
    else:
        accepted = inspect.signature(factory).parameters
        requested_config = {key: value for key, value in config.items() if key in accepted}
    counter = factory(**requested_config)
    results = []
    replay_started = time.perf_counter_ns()
    for frame in frames:
        result = counter.update(frame.frame_id, frame.mask)
        results.append({
            "frame_id": frame.frame_id,
            "total_count": result.total_count,
            "roi": {"top": result.roi_top, "bottom_exclusive": result.roi_bottom},
            "raw_detections": [asdict(item) for item in result.raw_detections],
            "detections": [asdict(item) for item in result.detections],
            "associations": list(counter.last_associations),
            "tracks": list(counter.tracking_snapshot()),
            "events": [asdict(item) for item in result.events],
            "suppressed_events": [asdict(item) for item in result.suppressed_events],
            "roi_foreground_pixels": result.roi_foreground_pixels,
            "ccl_executed": result.ccl_executed,
        })
    elapsed_ns = time.perf_counter_ns() - replay_started
    replay = {
        "schema_version": 1,
        "status": "REPLAY_COMPLETE",
        "algorithm": algorithm_name,
        "dataset_source_type": manifest.get("source_type"),
        "dataset_records_sha256": manifest.get("frame_records_sha256"),
        "frame_count": len(frames),
        "total_count": counter.total_count,
        "frames": results,
        "performance": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "input": "verified stored raw Binary8 NPY masks",
            "frames": len(frames),
            "elapsed_ms": elapsed_ns / 1_000_000,
            "mean_replay_ms_per_frame": (elapsed_ns / 1_000_000 / len(frames)
                                          if frames else None),
            "statistic": "single sequential replay wall time; not board real-time performance",
        },
    }
    truth_path = Path(dataset_dir) / "ground-truth.json"
    if truth_path.is_file():
        truth_bytes = truth_path.read_bytes()
        replay["ground_truth_source"] = {
            "path": "ground-truth.json",
            "sha256": hashlib.sha256(truth_bytes).hexdigest(),
            "status": "PRESENT",
        }
        try:
            ground_truth = json.loads(truth_bytes.decode("utf-8"))
            replay["entity_report"] = evaluate_entities(ground_truth, results)
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            replay["ground_truth_source"]["status"] = "INVALID"
            replay["entity_report"] = {
                "status": "NOT VERIFIED",
                "reason": f"independent ground-truth file is invalid: {type(exc).__name__}: {exc}",
                "entities": [],
                "duplicate_count": None,
                "missed_count": None,
                "wrong_merge_count": None,
                "unmatched_event_count": None,
            }
    else:
        replay["ground_truth_source"] = {"path": "ground-truth.json", "sha256": None,
                                          "status": "ABSENT"}
        replay["entity_report"] = {
            "status": "NOT VERIFIED",
            "reason": "no independent ground-truth.json annotation is present",
            "entities": [],
            "duplicate_count": None,
            "missed_count": None,
            "wrong_merge_count": None,
            "unmatched_event_count": None,
        }
    return replay


def _write_exclusive(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True,
                  allow_nan=False)
        stream.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True,
                        help="new, exclusive path for replay JSON output")
    args = parser.parse_args(argv)
    report = replay_capture_dataset(args.dataset_dir)
    _write_exclusive(args.output, report)
    print(json.dumps({"status": report["status"], "algorithm": report["algorithm"],
                      "frames": report["frame_count"], "total_count": report["total_count"],
                      "entity_report_status": report["entity_report"]["status"],
                      "output": str(args.output)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
