"""Offline H3 evaluator for the isolated PC/PS reference pipeline.

Ground-truth identities are consumed only after the detector and tracker have
run, for feature/event scoring.  ``gt_geometry`` is a diagnostic upper-bound
mode and is reported separately from image-derived regions.
"""

import argparse
import csv
from dataclasses import asdict
import hashlib
import json
import math
import platform
from pathlib import Path
import random
import statistics
import sys
import time

import cv2
import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[3]
PYTHON_ROOT = ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from refmodel.ps_reference.regions import Region, extract_regions
from refmodel.ps_reference.tracking import Tracker, TrackerConfig


_MODES = ("image", "gt_geometry")
_TIMING_KEYS = {"elapsed_us", "processing_us", "wall_time_ms", "total_elapsed_ms"}


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def _int_field(value, field: str, path: Path, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{path}: {field} must be an integer >= {minimum}")
    return value


def _bbox_from_object(obj: dict) -> tuple[int, int, int, int]:
    return int(obj["x_min"]), int(obj["x_max"]), int(obj["y_min"]), int(obj["y_max"])


def _validate_object(obj: dict, frame_id: int, width: int, height: int, path: Path) -> None:
    required = ("frame_id", "object_id", "x_min", "x_max", "y_min", "y_max",
                "centroid_x", "centroid_y", "visible")
    if not isinstance(obj, dict) or any(key not in obj for key in required):
        raise ValueError(f"{path}: object record is missing required fields")
    if _int_field(obj["frame_id"], "object.frame_id", path) != frame_id:
        raise ValueError(f"{path}: object frame_id differs from containing frame")
    _int_field(obj["object_id"], "object.object_id", path)
    x_min, x_max, y_min, y_max = _bbox_from_object(obj)
    if not (0 <= x_min <= x_max < width and 0 <= y_min <= y_max < height):
        raise ValueError(f"{path}: object bbox is outside the image or inverted")
    for key in ("centroid_x", "centroid_y"):
        if isinstance(obj[key], bool) or not isinstance(obj[key], (float, int)) or not math.isfinite(obj[key]):
            raise ValueError(f"{path}: object.{key} must be finite")
    if not isinstance(obj["visible"], bool):
        raise ValueError(f"{path}: object.visible must be boolean")


def _load_sequences(data_dir: Path) -> tuple[dict, list[dict], list[Path]]:
    data_dir = data_dir.resolve()
    root_path = data_dir / "config.json"
    root_config = _read_json(root_path)
    root_conditions = root_config.get("conditions")
    if not isinstance(root_conditions, list) or not root_conditions:
        raise ValueError(f"{root_path}: conditions must be a non-empty list")

    root_by_id = {}
    for item in root_conditions:
        if not isinstance(item, dict):
            raise ValueError(f"{root_path}: each condition must be an object")
        condition_id = item.get("condition_id", item.get("id"))
        if not isinstance(condition_id, str) or not condition_id or condition_id in root_by_id:
            raise ValueError(f"{root_path}: condition IDs must be unique non-empty strings")
        root_by_id[condition_id] = item

    actual_dirs = {path.name: path for path in data_dir.iterdir() if path.is_dir()}
    if set(actual_dirs) != set(root_by_id):
        missing = sorted(set(root_by_id) - set(actual_dirs))
        extra = sorted(set(actual_dirs) - set(root_by_id))
        raise ValueError(f"dataset condition directories differ from config (missing={missing}, extra={extra})")

    sequences = []
    source_files = [root_path]
    dataset_id = root_config.get("dataset_id")
    for condition_id in sorted(root_by_id):
        condition_dir = actual_dirs[condition_id]
        config_path = condition_dir / "config.json"
        gt_path = condition_dir / "ground_truth.json"
        condition_config = _read_json(config_path)
        gt = _read_json(gt_path)
        root_item = root_by_id[condition_id]
        if condition_config.get("condition_id", condition_config.get("id")) != condition_id:
            raise ValueError(f"{config_path}: condition ID does not match directory")
        if condition_config.get("dataset_id") != dataset_id or gt.get("dataset_id") != dataset_id:
            raise ValueError(f"{condition_id}: dataset_id differs across source/config/GT")
        if gt.get("condition_id") != condition_id:
            raise ValueError(f"{gt_path}: condition_id does not match directory")

        seqs = condition_config.get("sequences")
        root_seqs = root_item.get("sequences")
        if not isinstance(seqs, list) or len(seqs) != 1 or not isinstance(root_seqs, list) or len(root_seqs) != 1:
            raise ValueError(f"{condition_id}: evaluator requires one explicit sequence per condition")
        seq = seqs[0]
        root_seq = root_seqs[0]
        if seq.get("condition_id") != condition_id or gt.get("sequence_id") != seq.get("sequence_id"):
            raise ValueError(f"{condition_id}: sequence identity differs across config and GT")
        for field in ("num_frames", "width", "height", "physical_object_count"):
            expected = seq.get(field)
            if field == "width":
                expected = gt.get("frame_width", expected)
            elif field == "height":
                expected = gt.get("frame_height", expected)
            actual = gt.get("num_frames" if field == "num_frames" else
                            "physical_object_count" if field == "physical_object_count" else
                            "frame_width" if field == "width" else "frame_height")
            if expected != actual or root_seq.get(field) != seq.get(field):
                raise ValueError(f"{condition_id}: {field} differs across root config, condition config, and GT")

        configured_drops = condition_config.get("dropped_frame_ids")
        sequence_drops = seq.get("dropped_frame_ids")
        root_drops = root_item.get("dropped_frame_ids")
        if not all(isinstance(ids, list) for ids in (configured_drops, sequence_drops, root_drops)):
            raise ValueError(f"{condition_id}: dropped_frame_ids must be lists in all configs")
        drop_sets = [set(ids) for ids in (configured_drops, sequence_drops, root_drops)]
        if drop_sets[0] != drop_sets[1] or drop_sets[0] != drop_sets[2]:
            raise ValueError(f"{condition_id}: dropped_frame_ids differ across configs")
        for ids in drop_sets:
            if any(type(frame_id) is not int or frame_id < 0 or frame_id >= seq["num_frames"] for frame_id in ids):
                raise ValueError(f"{condition_id}: dropped_frame_ids contain an invalid frame")

        frames = gt.get("frames")
        if not isinstance(frames, list) or len(frames) != seq["num_frames"]:
            raise ValueError(f"{gt_path}: GT frame count differs from config")
        visible_drops = []
        object_ids = None
        normalized_frames = []
        for expected_frame_id, frame in enumerate(frames):
            if not isinstance(frame, dict) or frame.get("frame_id") != expected_frame_id:
                raise ValueError(f"{gt_path}: frame IDs must be complete and ordered")
            objects = frame.get("objects")
            if not isinstance(objects, list) or len(objects) != seq["physical_object_count"]:
                raise ValueError(f"{gt_path}: frame {expected_frame_id} object count differs from config")
            for obj in objects:
                _validate_object(obj, expected_frame_id, seq["width"], seq["height"], gt_path)
            frame_ids = [obj["object_id"] for obj in objects]
            if len(set(frame_ids)) != len(frame_ids):
                raise ValueError(f"{gt_path}: duplicate object IDs in frame {expected_frame_id}")
            if object_ids is None:
                object_ids = set(frame_ids)
            elif object_ids != set(frame_ids):
                raise ValueError(f"{gt_path}: object identity set changes between frames")
            visibility = {obj["visible"] for obj in objects}
            if len(visibility) != 1:
                raise ValueError(f"{gt_path}: H3 visibility must describe a whole-frame observation")
            visible = visibility.pop()
            if not visible:
                visible_drops.append(expected_frame_id)
            normalized_frames.append(frame)

        if set(visible_drops) != drop_sets[0]:
            raise ValueError(f"{condition_id}: config dropped frames differ from GT visible flags")

        frames_dir = condition_dir / "frames"
        if not frames_dir.is_dir():
            raise ValueError(f"{condition_id}: missing frames directory")
        pngs = list(frames_dir.glob("*.png"))
        actual_names = {path.name for path in pngs}
        expected_names = {f"frame_{frame['frame_id']:04d}.png" for frame in normalized_frames
                          if frame["objects"][0]["visible"]}
        if actual_names != expected_names:
            missing = sorted(expected_names - actual_names)
            extra = sorted(actual_names - expected_names)
            raise ValueError(f"{condition_id}: PNG files disagree with GT/config (missing={missing}, extra={extra})")
        ordered_pngs = []
        for frame in normalized_frames:
            if not frame["objects"][0]["visible"]:
                continue
            image_path = frames_dir / f"frame_{frame['frame_id']:04d}.png"
            try:
                with Image.open(image_path) as image:
                    if image.format != "PNG" or image.mode != "L" or image.size != (seq["width"], seq["height"]):
                        raise ValueError(f"{image_path}: expected {seq['width']}x{seq['height']} Gray8 PNG")
                    image.verify()
            except (OSError, Image.DecompressionBombError) as exc:
                raise ValueError(f"{image_path}: invalid PNG: {exc}") from exc
            ordered_pngs.append(image_path)

        source_files.extend((config_path, gt_path, *ordered_pngs))
        sequences.append({
            "condition_id": condition_id,
            "sequence_id": seq["sequence_id"],
            "condition_dir": condition_dir,
            "config_path": config_path,
            "gt_path": gt_path,
            "frames": normalized_frames,
            "width": seq["width"],
            "height": seq["height"],
            "num_frames": seq["num_frames"],
            "physical_object_count": seq["physical_object_count"],
            "dropped_frame_ids": sorted(drop_sets[0]),
        })
    return root_config, sequences, source_files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _input_sha256(data_dir: Path, source_files: list[Path]) -> dict[str, str]:
    return {path.resolve().relative_to(data_dir.resolve()).as_posix(): _sha256(path)
            for path in sorted(source_files, key=lambda item: item.as_posix())}


def derive_ground_truth_events(
    frames: list[dict], line_x: float = 160.0, hysteresis: float = 2.0, direction: int = 1
) -> dict:
    """Return independent center-line and qualified events keyed by GT object ID.

    GT IDs are used only by this scoring helper.  Qualification follows the
    strict hysteresis contract: first observe the incoming side beyond ``h``,
    then observe the outgoing side beyond ``h``.  The allowed output frame is
    the first visible source frame at or after qualification.
    """

    if not math.isfinite(line_x) or not math.isfinite(hysteresis) or hysteresis <= 0:
        raise ValueError("line_x must be finite and hysteresis must be finite and positive")
    if direction not in (-1, 1):
        raise ValueError("direction must be +1 or -1")
    by_object = {}
    for frame in frames:
        frame_id = frame["frame_id"]
        for obj in frame["objects"]:
            by_object.setdefault(obj["object_id"], []).append(
                (frame_id, float(obj["centroid_x"]), bool(obj["visible"]))
            )

    center_events = {}
    qualified_events = {}
    allowed_observation_frames = {}
    pending = []
    for object_id, observations in by_object.items():
        observations.sort(key=lambda item: item[0])
        side_values = [direction * (x - line_x) for _, x, _ in observations]
        center_frame = None
        previous_side = None
        for index, side in enumerate(side_values):
            if previous_side is not None and previous_side < 0 <= side:
                center_frame = observations[index][0]
                center_events[object_id] = center_frame
                break
            previous_side = side

        armed = False
        qualified_index = None
        for index, side in enumerate(side_values):
            if side < -hysteresis:
                armed = True
            elif armed and side > hysteresis:
                qualified_index = index
                break
        if qualified_index is not None:
            qualified_frame = observations[qualified_index][0]
            qualified_events[object_id] = qualified_frame
            first_observed = next((frame_id for frame_id, _, visible in observations
                                   if frame_id >= qualified_frame and visible), None)
            allowed_observation_frames[object_id] = first_observed
        elif center_frame is not None:
            pending.append(object_id)

    return {
        "center_events": dict(sorted(center_events.items())),
        "qualified_events": dict(sorted(qualified_events.items())),
        "allowed_observation_frames": dict(sorted(allowed_observation_frames.items())),
        "pending_object_ids": sorted(pending),
    }


def regions_from_gt_objects(objects: list[dict], seed: int = 0) -> list[Region]:
    """Make geometry-only diagnostic features, stripping every identity field."""

    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    visible_objects = [obj for obj in objects if obj.get("visible", True)]
    regions = []
    for obj in visible_objects:
        x_min, x_max, row_min, row_max = _bbox_from_object(obj)
        if x_min > x_max or row_min > row_max:
            raise ValueError("GT bbox is inverted")
        regions.append(Region(
            x_min=x_min,
            x_max=x_max,
            row_min=row_min,
            row_max=row_max,
            area=(x_max - x_min + 1) * (row_max - row_min + 1),
            centroid_x=float(obj.get("centroid_x", (x_min + x_max) / 2.0)),
            centroid_y=float(obj.get("centroid_y", (row_min + row_max) / 2.0)),
            peak=0,
            flags=4,
        ))
    random.Random(seed).shuffle(regions)
    return regions


def _iou(region: Region, obj: dict) -> float:
    ax0, ax1, ay0, ay1 = region.bbox
    bx0, bx1, by0, by1 = _bbox_from_object(obj)
    width = max(0, min(ax1, bx1) - max(ax0, bx0) + 1)
    height = max(0, min(ay1, by1) - max(ay0, by0) + 1)
    intersection = width * height
    if not intersection:
        return 0.0
    area_a = (ax1 - ax0 + 1) * (ay1 - ay0 + 1)
    area_b = (bx1 - bx0 + 1) * (by1 - by0 + 1)
    return intersection / float(area_a + area_b - intersection)


def match_regions_to_ground_truth(
    regions: list[Region], objects: list[dict], iou_threshold: float = 0.5
) -> tuple[dict[int, dict], dict[str, int]]:
    """Deterministic greedy one-to-one bbox matching for offline scoring only."""

    visible = [obj for obj in objects if obj.get("visible", True)]
    visible.sort(key=lambda obj: (_bbox_from_object(obj), int(obj["object_id"])))
    candidates = []
    for region_index, region in enumerate(regions):
        for object_index, obj in enumerate(visible):
            overlap = _iou(region, obj)
            if overlap >= iou_threshold:
                candidates.append((-overlap, region_index, _bbox_from_object(obj),
                                   int(obj["object_id"]), object_index))
    matched_regions = set()
    matched_objects = set()
    matches = {}
    for negative_iou, region_index, _, _, object_index in sorted(candidates):
        if region_index in matched_regions or object_index in matched_objects:
            continue
        matched_regions.add(region_index)
        matched_objects.add(object_index)
        matches[region_index] = {
            "object_id": int(visible[object_index]["object_id"]),
            "iou": -negative_iou,
        }
    metrics = {
        "feature_tp": len(matches),
        "feature_fn": len(visible) - len(matches),
        "feature_fp": len(regions) - len(matches),
    }
    return matches, metrics


def score_count_events(truth: dict, predicted_events: list[dict]) -> dict:
    """Score one-to-one physical events; equal totals alone never imply a TP."""

    qualified = truth["qualified_events"]
    allowed = truth["allowed_observation_frames"]
    seen_objects = set()
    true_positive_objects = set()
    duplicate_fp = 0
    spurious_fp = 0
    for event in predicted_events:
        object_id = event.get("object_id")
        frame_id = event.get("frame_id")
        if object_id is None:
            spurious_fp += 1
            continue
        if object_id in seen_objects:
            duplicate_fp += 1
            continue
        seen_objects.add(object_id)
        if (object_id in qualified and allowed.get(object_id) is not None
                and frame_id == allowed[object_id]):
            true_positive_objects.add(object_id)
        else:
            spurious_fp += 1

    event_tp = len(true_positive_objects)
    event_fn = len(qualified) - event_tp
    predicted_count = len(predicted_events)
    qualified_count = len(qualified)
    precision_denominator = event_tp + duplicate_fp + spurious_fp
    recall_denominator = event_tp + event_fn
    return {
        "predicted_event_count": predicted_count,
        "center_truth_count": len(truth["center_events"]),
        "qualified_truth_count": qualified_count,
        "pending_event_count": len(truth["pending_object_ids"]),
        "raw_count_diff_center": predicted_count - len(truth["center_events"]),
        "raw_count_diff_qualified": predicted_count - qualified_count,
        "event_tp": event_tp,
        "event_fn": event_fn,
        "duplicate_fp": duplicate_fp,
        "spurious_fp": spurious_fp,
        "qualified_precision": event_tp / precision_denominator if precision_denominator else None,
        "qualified_recall": event_tp / recall_denominator if recall_denominator else None,
    }


def canonical_trace_sha256(trace: dict) -> str:
    """Hash a trace after dropping nondeterministic timing fields recursively."""

    def strip_timing(value):
        if isinstance(value, dict):
            return {
                key: strip_timing(item)
                for key, item in value.items()
                if key not in _TIMING_KEYS and not key.startswith("pc_elapsed_")
            }
        if isinstance(value, list):
            return [strip_timing(item) for item in value]
        return value

    canonical = json.dumps(strip_timing(trace), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _region_record(region: Region) -> dict:
    result = asdict(region)
    result["bbox"] = list(region.bbox)
    return result


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=np.float64), percentile))


