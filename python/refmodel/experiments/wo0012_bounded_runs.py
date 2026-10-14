"""Offline bounded row-run feature extractor evaluation for WO-0012.

This is an isolated PC reference model. It makes no claims about a synthesized
PL implementation and does not modify the WO-0010 detector or production RTL.
"""
import argparse
import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys

import cv2
import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[3]
PYTHON_ROOT = ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from refmodel.ps_reference.regions import Region, preprocess


_MAX_AREA = 0xFFFF
_FLAG_MERGE_SUSPECT = 1
_FLAG_TOUCHES_FRAME_EDGE = 2
_FLAG_TWO_DIMENSIONAL = 4
_MORPH_MODES = (("bypass", 0), ("erosion", 1), ("dilation", 2))


@dataclass(frozen=True)
class RunCaps:
    """Independent positive limits for one row, active labels and output records."""

    row_run_cap: int
    active_cap: int
    record_cap: int

    def __post_init__(self):
        for name, value in (("row_run_cap", self.row_run_cap),
                            ("active_cap", self.active_cap),
                            ("record_cap", self.record_cap)):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass
class _Accumulator:
    slot: int
    area: int = 0
    sum_x: int = 0
    sum_y: int = 0
    x_min: int | None = None
    x_max: int | None = None
    row_min: int | None = None
    row_max: int | None = None

    def add_run(self, y: int, x_min: int, x_max: int) -> None:
        length = x_max - x_min + 1
        self.area += length
        self.sum_x += (x_min + x_max) * length // 2
        self.sum_y += y * length
        self.x_min = x_min if self.x_min is None else min(self.x_min, x_min)
        self.x_max = x_max if self.x_max is None else max(self.x_max, x_max)
        self.row_min = y if self.row_min is None else min(self.row_min, y)
        self.row_max = y if self.row_max is None else max(self.row_max, y)


@dataclass
class RunFrameResult:
    accepted: bool
    rejection_reason: str | None
    regions: list[Region]
    component_sums: list[dict[str, int]]
    stats: dict[str, int]
    min_area: int
    max_width: int


def _validate_binary(binary: np.ndarray) -> np.ndarray:
    if not isinstance(binary, np.ndarray) or binary.ndim != 2 or binary.dtype != np.uint8:
        raise ValueError("binary must be a two-dimensional uint8 NumPy array")
    if binary.shape[0] == 0 or binary.shape[1] == 0:
        raise ValueError("binary frame dimensions must be non-zero")
    if np.any((binary != 0x00) & (binary != 0xFF)):
        raise ValueError("binary pixels must be encoded as 0x00 or 0xFF")
    return binary


