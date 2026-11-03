"""Full-data offline baseline/candidate split ablation for WO-0016.

This runner reads the explicitly listed camera/PL recordings and calibration
fixtures. It never accesses the board and never writes into the repository's
data tree. Candidate logic is loaded only from an out-of-tree Python module.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Iterator, Mapping

import cv2
import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import stream_counter as stream_counter_module  # noqa: E402
from live_calibration import MonochromePreprocessor, frame_geometry  # noqa: E402
from offline_threshold import software_binary8  # noqa: E402
from split_touching import is_single_connected_component  # noqa: E402
from stream_counter import Detection, MaskCounter  # noqa: E402


CURRENT_DOMAIN_BOUNDARY = {"max_area": 540, "max_bbox_width": 85, "max_bbox_height": 85}
CURRENT_PROFILE_SOURCE = (
    "data/results/lighting-calibration-20261025/board-approval/b2-attempt-03/"
    "evidence/b2-capture-b2-m6touch-220-20261025-03/frame-records.jsonl"
)
HISTORICAL_WINDOW_SOURCE = "data/results/wo0017-touching-diagnosis-20261024/README.md"


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _read_frame_records(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
    return records


def _current_profile_config(b2_record: Mapping[str, Any]) -> dict[str, Any]:
    """Use the accepted 10/25 profile, pinned to B2-03's recorded config."""
    config = dict(b2_record["metadata"]["algorithm_config"])
    config["min_split_child_area"] = 48
    config.setdefault("split_recursive_item_area", 80)
    expected = {
        "roi_top": 30,
        "roi_bottom": 210,
        "roi_mode": "full",
        "min_area": 15,
        "single_intact_area": 64,
        "max_area": 540,
        "max_split_area": 540,
        "max_bbox_width": 85,
        "max_bbox_height": 85,
        "component_cluster_gap": 0.0,
    }
    mismatches = {
        name: {"recorded": config.get(name), "expected": expected_value}
        for name, expected_value in expected.items()
        if config.get(name) != expected_value
    }
    if mismatches:
        raise ValueError(f"B2-03 no longer matches the pinned 10/25 profile: {mismatches}")
    return config


def _historical_profile_config() -> dict[str, Any]:
    """10/24 full-width-mixed profile, kept separate from the 10/25 scale."""
    return {
        "count_axis": "x",
        "line_x": 160,
        "line_y": 120,
        "direction": 1,
        "min_area": 12,
        "max_area": 900,
        "max_bbox_width": 85,
        "max_bbox_height": 85,
        "max_step": 48.0,
        "max_lateral_step": 10.0,
        "hysteresis": 2.0,
        "confirm_hits": 2,
        "component_cluster_dx": 0.0,
        "component_cluster_dy": 12.0,
        "component_cluster_gap": 14.0,
        "cooldown_frames": 0,
        "cooldown_lateral": 15.0,
        "edge_max_missing": 0,
        "prediction_max_missing": 3,
        "roi_mode": "full",
        "guarded_merge": True,
        "single_intact_area": 95,
        "max_merge_area": 260,
        "max_split_area": 900,
        "max_merged_span": 24,
        "strict_lateral_gate": True,
        "max_missing": 3,
        "backward_tolerance": 4.0,
    }


def _effective_counter_configuration(config: Mapping[str, Any]) -> dict[str, Any]:
    """Record the complete live counter values, including constructor defaults."""
    counter = MaskCounter(**dict(config))
    names = (
        "width", "height", "line_y", "roi_half_height", "roi_mode", "roi_top", "roi_bottom",
        "min_area", "max_area", "max_bbox_width", "max_bbox_height", "count_axis", "line_x",
        "direction", "hysteresis", "confirm_hits", "max_missing", "max_step", "max_x_step",
        "max_lateral_step", "backward_tolerance", "component_cluster_dx", "component_cluster_dy",
        "component_cluster_gap", "strict_lateral_gate", "guarded_merge", "single_intact_area",
        "max_merge_area", "max_split_area", "max_merged_span", "min_split_child_area",
        "split_min_hole_area", "split_min_hole_distance", "split_hole_area_cap",
        "split_recursive_item_area", "cooldown_frames", "cooldown_lateral", "edge_max_missing",
        "prediction_max_missing", "discard_finished_tracks", "motion_timebase_s",
    )
    return {name: getattr(counter, name) for name in names}


def _historical_activity(dataset_id: str) -> dict[str, Any]:
    ranges = {
        "wo_touching_pair_motion_20261024_03": {
            "start_inclusive": 0,
            "end_exclusive": 901,
            "description": "README target replay frames 0-900; later near-camera occluder excluded",
            "readme_line": 31,
        },
        "wo_touching_three_motion_20261024_02": {
            "start_inclusive": 0,
            "end_exclusive": 100,
            "description": "README target replay frames 0-99",
            "readme_line": 39,
        },
        "wo_touching_three_motion_20261024_04": {
            "start_inclusive": 0,
            "end_exclusive": 80,
            "description": "README target replay frames 0-79",
            "readme_line": 49,
        },
    }
    result = dict(ranges[dataset_id])
    result["source"] = HISTORICAL_WINDOW_SOURCE
    result["accuracy_scope"] = "window counts are diagnostic; no independent per-frame entity labels"
    return result