def _evaluate_sequence(
    sequence: dict,
    mode: str,
    tracker_config: TrackerConfig,
    threshold: int,
    min_area: int,
    morph_mode: int,
    max_width: int,
    geometry_seed: int,
) -> tuple[dict, dict]:
    gt = _read_json(sequence["gt_path"])
    truth = derive_ground_truth_events(
        sequence["frames"], tracker_config.line_x, tracker_config.hysteresis, tracker_config.direction
    )
    tracker = Tracker(tracker_config)
    trace_frames = []
    predicted_events = []
    id_switches = 0
    last_track_for_object = {}
    feature_totals = {"feature_tp": 0, "feature_fn": 0, "feature_fp": 0}
    elapsed_us = []
    frames_by_id = {frame["frame_id"]: frame for frame in sequence["frames"]}
    for frame_id in range(sequence["num_frames"]):
        frame = frames_by_id[frame_id]
        objects = frame["objects"]
        visible = bool(objects[0]["visible"]) if objects else True
        image_path = sequence["condition_dir"] / "frames" / f"frame_{frame_id:04d}.png"
        start_ns = time.perf_counter_ns()
        if not visible:
            detections = []
        elif mode == "image":
            try:
                with Image.open(image_path) as image:
                    gray = np.array(image, dtype=np.uint8, copy=True)
            except OSError as exc:
                raise ValueError(f"cannot read {image_path}: {exc}") from exc
            detections = extract_regions(gray, threshold, min_area, morph_mode, max_width)
        else:
            detections = regions_from_gt_objects(objects, seed=geometry_seed + frame_id)

        # Finish inference before any truth identity matching or scoring.
        events = tracker.update(frame_id, detections)
        gt_matches, feature_metrics = match_regions_to_ground_truth(detections, objects)
        for key in feature_totals:
            feature_totals[key] += feature_metrics[key]
        assignments = list(tracker.last_assignments)
        assignment_by_track = {track_id: feature_index for track_id, feature_index in assignments}
        assignment_records = []
        for track_id, feature_index in assignments:
            match = gt_matches.get(feature_index)
            object_id = match["object_id"] if match else None
            switched = False
            if object_id is not None:
                previous_track = last_track_for_object.get(object_id)
                if previous_track is not None and previous_track != track_id:
                    id_switches += 1
                    switched = True
                last_track_for_object[object_id] = track_id
            assignment_records.append({
                "track_id": track_id,
                "feature_index": feature_index,
                "ground_truth_object_id": object_id,
                "bbox_iou": match["iou"] if match else None,
                "id_switch": switched,
            })

        event_records = []
        for event in events:
            feature_index = assignment_by_track.get(event.track_id)
            match = gt_matches.get(feature_index) if feature_index is not None else None
            object_id = match["object_id"] if match else None
            record = {
                "track_id": event.track_id,
                "frame_id": event.frame_id,
                "centroid_x": event.centroid_x,
                "centroid_y": event.centroid_y,
                "feature_index": feature_index,
                "ground_truth_object_id": object_id,
            }
            event_records.append(record)
            predicted_events.append({"object_id": object_id, "frame_id": event.frame_id})

        frame_elapsed = (time.perf_counter_ns() - start_ns) / 1000.0
        elapsed_us.append(frame_elapsed)
        trace_frames.append({
            "frame_id": frame_id,
            "observation_present": visible,
            "regions": [_region_record(region) for region in detections],
            "feature_metrics": feature_metrics,
            "assignments": assignment_records,
            "events": event_records,
            "tracks": [
                {
                    "track_id": track.track_id,
                    "state": track.state,
                    "hits": track.hits,
                    "confirmed": track.confirmed,
                    "counted": track.counted,
                    "born_counted": track.born_counted,
                    "last_frame": track.last_frame,
                }
                for track in sorted(tracker.tracks.values(), key=lambda item: item.track_id)
            ],
            "elapsed_us": frame_elapsed,
        })

    event_metrics = score_count_events(truth, predicted_events)
    missing_frames = len(sequence["dropped_frame_ids"])
    all_metrics = {
        **feature_totals,
        **event_metrics,
        "id_switches": id_switches,
        "frame_count": sequence["num_frames"],
        "png_frames": sequence["num_frames"] - missing_frames,
        "missing_frames": missing_frames,
        "tracker_internal_count": tracker.count,
        "duplicate_detections": tracker.duplicate_detections,
        "merge_observations": tracker.merge_observations,
        "pc_elapsed_total_ms": sum(elapsed_us) / 1000.0,
        "pc_elapsed_mean_us": statistics.fmean(elapsed_us) if elapsed_us else None,
        "pc_elapsed_p50_us": _percentile(elapsed_us, 50),
        "pc_elapsed_p95_us": _percentile(elapsed_us, 95),
    }
    trace = {
        "dataset_id": gt["dataset_id"],
        "condition_id": sequence["condition_id"],
        "sequence_id": sequence["sequence_id"],
        "mode": mode,
        "input_mode_note": "diagnostic GT geometry; identities are stripped before tracker input"
        if mode == "gt_geometry" else "Gray8 PNG image-derived features",
        "parameters": {
            "threshold": threshold,
            "min_area": min_area,
            "morph_mode": morph_mode,
            "connectivity": 8,
            "max_width": max_width,
            "tracker": asdict(tracker_config),
        },
        "frames": trace_frames,
        "summary": all_metrics,
    }
    return trace, all_metrics