def _validate_min_area(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("min_area must be a non-negative integer")
    return value


def _runs_by_row(binary: np.ndarray) -> list[list[tuple[int, int]]]:
    rows = []
    for row in binary:
        foreground = row == 0xFF
        edges = np.diff(np.pad(foreground.astype(np.int8), (1, 1)))
        starts = np.flatnonzero(edges == 1)
        ends = np.flatnonzero(edges == -1) - 1
        rows.append([(int(start), int(end)) for start, end in zip(starts, ends)])
    return rows


def _result(
    *,
    accepted: bool,
    reason: str | None,
    staged: list[tuple[Region, dict[str, int]]],
    stats: dict[str, int],
    min_area: int,
    max_width: int,
) -> RunFrameResult:
    if not accepted:
        stats["completed_before_reject"] = max(stats["completed_before_reject"], len(staged))
        stats["completed_regions_per_frame"] = 0
        return RunFrameResult(False, reason, [], [], stats, min_area, max_width)
    # Preserve close order; the proposed hardware does not include an x sorter.
    # OpenCV comparisons below independently sort both feature sets.
    stats["completed_regions_per_frame"] = len(staged)
    return RunFrameResult(
        True,
        None,
        [item[0] for item in staged],
        [item[1] for item in staged],
        stats,
        min_area,
        max_width,
    )


def extract_run_frame(
    binary: np.ndarray,
    *,
    caps: RunCaps,
    min_area: int = 16,
    max_width: int = 24,
) -> RunFrameResult:
    """Extract 8-connected features using bounded row runs and fixed label slots.

    A current run may inherit one distinct previous-row slot when the intervals
    touch after a one-pixel 8-connectivity expansion. Multiple predecessor slots
    reject the entire frame. Multiple runs can inherit one slot, allowing a
    single component with a hole or branching contour. Image EOF flushes every
    active slot. Any capacity or 16-bit-area failure returns no partial regions.
    """
    binary = _validate_binary(binary)
    min_area = _validate_min_area(min_area)
    if not isinstance(caps, RunCaps):
        raise ValueError("caps must be a RunCaps instance")
    if type(max_width) is not int or max_width < 0:
        raise ValueError("max_width must be a non-negative integer")

    height, width = binary.shape
    rows = _runs_by_row(binary)
    stats = {
        "width": int(width),
        "height": int(height),
        "foreground_pixels": int(np.count_nonzero(binary)),
        "run_count": sum(len(row) for row in rows),
        "max_runs_per_row": max((len(row) for row in rows), default=0),
        "active_peak": 0,
        "completed_regions_per_frame": 0,
        "completed_before_reject": 0,
        "comparison_count": 0,
    }
    staged: list[tuple[Region, dict[str, int]]] = []
    if stats["max_runs_per_row"] > caps.row_run_cap:
        return _result(accepted=False, reason="row_run_cap_exceeded", staged=staged,
                       stats=stats, min_area=min_area, max_width=max_width)

    active: dict[int, _Accumulator] = {}
    previous_runs: list[tuple[int, int, int]] = []
    for y, row_runs in enumerate(rows):
        inherited: list[int | None] = []
        for x_min, x_max in row_runs:
            predecessors = set()
            for prev_min, prev_max, prev_slot in previous_runs:
                stats["comparison_count"] += 1
                if x_min <= prev_max + 1 and x_max >= prev_min - 1:
                    predecessors.add(prev_slot)
            if len(predecessors) > 1:
                return _result(accepted=False, reason="multi_root_merge", staged=staged,
                               stats=stats, min_area=min_area, max_width=max_width)
            inherited.append(next(iter(predecessors)) if predecessors else None)

        inherited_slots = {slot for slot in inherited if slot is not None}
        new_roots = sum(slot is None for slot in inherited)
        closing = sorted(set(active) - inherited_slots)
        for slot in closing:
            try:
                outcome = _finish_component(active.pop(slot), width, height, min_area, max_width)
            except _AreaOverflow:
                return _result(accepted=False, reason="area_overflow", staged=staged,
                               stats=stats, min_area=min_area, max_width=max_width)
            if outcome is not None:
                if len(staged) >= caps.record_cap:
                    stats["completed_before_reject"] = len(staged)
                    return _result(accepted=False, reason="record_cap_exceeded", staged=staged,
                                   stats=stats, min_area=min_area, max_width=max_width)
                staged.append(outcome)

        # Slots whose roots ended on the previous row are released before new
        # disconnected runs take labels on this row. This avoids a false
        # old-plus-new transient peak for adjacent rows with no 8-connectivity.
        required_active = len(inherited_slots) + new_roots
        stats["active_peak"] = max(stats["active_peak"], required_active)
        if required_active > caps.active_cap:
            return _result(accepted=False, reason="active_cap_exceeded", staged=staged,
                           stats=stats, min_area=min_area, max_width=max_width)

        free_slots = [slot for slot in range(caps.active_cap) if slot not in active]
        current_runs = []
        for (x_min, x_max), slot in zip(row_runs, inherited):
            if slot is None:
                slot = free_slots.pop(0)
                active[slot] = _Accumulator(slot)
            accumulator = active.get(slot)
            if accumulator is None:
                raise AssertionError("inherited run refers to an inactive fixed slot")
            accumulator.add_run(y, x_min, x_max)
            current_runs.append((x_min, x_max, slot))
        previous_runs = current_runs

    for slot in sorted(active):
        try:
            outcome = _finish_component(active[slot], width, height, min_area, max_width)
        except _AreaOverflow:
            return _result(accepted=False, reason="area_overflow", staged=staged,
                           stats=stats, min_area=min_area, max_width=max_width)
        if outcome is not None:
            if len(staged) >= caps.record_cap:
                stats["completed_before_reject"] = len(staged)
                return _result(accepted=False, reason="record_cap_exceeded", staged=staged,
                               stats=stats, min_area=min_area, max_width=max_width)
            staged.append(outcome)

    return _result(accepted=True, reason=None, staged=staged, stats=stats,
                   min_area=min_area, max_width=max_width)


def _finish_component(
    component: _Accumulator,
    width: int,
    height: int,
    min_area: int,
    max_width: int,
) -> tuple[Region, dict[str, int]] | None:
    if component.area > _MAX_AREA:
        raise _AreaOverflow
    if component.area < min_area:
        return None
    assert component.area > 0
    assert component.x_min is not None and component.x_max is not None
    assert component.row_min is not None and component.row_max is not None
    box_width = component.x_max - component.x_min + 1
    flags = _FLAG_TWO_DIMENSIONAL
    if max_width not in (0, None) and box_width > max_width:
        flags |= _FLAG_MERGE_SUSPECT
    if (component.x_min == 0 or component.row_min == 0 or
            component.x_max == width - 1 or component.row_max == height - 1):
        flags |= _FLAG_TOUCHES_FRAME_EDGE
    region = Region(
        x_min=component.x_min,
        x_max=component.x_max,
        row_min=component.row_min,
        row_max=component.row_max,
        area=component.area,
        centroid_x=component.sum_x / component.area,
        centroid_y=component.sum_y / component.area,
        peak=0,
        flags=flags,
    )
    return region, {"sum_x": component.sum_x, "sum_y": component.sum_y}


class _AreaOverflow(Exception):
    pass


def compare_with_opencv(binary: np.ndarray, result: RunFrameResult) -> dict:
    """Compare accepted candidate records and exact integer moments with OpenCV."""
    binary = _validate_binary(binary)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        binary, connectivity=8, ltype=cv2.CV_32S
    )
    height, width = binary.shape
    label_count = count - 1
    x_grid = np.broadcast_to(np.arange(width, dtype=np.float64), binary.shape)
    y_grid = np.broadcast_to(np.arange(height, dtype=np.float64)[:, None], binary.shape)
    sums_x = np.bincount(labels.ravel(), weights=x_grid.ravel(), minlength=count)
    sums_y = np.bincount(labels.ravel(), weights=y_grid.ravel(), minlength=count)
    expected = []
    for label in range(1, count):
        x_min, y_min, box_width, box_height, area = (int(value) for value in stats[label])
        if area < result.min_area:
            continue
        x_max = x_min + box_width - 1
        y_max = y_min + box_height - 1
        flags = _FLAG_TWO_DIMENSIONAL
        if result.max_width not in (0, None) and box_width > result.max_width:
            flags |= _FLAG_MERGE_SUSPECT
        if x_min == 0 or y_min == 0 or x_max == width - 1 or y_max == height - 1:
            flags |= _FLAG_TOUCHES_FRAME_EDGE
        sx, sy = int(round(sums_x[label])), int(round(sums_y[label]))
        expected.append((
            (x_min, x_max, y_min, y_max), area, sx, sy,
            sx / area, sy / area, flags,
        ))
    expected.sort(key=lambda item: (item[4], item[5], item[0]))

    if not result.accepted:
        return {
            "compared": False,
            "exact": None,
            "reason": "candidate_frame_rejected",
            "opencv_components": label_count,
            "opencv_filtered_components": len(expected),
            "candidate_components": 0,
        }

    actual = []
    for region, moments in zip(result.regions, result.component_sums):
        actual.append((region.bbox, region.area, moments["sum_x"], moments["sum_y"],
                       region.centroid_x, region.centroid_y, region.flags))
    actual.sort(key=lambda item: (item[4], item[5], item[0]))
    exact = len(actual) == len(expected) and all(
        left[:4] == right[:4] and left[6] == right[6]
        and abs(left[4] - right[4]) <= 1e-12
        and abs(left[5] - right[5]) <= 1e-12
        for left, right in zip(actual, expected)
    )
    mismatch_index = None
    if not exact:
        mismatch_index = next((index for index, pair in enumerate(zip(actual, expected))
                               if pair[0] != pair[1]), min(len(actual), len(expected)))
    return {
        "compared": True,
        "exact": bool(exact),
        "opencv_components": label_count,
        "opencv_filtered_components": len(expected),
        "candidate_components": len(actual),
        "mismatch_index": mismatch_index,
    }


