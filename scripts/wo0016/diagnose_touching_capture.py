"""Offline diagnosis of adjacent-object losses in verified WO-0017 captures."""
from __future__ import annotations

import argparse
import inspect
import json
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np

from capture_dataset import load_dataset
from stream_counter import MaskCounter


def analyze_frames(frames, config: dict) -> dict:
    """Compare the recorded counter with proximity clustering disabled.

    The second run is a diagnostic candidate, not an accuracy result. Both
    counters consume identical Binary8 masks in the original frame order.
    """
    baseline = MaskCounter(**config)
    without_gap = MaskCounter(**{**config, "component_cluster_gap": 0.0})
    rows = []
    for frame_id, mask in frames:
        observed = baseline.update(frame_id, mask)
        alternative = without_gap.update(frame_id, mask)
        roi = mask[observed.roi_top:observed.roi_bottom]
        _, _, stats, _ = cv2.connectedComponentsWithStats(roi, connectivity=8)
        qualified_cc = int(np.count_nonzero(
            stats[1:, cv2.CC_STAT_AREA] >= baseline.min_area))
        merged_separate = (qualified_cc >= 2 and
                           len(observed.raw_detections) > len(observed.detections))
        rows.append({
            "frame_id": frame_id,
            "qualified_binary8_components": qualified_cc,
            "precluster_detections": len(observed.raw_detections),
            "baseline_detections": len(observed.detections),
            "no_gap_detections": len(alternative.detections),
            "rejected_large_components": len(baseline.rejected_detections),
            "proximity_merge_candidate": merged_separate,
            "single_binary8_component": qualified_cc == 1,
            "baseline_events": [asdict(event) for event in observed.events],
            "no_gap_events": [asdict(event) for event in alternative.events],
            "baseline_associations": list(baseline.last_associations),
            "baseline_tracks": list(observed.tracks),
        })
    return {
        "status": "DIAGNOSTIC_ONLY",
        "ground_truth_status": "NOT_VERIFIED",
        "frame_count": len(rows),
        "baseline_total": baseline.total_count,
        "no_gap_total": without_gap.total_count,
        "proximity_merge_frames": [row["frame_id"] for row in rows
                                   if row["proximity_merge_candidate"]],
        "note": ("Different totals do not establish accuracy; compare each event "
                 "with independently annotated physical objects."),
        "frames": rows,
    }


def analyze_capture(dataset_dir: str | Path) -> dict:
    manifest, frames = load_dataset(dataset_dir)
    config = manifest["config"]
    nested = config.get("counter_config")
    if isinstance(nested, dict):
        config = nested
    else:
        accepted = inspect.signature(MaskCounter).parameters
        config = {key: value for key, value in config.items() if key in accepted}
    report = analyze_frames(((frame.frame_id, frame.mask) for frame in frames), config)
    report["source_manifest"] = str(Path(dataset_dir).resolve() / "manifest.json")
    report["frame_records_sha256"] = manifest["frame_records_sha256"]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = analyze_capture(args.dataset_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({key: report[key] for key in (
        "status", "frame_count", "baseline_total", "no_gap_total",
        "proximity_merge_frames")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
