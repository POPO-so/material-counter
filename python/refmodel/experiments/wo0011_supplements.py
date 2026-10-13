"""Small, deterministic image ablations supplementing WO-0011 H3 analysis.

The generated scenes are independent of the frozen H3 generator.  Candidate
filters run on Gray8/Binary8 arrays, and only Region observations enter the
tracker.  Ground-truth matching and event scoring happen after each update.
"""

from dataclasses import dataclass
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[3]
PYTHON_ROOT = ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from refmodel.ps_reference.regions import extract_regions, preprocess
from refmodel.ps_reference.tracking import Tracker, TrackerConfig
from refmodel.ps_reference.evaluate_h3 import (
    derive_ground_truth_events,
    match_regions_to_ground_truth,
    score_count_events,
)


STRATEGIES = ("bypass", "erosion", "dilation", "opening", "hole_fill")
SEED = 20261013
THRESHOLD = 128
MIN_AREA = 16
LINE_X = 100.0
HYSTERESIS = 2.0
_KERNEL = np.ones((3, 3), dtype=np.uint8)


@dataclass(frozen=True)
class Scenario:
    case_id: str
    shapes: tuple[str, ...]
    start_x: tuple[int, ...]
    center_y: tuple[int, ...]
    frame_count: int = 32
    speed: int = 2
    gap_frames: tuple[int, ...] = ()
    bridge: bool = False


def _shape_mask(shape: str) -> np.ndarray:
    if shape == "square7":
        return np.ones((7, 7), dtype=np.uint8)
    if shape == "small3":
        return np.ones((3, 3), dtype=np.uint8)
    if shape == "ring15_t3":
        result = np.ones((15, 15), dtype=np.uint8)
        result[3:12, 3:12] = 0
        return result
    if shape == "ring13_t1":
        result = np.ones((13, 13), dtype=np.uint8)
        result[1:-1, 1:-1] = 0
        return result
    raise ValueError(f"unsupported synthetic target shape: {shape}")


def scenarios() -> tuple[Scenario, ...]:
    """Return the fixed case set; all targets move through the same line."""

    cases = [
        Scenario("separated_rectangles", ("square7", "square7"), (60, 79), (52, 52)),
        Scenario("contact_rectangles", ("square7", "square7"), (60, 67), (52, 52)),
        Scenario("thin_bridge", ("square7", "square7"), (60, 70), (52, 52), bridge=True),
        Scenario("thick_ring", ("ring15_t3",), (60,), (52,)),
        Scenario("thin_ring", ("ring13_t1",), (60,), (52,)),
        Scenario("boundary_target", ("square7",), (2,), (52,), frame_count=61),
        Scenario("below_min_area", ("small3",), (60,), (52,)),
        Scenario("projection_overlap_rows", ("square7", "square7"), (60, 60), (42, 78)),
    ]
    for gap in (1, 2, 3):
        cases.append(Scenario(
            f"gap_{gap}px", ("square7", "square7"), (60, 60 + 7 + gap), (52, 52)
        ))
    for missing in (1, 2, 3):
        first = 20
        cases.append(Scenario(
            f"interrupt_{missing}frames", ("square7",), (60,), (52,),
            gap_frames=tuple(range(first, first + missing)),
        ))
    return tuple(cases)


def _object_geometry(shape: np.ndarray, center_x: int, center_y: int, width: int, height: int):
    ys, xs = np.nonzero(shape)
    top = center_y - shape.shape[0] // 2
    left = center_x - shape.shape[1] // 2
    x0 = max(0, left + int(xs.min()))
    x1 = min(width - 1, left + int(xs.max()))
    y0 = max(0, top + int(ys.min()))
    y1 = min(height - 1, top + int(ys.max()))
    return x0, x1, y0, y1


def _draw_mask(image: np.ndarray, mask: np.ndarray, center_x: int, center_y: int) -> None:
    height, width = image.shape
    top = center_y - mask.shape[0] // 2
    left = center_x - mask.shape[1] // 2
    dst_x0, dst_y0 = max(0, left), max(0, top)
    dst_x1, dst_y1 = min(width, left + mask.shape[1]), min(height, top + mask.shape[0])
    if dst_x0 >= dst_x1 or dst_y0 >= dst_y1:
        return
    src_x0, src_y0 = dst_x0 - left, dst_y0 - top
    src_x1, src_y1 = src_x0 + dst_x1 - dst_x0, src_y0 + dst_y1 - dst_y0
    patch = mask[src_y0:src_y1, src_x0:src_x1].astype(bool)
    view = image[dst_y0:dst_y1, dst_x0:dst_x1]
    view[patch] = 220


