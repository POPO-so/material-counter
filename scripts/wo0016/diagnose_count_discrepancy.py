"""Audit a complete WO-0017 capture without changing or replaying its counter."""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np

from capture_dataset import CapturedFrame, load_dataset


_DIAGNOSTIC_FIELDS = (
    "rejected_components", "split_attempts", "ambiguous_associations", "lost_tracks",
)


def _metadata_field(metadata: dict[str, Any], field: str) -> tuple[bool, Any]:
    diagnostics = metadata.get("diagnostics")
    if isinstance(diagnostics, dict) and field in diagnostics:
        return True, diagnostics[field]
    if field in metadata:
        return True, metadata[field]
    return False, None


def _coverage(frames: Iterable[CapturedFrame]) -> dict[str, str]:
    frame_list = tuple(frames)
    result = {}
    for field in _DIAGNOSTIC_FIELDS:
        present = sum(_metadata_field(frame.metadata, field)[0] for frame in frame_list)
        result[field] = ("missing" if not present else
                         "present" if present == len(frame_list) else "partial")
    return result


def _config(manifest: dict[str, Any], frames: tuple[CapturedFrame, ...]) -> dict[str, Any]:
    config = manifest.get("config", {})
    nested = config.get("counter_config") if isinstance(config, dict) else None
    if isinstance(nested, dict):
        return nested
    first = frames[0].metadata.get("algorithm_config", {}) if frames else {}
    return first if isinstance(first, dict) else {}


def _detection(track: dict[str, Any]) -> dict[str, Any]:
    value = track.get("detection")
    return value if isinstance(value, dict) else track