def _dataset_frames(data_dir: Path) -> tuple[list[dict], dict[str, str]]:
    data_dir = data_dir.resolve()
    root_config_path = data_dir / "config.json"
    root_config = json.loads(root_config_path.read_text(encoding="utf-8"))
    conditions = root_config.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        raise ValueError("H3 root config must declare non-empty conditions")
    frames = []
    input_files = [root_config_path]
    for root_condition in sorted(conditions, key=lambda item: item.get("condition_id", item.get("id", ""))):
        condition_id = root_condition.get("condition_id", root_condition.get("id"))
        condition_dir = data_dir / condition_id
        config_path = condition_dir / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        sequences = config.get("sequences")
        if not isinstance(sequences, list) or len(sequences) != 1:
            raise ValueError(f"{condition_id}: expected exactly one H3 sequence")
        sequence = sequences[0]
        count = sequence.get("num_frames")
        width, height = sequence.get("width"), sequence.get("height")
        drops = set(sequence.get("dropped_frame_ids", []))
        if type(count) is not int or count <= 0 or type(width) is not int or type(height) is not int:
            raise ValueError(f"{condition_id}: invalid H3 frame dimensions/count")
        if any(type(fid) is not int or not 0 <= fid < count for fid in drops):
            raise ValueError(f"{condition_id}: invalid dropped frame id")
        frame_dir = condition_dir / "frames"
        expected_names = {f"frame_{fid:04d}.png" for fid in range(count) if fid not in drops}
        actual_names = {path.name for path in frame_dir.glob("*.png")}
        if actual_names != expected_names:
            raise ValueError(f"{condition_id}: image files disagree with sequence config")
        input_files.append(config_path)
        for frame_id in range(count):
            if frame_id in drops:
                continue
            path = frame_dir / f"frame_{frame_id:04d}.png"
            input_files.append(path)
            frames.append({
                "condition_id": condition_id,
                "frame_id": frame_id,
                "path": path,
                "width": width,
                "height": height,
            })
    hashes = {}
    for path in sorted(set(input_files)):
        relative = path.relative_to(data_dir).as_posix()
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return frames, hashes