def _make_sequence(case: Scenario, case_index: int) -> tuple[list[np.ndarray], list[dict]]:
    width, height = 200, 120
    masks = [_shape_mask(shape) for shape in case.shapes]
    images, gt_frames = [], []
    for frame_id in range(case.frame_count):
        # Seeded low-level sensor grain remains below threshold in every mode.
        rng = np.random.default_rng(SEED + case_index * 1009 + frame_id)
        gray = rng.integers(0, 32, size=(height, width), dtype=np.uint8)
        objects = []
        centers = [start + case.speed * frame_id for start in case.start_x]
        for index, (mask, center_x, center_y) in enumerate(zip(masks, centers, case.center_y), start=1):
            visible = frame_id not in case.gap_frames
            if visible:
                _draw_mask(gray, mask, center_x, center_y)
            x0, x1, y0, y1 = _object_geometry(mask, center_x, center_y, width, height)
            objects.append({
                "frame_id": frame_id,
                "object_id": index,
                "x_min": x0,
                "x_max": x1,
                "y_min": y0,
                "y_max": y1,
                "centroid_x": float(center_x),
                "centroid_y": float(center_y),
                "visible": visible,
            })
        # The one-pixel bridge is an imaging artifact, not a truth object.
        if case.bridge:
            x_left, x_right = sorted(centers)
            y = case.center_y[0]
            left_edge = x_left + masks[0].shape[1] // 2
            right_edge = x_right - masks[1].shape[1] // 2
            gray[y, left_edge + 1:right_edge] = 220
        images.append(gray)
        gt_frames.append({"frame_id": frame_id, "objects": objects})
    return images, gt_frames


def _fill_enclosed_holes(binary: np.ndarray) -> np.ndarray:
    padded = np.pad(binary, 1, mode="constant", constant_values=0)
    background = np.where(padded == 0, 255, 0).astype(np.uint8)
    cv2.floodFill(background, None, (0, 0), 127, flags=4)
    holes = background[1:-1, 1:-1] == 255
    filled = binary.copy()
    filled[holes] = 255
    return filled


def _hole_stats(binary: np.ndarray) -> tuple[int, int]:
    padded = np.pad(binary, 1, mode="constant", constant_values=0)
    background = np.where(padded == 0, 255, 0).astype(np.uint8)
    cv2.floodFill(background, None, (0, 0), 127, flags=4)
    holes = background[1:-1, 1:-1] == 255
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        holes.astype(np.uint8), connectivity=4, ltype=cv2.CV_32S
    )
    hole_count = max(0, count - 1)
    return hole_count, int(sum(stats[label, cv2.CC_STAT_AREA] for label in range(1, count)))


def _apply_strategy(gray: np.ndarray, strategy: str) -> np.ndarray:
    if strategy == "bypass":
        return preprocess(gray, THRESHOLD, morph_mode=0)
    if strategy == "erosion":
        return preprocess(gray, THRESHOLD, morph_mode=1)
    if strategy == "dilation":
        return preprocess(gray, THRESHOLD, morph_mode=2)
    binary = preprocess(gray, THRESHOLD, morph_mode=0)
    if strategy == "opening":
        eroded = cv2.erode(binary, _KERNEL, borderType=cv2.BORDER_CONSTANT, borderValue=0)
        return cv2.dilate(eroded, _KERNEL, borderType=cv2.BORDER_CONSTANT, borderValue=0)
    if strategy == "hole_fill":
        return _fill_enclosed_holes(binary)
    raise ValueError(f"unsupported candidate strategy: {strategy}")


def _evaluate_case(case: Scenario, case_index: int, strategy: str) -> dict:
    images, gt_frames = _make_sequence(case, case_index)
    truth = derive_ground_truth_events(gt_frames, LINE_X, HYSTERESIS, direction=1)
    tracker = Tracker(TrackerConfig(line_x=LINE_X, hysteresis=HYSTERESIS))
    predicted_events = []
    frame_trace = []
    feature_totals = {"feature_tp": 0, "feature_fn": 0, "feature_fp": 0}

    for frame_id, gray in enumerate(images):
        binary = _apply_strategy(gray, strategy)
        if not np.isin(binary, (0, 255)).all():
            raise AssertionError("candidate preprocessing must preserve Binary8 00/FF encoding")
        regions = extract_regions(binary, THRESHOLD, MIN_AREA, morph_mode=0, max_width=24)

        # This is the full inference step. No GT identity/visibility reaches Tracker.
        events = tracker.update(frame_id, regions)

        # Evaluation-only identity matching occurs after detector and tracker work.
        visible_objects = gt_frames[frame_id]["objects"]
        matches, feature_metrics = match_regions_to_ground_truth(regions, visible_objects)
        for key in feature_totals:
            feature_totals[key] += feature_metrics[key]
        assignment_by_track = {track_id: region_index for track_id, region_index in tracker.last_assignments}
        for event in events:
            region_index = assignment_by_track.get(event.track_id)
            match = matches.get(region_index) if region_index is not None else None
            predicted_events.append({
                "object_id": match["object_id"] if match else None,
                "frame_id": event.frame_id,
            })

        hole_count, hole_area = _hole_stats(binary)
        frame_trace.append({
            "frame_id": frame_id,
            "regions": [
                {"bbox": list(region.bbox), "area": region.area,
                 "centroid_x": region.centroid_x, "centroid_y": region.centroid_y,
                 "flags": region.flags}
                for region in regions
            ],
            "hole_count": hole_count,
            "hole_area": hole_area,
            "edge_region_count": sum(bool(region.flags & 2) for region in regions),
            "feature_metrics": feature_metrics,
            "predicted_events": [
                {"track_id": event.track_id, "frame_id": event.frame_id}
                for event in events
            ],
        })

    count_metrics = score_count_events(truth, predicted_events)
    first = frame_trace[0]
    return {
        "candidate_chain_status": "offline_only_not_in_production_chain"
        if strategy in ("opening", "hole_fill") else "existing_preprocess_mode",
        "first_observed_region_count": len(first["regions"]),
        "first_observed_region_areas": [row["area"] for row in first["regions"]],
        "first_observed_hole_count": first["hole_count"],
        "first_observed_hole_area": first["hole_area"],
        "first_observed_edge_region_count": first["edge_region_count"],
        **feature_totals,
        **count_metrics,
        "trace": frame_trace,
    }