def _same_detection_geometry(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left, right = _detection(left), _detection(right)
    bounds = ("x_min", "x_max", "y_min", "y_max")
    if all(key in left and key in right for key in bounds):
        return all(left[key] == right[key] for key in bounds)
    coordinates = ("centroid_x", "centroid_y")
    if (all(key in left and key in right for key in coordinates)
            and left.get("area") is not None and right.get("area") is not None):
        return (abs(float(left["centroid_x"]) - float(right["centroid_x"])) <= 1.5
                and abs(float(left["centroid_y"]) - float(right["centroid_y"])) <= 1.5
                and abs(float(left["area"]) - float(right["area"])) <= 1)
    return False


def _track_map(metadata: dict[str, Any]) -> dict[int, dict[str, Any]]:
    tracks = metadata.get("tracks")
    if not isinstance(tracks, list):
        return {}
    return {int(track["track_id"]): track for track in tracks
            if isinstance(track, dict) and track.get("track_id") is not None}


def _track_history(frames: tuple[CapturedFrame, ...]) -> dict[int, list[tuple[int, dict[str, Any]]]]:
    history: dict[int, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for frame in frames:
        for track_id, track in _track_map(frame.metadata).items():
            history[track_id].append((frame.frame_id, track))
    for observations in history.values():
        observations.sort(key=lambda item: item[0])
    return history


def _record(category: str, frame_id: int, *, track_id: int | None = None,
            description: str, evidence: dict[str, Any] | None = None,
            confidence: str = "candidate") -> dict[str, Any]:
    return {"category": category, "frame_id": int(frame_id),
            "track_id": track_id, "description": description,
            "evidence": evidence or {}, "confidence": confidence,
            "contact_sheet": None}


def _event_track(frame: CapturedFrame, event: dict[str, Any]) -> dict[str, Any] | None:
    track_id = event.get("track_id")
    if track_id is not None:
        track = _track_map(frame.metadata).get(int(track_id))
        if track is not None:
            return track
    center_x, center_y = event.get("centroid_x"), event.get("centroid_y")
    if center_x is None or center_y is None:
        return None
    detections = frame.metadata.get("detections")
    if not isinstance(detections, list):
        return None
    candidates = [d for d in detections if isinstance(d, dict)
                  and d.get("centroid_x") is not None and d.get("centroid_y") is not None]
    if not candidates:
        return None
    closest = min(candidates, key=lambda d: (float(d["centroid_x"]) - float(center_x)) ** 2
                  + (float(d["centroid_y"]) - float(center_y)) ** 2)
    distance2 = (float(closest["centroid_x"]) - float(center_x)) ** 2 + (
        float(closest["centroid_y"]) - float(center_y)) ** 2
    return {"track_id": track_id, "detection": closest} if distance2 <= 400 else None


def _has_split_attempt(metadata: dict[str, Any], detection: dict[str, Any]) -> tuple[bool, bool]:
    present, attempts = _metadata_field(metadata, "split_attempts")
    if not present or not isinstance(attempts, list):
        return False, False
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        parent = _detection(attempt)
        accepted = bool(attempt.get("accepted", attempt.get("split_accepted", False)))
        children = attempt.get("children", [])
        if accepted and isinstance(children, list) and any(
                isinstance(child, dict) and _same_detection_geometry(child, detection)
                for child in children):
            return True, True
        if _same_detection_geometry(parent, detection):
            return True, False
    return True, False


def _median_area(frames: tuple[CapturedFrame, ...]) -> float | None:
    values = []
    for frame in frames:
        detections = frame.metadata.get("detections")
        if not isinstance(detections, list):
            continue
        values.extend(float(item["area"]) for item in detections
                      if isinstance(item, dict) and isinstance(item.get("area"), (int, float))
                      and item["area"] > 0)
    if not values:
        return None
    return float(statistics.median(values))


def _nearby_mother_area_drops(history: dict[int, list[tuple[int, dict[str, Any]]]],
                              frame_id: int, detection: dict[str, Any]
                              ) -> list[dict[str, Any]]:
    """Find adjacent-frame tracks with a local >50% area drop near a new track."""
    x, y = detection.get("centroid_x"), detection.get("centroid_y")
    if x is None or y is None:
        return []
    clues = []
    for track_id, observations in history.items():
        previous = next((track for observed_frame, track in reversed(observations)
                         if observed_frame == frame_id - 1), None)
        current = next((track for observed_frame, track in observations
                        if observed_frame == frame_id), None)
        if previous is None or current is None:
            continue
        old, new = _detection(previous), _detection(current)
        old_area, new_area = old.get("area"), new.get("area")
        if not isinstance(old_area, (int, float)) or old_area <= 0 \
                or not isinstance(new_area, (int, float)):
            continue
        if (float(old_area) - float(new_area)) / float(old_area) <= 0.5:
            continue
        px, py = new.get("centroid_x"), new.get("centroid_y")
        if px is None or py is None or (float(px) - float(x)) ** 2 + (float(py) - float(y)) ** 2 > 900:
            continue
        clues.append({"track_id": track_id, "previous_frame_id": frame_id - 1,
                      "previous_area": old_area, "area": new_area,
                      "centroid_x": px, "centroid_y": py})
    return clues


def _detect_records(frames: tuple[CapturedFrame, ...], config: dict[str, Any], *,
                    min_area_margin: int, large_area_px: int,
                    large_area_ratio: float, late_birth_x: float,
                    area_jump_ratio: float) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    records: list[dict[str, Any]] = []
    coverage = _coverage(frames)
    limitations = []
    line = float(config.get("line_x", 160))
    min_area = int(config.get("min_area", 15))
    roi_top = int(config.get("roi_top", config.get("roi", {}).get("top", 0)))
    roi_bottom = int(config.get("roi_bottom", config.get("roi", {}).get("bottom", 240)))
    median_area = _median_area(frames)
    history = _track_history(frames)
    first_seen = {track_id: observations[0][0]
                  for track_id, observations in history.items() if observations}
    event_track_ids = {
        int(event["track_id"])
        for captured in frames
        for event in (captured.metadata.get("events") or [])
        if isinstance(event, dict) and event.get("track_id") is not None
    }
    for field, status in coverage.items():
        if status != "present":
            limitations.append(f"{field} diagnostic data is {status}; affected negative findings are unknown.")

    events_by_frame_track: set[tuple[int, int]] = set()
    for frame in frames:
        metadata = frame.metadata
        events = metadata.get("events")
        for event in events if isinstance(events, list) else []:
            if not isinstance(event, dict):
                continue
            track_id = event.get("track_id")
            track_id = None if track_id is None else int(track_id)
            if track_id is not None:
                events_by_frame_track.add((frame.frame_id, track_id))
            track = _event_track(frame, event)
            detection = {} if track is None else _detection(track)
            area = detection.get("area", event.get("area"))
            records.append(_record("EVENT", frame.frame_id, track_id=track_id,
                                   description="Recorded count event; physical identity requires independent review.",
                                   evidence={"event": event, "area": area,
                                             "centroid_x": detection.get("centroid_x", event.get("centroid_x")),
                                             "centroid_y": detection.get("centroid_y", event.get("centroid_y")),
                                             "detection_available": bool(detection)}))
            if isinstance(area, (int, float)) and (
                    area >= large_area_px or (median_area is not None
                                              and area >= large_area_ratio * median_area)):
                split_present, split_accepted = _has_split_attempt(metadata, detection)
                if not split_accepted:
                    state = ("no accepted split for this detection" if split_present
                             else "split status unknown")
                    records.append(_record(
                        "A", frame.frame_id, track_id=track_id,
                        description="Large detection at a count event; review whether multiple objects remained joined.",
                        evidence={"area": area, "median_area": median_area,
                                  "large_area_px": large_area_px,
                                  "large_area_ratio": large_area_ratio,
                                  "split_status": state,
                                  "pattern": "large_detection_at_count_event",
                                  "centroid_x": detection.get("centroid_x", event.get("centroid_x")),
                                  "centroid_y": detection.get("centroid_y", event.get("centroid_y"))}))

        associations = metadata.get("associations")
        new_associations = {}
        for association in associations if isinstance(associations, list) else []:
            if not isinstance(association, dict) or association.get("kind") != "new":
                continue
            try:
                track_id = int(association["track_id"])
            except (KeyError, TypeError, ValueError):
                continue
            new_associations[track_id] = association
        tracks_here = _track_map(metadata)
        birth_ids = set(new_associations)
        birth_ids.update(track_id for track_id, track in tracks_here.items()
                         if track.get("born_past_line") is True
                         and first_seen.get(track_id) == frame.frame_id)
        for track_id in sorted(birth_ids):
            track = tracks_here.get(track_id)
            if track is None or (frame.frame_id, track_id) in events_by_frame_track:
                continue
            detection = _detection(track)
            x = detection.get("centroid_x")
            if x is None or float(x) < late_birth_x:
                continue
            association = new_associations.get(track_id, {})
            born_past_line = association.get("born_past_line", track.get("born_past_line"))
            records.append(_record(
                "B", frame.frame_id, track_id=track_id,
                description="New track appears in the count-line neighborhood without a same-frame event.",
                evidence={"centroid_x": x, "area": detection.get("area"),
                          "late_birth_x": late_birth_x,
                          "born_past_line": born_past_line,
                          "armed": track.get("armed"), "counted": track.get("counted"),
                          "mother_track_area_drop": _nearby_mother_area_drops(
                              history, frame.frame_id, detection)}))

        present, lost_tracks = _metadata_field(metadata, "lost_tracks")
        if present and isinstance(lost_tracks, list):
            for track in lost_tracks:
                if not isinstance(track, dict) or not track.get("armed") or track.get("counted"):
                    continue
                try:
                    track_id = int(track["track_id"])
                except (KeyError, TypeError, ValueError):
                    track_id = None
                records.append(_record(
                    "C", frame.frame_id, track_id=track_id,
                    description="Armed track was lost before a recorded count event.",
                    evidence={"track": track, "lost_track_observed": True,
                              "evidence_scope": "explicit_lost_tracks"}))
        present, ambiguous = _metadata_field(metadata, "ambiguous_associations")
        if present and isinstance(ambiguous, list):
            for association in ambiguous:
                if not isinstance(association, dict):
                    continue
                track_id = association.get("track_id")
                records.append(_record(
                    "C", frame.frame_id,
                    track_id=(None if track_id is None else
                              int(track_id) if str(track_id).lstrip("-").isdigit() else None),
                    description="Association was rejected as ambiguous among multiple candidates.",
                    evidence={"ambiguous_association": association,
                              "lost_track_observed": None,
                              "evidence_scope": "explicit_ambiguous_association"}))

        present, rejected = _metadata_field(metadata, "rejected_components")
        if present and isinstance(rejected, list):
            for component in rejected:
                if not isinstance(component, dict):
                    continue
                if component.get("rejected") is False:
                    continue
                detection = _detection(component)
                area = detection.get("area")
                reason = str(component.get("reason", "unknown"))
                near_min = isinstance(area, (int, float)) and min_area - min_area_margin <= area < min_area
                centroid_y = detection.get("centroid_y")
                within_roi = (centroid_y is None or
                              roi_top <= float(centroid_y) < roi_bottom)
                if near_min and within_roi:
                    records.append(_record(
                        "D", frame.frame_id,
                        description="Rejected component is just below the configured minimum area inside the ROI.",
                        evidence={"area": area, "min_area": min_area,
                                  "reason": reason, "centroid_x": detection.get("centroid_x"),
                                  "centroid_y": centroid_y,
                                  "roi_top": roi_top, "roi_bottom": roi_bottom}))

    for track_id, observations in history.items():
        for (previous_frame, previous), (frame_id, current) in zip(observations, observations[1:]):
            old_area = _detection(previous).get("area")
            new_area = _detection(current).get("area")
            if (not isinstance(old_area, (int, float)) or old_area <= 0
                    or not isinstance(new_area, (int, float))):
                continue
            change_ratio = abs(float(new_area) - float(old_area)) / float(old_area)
            if change_ratio > area_jump_ratio:
                records.append(_record(
                    "D", frame_id, track_id=track_id,
                    description="Track detection area changed sharply between adjacent observations; inspect mask continuity.",
                    evidence={"previous_frame_id": previous_frame,
                              "previous_area": old_area, "area": new_area,
                              "relative_change": change_ratio,
                              "centroid_x": _detection(current).get("centroid_x"),
                              "centroid_y": _detection(current).get("centroid_y")}))

    # Old datasets may omit explicit lost-track records. A retained finished
    # flag or permanent disappearance after a non-final frame is weaker
    # evidence and is labeled accordingly. A final-frame active track is live.
    explicit_lost_ids = set()
    for frame in frames:
        present, lost = _metadata_field(frame.metadata, "lost_tracks")
        if present and isinstance(lost, list):
            explicit_lost_ids.update(
                int(item["track_id"]) for item in lost
                if isinstance(item, dict)
                and str(item.get("track_id", "")).lstrip("-").isdigit())
    for track_id, observations in history.items():
        if not observations or track_id in event_track_ids or track_id in explicit_lost_ids:
            continue
        last_frame, track = observations[-1]
        if not track.get("armed") or track.get("counted"):
            continue
        finished = track.get("finished") is True
        if not finished and last_frame >= frames[-1].frame_id:
            continue
        evidence_scope = ("finished_flag_in_track_snapshot" if finished
                          else "inferred_track_snapshot_disappearance")
        loss_frame = last_frame if finished else last_frame + 1
        records.append(_record(
            "C", loss_frame, track_id=track_id,
            description="Armed uncounted track appears finished or disappears before capture end.",
            evidence={"track": track, "lost_track_observed": False,
                      "evidence_scope": evidence_scope,
                      "last_observed_frame": last_frame,
                      "finished_flag": finished}))

    # Include real line crossings with no event, separately from births beyond it.
    axis = str(config.get("count_axis", "x"))
    direction = int(config.get("direction", 1))
    line_position = float(config.get("line_x", 160) if axis == "x"
                          else config.get("line_y", 120))
    hysteresis = float(config.get("hysteresis", 2.0))

    def side(detection: dict[str, Any]) -> float | None:
        value = detection.get("centroid_x" if axis == "x" else "centroid_y")
        return None if value is None else direction * (float(value) - line_position)

    for track_id, observations in history.items():
        for (previous_frame, previous), (frame_id, current) in zip(observations, observations[1:]):
            if (frame_id, track_id) in events_by_frame_track:
                continue
            if previous.get("born_past_line") or current.get("born_past_line"):
                continue
            old_side, new_side = side(_detection(previous)), side(_detection(current))
            if (old_side is None or new_side is None or old_side >= -hysteresis
                    or new_side < hysteresis
                    or not (previous.get("armed") or current.get("armed"))):
                continue
            detection = _detection(current)
            coordinate = "centroid_x" if axis == "x" else "centroid_y"
            records.append(_record(
                "A", frame_id, track_id=track_id,
                description="Armed track crosses the count line without a recorded event.",
                evidence={"pattern": "armed_crossing_without_event",
                          "previous_frame_id": previous_frame,
                          "previous_centroid": _detection(previous).get(coordinate),
                          "centroid_x": detection.get("centroid_x"),
                          "centroid_y": detection.get("centroid_y"),
                          "area": detection.get("area"),
                          "count_axis": axis, "line_position": line_position}))

    # Reuse one contact sheet per anchor frame while keeping every record linked.
    # This bounds output size when several diagnostic categories hit one frame.
    records.sort(key=lambda row: (row["frame_id"], row["category"],
                                  -1 if row["track_id"] is None else row["track_id"]))
    return records, coverage, limitations


def _make_contact_sheet(frames: tuple[CapturedFrame, ...], frame_index: dict[int, CapturedFrame],
                        anchor: int, rows: list[dict[str, Any]], output_dir: Path,
                        *, line_x: int, roi_top: int, roi_bottom: int) -> str:
    start, stop = max(0, anchor - 4), min(len(frames) - 1, anchor + 4)
    ids = list(range(start, stop + 1))
    global_header_h, tile_w, tile_h, tile_header_h, cols = 24, 640, 240, 44, 3
    rows_count = (len(ids) + cols - 1) // cols
    canvas = np.full((global_header_h + rows_count * (tile_h + tile_header_h),
                      cols * tile_w), 28, dtype=np.uint8)
    anchor_records = [row for row in rows if row["frame_id"] == anchor
                      and row["category"] in {"A", "B", "C", "D"}]
    summary = ", ".join(
        f'{row["category"]}:T{row["track_id"]}' if row["track_id"] is not None
        else row["category"] for row in anchor_records)
    missing_before, missing_after = 4 - (anchor - start), 4 - (stop - anchor)
    anchor_text = (f"anchor {anchor} | review {summary or 'none'} | "
                   f"missing before/after {missing_before}/{missing_after}")
    cv2.putText(canvas, anchor_text[:180], (8, 17),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, 255, 1, cv2.LINE_AA)
    for index, frame_id in enumerate(ids):
        captured = frame_index[frame_id]
        y, x = divmod(index, cols)
        cell_top = global_header_h + y * (tile_h + tile_header_h)
        top = cell_top + tile_header_h
        left = x * tile_w
        current_tracks = []
        for track in _track_map(captured.metadata).values():
            if track.get("finished") is True:
                continue
            if track.get("last_frame") != frame_id:
                continue
            detection = _detection(track)
            track_id, area = track.get("track_id"), detection.get("area")
            if track_id is not None and area is not None:
                current_tracks.append(f"T{track_id}/A{area}px")
        current_tracks.sort()
        observations = ", ".join(current_tracks) if current_tracks else "none"
        cv2.putText(canvas, f"frame {frame_id} | Gray8 | Binary8",
                    (left + 5, cell_top + 17), cv2.FONT_HERSHEY_SIMPLEX,
                    0.38, 240, 1, cv2.LINE_AA)
        cv2.putText(canvas, f"current tracks: {observations[:90]}",
                    (left + 5, cell_top + 36), cv2.FONT_HERSHEY_SIMPLEX,
                    0.34, 240, 1, cv2.LINE_AA)
        pair = np.concatenate((captured.gray, captured.mask), axis=1)
        canvas[top:top + tile_h, left:left + tile_w] = pair
        for offset in (0, 320):
            line = max(0, min(319, line_x))
            cv2.line(canvas, (left + offset + line, top),
                     (left + offset + line, top + tile_h - 1), 180, 1)
            if 0 <= roi_top < tile_h:
                cv2.line(canvas, (left + offset, top + roi_top),
                         (left + offset + 319, top + roi_top), 110, 1)
            if 0 <= roi_bottom - 1 < tile_h:
                cv2.line(canvas, (left + offset, top + roi_bottom - 1),
                         (left + offset + 319, top + roi_bottom - 1), 110, 1)
    sheet_dir = output_dir / "contact-sheets"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    filename = f"frame-{anchor:06d}.png"
    if not cv2.imwrite(str(sheet_dir / filename), canvas):
        raise OSError(f"failed to write contact sheet for frame {anchor}")
    return f"contact-sheets/{filename}"


def _markdown_report(report: dict[str, Any]) -> str:
    lines = ["# Offline count discrepancy audit", "",
             "Status: DIAGNOSTIC_ONLY. These records are review candidates, not confirmed causes or physical ground truth.", "",
             f"Capture: `{report['capture_dir']}`  ",
             f"Frames: {report['frame_count']}  ",
             f"Recorded total at final frame: {report['final_total_count']}  ",
             f"Binary8 / Gray8 source: `{report['source_data']}`", "",
             "## Diagnostic field coverage", "",
             "| Field | Coverage |", "|---|---|"]
    for field, status in report["diagnostic_field_coverage"].items():
        lines.append(f"| `{field}` | {status} |")
    for limitation in report["limitations"]:
        lines.append(f"- {limitation}")
    lines.extend(["", "## Candidate records", "",
                  "| Type | Frame | Track | Evidence | Contact sheet |",
                  "|---|---:|---:|---|---|"])
    for row in report["records"]:
        evidence = json.dumps(row["evidence"], ensure_ascii=False, sort_keys=True)
        evidence = evidence.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
        sheet = row["contact_sheet"]
        lines.append(f"| {row['category']} | {row['frame_id']} | {row['track_id'] if row['track_id'] is not None else '—'} | {evidence} | [打开联系表]({sheet}) |")
    lines.extend(["", "A record indicates a pattern worth checking in the raw frames. It does not establish that an object was missed.", ""])
    return "\n".join(lines)


def analyze_capture(dataset_dir: str | Path, *, output_dir: str | Path | None = None,
                    min_area_margin: int = 5, large_area_px: int = 210,
                    large_area_ratio: float = 1.5, late_birth_x: float = 150.0,
                    area_jump_ratio: float = 0.5) -> dict[str, Any]:
    """Validate a complete capture and write JSON/Markdown audits and image sheets."""
    if (isinstance(min_area_margin, bool) or not isinstance(min_area_margin, int)
            or min_area_margin < 0):
        raise ValueError("min_area_margin must be a nonnegative integer")
    if (isinstance(large_area_px, bool) or not isinstance(large_area_px, int)
            or large_area_px <= 0):
        raise ValueError("large_area_px must be a positive integer")
    for name, value in (("large_area_ratio", large_area_ratio),
                        ("late_birth_x", late_birth_x),
                        ("area_jump_ratio", area_jump_ratio)):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(float(value))):
            raise ValueError(f"{name} must be a finite number")
    if large_area_ratio <= 0 or late_birth_x <= 0 or area_jump_ratio < 0:
        raise ValueError("large_area_ratio and late_birth_x must be positive; area_jump_ratio cannot be negative")
    manifest, frames = load_dataset(dataset_dir, require_complete=True)
    if not frames:
        raise ValueError("complete capture contains no frames")
    config = _config(manifest, frames)
    records, coverage, limitations = _detect_records(
        frames, config, min_area_margin=min_area_margin, large_area_px=large_area_px,
        large_area_ratio=large_area_ratio, late_birth_x=late_birth_x,
        area_jump_ratio=area_jump_ratio)
    output = Path(output_dir).resolve() if output_dir else Path(dataset_dir).resolve() / "diagnosis"
    if output.exists():
        if not output.is_dir() or any(output.iterdir()):
            raise FileExistsError(f"diagnosis output directory already contains evidence: {output}")
    else:
        output.mkdir(parents=True)
    frame_index = {frame.frame_id: frame for frame in frames}
    line_x = int(config.get("line_x", 160))
    roi_top = int(config.get("roi_top", 0))
    roi_bottom = int(config.get("roi_bottom", 240))
    by_anchor: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        by_anchor[row["frame_id"]].append(row)
    sheets = {}
    for anchor, rows in by_anchor.items():
        sheets[anchor] = _make_contact_sheet(
            frames, frame_index, anchor, rows, output,
            line_x=line_x, roi_top=roi_top, roi_bottom=roi_bottom)
    for row in records:
        row["contact_sheet"] = sheets[row["frame_id"]]
        row["window_missing_before"] = max(0, 4 - row["frame_id"])
        row["window_missing_after"] = max(0, row["frame_id"] + 4 - (len(frames) - 1))
    report = {
        "schema_version": 1, "status": "DIAGNOSTIC_ONLY",
        "ground_truth_status": "NOT_VERIFIED",
        "capture_dir": str(Path(dataset_dir).resolve()),
        "frame_count": len(frames),
        "final_total_count": frames[-1].metadata.get("total_count"),
        "source_data": "captured PL Binary8 mask and FPGA-input Gray8",
        "config": config,
        "diagnostic_field_coverage": coverage,
        "limitations": limitations,
        "records": records,
        "contact_sheets": {str(key): value for key, value in sorted(sheets.items())},
        "candidate_thresholds": {"large_area_px": large_area_px,
                                 "large_area_ratio": large_area_ratio,
                                 "late_birth_x": late_birth_x,
                                 "min_area_margin_px": min_area_margin,
                                 "area_jump_ratio": area_jump_ratio},
    }
    (output / "audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    (output / "audit.md").write_text(_markdown_report(report), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_dir", type=Path, help="complete CaptureWriter dataset")
    parser.add_argument("--output", type=Path, help="diagnosis output directory (default: CAPTURE/diagnosis)")
    parser.add_argument("--min-area-margin", type=int, default=5)
    parser.add_argument("--large-area-px", type=int, default=210)
    parser.add_argument("--large-area-ratio", type=float, default=1.5)
    parser.add_argument("--late-birth-x", type=float, default=150.0)
    parser.add_argument("--area-jump-ratio", type=float, default=0.5)
    args = parser.parse_args(argv)
    float_thresholds = (args.large_area_ratio, args.late_birth_x, args.area_jump_ratio)
    if (args.min_area_margin < 0 or args.large_area_px < 1
            or not all(math.isfinite(value) for value in float_thresholds)
            or args.large_area_ratio <= 0 or args.late_birth_x <= 0
            or args.area_jump_ratio < 0):
        parser.error("thresholds must be finite; area and position thresholds must be positive")
    try:
        report = analyze_capture(args.capture_dir, output_dir=args.output,
                                 min_area_margin=args.min_area_margin,
                                 large_area_px=args.large_area_px,
                                 large_area_ratio=args.large_area_ratio,
                                 late_birth_x=args.late_birth_x,
                                 area_jump_ratio=args.area_jump_ratio)
    except (FileExistsError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"status": report["status"], "frame_count": report["frame_count"],
                      "record_count": len(report["records"]),
                      "contact_sheet_count": len(report["contact_sheets"]),
                      "diagnostic_field_coverage": report["diagnostic_field_coverage"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
