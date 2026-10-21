"""Offline-only replay of persisted CCL records; never imports PYNQ or opens cameras."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from stream_counter import Detection, MaskCounter


def replay(frames, config):
    counter = MaskCounter(**config)
    associations, events, totals = [], [], []
    for frame in frames:
        result = counter.update_detections(frame["frame_id"],
                                           [Detection(**item) for item in
                                            frame.get("raw_detections", frame["detections"])])
        for match in counter.last_associations:
            detection = result.detections[match["detection_index"]]
            associations.append({"frame_id": result.frame_id, **match, **asdict(detection)})
        events.extend(asdict(event) for event in result.events)
        totals.append({"frame_id": result.frame_id, "accepted_components": len(result.detections),
                       "total_count": result.total_count})
    return {"configuration": config, "frames_processed": len(totals),
            "replayed_total_count": counter.total_count, "events": events,
            "associations": associations, "frame_totals": totals}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    raw = args.source.read_bytes()
    frames = json.loads(raw)
    config = dict(count_axis="x", line_x=160, line_y=120, direction=1,
                  min_area=12, max_area=None, max_step=60.0, max_lateral_step=10.0,
                  hysteresis=2.0, confirm_hits=2)
    result = replay(frames, config)
    result.update(source_sha256=hashlib.sha256(raw).hexdigest(), board_accessed=False,
                  evidence_scope="Recorded qualified CCL only; no masks, no physical object labels")
    result["sensitivity"] = []
    for min_area in (12, 24, 32, 48, 64, 80, 120):
        for max_area in (None, 600):
            trial = replay(frames, {**config, "min_area": min_area, "max_area": max_area})
            result["sensitivity"].append({"min_area": min_area, "max_area": max_area,
                                          "total": trial["replayed_total_count"], "events": trial["events"]})
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/"replay.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    for name, rows in (("associations", result["associations"]), ("crossing-events", result["events"]),
                       ("frame-totals", result["frame_totals"])):
        with (args.output/f"{name}.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ["frame_id"])
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps({key: result[key] for key in ("source_sha256", "frames_processed", "replayed_total_count", "events", "sensitivity")}))


if __name__ == "__main__":
    main()