def _aggregate_capacity(rows: list[dict], profile: dict, mode: str) -> dict:
    reasons: dict[str, int] = {}
    examples = []
    for row in rows:
        reason = row["rejection_reason"]
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
            examples.append({
                "condition_id": row["condition_id"],
                "frame_id": row["frame_id"],
                "reason": reason,
                "run_count": row["run_count"],
                "max_runs_per_row": row["max_runs_per_row"],
                "active_peak": row["active_peak"],
                "completed_before_reject": row["completed_before_reject"],
            })
    compared = [row for row in rows if row["opencv_compared"]]
    return {
        "morphology": mode,
        "capacity_profile": profile["name"],
        "row_run_cap": profile["row_run_cap"],
        "active_cap": profile["active_cap"],
        "record_cap": profile["record_cap"],
        "frame_count": len(rows),
        "accepted_frames": sum(row["accepted"] for row in rows),
        "rejected_frames": len(rows) - sum(row["accepted"] for row in rows),
        "rejections_by_reason": dict(sorted(reasons.items())),
        "max_runs_per_row": max((row["max_runs_per_row"] for row in rows), default=0),
        "total_run_count": sum(row["run_count"] for row in rows),
        "max_active_peak_observed": max((row["active_peak"] for row in rows), default=0),
        "max_completed_regions_per_frame": max(
            (row["completed_regions_per_frame"] for row in rows), default=0
        ),
        "max_completed_before_reject": max(
            (row["completed_before_reject"] for row in rows), default=0
        ),
        "comparison_count": sum(row["comparison_count"] for row in rows),
        "opencv_filtered_components": sum(row["opencv_filtered_components"] for row in rows),
        "opencv_max_filtered_components_per_frame": max(
            (row["opencv_filtered_components"] for row in rows), default=0
        ),
        "opencv_exact_frames": sum(row["opencv_exact"] is True for row in compared),
        "opencv_mismatched_frames": sum(row["opencv_exact"] is False for row in compared),
        "opencv_withheld_rejected_frames": len(rows) - len(compared),
        "exact_candidate_components_compared": sum(
            row["candidate_components"] for row in compared if row["opencv_exact"] is True
        ),
        "failure_examples": examples,
    }