def _validate_output_path(out_dir: Path) -> Path:
    """Resolve a new evidence path and protect the frozen synthetic inputs."""

    resolved = Path(out_dir).resolve()
    frozen_root = (ROOT / "data" / "synthetic").resolve()
    if resolved == frozen_root or frozen_root in resolved.parents:
        raise ValueError(f"output path is inside the frozen synthetic data tree: {resolved}")
    if resolved.exists():
        raise ValueError(f"output directory already exists: {resolved}")
    return resolved


def run_supplements(out_dir: Path | None = None) -> dict:
    """Run every synthetic case under each strategy; optionally write new evidence."""

    if out_dir is not None:
        _validate_output_path(out_dir)
    case_rows = []
    for index, case in enumerate(scenarios()):
        case_rows.append({
            "case_id": case.case_id,
            "physical_object_count": len(case.shapes),
            "frame_count": case.frame_count,
            "synthetic_missing_frames": list(case.gap_frames),
            "truth_frame_observation_gaps": list(case.gap_frames),
            "strategies": {
                strategy: _evaluate_case(case, index, strategy)
                for strategy in STRATEGIES
            },
        })
    result = {
        "work_order": "WO-0011",
        "scope": "independent synthetic supplement; not frozen H3 or real washer/nut accuracy",
        "parameters": {
            "threshold": THRESHOLD,
            "foreground_rule": "Gray >= threshold -> FF; otherwise 00",
            "min_area": MIN_AREA,
            "connectivity": 8,
            "morphology_border": "outside-image pixels are background 00",
            "line_x": LINE_X,
            "hysteresis": HYSTERESIS,
            "direction": "+X",
            "confirm_hits": 2,
            "max_missing": 2,
            "max_step": 6.0,
            "max_y": 8.0,
            "random_seed": SEED,
            "image_size": [200, 120],
        },
        "protocol": {
            "event_frame_rule": "first visible frame at or after strict hysteresis qualification",
            "event_scorer": "WO-0010 score_count_events; exact event frame and object identity",
            "feature_scorer": "WO-0010 one-to-one bbox IoU >= 0.5, after inference",
            "gt_identity_to_tracker": False,
            "gt_future_to_tracker": False,
            "opening_and_hole_fill": "isolated PC candidates; not production morphology RTL",
        },
        "cases": case_rows,
    }
    digest_input = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
    result["trace_sha256"] = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    if out_dir is not None:
        _write_results(Path(out_dir), result)
    return result


def _write_results(out_dir: Path, result: dict) -> None:
    out_dir = _validate_output_path(out_dir)
    out_dir.mkdir(parents=True)
    (out_dir / "summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    (out_dir / "trace.json").write_text(
        json.dumps({"cases": result["cases"]}, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    with (out_dir / "cases.csv").open("w", newline="", encoding="utf-8") as stream:
        fields = [
            "case_id", "strategy", "physical_object_count", "first_observed_region_count",
            "first_observed_region_areas", "first_observed_hole_count", "first_observed_hole_area",
            "first_observed_edge_region_count", "feature_tp", "feature_fn", "feature_fp",
            "qualified_truth_count", "event_tp", "event_fn", "duplicate_fp", "spurious_fp",
            "qualified_precision", "qualified_recall",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for case in result["cases"]:
            for strategy, row in case["strategies"].items():
                writer.writerow({
                    "case_id": case["case_id"],
                    "strategy": strategy,
                    "physical_object_count": case["physical_object_count"],
                    **{field: json.dumps(row[field], separators=(",", ":"))
                       if isinstance(row[field], list) else row[field] for field in fields[3:]},
                })


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "results" / "wo0011-supplements-20261013",
        help="new output directory (must not already exist)",
    )
    args = parser.parse_args(argv)
    result = run_supplements(args.out)
    print(json.dumps({
        "case_count": len(result["cases"]),
        "strategy_count": len(STRATEGIES),
        "trace_sha256": result["trace_sha256"],
        "output_directory": str(args.out.resolve()),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