def _counter_roi(config: Mapping[str, Any], height: int) -> tuple[int, int]:
    top = int(config.get("roi_top", 0))
    bottom = int(config.get("roi_bottom", height))
    if config.get("roi_mode", "lane") == "lane":
        line = int(config.get("line_y", height // 2))
        half = int(config.get("roi_half_height", 24))
        top = max(top, line - half)
        bottom = min(bottom, line + half + 1)
    if not 0 <= top < bottom <= height:
        raise ValueError(f"invalid counter ROI [{top},{bottom}) for height {height}")
    return top, bottom


def _raw_ccl_report(mask: np.ndarray, config: Mapping[str, Any], *, current_domain: bool) -> dict[str, Any]:
    image = np.asarray(mask)
    top, bottom = _counter_roi(config, image.shape[0])
    roi = np.ascontiguousarray(image[top:bottom, :])
    fast_ccl = getattr(cv2, "connectedComponentsWithStatsWithAlgorithm", None)
    fast_algorithm = getattr(cv2, "CCL_GRANA", None)
    if callable(fast_ccl) and fast_algorithm is not None:
        count, _labels, stats, centers = fast_ccl(roi, 8, cv2.CV_16U, fast_algorithm)
    else:
        count, _labels, stats, centers = cv2.connectedComponentsWithStats(
            roi, connectivity=8, ltype=cv2.CV_16U
        )
    min_area = int(config.get("min_area", 12))
    components: list[dict[str, Any]] = []
    boundary_components: list[dict[str, Any]] = []
    max_raw_area = 0
    max_raw_width = 0
    max_raw_height = 0
    boundary = CURRENT_DOMAIN_BOUNDARY if current_domain else None
    for label in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[label])
        max_raw_area = max(max_raw_area, area)
        max_raw_width = max(max_raw_width, width)
        max_raw_height = max(max_raw_height, height)
        component = {
            "label": label,
            "x_min": x,
            "x_max": x + width - 1,
            "y_min": top + y,
            "y_max": top + y + height - 1,
            "bbox_width": width,
            "bbox_height": height,
            "area": area,
            "centroid_x": round(float(centers[label, 0]), 3),
            "centroid_y": round(float(centers[label, 1] + top), 3),
            "passes_min_area": area >= min_area,
        }
        if area >= min_area:
            components.append(component)
        if boundary is not None:
            exceeded = {
                "area_gt_540": area > boundary["max_area"],
                "bbox_width_gt_85": width > boundary["max_bbox_width"],
                "bbox_height_gt_85": height > boundary["max_bbox_height"],
            }
            if any(exceeded.values()):
                boundary_components.append({**component, "exceeded": exceeded})
    report = {
        "roi_bounds_y": [top, bottom],
        "raw_component_count_before_min_area": int(count - 1),
        "min_area_threshold": min_area,
        "min_area_qualified_count": len(components),
        "below_min_area_count": int(count - 1 - len(components)),
        "max_raw_area": max_raw_area,
        "max_raw_bbox_width": max_raw_width,
        "max_raw_bbox_height": max_raw_height,
        "components_min_area_qualified": components,
    }
    if boundary is not None:
        report["raw_boundary_scope"] = {
            "domain": "10/25 lighting-calibrated output ROI only",
            "exceedance_is_strictly_greater_than": boundary,
            "exceedance_component_count": len(boundary_components),
            "area_gt_540_count": sum(item["exceeded"]["area_gt_540"] for item in boundary_components),
            "bbox_width_gt_85_count": sum(item["exceeded"]["bbox_width_gt_85"] for item in boundary_components),
            "bbox_height_gt_85_count": sum(item["exceeded"]["bbox_height_gt_85"] for item in boundary_components),
            "components": boundary_components,
        }
    else:
        report["raw_boundary_scope"] = None
    return report


def _detection_json(detection: Detection) -> dict[str, Any]:
    return {
        "x_min": detection.x_min,
        "x_max": detection.x_max,
        "y_min": detection.y_min,
        "y_max": detection.y_max,
        "bbox_width": detection.x_max - detection.x_min + 1,
        "bbox_height": detection.y_max - detection.y_min + 1,
        "area": detection.area,
        "centroid_x": round(float(detection.centroid_x), 3),
        "centroid_y": round(float(detection.centroid_y), 3),
    }


def _event_json(event: Any) -> dict[str, Any]:
    return {
        "track_id": int(event.track_id),
        "frame_id": int(event.frame_id),
        "centroid_x": round(float(event.centroid_x), 3),
        "centroid_y": round(float(event.centroid_y), 3),
        "inferred_gap": bool(event.inferred_gap),
    }


def _detection_signature(detection: Detection) -> tuple[Any, ...]:
    return (
        int(detection.x_min), int(detection.x_max), int(detection.y_min), int(detection.y_max),
        int(detection.area), round(float(detection.centroid_x), 3), round(float(detection.centroid_y), 3),
    )


def _event_signature(event: Any) -> tuple[Any, ...]:
    return (
        int(event.frame_id), round(float(event.centroid_x), 3),
        round(float(event.centroid_y), 3), bool(event.inferred_gap),
    )


def _strict_result_state(result: Any, counter: MaskCounter) -> dict[str, Any]:
    """Compare every FrameResult semantic field and the caller-visible associations."""
    state = asdict(result)
    state.pop("roi_subtimings_ms", None)
    state["last_associations"] = deepcopy(counter.last_associations)
    return state


def _strict_state_difference(baseline_result: Any, baseline_counter: MaskCounter,
                             comparison_result: Any, comparison_counter: MaskCounter) -> dict[str, Any]:
    baseline_state = _strict_result_state(baseline_result, baseline_counter)
    comparison_state = _strict_result_state(comparison_result, comparison_counter)
    fields = sorted(set(baseline_state) | set(comparison_state))
    changed = [
        name for name in fields
        if json.dumps(baseline_state.get(name), sort_keys=True, default=_json_default)
        != json.dumps(comparison_state.get(name), sort_keys=True, default=_json_default)
    ]
    return {
        "changed_fields": changed,
        "field_differences": {
            name: {"baseline": baseline_state.get(name), "comparison": comparison_state.get(name)}
            for name in changed
        },
    }


def _multiset_delta(before: Iterable[tuple[Any, ...]], after: Iterable[tuple[Any, ...]]) -> dict[str, Any]:
    before_counts = Counter(before)
    after_counts = Counter(after)
    removed = list((before_counts - after_counts).elements())
    added = list((after_counts - before_counts).elements())
    return {"removed_from_baseline": removed, "added_by_candidate": added}


def _call_with_supported_kwargs(function: Any, component: np.ndarray,
                                kwargs: Mapping[str, Any]) -> Any:
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return function(component)
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    selected = dict(kwargs) if accepts_kwargs else {
        name: value for name, value in kwargs.items() if name in signature.parameters
    }
    return function(component, **selected)


class CandidateAdapter:
    def __init__(self, module_path: Path, options: Mapping[str, Any]):
        self.path = module_path.resolve()
        source = self.path.read_bytes()
        self.source_sha256 = hashlib.sha256(source).hexdigest()
        spec = importlib.util.spec_from_file_location("external_split_candidate", self.path)
        if spec is None or spec.loader is None:
            raise ValueError(f"cannot import candidate module: {self.path}")
        module = importlib.util.module_from_spec(spec)
        # Execute the exact bytes hashed above. Reading the path again through the
        # loader could make the recorded SHA drift if the external module changes
        # while a long full-data run is in progress.
        exec(compile(source, str(self.path), "exec"), module.__dict__)
        self.module = module
        self.options = dict(options)
        self.min_child_area_override = self.options.pop("_min_child_area_override", None)
        self.fallback_after_baseline = getattr(module, "fallback_after_baseline", None)
        self.candidate_split = getattr(module, "candidate_split", None)
        self.try_split = getattr(module, "try_foreground_distance_peak_split", None)
        if not callable(self.fallback_after_baseline) and not callable(self.candidate_split) and not callable(self.try_split):
            raise ValueError(
                "candidate module must export fallback_after_baseline, candidate_split, "
                "or try_foreground_distance_peak_split"
            )

    def apply(self, component: np.ndarray, baseline_pieces: Iterable[np.ndarray],
              options: Mapping[str, Any] | None = None) -> tuple[np.ndarray, ...]:
        selected_options = dict(self.options if options is None else options)
        if callable(self.fallback_after_baseline):
            result = self.fallback_after_baseline(component, baseline_pieces, **selected_options)
        else:
            function = self.candidate_split or self.try_split
            result = _call_with_supported_kwargs(function, component, selected_options)
            if result is None:
                result = baseline_pieces
        return tuple(np.asarray(piece) != 0 for piece in result)


def _valid_candidate_partition(parent: np.ndarray, pieces: Iterable[np.ndarray]) -> bool:
    pieces = tuple(np.asarray(piece) != 0 for piece in pieces)
    if not 2 <= len(pieces) <= 4 or any(piece.shape != parent.shape for piece in pieces):
        return False
    if not all(is_single_connected_component(piece) for piece in pieces):
        return False
    coverage = np.sum(np.stack(pieces, axis=0), axis=0)
    return np.array_equal(coverage > 0, parent) and not np.any(coverage > 1)


def _candidate_update(counter: MaskCounter, frame_id: int, mask: np.ndarray,
                      adapter: CandidateAdapter, config: Mapping[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    baseline_splitter = stream_counter_module.split_touching_component
    trace: list[dict[str, Any]] = []

    def split_with_fallback(component: np.ndarray, **kwargs: Any) -> tuple[np.ndarray, ...]:
        baseline_pieces = tuple(baseline_splitter(component, **kwargs))
        pixels = np.asarray(component) != 0
        intact_single = (
            len(baseline_pieces) == 1
            and np.asarray(baseline_pieces[0]).shape == pixels.shape
            and np.array_equal(np.asarray(baseline_pieces[0]) != 0, pixels)
        )
        split_child_floor = (
            int(adapter.min_child_area_override)
            if adapter.min_child_area_override is not None
            else int(kwargs.get("min_child_area", config.get("min_split_child_area", config.get("min_area", 12))))
        )
        candidate_options = dict(adapter.options)
        candidate_options["min_child_area"] = split_child_floor
        candidate_options["single_intact_area"] = int(config.get("single_intact_area", 64))
        min_candidate_parent = max(candidate_options["single_intact_area"], 2 * split_child_floor)
        if not intact_single or int(np.count_nonzero(pixels)) < min_candidate_parent:
            return baseline_pieces
        candidate_pieces = adapter.apply(pixels, baseline_pieces, candidate_options)
        valid = _valid_candidate_partition(pixels, candidate_pieces)
        accepted = valid and len(candidate_pieces) > 1
        trace.append({
            "parent_area": int(np.count_nonzero(pixels)),
            "baseline_piece_count": len(baseline_pieces),
            "candidate_called_after_intact_baseline": True,
            "candidate_split_accepted": bool(accepted),
            "candidate_child_areas": [int(np.count_nonzero(piece)) for piece in candidate_pieces]
            if accepted else [],
        })
        return tuple(candidate_pieces) if accepted else baseline_pieces

    stream_counter_module.split_touching_component = split_with_fallback
    try:
        result = counter.update(frame_id, mask)
    finally:
        stream_counter_module.split_touching_component = baseline_splitter
    return result, trace


def _count_raw_boundary(report: Mapping[str, Any]) -> dict[str, int]:
    boundary = report.get("raw_boundary_scope")
    if not boundary:
        return {"area_gt_540": 0, "bbox_width_gt_85": 0, "bbox_height_gt_85": 0}
    return {
        "area_gt_540": int(boundary["area_gt_540_count"]),
        "bbox_width_gt_85": int(boundary["bbox_width_gt_85_count"]),
        "bbox_height_gt_85": int(boundary["bbox_height_gt_85_count"]),
    }


def _frame_counter_record(counter: MaskCounter, result: Any) -> dict[str, Any]:
    return {
        "raw_detections_after_split_before_cluster": [_detection_json(item) for item in result.raw_detections],
        "clustered_detections": [_detection_json(item) for item in result.detections],
        "events": [_event_json(item) for item in result.events],
        "cumulative_total_count": int(result.total_count),
        "rejected_before_split": [_detection_json(item) for item in counter.rejected_detections],
        "rejected_before_split_count": len(counter.rejected_detections),
        "rejected_before_split_area_px": int(sum(item.area for item in counter.rejected_detections)),
    }


def _make_frame_row(dataset_id: str, frame_id: int, raw_report: dict[str, Any],
                    baseline_counter: MaskCounter, baseline_result: Any,
                    candidate_counter: MaskCounter | None, candidate_result: Any,
                    candidate_trace: list[dict[str, Any]],
                    recorded_counter: MaskCounter | None, recorded_result: Any,
                    activity: Mapping[str, Any] | None) -> dict[str, Any]:
    base = _frame_counter_record(baseline_counter, baseline_result)
    row: dict[str, Any] = {
        "dataset_id": dataset_id,
        "frame_id": int(frame_id),
        "activity_window_included": None,
        "raw_ccl_before_split": raw_report,
        "baseline_current_profile": base,
    }
    if activity is not None:
        row["activity_window_included"] = (
            int(activity["start_inclusive"]) <= frame_id < int(activity["end_exclusive"])
        )
    if candidate_counter is not None and candidate_result is not None:
        candidate = _frame_counter_record(candidate_counter, candidate_result)
        baseline_split = tuple(_detection_signature(item) for item in baseline_result.raw_detections)
        candidate_split = tuple(_detection_signature(item) for item in candidate_result.raw_detections)
        baseline_final = tuple(_detection_signature(item) for item in baseline_result.detections)
        candidate_final = tuple(_detection_signature(item) for item in candidate_result.detections)
        baseline_events = tuple(_event_signature(item) for item in baseline_result.events)
        candidate_events = tuple(_event_signature(item) for item in candidate_result.events)
        strict_difference = _strict_state_difference(
            baseline_result, baseline_counter, candidate_result, candidate_counter
        )
        row["candidate"] = candidate
        row["candidate_fallback_checks"] = candidate_trace
        row["semantic_difference"] = {
            "component_segmentation_changed": baseline_split != candidate_split,
            "component_count_delta_after_split": len(candidate_split) - len(baseline_split),
            "clustered_detection_changed": baseline_final != candidate_final,
            "clustered_detection_count_delta": len(candidate_final) - len(baseline_final),
            "event_count_delta_this_frame": len(candidate_events) - len(baseline_events),
            "events": _multiset_delta(baseline_events, candidate_events),
            "cumulative_count_delta": int(candidate_result.total_count - baseline_result.total_count),
            "strict_counter_state_changed": bool(strict_difference["changed_fields"]),
            "strict_counter_state_changed_fields": strict_difference["changed_fields"],
            "strict_counter_state_field_differences": strict_difference["field_differences"],
        }
    if recorded_counter is not None and recorded_result is not None:
        row["recorded_capture_config_diagnostic"] = _frame_counter_record(
            recorded_counter, recorded_result
        )
    return row


def _new_run_counters(config: Mapping[str, Any], candidate_present: bool,
                      recorded_config: Mapping[str, Any] | None,
                      framewise: bool) -> tuple[MaskCounter, MaskCounter | None, MaskCounter | None]:
    baseline = MaskCounter(**dict(config))
    candidate = MaskCounter(**dict(config)) if candidate_present else None
    recorded = None
    if recorded_config is not None and dict(recorded_config) != dict(config):
        recorded = MaskCounter(**dict(recorded_config))
    return baseline, candidate, recorded


def _sequence_summary(dataset_id: str, frames_path: Path, source_paths: list[Path],
                      status: str, config: Mapping[str, Any], frames: Iterable[tuple[int, np.ndarray, dict[str, Any] | None]],
                      *, current_domain: bool, candidate_adapter: CandidateAdapter | None,
                      activity: Mapping[str, Any] | None = None,
                      recorded_config: Mapping[str, Any] | None = None,
                      framewise: bool = False,
                      cpu_threshold_compare: bool = False,
                      preprocessing_metadata: Mapping[str, Any] | None = None,
                      ground_truth_note: str | None = None,
                      static_reference: Mapping[str, Any] | None = None) -> dict[str, Any]:
    effective_config = _effective_counter_configuration(config)
    baseline_counter, candidate_counter, recorded_counter = _new_run_counters(
        config, candidate_adapter is not None, recorded_config, framewise
    )
    cpu_counter = MaskCounter(**dict(config)) if cpu_threshold_compare else None
    baseline_events = candidate_events = recorded_events = 0
    baseline_events_window = candidate_events_window = 0
    baseline_events_post_window = candidate_events_post_window = 0
    count_at_window_end_baseline = count_at_window_end_candidate = None
    frame_count = 0
    raw_components_total = min_area_components_total = 0
    raw_boundaries_total = Counter()
    rejected_baseline_count = rejected_candidate_count = 0
    rejected_baseline_area = rejected_candidate_area = 0
    fallback_checks = fallback_splits = 0
    semantic_frames = detection_frames = event_frames = 0
    strict_state_frames = strict_state_frames_window = strict_state_frames_post_window = 0
    semantic_frames_window = detection_frames_window = event_frames_window = 0
    semantic_frames_post_window = detection_frames_post_window = event_frames_post_window = 0
    fallback_checks_window = fallback_splits_window = 0
    fallback_checks_post_window = fallback_splits_post_window = 0
    max_raw_area = max_raw_width = max_raw_height = 0
    total_input_foreground_px = 0
    baseline_split_components_total = baseline_clustered_detections_total = 0
    candidate_split_components_total = candidate_clustered_detections_total = 0
    cpu_pixel_mismatch_total = cpu_false_positive_total = cpu_false_negative_total = 0
    cpu_frames_exact_mask = cpu_semantic_frames = cpu_detection_frames = cpu_event_frames = 0
    cpu_strict_state_frames = 0
    cpu_foreground_total = pl_foreground_total = 0
    cpu_events = 0
    with frames_path.open("w", encoding="utf-8", newline="\n") as output:
        for source_frame_id, mask, metadata in frames:
            frame_id = 0 if framewise else int(source_frame_id)
            if framewise:
                baseline_counter, candidate_counter, recorded_counter = _new_run_counters(
                    config, candidate_adapter is not None, recorded_config, framewise
                )
            raw_report = _raw_ccl_report(mask, config, current_domain=current_domain)
            raw_components_total += int(raw_report["raw_component_count_before_min_area"])
            min_area_components_total += int(raw_report["min_area_qualified_count"])
            total_input_foreground_px += int(np.count_nonzero(mask))
            max_raw_area = max(max_raw_area, int(raw_report["max_raw_area"]))
            max_raw_width = max(max_raw_width, int(raw_report["max_raw_bbox_width"]))
            max_raw_height = max(max_raw_height, int(raw_report["max_raw_bbox_height"]))
            raw_boundaries_total.update(_count_raw_boundary(raw_report))

            baseline_result = baseline_counter.update(frame_id, mask)
            cpu_result = None
            cpu_frame_report = None
            if cpu_counter is not None:
                gray = (metadata or {}).get("gray")
                if not isinstance(gray, np.ndarray):
                    raise ValueError(f"CPU threshold comparison requires paired Gray8 at {dataset_id}:{source_frame_id}")
                cpu_mask = software_binary8(gray, threshold=100, roi=(30, 210))
                differing = cpu_mask != mask
                mismatch_pixels = int(np.count_nonzero(differing))
                cpu_foreground = int(np.count_nonzero(cpu_mask))
                pl_foreground = int(np.count_nonzero(mask))
                false_positive = int(np.count_nonzero((cpu_mask != 0) & (mask == 0)))
                false_negative = int(np.count_nonzero((cpu_mask == 0) & (mask != 0)))
                cpu_result = cpu_counter.update(frame_id, cpu_mask)
                cpu_strict_difference = _strict_state_difference(
                    baseline_result, baseline_counter, cpu_result, cpu_counter
                )
                cpu_frame_report = {
                    "threshold": ">100 on recorded prepared Gray8",
                    "roi_y": [30, 210],
                    "pixel_mismatch_count": mismatch_pixels,
                    "cpu_foreground_pixels": cpu_foreground,
                    "pl_foreground_pixels": pl_foreground,
                    "cpu_only_foreground_pixels": false_positive,
                    "pl_only_foreground_pixels": false_negative,
                    "masks_exactly_equal": mismatch_pixels == 0,
                    "cpu_baseline_detections_after_split": [_detection_json(item) for item in cpu_result.raw_detections],
                    "cpu_baseline_clustered_detections": [_detection_json(item) for item in cpu_result.detections],
                    "cpu_baseline_events": [_event_json(item) for item in cpu_result.events],
                    "cpu_cumulative_total_count": int(cpu_result.total_count),
                    "semantic_difference_from_pl": {
                        "component_segmentation_changed": tuple(_detection_signature(item) for item in baseline_result.raw_detections)
                        != tuple(_detection_signature(item) for item in cpu_result.raw_detections),
                        "clustered_detection_changed": tuple(_detection_signature(item) for item in baseline_result.detections)
                        != tuple(_detection_signature(item) for item in cpu_result.detections),
                        "event_semantic_changed": Counter(_event_signature(item) for item in baseline_result.events)
                        != Counter(_event_signature(item) for item in cpu_result.events),
                        "cumulative_count_delta_cpu_minus_pl": int(cpu_result.total_count - baseline_result.total_count),
                        "strict_counter_state_changed": bool(cpu_strict_difference["changed_fields"]),
                        "strict_counter_state_changed_fields": cpu_strict_difference["changed_fields"],
                        "strict_counter_state_field_differences": cpu_strict_difference["field_differences"],
                    },
                }
                cpu_pixel_mismatch_total += mismatch_pixels
                cpu_false_positive_total += false_positive
                cpu_false_negative_total += false_negative
                cpu_frames_exact_mask += int(mismatch_pixels == 0)
                cpu_foreground_total += cpu_foreground
                pl_foreground_total += pl_foreground
                cpu_events += len(cpu_result.events)
                cpu_semantic_frames += int(cpu_frame_report["semantic_difference_from_pl"]["component_segmentation_changed"])
                cpu_detection_frames += int(cpu_frame_report["semantic_difference_from_pl"]["clustered_detection_changed"])
                cpu_event_frames += int(cpu_frame_report["semantic_difference_from_pl"]["event_semantic_changed"])
                cpu_strict_state_frames += int(cpu_frame_report["semantic_difference_from_pl"]["strict_counter_state_changed"])
            candidate_result = None
            trace: list[dict[str, Any]] = []
            if candidate_counter is not None and candidate_adapter is not None:
                candidate_result, trace = _candidate_update(
                    candidate_counter, frame_id, mask, candidate_adapter, config
                )
            recorded_result = recorded_counter.update(frame_id, mask) if recorded_counter else None
            base_events = len(baseline_result.events)
            baseline_events += base_events
            baseline_split_components_total += len(baseline_result.raw_detections)
            baseline_clustered_detections_total += len(baseline_result.detections)
            rejected_baseline_count += len(baseline_counter.rejected_detections)
            rejected_baseline_area += sum(item.area for item in baseline_counter.rejected_detections)
            fallback_checks += len(trace)
            fallback_splits += sum(bool(item["candidate_split_accepted"]) for item in trace)
            if activity is not None:
                in_activity = int(activity["start_inclusive"]) <= source_frame_id < int(activity["end_exclusive"])
                if in_activity:
                    fallback_checks_window += len(trace)
                    fallback_splits_window += sum(bool(item["candidate_split_accepted"]) for item in trace)
                else:
                    fallback_checks_post_window += len(trace)
                    fallback_splits_post_window += sum(bool(item["candidate_split_accepted"]) for item in trace)
            if recorded_result is not None:
                recorded_events += len(recorded_result.events)
            if candidate_counter is not None and candidate_result is not None:
                candidate_events += len(candidate_result.events)
                candidate_split_components_total += len(candidate_result.raw_detections)
                candidate_clustered_detections_total += len(candidate_result.detections)
                rejected_candidate_count += len(candidate_counter.rejected_detections)
                rejected_candidate_area += sum(item.area for item in candidate_counter.rejected_detections)
                semantic = (
                    tuple(_detection_signature(item) for item in baseline_result.raw_detections)
                    != tuple(_detection_signature(item) for item in candidate_result.raw_detections)
                )
                detections_changed = (
                    tuple(_detection_signature(item) for item in baseline_result.detections)
                    != tuple(_detection_signature(item) for item in candidate_result.detections)
                )
                events_changed = Counter(_event_signature(item) for item in baseline_result.events) != Counter(
                    _event_signature(item) for item in candidate_result.events
                )
                strict_difference = _strict_state_difference(
                    baseline_result, baseline_counter, candidate_result, candidate_counter
                )
                strict_changed = bool(strict_difference["changed_fields"])
                semantic_frames += int(semantic)
                detection_frames += int(detections_changed)
                event_frames += int(events_changed)
                strict_state_frames += int(strict_changed)
                if activity is not None:
                    if int(activity["start_inclusive"]) <= source_frame_id < int(activity["end_exclusive"]):
                        semantic_frames_window += int(semantic)
                        detection_frames_window += int(detections_changed)
                        event_frames_window += int(events_changed)
                        strict_state_frames_window += int(strict_changed)
                    else:
                        semantic_frames_post_window += int(semantic)
                        detection_frames_post_window += int(detections_changed)
                        event_frames_post_window += int(events_changed)
                        strict_state_frames_post_window += int(strict_changed)
            if activity is not None and int(activity["start_inclusive"]) <= source_frame_id < int(activity["end_exclusive"]):
                baseline_events_window += base_events
                if candidate_result is not None:
                    candidate_events_window += len(candidate_result.events)
            elif activity is not None:
                baseline_events_post_window += base_events
                if candidate_result is not None:
                    candidate_events_post_window += len(candidate_result.events)
            if activity is not None and source_frame_id == int(activity["end_exclusive"]) - 1:
                count_at_window_end_baseline = int(baseline_result.total_count)
                if candidate_result is not None:
                    count_at_window_end_candidate = int(candidate_result.total_count)

            row = _make_frame_row(
                dataset_id, int(source_frame_id), raw_report,
                baseline_counter, baseline_result, candidate_counter, candidate_result,
                trace, recorded_counter, recorded_result, activity,
            )
            if cpu_frame_report is not None:
                row["cpu_threshold_vs_pl"] = cpu_frame_report
            if isinstance(metadata, Mapping):
                source_record = {
                    key: metadata[key]
                    for key in ("frame_id", "gray_path", "gray_sha256", "mask_path", "mask_sha256")
                    if key in metadata
                }
                if source_record:
                    row["source_record"] = source_record
                if metadata.get("source_gray8_only"):
                    row["source_input"] = {
                        "kind": "camera Gray8 only, not PL Binary8",
                        "software_threshold": metadata.get("threshold"),
                    }
            if framewise:
                row["events_evaluated"] = False
                row["note"] = "framewise segmentation only; event totals are intentionally not inferred"
            output.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")
            frame_count += 1

    summary = {
        "dataset_id": dataset_id,
        "status": status,
        "frame_count_processed": frame_count,
        "frame_results_jsonl": str(frames_path),
        "source_paths": [_relative(path) for path in source_paths],
        "source_sha256": { _relative(path): _sha256(path) for path in source_paths if path.is_file() },
        "counter_configuration": effective_config,
        "configuration_source": CURRENT_PROFILE_SOURCE if current_domain else "scripts/wo0016/run_stream_counter.py::counting_configuration(--counting-profile full-width-mixed)",
        "preprocessing": (
            dict(preprocessing_metadata) if preprocessing_metadata is not None else
            frame_geometry("lighting-calibrated", reference_area=111.0, threshold=100)
            if dataset_id.startswith("lighting-calibration-") else None
        ),
        "candidate_configuration_note": (
            "candidate reference_area=111 is inherited from the current M6 profile for offline guard diagnostics; the 10/24 optical scale was not independently measured"
            if candidate_adapter is not None and not current_domain else None
        ),
        "event_evaluation": "disabled; framewise split/control only" if framewise else "counter events recorded; no accuracy scored without independent frame/object labels",
        "ground_truth_status": ground_truth_note or "no independent per-frame entity ground truth; diagnostic only",
        "static_reference": dict(static_reference) if static_reference else None,
        "raw_ccl_before_split": {
            "total_components_before_min_area": raw_components_total,
            "total_components_at_or_above_min_area": min_area_components_total,
            "max_component_area": max_raw_area,
            "max_bbox_width": max_raw_width,
            "max_bbox_height": max_raw_height,
            "current_domain_boundary_exceedance_totals": dict(raw_boundaries_total) if current_domain else None,
            "boundary_thresholds_apply_only_to": "10/25 lighting-calibrated current profile" if current_domain else None,
        },
        "baseline_current_profile": {
            "final_cumulative_count": int(baseline_counter.total_count),
            "raw_detections_after_split_total_over_frames": baseline_split_components_total,
            "clustered_detections_total_over_frames": baseline_clustered_detections_total,
            "events_all_processed_frames": baseline_events,
            "events_in_activity_window": baseline_events_window if activity is not None else None,
            "events_outside_activity_window_diagnostic": baseline_events_post_window if activity is not None else None,
            "cumulative_count_at_activity_window_end": count_at_window_end_baseline,
            "components_rejected_before_split_count": rejected_baseline_count,
            "components_rejected_before_split_area_px": rejected_baseline_area,
        },
        "candidate": None,
        "candidate_difference": None,
        "recorded_capture_configuration_diagnostic": None,
        "activity_window": dict(activity) if activity else None,
        "framewise_segmentation_only": framewise,
        "total_foreground_pixels_over_frames": total_input_foreground_px,
        "cpu_threshold_vs_pl": None,
    }
    if cpu_counter is not None:
        summary["cpu_threshold_vs_pl"] = {
            "source": "scripts/wo0016/offline_threshold.py::software_binary8",
            "threshold": ">100 on recorded prepared Gray8",
            "roi_y": [30, 210],
            "frames_compared": frame_count,
            "frames_with_exact_mask_match": cpu_frames_exact_mask,
            "pixel_mismatch_count_total": cpu_pixel_mismatch_total,
            "cpu_only_foreground_pixels_total": cpu_false_positive_total,
            "pl_only_foreground_pixels_total": cpu_false_negative_total,
            "cpu_foreground_pixels_total": cpu_foreground_total,
            "pl_foreground_pixels_total": pl_foreground_total,
            "frames_with_component_segmentation_difference": cpu_semantic_frames,
            "frames_with_clustered_detection_difference": cpu_detection_frames,
            "frames_with_event_semantic_difference": cpu_event_frames,
            "frames_with_strict_counter_state_difference": cpu_strict_state_frames,
            "cpu_events_all_processed_frames": cpu_events,
            "final_cumulative_count_cpu_minus_pl": int(cpu_counter.total_count - baseline_counter.total_count),
        }
    if candidate_counter is not None:
        summary["candidate"] = {
            "final_cumulative_count": int(candidate_counter.total_count),
            "raw_detections_after_split_total_over_frames": candidate_split_components_total,
            "clustered_detections_total_over_frames": candidate_clustered_detections_total,
            "events_all_processed_frames": candidate_events,
            "events_in_activity_window": candidate_events_window if activity is not None else None,
            "events_outside_activity_window_diagnostic": candidate_events_post_window if activity is not None else None,
            "cumulative_count_at_activity_window_end": count_at_window_end_candidate,
            "components_rejected_before_split_count": rejected_candidate_count,
            "components_rejected_before_split_area_px": rejected_candidate_area,
            "fallback_parent_checks": fallback_checks,
            "accepted_fallback_splits": fallback_splits,
            "fallback_parent_checks_in_activity_window": fallback_checks_window if activity is not None else None,
            "accepted_fallback_splits_in_activity_window": fallback_splits_window if activity is not None else None,
            "fallback_parent_checks_outside_activity_window_diagnostic": fallback_checks_post_window if activity is not None else None,
            "accepted_fallback_splits_outside_activity_window_diagnostic": fallback_splits_post_window if activity is not None else None,
        }
        summary["candidate_difference"] = {
            "frames_with_component_segmentation_change": semantic_frames,
            "frames_with_clustered_detection_change": detection_frames,
            "frames_with_event_semantic_change": event_frames,
            "frames_with_strict_counter_state_change": strict_state_frames,
            "activity_window_semantic_changes": {
                "frames_with_component_segmentation_change": semantic_frames_window if activity is not None else None,
                "frames_with_clustered_detection_change": detection_frames_window if activity is not None else None,
                "frames_with_event_semantic_change": event_frames_window if activity is not None else None,
                "frames_with_strict_counter_state_change": strict_state_frames_window if activity is not None else None,
            },
            "outside_activity_window_diagnostic_semantic_changes": {
                "frames_with_component_segmentation_change": semantic_frames_post_window if activity is not None else None,
                "frames_with_clustered_detection_change": detection_frames_post_window if activity is not None else None,
                "frames_with_event_semantic_change": event_frames_post_window if activity is not None else None,
                "frames_with_strict_counter_state_change": strict_state_frames_post_window if activity is not None else None,
            },
            "final_cumulative_count_delta": int(candidate_counter.total_count - baseline_counter.total_count),
            "events_in_activity_window_delta": candidate_events_window - baseline_events_window if activity is not None else None,
        }
    if recorded_counter is not None:
        summary["recorded_capture_configuration_diagnostic"] = {
            "counter_configuration": _effective_counter_configuration(recorded_config or {}),
            "final_cumulative_count": int(recorded_counter.total_count),
            "events_all_processed_frames": recorded_events,
            "interpretation": "historical recorded min_split_child_area; not the current 10/25 baseline and not a candidate gain",
        }
    return summary


def _frame_pairs_dataset(root: Path) -> tuple[list[dict[str, Any]], Iterator[tuple[int, np.ndarray, dict[str, Any] | None]]]:
    records_path = root / "frame-records.jsonl"
    records = _read_frame_records(records_path)

    def iterator() -> Iterator[tuple[int, np.ndarray, dict[str, Any] | None]]:
        for record in records:
            gray = np.load(root / record["gray_path"], allow_pickle=False)
            mask = np.load(root / record["mask_path"], allow_pickle=False)
            if gray.shape != (240, 320) or mask.shape != (240, 320):
                raise ValueError(f"unexpected paired frame shape in {root}: frame {record.get('frame_id')}")
            if mask.dtype != np.uint8 or np.any((mask != 0) & (mask != 255)):
                raise ValueError(f"non-Binary8 mask in {root}: frame {record.get('frame_id')}")
            record_metadata = dict(record)
            record_metadata["gray"] = gray
            yield int(record["frame_id"]), mask, record_metadata
    return records, iterator()


def _capture_npz_frames(path: Path) -> Iterator[tuple[int, np.ndarray, dict[str, Any] | None]]:
    with np.load(path, allow_pickle=False) as archive:
        gray_frames = archive["raw_gray8"]
        masks = archive["raw_binary8"]
        if gray_frames.shape != masks.shape or gray_frames.ndim != 3:
            raise ValueError(f"invalid Gray8/Binary8 archive geometry: {path}")
        if np.any((masks != 0) & (masks != 255)):
            raise ValueError(f"non-Binary8 values in archive: {path}")
        for frame_id in range(len(masks)):
            yield frame_id, masks[frame_id], {"gray": gray_frames[frame_id]}


def _historical_gray_frames(path: Path) -> Iterator[tuple[int, np.ndarray, dict[str, Any] | None]]:
    with np.load(path, allow_pickle=False) as archive:
        gray_frames = archive["input_gray320x240"]
        if gray_frames.shape[1:] != (240, 320):
            raise ValueError(f"unexpected historical Gray8 frame shape: {gray_frames.shape}")
        for frame_id, gray in enumerate(gray_frames):
            mask = software_binary8(gray, threshold=128, roi=(0, 240))
            yield frame_id, mask, {"source_gray8_only": True, "threshold": ">128"}


def _static_calibration_mask(image_path: Path, preprocessor: MonochromePreprocessor) -> np.ndarray:
    source = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
    if source is None:
        raise FileNotFoundError(f"could not decode calibration image: {image_path}")
    _gray, mask = preprocessor(source)
    return mask.copy()


def _fixture_frames(path: Path) -> Iterator[tuple[int, np.ndarray, dict[str, Any] | None]]:
    with np.load(path, allow_pickle=False) as archive:
        frame_ids = archive["frame_ids"]
        masks = archive["masks"]
        if masks.shape != (len(frame_ids), 240, 320):
            raise ValueError(f"unexpected fixture shape: {masks.shape}")
        for frame_id, mask in zip(frame_ids, masks):
            yield int(frame_id), mask, {"source": "framewise fixture mask", "events_not_evaluated": True}


def _candidate_options(args: argparse.Namespace, config: Mapping[str, Any]) -> dict[str, Any]:
    profile_min_child = int(config.get("min_split_child_area", config.get("min_area", 12)))
    return {
        "strategy": args.candidate_strategy,
        "reference_area": args.candidate_reference_area,
        "beta": args.candidate_beta,
        "min_child_area": (
            int(args.candidate_min_child_area)
            if args.candidate_min_child_area is not None
            else profile_min_child
        ),
        "single_intact_area": int(config.get("single_intact_area", 64)),
        "min_local_prominence": args.candidate_min_local_prominence,
        "max_objects": args.candidate_max_objects,
    }


def _add_dataset(summaries: list[dict[str, Any]], output_frames: Path, **kwargs: Any) -> dict[str, Any]:
    frames_path = output_frames / f"{kwargs['dataset_id']}.jsonl"
    summary = _sequence_summary(frames_path=frames_path, **kwargs)
    reference = kwargs.get("static_reference") or {}
    isolated_count = reference.get("isolated_single_item_control_count")
    if isolated_count is not None:
        raw_count = summary["raw_ccl_before_split"]["total_components_at_or_above_min_area"]
        rejected = summary["baseline_current_profile"]["components_rejected_before_split_count"]
        if raw_count == isolated_count and rejected == 0:
            summary["single_piece_static_false_split_assessment"] = {
                "isolated_single_item_control_count": isolated_count,
                "raw_ccl_count_matches_controls": True,
                "pre_split_rejected_count": rejected,
                "baseline_false_split_increment": (
                    summary["baseline_current_profile"]["raw_detections_after_split_total_over_frames"] - raw_count
                ),
                "candidate_false_split_increment": (
                    summary["candidate"]["raw_detections_after_split_total_over_frames"] - raw_count
                    if summary["candidate"] else None
                ),
                "interpretation": "static isolated-item regression control only; not moving accuracy",
            }
        else:
            summary["single_piece_static_false_split_assessment"] = {
                "isolated_single_item_control_count": isolated_count,
                "raw_ccl_count_matches_controls": False,
                "observed_raw_ccl_count": raw_count,
                "pre_split_rejected_count": rejected,
                "interpretation": "not scored because the output-domain CCL does not correspond one-to-one with the three isolated source controls",
            }
    summaries.append(summary)
    return summary


def run(args: argparse.Namespace) -> Path:
    output_dir = Path(args.output_dir).expanduser().resolve()
    if output_dir.is_relative_to(REPO_ROOT.resolve()):
        raise ValueError("ablation output must be outside the repository")
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite existing run: {output_dir}")
    output_frames = output_dir / "frames"
    output_frames.mkdir(parents=True, exist_ok=False)

    current_capture = REPO_ROOT / (
        "data/results/lighting-calibration-20261025/board-approval/b2-attempt-03/"
        "evidence/b2-capture-b2-m6touch-220-20261025-03"
    )
    incomplete_capture = REPO_ROOT / (
        "data/results/lighting-calibration-20261025/board-approval/stage1-currentboot/"
        "stageb2-currentboot-incomplete-capture/b2-capture-b2-m6touch-220-20261025-01"
    )
    calibration_dir = REPO_ROOT / "data/calibration/lighting-20261025"
    recordings_dir = REPO_ROOT / "data/recorded/wo0017-touching-20261024"
    fixture_path = SCRIPT_DIR / "fixtures/lighting_b2_m6_touch_masks.npz"

    current_records = _read_frame_records(current_capture / "frame-records.jsonl")
    if not current_records:
        raise ValueError("B2-03 has no frame records")
    current_config = _current_profile_config(current_records[0])
    candidate = (
        CandidateAdapter(
            Path(args.candidate_module),
            {**_candidate_options(args, current_config), "_min_child_area_override": args.candidate_min_child_area},
        )
        if args.candidate_module else None
    )
    candidate_path = candidate.path if candidate else None
    summaries: list[dict[str, Any]] = []

    current_manifest = _read_json(current_capture / "manifest.json")
    b2_npz = current_capture / "frames_gray8_binary8.npz"
    _add_dataset(
        summaries, output_frames,
        dataset_id="b2-03-current-pl-gray8-binary8",
        source_paths=[b2_npz, current_capture / "manifest.json", current_capture / "frame-records.jsonl"],
        status=f"{current_manifest.get('status')} ({current_manifest.get('captured_frames')}/{current_manifest.get('expected_frames')} frames)",
        config=current_config,
        frames=_capture_npz_frames(b2_npz),
        current_domain=True,
        candidate_adapter=candidate,
        cpu_threshold_compare=True,
        ground_truth_note="M6 touching-nut camera/PL capture; no independent per-frame entity labels or M8 dynamic sequence; diagnostic only, no offline accuracy claim",
    )

    incomplete_records, incomplete_frames = _frame_pairs_dataset(incomplete_capture)
    incomplete_manifest = _read_json(incomplete_capture / "manifest.json")
    recorded_config = dict(incomplete_records[0]["metadata"]["algorithm_config"])
    # The accepted 10/25 profile uses 48 px. Preserve the capture's 60 px only as
    # a clearly separated historical-configuration diagnostic counter.
    _add_dataset(
        summaries, output_frames,
        dataset_id="b2-incomplete-current48-recorded60-diagnostic",
        source_paths=[incomplete_capture / "manifest.json", incomplete_capture / "frame-records.jsonl"],
        status=f"{incomplete_manifest.get('status')} ({len(incomplete_records)} paired records; included for read-only diagnosis despite incomplete capture)",
        config=current_config,
        frames=incomplete_frames,
        current_domain=True,
        candidate_adapter=candidate,
        recorded_config=recorded_config,
        cpu_threshold_compare=True,
        ground_truth_note="incomplete paired Gray8/PL Binary8 capture; no independent per-frame entity labels; current 48 px baseline is primary, recorded 60 px run is diagnostic only",
    )

    preprocessor_source = MonochromePreprocessor(mapping="lighting-calibrated", reference_area=111.0, threshold=100)
    preprocessor_output_roi = MonochromePreprocessor(mapping="lighting-calibrated-output-roi", reference_area=111.0, threshold=100)
    calibration_jsons = sorted(calibration_dir.glob("lighting_*.json"))
    static_preprocessing_comparisons = []
    for calibration_json in calibration_jsons:
        metadata = _read_json(calibration_json)
        stem = calibration_json.stem
        image_path = calibration_json.with_suffix(".png")
        if not image_path.is_file():
            raise FileNotFoundError(f"calibration JSON has no matching PNG: {calibration_json}")
        source_image = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
        if source_image is None or source_image.shape[:2] != (400, 640):
            raise ValueError(f"static source-vs-output-ROI comparison requires the real 640x400 PNG: {image_path}")
        source_gray, source_mask = preprocessor_source(source_image)
        output_roi_gray, output_roi_mask = preprocessor_output_roi(source_image)
        visible_reference = int(metadata.get("metrics", {}).get("part_count", 0))
        static_reference = {
            "visible_or_source_contour_count": visible_reference,
            "count_source": "paired calibration JSON metrics.part_count; static snapshot only",
            "independent_ground_truth": False,
            "status": metadata.get("metrics", {}).get("status_overexp"),
            "source_resolution": [640, 400],
            "source_contour_records": metadata.get("metrics", {}).get("parts", []),
        }
        if "172731" in stem:
            static_reference.update({
                "isolated_single_item_control_count": 3,
                "single_item_output_area_hints_px": [80, 111, 150],
                "single_item_reference_status": "three isolated static controls; negative split control only",
            })
        if "174438" in stem:
            static_reference.update({
                "visible_annotation_count": 15,
                "connected_source_contour_group_count": 6,
                "interpretation": "six connected source contour groups represent touching groups; they are not six independent single-item controls",
                "annotation_source": "data/results/lighting-calibration-20261025/replay.py static visible_annotation_count",
                "annotation_is_independent_ground_truth": False,
            })
        source_summary = _add_dataset(
            summaries, output_frames,
            dataset_id=f"lighting-calibration-{stem}-source-filter",
            source_paths=[image_path, calibration_json],
            status="static calibration image; processed through lighting-calibrated source preprocessing",
            config=current_config,
            frames=iter([(0, source_mask.copy(), {"source_resolution": [640, 400]})]),
            current_domain=True,
            candidate_adapter=candidate,
            framewise=True,
            preprocessing_metadata=frame_geometry("lighting-calibrated", reference_area=111.0, threshold=100),
            ground_truth_note="static calibration image; visible item count/material mapping is calibration context, not independent specimen-level ground truth",
            static_reference=static_reference,
        )
        output_roi_summary = _add_dataset(
            summaries, output_frames,
            dataset_id=f"lighting-calibration-{stem}-output-roi",
            source_paths=[image_path, calibration_json],
            status="static calibration image; processed through lighting-calibrated-output-roi preprocessing",
            config=current_config,
            frames=iter([(0, output_roi_mask.copy(), {"source_resolution": [640, 400]})]),
            current_domain=True,
            candidate_adapter=candidate,
            framewise=True,
            preprocessing_metadata=frame_geometry("lighting-calibrated-output-roi", reference_area=111.0, threshold=100),
            ground_truth_note="static calibration image; output-ROI-only preprocessing comparison, not physical accuracy",
            static_reference=static_reference,
        )
        static_preprocessing_comparisons.append({
            "image_path": _relative(image_path),
            "image_source_resolution": [640, 400],
            "source_filter_dataset_id": source_summary["dataset_id"],
            "output_roi_dataset_id": output_roi_summary["dataset_id"],
            "source_filter_mask_foreground_px": int(np.count_nonzero(source_mask)),
            "output_roi_mask_foreground_px": int(np.count_nonzero(output_roi_mask)),
            "mask_pixel_mismatch_count": int(np.count_nonzero(source_mask != output_roi_mask)),
            "source_filter_raw_ccl_count": source_summary["raw_ccl_before_split"]["total_components_at_or_above_min_area"],
            "output_roi_raw_ccl_count": output_roi_summary["raw_ccl_before_split"]["total_components_at_or_above_min_area"],
            "source_filter_baseline_split_detections": source_summary["baseline_current_profile"]["raw_detections_after_split_total_over_frames"],
            "output_roi_baseline_split_detections": output_roi_summary["baseline_current_profile"]["raw_detections_after_split_total_over_frames"],
            "source_filter_candidate_split_detections": source_summary["candidate"]["raw_detections_after_split_total_over_frames"] if source_summary["candidate"] else None,
            "output_roi_candidate_split_detections": output_roi_summary["candidate"]["raw_detections_after_split_total_over_frames"] if output_roi_summary["candidate"] else None,
            "ground_truth_status": "static preprocessing sensitivity comparison only; no dynamic entity accuracy",
        })

    _add_dataset(
        summaries, output_frames,
        dataset_id="lighting-b2-m6-touch-15-frame-fixture",
        source_paths=[fixture_path],
        status="15-frame Binary8 fixture; framewise split comparison only",
        config=current_config,
        frames=_fixture_frames(fixture_path),
        current_domain=True,
        candidate_adapter=candidate,
        framewise=True,
        ground_truth_note="B2 M6 touch fixture has no per-frame entity-count labels; sequence timing is not used for tracker event claims",
        static_reference={"frame_ids": [48, 62], "frame_count": 15, "independent_ground_truth": False},
    )

    history_config = _historical_profile_config()
    historical_expected = {
        "wo_touching_pair_motion_20261024_03": "two touching M6 nuts; README target window 0-900",
        "wo_touching_three_motion_20261024_02": "three close M6 nuts; README target window 0-99",
        "wo_touching_three_motion_20261024_04": "three close M6 nuts; README target window 0-79",
    }
    for dataset_dir in sorted(recordings_dir.iterdir()):
        archive_path = dataset_dir / "camera_gray_only.npz"
        if not archive_path.is_file():
            continue
        manifest = _read_json(dataset_dir / "manifest.json")
        activity = _historical_activity(dataset_dir.name)
        _add_dataset(
            summaries, output_frames,
            dataset_id=dataset_dir.name,
            source_paths=[archive_path, dataset_dir / "manifest.json"],
            status=f"{manifest.get('status')} ({manifest.get('frame_count')} Gray8 frames; CAMERA_ONLY_NOT_PL_BINARY8)",
            config=history_config,
            frames=_historical_gray_frames(archive_path),
            current_domain=False,
            candidate_adapter=candidate,
            activity=activity,
            ground_truth_note=(
                historical_expected[dataset_dir.name]
                + "; historical Gray8-only replay with strict threshold >128; no independent per-frame object labels; post-window frames remain diagnostic and are excluded from target-window counts"
            ),
        )

    report = {
        "schema_version": 1,
        "kind": "offline full-data baseline and optional split-candidate ablation",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "repository_root": str(REPO_ROOT),
        "script_path": _relative(Path(__file__)),
        "script_sha256": _sha256(Path(__file__)),
        "output_dir": str(output_dir),
        "candidate_module": str(candidate_path) if candidate_path else None,
        "candidate_module_sha256": candidate.source_sha256 if candidate else None,
        "candidate_module_source_hashes": getattr(candidate.module, "SOURCE_SHA256", None) if candidate else None,
        "candidate_options_by_profile": {
            "10/25 lighting-calibrated": _candidate_options(args, current_config),
            "10/24 full-width-mixed": {
                **_candidate_options(args, history_config),
                "reference_area_calibration": "unmeasured for the historical camera/optics; inherited 111 is diagnostic only",
            },
        } if candidate else None,
        "scope": {
            "board_or_rtl_accessed": False,
            "repository_data_written": False,
            "candidate_replaces_baseline": False,
            "candidate_hook": "only when the existing splitter returns one intact piece and parent area meets max(profile single_intact_area, 2*candidate min_child_area)",
            "current_10_25_main_profile": {
                "source": CURRENT_PROFILE_SOURCE,
                "min_area": 15,
                "min_split_child_area": 48,
                "single_intact_area": 64,
                "split_max_area": 540,
                "component_max_area": 540,
                "component_max_bbox": [85, 85],
                "raw_boundary_domain_only": "10/25 lighting-calibrated output ROI",
            },
            "historical_10_24_guard_profile": {
                "source": "scripts/wo0016/run_stream_counter.py::counting_configuration(--counting-profile full-width-mixed)",
                "threshold": ">128 on stored camera Gray8; not PL Binary8",
                "single_intact_area": 95,
                "max_split_area": 900,
                "max_area": 900,
                "component_cluster_gap": 14,
                "max_merged_span": 24,
                "candidate_reference_area": 111,
                "candidate_reference_area_calibration": "unmeasured for the historical camera/optics; candidate output is diagnostic only",
                "activity_windows_source": HISTORICAL_WINDOW_SOURCE,
                "all_1800_frames_processed": True,
                "post_window_frames": "full diagnostic only; not included in target-window event counts or accuracy claims",
            },
            "missing_ground_truth": [
                "independent per-frame entity labels for B2/incomplete captures",
                "single-piece specimen ground truth across pose/position variation",
                "dynamic M8 sequence ground truth",
                "historical recordings have no independent per-frame labels",
            ],
        },
        "datasets": summaries,
        "static_source_vs_output_roi_comparisons": static_preprocessing_comparisons,
        "interpretation": "Counts and component changes are offline diagnostics; no physical accuracy is scored without independent ground truth.",
    }
    _write_json(output_dir / "summary.json", report)
    _write_json(output_dir / "static-source-vs-output-roi.json", static_preprocessing_comparisons)
    return output_dir


def _positive_float(value: str) -> float:
    result = float(value)
    if not np.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("value must be positive and finite")
    return result


def _nonnegative_float(value: str) -> float:
    result = float(value)
    if not np.isfinite(result) or result < 0:
        raise argparse.ArgumentTypeError("value must be nonnegative and finite")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-module", help="optional out-of-tree Python candidate module")
    parser.add_argument("--candidate-strategy", choices=("raw", "filled"), default="raw")
    parser.add_argument("--candidate-reference-area", type=_positive_float, default=111.0)
    parser.add_argument("--candidate-beta", type=_positive_float, default=1.0)
    parser.add_argument("--candidate-min-child-area", type=int, default=None,
                        help="optional override; defaults to actual splitter gate (48 current, 12 historical)")
    parser.add_argument("--candidate-min-local-prominence", type=_nonnegative_float, default=0.75)
    parser.add_argument("--candidate-max-objects", type=int, choices=(2, 3), default=3)
    parser.add_argument("--output-dir", required=True, help="new results directory outside the repository")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.candidate_min_child_area is not None and args.candidate_min_child_area < 1:
        parser.error("--candidate-min-child-area must be positive")
    output_dir = run(args)
    print(json.dumps({"status": "complete", "output_dir": str(output_dir), "summary": str(output_dir / "summary.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