def _aggregate(rows: list[dict]) -> dict:
    summed_fields = (
        "feature_tp", "feature_fn", "feature_fp", "center_truth_count", "qualified_truth_count",
        "pending_event_count", "predicted_event_count", "event_tp", "event_fn", "duplicate_fp",
        "spurious_fp", "raw_count_diff_center", "raw_count_diff_qualified", "id_switches",
        "frame_count", "png_frames", "missing_frames", "tracker_internal_count",
        "duplicate_detections", "merge_observations",
    )
    result = {field: sum(row[field] for row in rows) for field in summed_fields}
    precision_den = result["event_tp"] + result["duplicate_fp"] + result["spurious_fp"]
    recall_den = result["event_tp"] + result["event_fn"]
    result["qualified_precision"] = result["event_tp"] / precision_den if precision_den else None
    result["qualified_recall"] = result["event_tp"] / recall_den if recall_den else None
    result["pc_elapsed_total_ms"] = sum(row["pc_elapsed_total_ms"] for row in rows)
    result["pc_elapsed_mean_us"] = (
        sum(row["pc_elapsed_mean_us"] * row["frame_count"] for row in rows) / result["frame_count"]
        if result["frame_count"] else None
    )
    return result


def evaluate_dataset(
    data: Path,
    out: Path,
    *,
    line_x: float = 160.0,
    hysteresis: float = 2.0,
    threshold: int = 128,
    min_area: int = 16,
    morph_mode: int = 0,
    max_width: int = 24,
    confirm_hits: int = 2,
    max_missing: int = 2,
    max_step: float = 6.0,
    max_y: float = 8.0,
    direction: int = 1,
    geometry_seed: int = 20261013,
) -> dict:
    """Evaluate all declared sequences in image and geometry diagnostic modes.

    ``out`` must be a new directory. The source dataset is validated before
    results are created and hashed again after evaluation to confirm it did
    not change during the run.
    """

    data_dir = Path(data).resolve()
    out_dir = Path(out).resolve()
    if not data_dir.is_dir():
        raise ValueError(f"data directory does not exist: {data_dir}")
    if out_dir == data_dir or data_dir in out_dir.parents:
        raise ValueError("output directory must be outside the input dataset")
    if out_dir.exists():
        raise ValueError(f"output directory must be new: {out_dir}")
    if type(geometry_seed) is not int:
        raise ValueError("geometry_seed must be an integer")
    tracker_config = TrackerConfig(
        line_x=line_x,
        hysteresis=hysteresis,
        direction=direction,
        confirm_hits=confirm_hits,
        max_missing=max_missing,
        max_step=max_step,
        max_y=max_y,
    )
    if type(threshold) is not int or not 0 <= threshold <= 255:
        raise ValueError("threshold must be an integer in 0..255")
    if type(min_area) is not int or min_area < 0:
        raise ValueError("min_area must be a non-negative integer")
    if morph_mode not in (0, 1, 2):
        raise ValueError("morph_mode must be 0, 1, or 2")
    if type(max_width) is not int or max_width < 0:
        raise ValueError("max_width must be a non-negative integer")

    root_config, sequences, source_files = _load_sequences(data_dir)
    input_sha256 = _input_sha256(data_dir, source_files)
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / "trace" / "image").mkdir(parents=True)
    (out_dir / "trace" / "gt_geometry").mkdir(parents=True)

    condition_summaries = {mode: {} for mode in _MODES}
    trace_digests = {mode: {} for mode in _MODES}
    all_rows = {mode: [] for mode in _MODES}
    for sequence in sequences:
        for mode in _MODES:
            trace, metrics = _evaluate_sequence(
                sequence, mode, tracker_config, threshold, min_area, morph_mode,
                max_width, geometry_seed,
            )
            trace_path = out_dir / "trace" / mode / f"{sequence['condition_id']}.json"
            trace_path.write_text(json.dumps(trace, indent=2, sort_keys=True, allow_nan=False) + "\n",
                                  encoding="utf-8")
            trace_digests[mode][sequence["condition_id"]] = canonical_trace_sha256(trace)
            condition_summaries[mode][sequence["condition_id"]] = metrics
            all_rows[mode].append({"condition_id": sequence["condition_id"], **metrics})

    after_sha256 = _input_sha256(data_dir, source_files)
    if input_sha256 != after_sha256:
        raise RuntimeError("input config/GT/PNG changed during evaluation")

    aggregate = {mode: _aggregate(all_rows[mode]) for mode in _MODES}
    truth_totals = {
        "center_truth_count": sum(row["center_truth_count"] for row in all_rows["image"]),
        "qualified_truth_count": sum(row["qualified_truth_count"] for row in all_rows["image"]),
        "pending_event_count": sum(row["pending_event_count"] for row in all_rows["image"]),
    }
    csv_path = out_dir / "conditions.csv"
    fieldnames = ["condition_id", "mode"] + sorted({key for mode in _MODES for row in all_rows[mode]
                                                    for key in row if key != "condition_id"})
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for mode in _MODES:
            for row in all_rows[mode]:
                writer.writerow({"mode": mode, **row})

    combined_trace_sha = hashlib.sha256(json.dumps(
        trace_digests, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    summary = {
        "dataset_id": root_config["dataset_id"],
        "sequence_count": len(sequences),
        "modes": list(_MODES),
        "parameters": {
            "threshold": threshold,
            "foreground_rule": "Gray >= threshold -> 0xFF; else 0x00",
            "min_area": min_area,
            "connectivity": 8,
            "morph_mode": morph_mode,
            "max_width": max_width,
            "tracker": asdict(tracker_config),
            "geometry_seed": geometry_seed,
        },
        "ground_truth_totals": truth_totals,
        "condition_summaries": condition_summaries,
        "aggregate": aggregate,
        "trace_sha256": combined_trace_sha,
        "trace_sha256_by_mode": trace_digests,
        "input_sha256": input_sha256,
        "input_unchanged_after_run": True,
        "runtime_classification": "PC Python wall-clock only; not PS/PYNQ real-time evidence",
        "runtime_environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "opencv": cv2.__version__,
        },
    }
    (out_dir / "input_sha256.json").write_text(
        json.dumps(input_sha256, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path, help="H3 dataset directory")
    parser.add_argument("--out", required=True, type=Path, help="new result directory")
    parser.add_argument("--line-x", type=float, default=160.0)
    parser.add_argument("--hysteresis", type=float, default=2.0)
    parser.add_argument("--threshold", type=int, default=128)
    parser.add_argument("--min-area", type=int, default=16)
    parser.add_argument("--morph-mode", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument("--max-width", type=int, default=24)
    parser.add_argument("--confirm-hits", type=int, default=2)
    parser.add_argument("--max-missing", type=int, default=2)
    parser.add_argument("--max-step", type=float, default=6.0)
    parser.add_argument("--max-y", type=float, default=8.0)
    parser.add_argument("--direction", type=int, choices=(-1, 1), default=1)
    parser.add_argument("--geometry-seed", type=int, default=20261013)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = evaluate_dataset(
            args.data,
            args.out,
            line_x=args.line_x,
            hysteresis=args.hysteresis,
            threshold=args.threshold,
            min_area=args.min_area,
            morph_mode=args.morph_mode,
            max_width=args.max_width,
            confirm_hits=args.confirm_hits,
            max_missing=args.max_missing,
            max_step=args.max_step,
            max_y=args.max_y,
            direction=args.direction,
            geometry_seed=args.geometry_seed,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "sequence_count": result["sequence_count"],
        "ground_truth_totals": result["ground_truth_totals"],
        "aggregate": result["aggregate"],
        "trace_sha256": result["trace_sha256"],
        "input_unchanged_after_run": result["input_unchanged_after_run"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