def run_h3_capacity_sweep(
    data_dir: Path,
    out_dir: Path,
    *,
    capacities: tuple[int, ...] = (20, 32, 64),
    extra_profiles: tuple[tuple[str, int, int, int], ...] = (("run64_active32_record32", 64, 32, 32),),
    threshold: int = 128,
    min_area: int = 16,
    max_width: int = 24,
) -> dict:
    """Evaluate image-derived H3 masks with bounded capacities and OpenCV checks."""
    data_dir, out_dir = Path(data_dir).resolve(), Path(out_dir).resolve()
    if not data_dir.is_dir():
        raise ValueError(f"H3 dataset does not exist: {data_dir}")
    if data_dir == out_dir or data_dir in out_dir.parents:
        raise ValueError("output directory must stay outside the synthetic input tree")
    if out_dir.exists():
        raise ValueError(f"output directory already exists: {out_dir}")
    if type(threshold) is not int or not 0 <= threshold <= 255:
        raise ValueError("threshold must be an integer in 0..255")
    min_area = _validate_min_area(min_area)
    if type(max_width) is not int or max_width < 0:
        raise ValueError("max_width must be a non-negative integer")
    if not capacities or any(type(value) is not int or value <= 0 for value in capacities):
        raise ValueError("capacities must contain positive integers")

    frames, input_hashes = _dataset_frames(data_dir)
    out_dir.mkdir(parents=True, exist_ok=False)
    profiles = [
        {"name": f"uniform_{capacity}", "row_run_cap": capacity,
         "active_cap": capacity, "record_cap": capacity}
        for capacity in capacities
    ]
    profiles.extend({
        "name": name,
        "row_run_cap": row,
        "active_cap": active,
        "record_cap": records,
    } for name, row, active, records in extra_profiles)
    all_rows = []
    summaries = []
    for mode, morph_mode in _MORPH_MODES:
        mode_rows = []
        for frame in frames:
            with Image.open(frame["path"]) as image:
                gray = np.array(image.convert("L"), dtype=np.uint8, copy=True)
            if gray.shape != (frame["height"], frame["width"]):
                raise ValueError(f"{frame['path']}: PNG dimensions differ from H3 config")
            binary = preprocess(gray, threshold=threshold, morph_mode=morph_mode)
            for profile in profiles:
                caps = RunCaps(profile["row_run_cap"], profile["active_cap"], profile["record_cap"])
                result = extract_run_frame(
                    binary, caps=caps, min_area=min_area, max_width=max_width
                )
                comparison = compare_with_opencv(binary, result)
                record = {
                    "condition_id": frame["condition_id"],
                    "frame_id": frame["frame_id"],
                    "morphology": mode,
                    "capacity_profile": profile["name"],
                    "row_run_cap": caps.row_run_cap,
                    "active_cap": caps.active_cap,
                    "record_cap": caps.record_cap,
                    "accepted": result.accepted,
                    "rejection_reason": result.rejection_reason,
                    "run_count": result.stats["run_count"],
                    "max_runs_per_row": result.stats["max_runs_per_row"],
                    "active_peak": result.stats["active_peak"],
                    "completed_regions_per_frame": result.stats["completed_regions_per_frame"],
                    "completed_before_reject": result.stats["completed_before_reject"],
                    "comparison_count": result.stats["comparison_count"],
                    "opencv_filtered_components": comparison["opencv_filtered_components"],
                    "opencv_compared": comparison["compared"],
                    "opencv_exact": comparison["exact"],
                    "candidate_components": comparison["candidate_components"],
                    "opencv_components": comparison["opencv_components"],
                }
                all_rows.append(record)
                mode_rows.append(record)
        for capacity in capacities:
            profile = next(item for item in profiles if item["name"] == f"uniform_{capacity}")
            selected = [row for row in mode_rows if row["capacity_profile"] == profile["name"]]
            summaries.append(_aggregate_capacity(selected, profile, mode))
        for profile in profiles[len(capacities):]:
            selected = [row for row in mode_rows if row["capacity_profile"] == profile["name"]]
            summaries.append(_aggregate_capacity(selected, profile, mode))

    csv_path = out_dir / "frame_capacity.csv"
    fieldnames = list(all_rows[0]) if all_rows else []
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)
    (out_dir / "input_sha256.json").write_text(
        json.dumps(input_hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    summary = {
        "dataset": "H3 image-derived masks only; GT annotations and object IDs are not loaded",
        "image_frames": len(frames),
        "dropped_frames_not_synthesized": True,
        "threshold": threshold,
        "foreground_encoding": "0xFF; background 0x00",
        "min_area": min_area,
        "max_area": _MAX_AREA,
        "max_width_flag": max_width,
        "morphology_modes": {name: value for name, value in _MORPH_MODES},
        "capacity_sweep": list(capacities),
        "additional_capacity_profiles": profiles[len(capacities):],
        "capacity_semantics": "uniform sweep sets all three caps equally; additional profiles keep them independent",
        "connectivity": 8,
        "merge_policy": "multiple distinct predecessor slots reject the complete frame; no union-find",
        "frame_output_policy": "any rejected frame returns zero feature records",
        "opencv_comparison": "accepted frames compare bbox, area, flags and exact integer first moments; rejected frames are withheld",
        "input_sha256": input_hashes,
        "synthetic_envelope_case": evaluate_hollow_target_envelope(),
        "runs": summaries,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data" / "synthetic" / "h3_full")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--capacities", type=int, nargs="+", default=(20, 32, 64))
    parser.add_argument("--threshold", type=int, default=128)
    parser.add_argument("--min-area", type=int, default=16)
    parser.add_argument("--max-width", type=int, default=24)
    return parser


def evaluate_hollow_target_envelope() -> dict:
    """Separate capacity stress mask: twenty disjoint 3-pixel-wall hollow targets."""
    target_count = 20
    pitch, box, height = 11, 9, 9
    binary = np.zeros((height, pitch * target_count), dtype=np.uint8)
    for index in range(target_count):
        x0 = index * pitch
        binary[:, x0:x0 + box] = 0xFF
        binary[3:6, x0 + 3:x0 + 6] = 0x00
    result = extract_run_frame(binary, caps=RunCaps(64, 32, 32), min_area=16, max_width=24)
    comparison = compare_with_opencv(binary, result)
    return {
        "scope": "separate synthetic stress case; excluded from H3 frame totals",
        "target_count_by_generator": target_count,
        "shape": "20 disjoint 9x9 rings, 3-pixel wall, all in one image row band",
        "capacity_profile": {"row_run_cap": 64, "active_cap": 32, "record_cap": 32},
        "accepted": result.accepted,
        "rejection_reason": result.rejection_reason,
        "regions": len(result.regions),
        "stats": result.stats,
        "opencv_comparison": comparison,
        "observed_row_run_peak": result.stats["max_runs_per_row"],
        "observed_active_peak": result.stats["active_peak"],
        "observed_record_count": result.stats["completed_regions_per_frame"],
    }


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = run_h3_capacity_sweep(
            args.data,
            args.out,
            capacities=tuple(args.capacities),
            threshold=args.threshold,
            min_area=args.min_area,
            max_width=args.max_width,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "image_frames": summary["image_frames"],
        "capacity_sweep": summary["capacity_sweep"],
        "runs": [
            {key: value for key, value in row.items() if key != "failure_examples"}
            for row in summary["runs"]
        ],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
