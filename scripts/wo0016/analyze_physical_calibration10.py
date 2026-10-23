#!/usr/bin/env python3
"""Read-only offline geometry and track-feature analysis for WO-0017 -10.

This tool reads the copied capture/evidence package and writes only to a new
analysis directory. It never connects to a board or alters source evidence.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import inspect
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from stream_counter import Detection, MaskCounter  # noqa: E402


RAW_NAME = "frames_gray8_binary8.npz"
SOURCE_NAME = "frames_source640x400.npz"
EXPECTED_PHYSICAL_COUNT = 12
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_array(array: np.ndarray) -> str:
    contiguous = np.ascontiguousarray(array)
    return hashlib.sha256(memoryview(contiguous)).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_sync_manifest(bundle_root: Path) -> dict[str, Any]:
    manifest_path = bundle_root / "_local_sync" / "sha256-manifest.json"
    sync = read_json(manifest_path)
    files = sync.get("files")
    if not isinstance(files, list):
        raise ValueError("local sync manifest has no files list")
    failures = []
    for item in files:
        path = Path(item["local_path"])
        if not path.is_absolute():
            path = (bundle_root / path).resolve()
        try:
            path.resolve().relative_to(bundle_root.resolve())
        except ValueError as exc:
            raise ValueError(f"sync manifest path escapes evidence bundle: {path}") from exc
        if (not path.is_file() or path.stat().st_size != item["size"]
                or sha256_file(path) != item["sha256"]
                or item.get("sha256_match") is not True):
            failures.append(str(item.get("remote_relative_path", path)))
    expected = int(sync.get("expected_files", -1))
    if (failures or len(files) != expected
            or int(sync.get("downloaded_files", -1)) != expected
            or int(sync.get("sha256_matches", -1)) != expected
            or bool(sync.get("missing"))
            or bool(sync.get("mismatches"))):
        raise ValueError(f"evidence sync verification failed: {failures[:5]}")
    return {"manifest": str(manifest_path), "files_verified": len(files),
            "expected_bytes": sync.get("expected_bytes"),
            "downloaded_bytes": sync.get("downloaded_bytes"),
            "boot_id": sync.get("boot_id")}


def verify_capture(capture_dir: Path, bundle_root: Path) -> tuple[
        dict[str, Any], np.ndarray, np.ndarray, list[dict[str, Any]], np.ndarray, list[int]]:
    manifest = read_json(capture_dir / "manifest.json")
    if manifest.get("status") != "COMPLETE" or manifest.get("writer_active") is not False:
        raise ValueError("capture manifest is not COMPLETE and writer-stopped")
    expected = manifest.get("expected_frames")
    if expected != 300 or manifest.get("captured_frames") != 300:
        raise ValueError(f"expected 300 complete frames, got {expected}/{manifest.get('captured_frames')}")
    records_path = capture_dir / "frame-records.jsonl"
    records_bytes = records_path.read_bytes()
    records_hash = hashlib.sha256(records_bytes).hexdigest()
    if manifest.get("frame_records_sha256") != records_hash:
        raise ValueError("frame-records.jsonl SHA-256 disagrees with capture manifest")
    records = [json.loads(line) for line in records_bytes.splitlines() if line.strip()]
    if len(records) != expected or [r.get("frame_id") for r in records] != list(range(expected)):
        raise ValueError("frame-records frame count or contiguous IDs are invalid")
    config = manifest.get("config")
    if not isinstance(config, dict):
        raise ValueError("manifest config is missing")

    raw_path = capture_dir / RAW_NAME
    raw_sidecar = raw_path.with_suffix(".npz.sha256")
    raw_archive = manifest.get("raw_frame_archive") or {}
    if (not raw_path.is_file() or not raw_sidecar.is_file()
            or sha256_file(raw_path) != raw_archive.get("sha256")
            or raw_path.stat().st_size != raw_archive.get("size_bytes")):
        raise ValueError("Gray8/Binary8 NPZ SHA or size does not match manifest")
    raw_sidecar_text = raw_sidecar.read_text(encoding="ascii").strip()
    if raw_sidecar_text != f"{sha256_file(raw_path)}  {RAW_NAME}":
        raise ValueError("Gray8/Binary8 NPZ sidecar does not match archive")
    with np.load(raw_path, allow_pickle=False) as stored:
        gray = np.array(stored["raw_gray8"], copy=True)
        binary = np.array(stored["raw_binary8"], copy=True)
        embedded = json.loads(str(stored["archive_metadata_json"].item()))
    if (gray.shape != (300, 240, 320) or gray.dtype != np.uint8
            or binary.shape != gray.shape or binary.dtype != np.uint8):
        raise ValueError("raw NPZ arrays have unexpected shape or dtype")
    gray_hash, binary_hash = sha256_array(gray), sha256_array(binary)
    if (gray_hash != raw_archive.get("raw_gray8_sha256")
            or binary_hash != raw_archive.get("raw_binary8_sha256")
            or embedded.get("raw_gray8_sha256") != gray_hash
            or embedded.get("raw_binary8_sha256") != binary_hash):
        raise ValueError("raw pixel-array SHA does not match manifest/embedded metadata")
    if not np.isin(binary, (0, 255)).all():
        raise ValueError("Binary8 archive contains values outside {0,255}")

    source_path = capture_dir / SOURCE_NAME
    source_sidecar = source_path.with_suffix(".npz.sha256")
    source_meta = manifest.get("source_frame_archive") or {}
    if (not source_path.is_file() or not source_sidecar.is_file()
            or sha256_file(source_path) != source_meta.get("sha256")
            or source_path.stat().st_size != source_meta.get("size_bytes")):
        raise ValueError("640x400 source NPZ SHA or size does not match manifest")
    source_sidecar_text = source_sidecar.read_text(encoding="ascii").strip()
    if source_sidecar_text != f"{sha256_file(source_path)}  {SOURCE_NAME}":
        raise ValueError("640x400 source NPZ sidecar does not match archive")
    with np.load(source_path, allow_pickle=False) as stored:
        source_ids_array = np.array(stored["source_frame_ids"], copy=True)
        source_gray = np.array(stored["source_gray8"], copy=True)
        source_embedded = json.loads(str(stored["archive_metadata_json"].item()))
    source_ids = [int(value) for value in source_ids_array.tolist()]
    if (source_gray.shape != (18, 400, 640) or source_gray.dtype != np.uint8
            or source_ids != manifest.get("source_frame_ids")
            or source_ids != source_meta.get("frame_ids")
            or source_embedded.get("frame_ids") != source_ids
            or sha256_array(source_gray) != source_meta.get("source_gray8_sha256")
            or sha256_array(source_gray) != source_embedded.get("source_gray8_sha256")):
        raise ValueError("source NPZ IDs, geometry, dtype, or pixel SHA mismatch")

    # Frame records carry per-frame pixel hashes; also tie each record to its arrays.
    for frame_id, record in enumerate(records):
        metadata = record.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("frame_id") != frame_id:
            raise ValueError(f"frame {frame_id} metadata missing or misindexed")
        if (metadata.get("algorithm_config") != config
                and metadata.get("algorithm_config") != config.get("counter_config")):
            # Some capture schema revisions wrap the counter config at top level.
            effective = {key: value for key, value in config.items()
                         if key not in {"algorithm", "threshold", "input_resolution",
                                        "mask_resolution", "roi_bounds_convention",
                                        "counter_config", "frame_geometry"}}
            if metadata.get("algorithm_config") != effective:
                raise ValueError(f"frame {frame_id} algorithm config differs from manifest")
        if (record.get("gray_sha256") != sha256_array(gray[frame_id])
                or record.get("mask_sha256") != sha256_array(binary[frame_id])):
            raise ValueError(f"frame {frame_id} NPZ pixels differ from frame-record hashes")
    return manifest, gray, binary, records, source_gray, source_ids


def det_dict(value: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    return dict(value)


def number_summary(values: list[float | int]) -> dict[str, Any]:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not clean:
        return {"n": 0, "min": None, "mean": None, "max": None}
    return {"n": len(clean), "min": min(clean), "mean": statistics.fmean(clean),
            "max": max(clean)}


def track_scope_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    raw_areas = [area for row in rows for area in row.get("raw_component_areas_px", [])]
    merges = [row for row in rows if row.get("fragment_merge")]
    centroid_y = [row["centroid_y"] for row in rows if row.get("centroid_y") is not None]
    bbox_y_min = [row["bbox_y_min"] for row in rows if row.get("bbox_y_min") is not None]
    bbox_y_max = [row["bbox_y_max"] for row in rows if row.get("bbox_y_max") is not None]
    border_rows = [row for row in rows if row.get("touch_active_border")]
    union_rows = [row for row in rows if int(row.get("raw_component_count", 0)) > 1]
    union_operations = sum(int(row["raw_component_count"]) - 1 for row in union_rows)
    return {
        "associated_frames": len(rows),
        "merged_area_px": number_summary([row["area_px"] for row in rows]),
        "merged_bbox_width_px": number_summary([row["bbox_width_px"] for row in rows]),
        "merged_bbox_height_px": number_summary([row["bbox_height_px"] for row in rows]),
        "centroid_y_px": number_summary(centroid_y),
        "centroid_y_range_px": ([min(centroid_y), max(centroid_y)] if centroid_y else None),
        "bbox_y_min_px": number_summary(bbox_y_min),
        "bbox_y_max_px": number_summary(bbox_y_max),
        "bbox_y_extent_px": ([min(bbox_y_min), max(bbox_y_max)]
                             if bbox_y_min and bbox_y_max else None),
        "touch_active_border_observation_count": len(border_rows),
        "touch_active_border_frames": [row["frame_id"] for row in border_rows],
        "raw_ccl_component_area_px": number_summary(raw_areas),
        "raw_ccl_component_count": len(raw_areas),
        "fragment_merge_frame_count": len(merges),
        "fragment_merge_frames": [row["frame_id"] for row in merges],
        "fragment_merge_union_operation_count": union_operations,
        "fragment_merge_union_operations_by_frame": [
            {"frame_id": row["frame_id"],
             "union_operations": int(row["raw_component_count"]) - 1}
            for row in union_rows],
    }


def row_profile(frames: np.ndarray) -> list[dict[str, float | int]]:
    frame_medians = np.median(frames, axis=2)
    frame_means = np.mean(frames, axis=2)
    frame_p10 = np.percentile(frames, 10, axis=2)
    frame_p90 = np.percentile(frames, 90, axis=2)
    gradients = np.abs(np.diff(frames.astype(np.int16), axis=1))
    med_gradient = np.median(gradients, axis=(0, 2))
    mean_gradient = np.mean(gradients, axis=(0, 2))
    result = []
    for row in range(frames.shape[1]):
        grad_index = min(row, len(med_gradient) - 1)
        result.append({
            "row": row,
            "mean_luma": float(np.mean(frame_means[:, row])),
            "median_luma": float(np.median(frame_medians[:, row])),
            "median_frame_p10": float(np.median(frame_p10[:, row])),
            "median_frame_p90": float(np.median(frame_p90[:, row])),
            "median_abs_vertical_gradient": float(med_gradient[grad_index]),
            "mean_abs_vertical_gradient": float(mean_gradient[grad_index]),
        })
    return result


def candidate_edges(profile: list[dict[str, Any]], start: int, stop: int,
                    count: int = 12) -> list[dict[str, Any]]:
    values = [float(row["median_abs_vertical_gradient"]) for row in profile]
    local_maxima = []
    for row in range(max(1, start), min(stop, len(values) - 1)):
        if values[row] >= values[row - 1] and values[row] >= values[row + 1]:
            local_maxima.append({"row": row, "median_abs_vertical_gradient": values[row]})
    return sorted(local_maxima, key=lambda item: (-item["median_abs_vertical_gradient"],
                                                    item["row"]))[:count]


def write_profile_csv(path: Path, profile: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(profile[0]))
        writer.writeheader()
        writer.writerows(profile)


def contact_sheet(frames: np.ndarray, frame_ids: list[int], path: Path, *,
                  title: str, tile_width: int, tile_height: int,
                  output_mapping: bool = False) -> None:
    columns = 6
    label_height = 28
    rows = math.ceil(len(frame_ids) / columns)
    sheet = np.zeros((rows * (tile_height + label_height),
                      columns * tile_width, 3), dtype=np.uint8)
    for index, frame_id in enumerate(frame_ids):
        row, col = divmod(index, columns)
        y0 = row * (tile_height + label_height)
        x0 = col * tile_width
        cv2.rectangle(sheet, (x0, y0), (x0 + tile_width - 1, y0 + label_height - 1),
                      (30, 30, 30), thickness=-1)
        cv2.putText(sheet, f"frame {frame_id}", (x0 + 6, y0 + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (245, 245, 245), 1, cv2.LINE_AA)
        image = frames[index]
        image = cv2.resize(image, (tile_width, tile_height),
                           interpolation=cv2.INTER_NEAREST)
        sheet[y0 + label_height:y0 + label_height + tile_height,
              x0:x0 + tile_width] = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        if output_mapping:
            # Mark the letterbox padding transition only; these are not asserted
            # to be measured conveyor boundaries.
            base_y = y0 + label_height
            for mark_y in (20, 220):
                cv2.line(sheet, (x0, base_y + mark_y),
                         (x0 + tile_width - 1, base_y + mark_y), (0, 80, 255), 1)
    cv2.putText(sheet, title, (8, sheet.shape[0] - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 0), 1, cv2.LINE_AA)
    if not cv2.imwrite(str(path), sheet):
        raise OSError(f"failed to write contact sheet: {path}")


def profile_plot(source_profile: list[dict[str, Any]],
                 mapped_profile: list[dict[str, Any]], path: Path) -> None:
    width, height, left, right, top, bottom = 1500, 850, 90, 700, 70, 790
    image = np.full((height, width, 3), 248, dtype=np.uint8)
    cv2.putText(image, "Source 640x400: row luminance and stable vertical gradient",
                (left, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.68, (20, 20, 20), 1, cv2.LINE_AA)
    cv2.putText(image, "Mapped 320x240: row luminance and stable vertical gradient",
                (right + 45, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.68, (20, 20, 20), 1, cv2.LINE_AA)
    for x0, prof in ((left, source_profile), (right + 45, mapped_profile)):
        panel_width = right - left
        panel_height = bottom - top
        cv2.rectangle(image, (x0, top), (x0 + panel_width, bottom), (220, 220, 220), 1)
        max_row = max(1, len(prof) - 1)
        max_grad = max((r["median_abs_vertical_gradient"] for r in prof), default=1) or 1
        mean = [r["mean_luma"] for r in prof]
        lo, hi = min(mean), max(mean)
        for idx in range(1, len(prof)):
            y_prev = top + round((idx - 1) * panel_height / max_row)
            y_now = top + round(idx * panel_height / max_row)
            x_mean_prev = x0 + round((mean[idx - 1] - lo) / max(1, hi - lo) * panel_width)
            x_mean_now = x0 + round((mean[idx] - lo) / max(1, hi - lo) * panel_width)
            cv2.line(image, (x_mean_prev, y_prev), (x_mean_now, y_now),
                     (220, 40, 30), 2)
            g0 = prof[idx - 1]["median_abs_vertical_gradient"]
            g1 = prof[idx]["median_abs_vertical_gradient"]
            xg0 = x0 + round(g0 / max_grad * panel_width)
            xg1 = x0 + round(g1 / max_grad * panel_width)
            cv2.line(image, (xg0, y_prev), (xg1, y_now), (20, 120, 20), 2)
            if x0 == right + 45 and idx in (20, 220):
                cv2.line(image, (x0, y_now), (x0 + panel_width, y_now),
                         (0, 80, 255), 1)
        for row in range(0, len(prof), max(1, len(prof) // 10)):
            y = top + round(row * panel_height / max_row)
            cv2.putText(image, str(row), (x0 - 42, y + 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (50, 50, 50), 1)
    cv2.putText(image, "red=luminance (normalized); green=median |row gradient|; mapped red lines=letterbox rows 20/220",
                (90, 830), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (30, 30, 30), 1, cv2.LINE_AA)
    if not cv2.imwrite(str(path), image):
        raise OSError(f"failed to write profile plot: {path}")


def detection_bbox(d: Any) -> tuple[int, int, int, int]:
    v = det_dict(d)
    return int(v["x_min"]), int(v["x_max"]), int(v["y_min"]), int(v["y_max"])


def guarded_clusters(raw: list[Any], config: dict[str, Any]
                     ) -> tuple[list[list[int]], list[dict[str, Any]]]:
    """Mirror the current guarded merge's union order and record gate margins."""
    ordered = sorted(enumerate(raw), key=lambda pair: (
        int(det_dict(pair[1])["area"]), int(det_dict(pair[1])["x_min"]),
        int(det_dict(pair[1])["x_max"]), int(det_dict(pair[1])["y_min"]),
        int(det_dict(pair[1])["y_max"]), float(det_dict(pair[1])["centroid_x"]),
        float(det_dict(pair[1])["centroid_y"])))
    count = len(ordered)
    parent = list(range(count))
    group_members = [{ordered[i][0]} for i in range(count)]
    group_area = [int(det_dict(v)["area"]) for _, v in ordered]
    group_bbox = [detection_bbox(v) for _, v in ordered]
    group_wx = [group_area[i] * float(det_dict(ordered[i][1])["centroid_x"])
                for i in range(count)]
    group_wy = [group_area[i] * float(det_dict(ordered[i][1])["centroid_y"])
                for i in range(count)]
    gap_limit = float(config["component_cluster_gap"])
    intact_limit = int(config["single_intact_area"])
    area_limit = int(config["max_merge_area"])
    span_limit = int(config["max_merged_span"])
    decisions: list[dict[str, Any]] = []

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for left_i in range(count):
        for right_i in range(left_i + 1, count):
            left_root, right_root = find(left_i), find(right_i)
            if left_root == right_root:
                continue
            left_box, right_box = group_bbox[left_root], group_bbox[right_root]
            gap_x = max(0, left_box[0] - right_box[1] - 1, right_box[0] - left_box[1] - 1)
            gap_y = max(0, left_box[2] - right_box[3] - 1, right_box[2] - left_box[3] - 1)
            distance = math.hypot(gap_x, gap_y)
            if distance > gap_limit:
                continue
            combined_area = group_area[left_root] + group_area[right_root]
            merged_box = (min(left_box[0], right_box[0]), max(left_box[1], right_box[1]),
                          min(left_box[2], right_box[2]), max(left_box[3], right_box[3]))
            merged_width = merged_box[1] - merged_box[0] + 1
            merged_height = merged_box[3] - merged_box[2] + 1
            reason = "merged"
            if group_area[left_root] >= intact_limit and group_area[right_root] >= intact_limit:
                reason = "blocked_both_intact"
            elif combined_area > area_limit:
                reason = "blocked_merge_area"
            elif merged_width > span_limit or merged_height > span_limit:
                reason = "blocked_merge_span"
            decisions.append({
                "left_members": sorted(group_members[left_root]),
                "right_members": sorted(group_members[right_root]),
                "pair_gap_x": gap_x, "pair_gap_y": gap_y, "gap_distance": distance,
                "gap_margin_px": gap_limit - distance,
                "left_group_area_px": group_area[left_root],
                "right_group_area_px": group_area[right_root],
                "combined_area_px": combined_area,
                "single_intact_margin_px": intact_limit - min(
                    group_area[left_root], group_area[right_root]),
                "merged_bbox_width_px": merged_width,
                "merged_bbox_height_px": merged_height,
                "merge_area_margin_px": area_limit - combined_area,
                "merge_span_margin_px": span_limit - max(merged_width, merged_height),
                "decision": reason,
            })
            if reason != "merged":
                continue
            if right_root < left_root:
                left_root, right_root = right_root, left_root
            parent[right_root] = left_root
            group_area[left_root] = combined_area
            group_bbox[left_root] = merged_box
            group_wx[left_root] += group_wx[right_root]
            group_wy[left_root] += group_wy[right_root]
            group_members[left_root] |= group_members[right_root]
    groups = []
    for root in range(count):
        if find(root) == root:
            members = sorted(group_members[root])
            groups.append(members)
    return groups, decisions


def serialize_frame_result(result: Any) -> dict[str, Any]:
    value = dataclasses.asdict(result)
    return json.loads(json.dumps(value))


def compare_results(actual: list[dict[str, Any]], replayed: list[dict[str, Any]]) -> dict[str, Any]:
    keys = ("frame_id", "total_count", "detections", "events", "roi_top", "roi_bottom",
            "ccl_executed", "raw_detections", "suppressed_events", "tracks",
            "glare_rejected_count", "glare_area_px")
    mismatch = []
    for index, (left, right) in enumerate(zip(actual, replayed)):
        for key in keys:
            if left.get(key) != right.get(key):
                mismatch.append({"frame_id": index, "field": key})
    return {"frames_compared": min(len(actual), len(replayed)),
            "frames_match": len(actual) == len(replayed) == 300 and not mismatch,
            "mismatch_count": len(mismatch), "first_mismatches": mismatch[:20],
            "comparison_fields": list(keys)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True,
                        help="local -10 evidence bundle root")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="new directory for analysis outputs; it must not exist")
    args = parser.parse_args()
    bundle = args.bundle.resolve(strict=True)
    output = args.output_dir.resolve(strict=False)
    if output.exists():
        allowed = {
            "row-profile-source640x400.csv", "row-profile-mapped320x240.csv",
            "contact-source640x400.png", "contact-mapped320x240.png",
            "contact-binary8-18.png", "row-profile-plot.png", "geometry-summary.json",
            "baseline-replay-validation.json", "track-observations.csv", "track-windows.json",
            "track-summary.csv", "fulltrack-observations.csv", "merge-gate-decisions.json",
            "feature-summary.json", "analysis-inputs.json",
            "merge-gate-decisions-window.json",
            "analysis-summary.md",
        }
        existing = {item.name for item in output.iterdir()}
        if not output.is_dir() or not existing.issubset(allowed):
            raise SystemExit(f"refusing to overwrite non-analysis output directory: {output}")
    capture = bundle / "capture"
    evidence_path = bundle / "evidence.json"
    results_path = bundle / "standby-reopen-102405062967732" / "frame-results.json"
    sync_check = verify_sync_manifest(bundle)
    evidence = read_json(evidence_path)
    manifest, gray, binary, records, source_gray, source_ids = verify_capture(capture, bundle)
    actual_results = read_json(results_path)
    records_hash = sha256_file(capture / "frame-records.jsonl")
    if not isinstance(actual_results, list) or len(actual_results) != 300:
        raise ValueError("frame-results.json must contain exactly 300 FrameResult objects")
    if evidence.get("status") != "PHYSICAL_CALIBRATION_COMPLETE":
        raise ValueError(f"physical run evidence is not complete: {evidence.get('status')}")
    if evidence.get("frames_processed") != 300:
        raise ValueError("evidence does not report 300 processed frames")
    if evidence.get("total_count") != EXPECTED_PHYSICAL_COUNT:
        raise ValueError(f"evidence total_count is not the operator-confirmed 12: {evidence.get('total_count')}")

    # Geometry: use actual source rows plus mapped Gray8. The 20-row bars are
    # reported as mapping geometry only; they are never used as a belt ruler.
    source_profile = row_profile(source_gray)
    mapped_profile = row_profile(gray)
    padding = {
        "top_gray8_nonzero_pixels": int(np.count_nonzero(gray[:, :20, :])),
        "bottom_gray8_nonzero_pixels": int(np.count_nonzero(gray[:, 220:240, :])),
        "top_binary8_nonzero_pixels": int(np.count_nonzero(binary[:, :20, :])),
        "bottom_binary8_nonzero_pixels": int(np.count_nonzero(binary[:, 220:240, :])),
    }
    padding["all_300_gray8_and_binary8_padding_zero"] = not any(padding.values())
    mapping_mismatches = []
    for source_index, frame_id in enumerate(source_ids):
        expected_mapped = source_gray[source_index, ::2, ::2]
        actual_mapped = gray[frame_id, 20:220, :]
        if not np.array_equal(expected_mapped, actual_mapped):
            mapping_mismatches.append({
                "frame_id": frame_id,
                "mismatched_pixels": int(np.count_nonzero(expected_mapped != actual_mapped)),
            })
    mapping_validation = {
        "sampled_source_frames_compared": len(source_ids),
        "source_to_mapped_rule": "source_gray8[::2,::2] == raw_gray8[frame_id,20:220,:]",
        "mismatch_count": len(mapping_mismatches),
        "mismatches": mapping_mismatches,
        "exact_sample_mapping": not mapping_mismatches,
        "padding": padding,
    }
    output.mkdir(parents=True, exist_ok=True)
    write_profile_csv(output / "row-profile-source640x400.csv", source_profile)
    write_profile_csv(output / "row-profile-mapped320x240.csv", mapped_profile)
    contact_sheet(source_gray, source_ids, output / "contact-source640x400.png",
                  title="-10 sampled original grayscale frames; review physical belt edges/marks",
                  tile_width=320, tile_height=200)
    mapped_samples = gray[source_ids]
    contact_sheet(mapped_samples, source_ids, output / "contact-mapped320x240.png",
                  title="Mapped Gray8 samples; red lines indicate letterbox rows 20/220 only",
                  tile_width=320, tile_height=240, output_mapping=True)
    contact_sheet(binary[source_ids], source_ids, output / "contact-binary8-18.png",
                  title="Binary8 mask for the same 18 sampled frame IDs",
                  tile_width=320, tile_height=240, output_mapping=True)
    profile_plot(source_profile, mapped_profile, output / "row-profile-plot.png")
    source_top = candidate_edges(source_profile, 2, 198)
    source_bottom = candidate_edges(source_profile, 202, 398)
    mapped_top = candidate_edges(mapped_profile, 21, 118)
    mapped_bottom = candidate_edges(mapped_profile, 122, 219)
    active_roi = manifest.get("config", {}).get("frame_geometry", {}).get(
        "active_output_roi_xyxy", [0, 20, 320, 220])
    geometry = {
        "status": "IMAGE_GEOMETRY_VERIFIED_PHYSICAL_SCALE_UNRESOLVED",
        "analysis_status": "IMAGE_GEOMETRY_VERIFIED_PHYSICAL_SCALE_UNRESOLVED",
        "physical_calibration_status": "NOT_MEASURED",
        "pixels_per_mm_y": None,
        "source_resolution": [640, 400], "mapped_resolution": [320, 240],
        "source_frame_ids": source_ids, "source_frame_count": len(source_ids),
        "mapped_frames_profiled": len(gray), "active_output_roi_xyxy": active_roi,
        "mapping_validation": mapping_validation,
        "known_mapping": "letterbox maps source[::2,::2] to mapped rows [20,220); padding rows are not physical belt edges",
        "nominal_belt_width_mm": manifest.get("config", {}).get("frame_geometry", {}).get("belt_width_mm_nominal"),
        "source_edge_candidates_top": source_top,
        "source_edge_candidates_bottom": source_bottom,
        "mapped_edge_candidates_top_inside_active_roi": mapped_top,
        "mapped_edge_candidates_bottom_inside_active_roi": mapped_bottom,
        "source_pixels_per_mm_y": None, "mapped_pixels_per_mm_y": None,
        "edge_adjudication": None,
        "limitation": "Image geometry and letterbox padding are verified; the visible belt endpoints/180 mm marks are not identifiable in this sampled set. The 200 active mapped rows are not a physical ruler. Physical pixels/mm remains unresolved.",
    }
    if not (mapping_validation["exact_sample_mapping"]
            and padding["all_300_gray8_and_binary8_padding_zero"]):
        geometry["status"] = "IMAGE_GEOMETRY_VALIDATION_FAILED_PHYSICAL_SCALE_UNRESOLVED"
        geometry["analysis_status"] = geometry["status"]
    (output / "geometry-summary.json").write_text(
        json.dumps(geometry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Baseline raw Binary8 replay provides exact frame-to-track and association
    # data; saved algorithm outcomes are compared before reporting measurements.
    config = manifest["config"]
    recorded_counter_configs = [record.get("metadata", {}).get("algorithm_config")
                                for record in records]
    if (not recorded_counter_configs or recorded_counter_configs[0] is None
            or any(item != recorded_counter_configs[0] for item in recorded_counter_configs)):
        raise ValueError("per-frame algorithm_config is absent or changes during capture")
    recorded_counter_config = recorded_counter_configs[0]
    if isinstance(recorded_counter_config, dict) and "counter_config" in recorded_counter_config:
        recorded_counter_config = recorded_counter_config["counter_config"]
    if not isinstance(recorded_counter_config, dict):
        raise ValueError("frame-record algorithm_config is not a mapping")
    accepted_counter_keys = set(inspect.signature(MaskCounter).parameters)
    unsupported_config = set(recorded_counter_config) - accepted_counter_keys
    if unsupported_config:
        raise ValueError(f"recorded algorithm_config has unsupported MaskCounter keys: {sorted(unsupported_config)}")
    counter_config = dict(recorded_counter_config)
    counter_config["width"] = int(binary.shape[2])
    counter_config["height"] = int(binary.shape[1])
    config_provenance = {
        "source": "capture/frame-records.jsonl metadata.algorithm_config",
        "all_300_frames_match": True,
        "recorded_counter_config": recorded_counter_config,
        "replay_dimensions_from_npz": {"width": int(binary.shape[2]), "height": int(binary.shape[1])},
    }
    counter = MaskCounter(**counter_config)
    replayed_results = []
    associations_by_frame = []
    cluster_by_frame: list[tuple[list[list[int]], list[dict[str, Any]]]] = []
    for frame_id, mask in enumerate(binary):
        result = counter.update(frame_id, mask)
        replayed_results.append(serialize_frame_result(result))
        associations_by_frame.append(list(counter.last_associations))
        cluster_by_frame.append(guarded_clusters(list(result.raw_detections), counter_config))
    replay_check = compare_results(actual_results, replayed_results)
    association_mismatches = [
        frame_id for frame_id, record in enumerate(records)
        if record.get("metadata", {}).get("associations") != associations_by_frame[frame_id]
    ]
    replay_check["association_frames_match"] = not association_mismatches
    replay_check["association_mismatch_frames"] = association_mismatches[:20]
    replay_check["algorithm_config_provenance"] = config_provenance
    actual_events = [(int(item["frame_id"]), int(event["track_id"]))
                     for item in actual_results for event in item.get("events", [])]
    replay_events = [(int(item["frame_id"]), int(event["track_id"]))
                     for item in replayed_results for event in item.get("events", [])]
    replay_check["actual_count_events"] = len(actual_events)
    replay_check["replay_count_events"] = len(replay_events)
    replay_check["event_rows_match"] = actual_events == replay_events
    replay_check["count_total"] = replayed_results[-1]["total_count"] if replayed_results else 0
    (output / "baseline-replay-validation.json").write_text(
        json.dumps(replay_check, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    observations = []
    fulltrack_observations = []
    parameter_decisions = []
    window_parameter_decisions = []
    event_summaries = []

    def make_observation(event_frame: int, frame_id: int, track_id: int
                         ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        association = next((item for item in associations_by_frame[frame_id]
                            if int(item["track_id"]) == track_id), None)
        row: dict[str, Any] = {
            "event_frame": event_frame, "track_id": track_id, "frame_id": frame_id,
            "relative_to_event": frame_id - event_frame,
            "observation": "ASSOCIATED" if association else "NO_ASSOCIATED_DETECTION",
        }
        if association is None:
            row.update({"area_px": None, "bbox_width_px": None, "bbox_height_px": None,
                        "bbox_x_min": None, "bbox_x_max": None,
                        "bbox_y_min": None, "bbox_y_max": None,
                        "centroid_x": None, "centroid_y": None,
                        "raw_component_count": 0, "raw_component_areas_px": [],
                        "fragment_merge": False, "touch_active_border": False})
            return row, []

        detection_index = int(association["detection_index"])
        detection = det_dict(replayed_results[frame_id]["detections"][detection_index])
        groups, decisions = cluster_by_frame[frame_id]
        group = next((items for items in groups if
            _group_matches_detection(items, replayed_results[frame_id]["raw_detections"],
                                     detection, counter_config)), None)
        if group is None:
            raise ValueError(f"cannot map frame {frame_id} detection {detection_index} to raw CCL members")
        raw_values = [det_dict(replayed_results[frame_id]["raw_detections"][i])
                      for i in group]
        widths = [v["x_max"] - v["x_min"] + 1 for v in raw_values]
        heights = [v["y_max"] - v["y_min"] + 1 for v in raw_values]
        y_min = int(detection["y_min"])
        y_max = int(detection["y_max"])
        active_y_min = int(active_roi[1])
        active_y_max = int(active_roi[3]) - 1
        row.update({
            "detection_index": detection_index,
            "area_px": int(detection["area"]),
            "bbox_width_px": int(detection["x_max"] - detection["x_min"] + 1),
            "bbox_height_px": int(detection["y_max"] - detection["y_min"] + 1),
            "bbox_x_min": int(detection["x_min"]), "bbox_x_max": int(detection["x_max"]),
            "bbox_y_min": y_min, "bbox_y_max": y_max,
            "centroid_x": float(detection["centroid_x"]),
            "centroid_y": float(detection["centroid_y"]),
            "raw_component_count": len(raw_values),
            "raw_component_areas_px": [int(v["area"]) for v in raw_values],
            "raw_component_bbox_widths_px": widths,
            "raw_component_bbox_heights_px": heights,
            "fragment_merge": len(raw_values) > 1,
            "touch_active_border": y_min <= active_y_min or y_max >= active_y_max,
        })
        selected_members = set(group)
        associated_decisions = []
        for decision in decisions:
            decision_members = set(decision["left_members"]) | set(decision["right_members"])
            if decision_members & selected_members:
                associated_decisions.append({
                    "event_frame": event_frame, "track_id": track_id,
                    "frame_id": frame_id, "merged_detection_index": detection_index,
                    **decision,
                })
        return row, associated_decisions

    for event_frame, track_id in replay_events:
        event_info = {"event_frame": event_frame, "track_id": track_id,
                      "window": [max(0, event_frame - 5), min(299, event_frame + 5)],
                      "track_id_is_physical_identity": False}
        track_associations = []
        for frame_id in range(len(replayed_results)):
            row, matching_decisions = make_observation(event_frame, frame_id, track_id)
            if row["observation"] == "ASSOCIATED":
                track_associations.append(row)
                parameter_decisions.extend(matching_decisions)
        fulltrack_observations.extend(track_associations)
        event_info["full_track_associated_observation_count"] = len(track_associations)
        event_info["full_track_frame_range"] = (
            [track_associations[0]["frame_id"], track_associations[-1]["frame_id"]]
            if track_associations else None)
        event_info["full_track"] = track_scope_summary(track_associations)

        event_observations = []
        for frame_id in range(event_info["window"][0], event_info["window"][1] + 1):
            row, matching_decisions = make_observation(event_frame, frame_id, track_id)
            observations.append(row)
            if row["observation"] == "ASSOCIATED":
                event_observations.append(row)
                window_parameter_decisions.extend(matching_decisions)
        event_info["associated_observation_count"] = len(event_observations)
        event_info["window_associated_observation_count"] = len(event_observations)
        event_info["fragment_merge_frames"] = [
            item["frame_id"] for item in event_observations if item["fragment_merge"]]
        event_info["full_track_fragment_merge_frames"] = [
            item["frame_id"] for item in track_associations if item["fragment_merge"]]
        event_info["before_crossing"] = track_scope_summary(
            [row for row in event_observations if row["relative_to_event"] < 0])
        event_info["at_crossing"] = track_scope_summary(
            [row for row in event_observations if row["relative_to_event"] == 0])
        event_info["after_crossing"] = track_scope_summary(
            [row for row in event_observations if row["relative_to_event"] > 0])
        event_info["whole_window"] = track_scope_summary(event_observations)
        event_summaries.append(event_info)
    with (output / "track-observations.csv").open("w", newline="", encoding="utf-8") as stream:
        fields = sorted({key for item in observations for key in item})
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(observations)
    with (output / "fulltrack-observations.csv").open("w", newline="", encoding="utf-8") as stream:
        fields = sorted({key for item in fulltrack_observations for key in item})
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(fulltrack_observations)
    (output / "track-windows.json").write_text(
        json.dumps(event_summaries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    track_rows = []
    track_fields = ["event_frame", "track_id", "track_id_is_physical_identity"]
    scopes = ("full_track", "before_crossing", "at_crossing", "after_crossing", "whole_window")
    distribution_metrics = (
        "merged_area_px", "merged_bbox_width_px", "merged_bbox_height_px",
        "raw_ccl_component_area_px", "centroid_y_px", "bbox_y_min_px", "bbox_y_max_px",
    )
    count_metrics = (
        "associated_frames", "raw_ccl_component_count", "fragment_merge_frame_count",
        "fragment_merge_union_operation_count", "touch_active_border_observation_count",
    )
    range_metrics = ("centroid_y_range_px", "bbox_y_extent_px",
                     "fragment_merge_frames", "fragment_merge_union_operations_by_frame",
                     "touch_active_border_frames")
    for scope_name in scopes:
        for metric in distribution_metrics:
            for statistic_name in ("min", "mean", "max", "n"):
                track_fields.append(f"{scope_name}_{metric}_{statistic_name}")
        track_fields.extend(f"{scope_name}_{metric}" for metric in count_metrics + range_metrics)
    for event in event_summaries:
        row = {"event_frame": event["event_frame"], "track_id": event["track_id"],
               "track_id_is_physical_identity": False}
        for scope_name in scopes:
            scope = event[scope_name]
            for metric in distribution_metrics:
                for statistic_name in ("min", "mean", "max", "n"):
                    row[f"{scope_name}_{metric}_{statistic_name}"] = scope[metric][statistic_name]
            for metric in count_metrics + range_metrics:
                value = scope[metric]
                row[f"{scope_name}_{metric}"] = (
                    json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value)
        track_rows.append(row)
    with (output / "track-summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=track_fields)
        writer.writeheader()
        writer.writerows(track_rows)
    (output / "merge-gate-decisions.json").write_text(
        json.dumps(parameter_decisions, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "merge-gate-decisions-window.json").write_text(
        json.dumps(window_parameter_decisions, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    window_associated = [row for row in observations if row.get("observation") == "ASSOCIATED"]
    associated = fulltrack_observations
    raw_areas = [area for row in associated for area in row.get("raw_component_areas_px", [])]
    merged_areas = [row["area_px"] for row in associated]
    merged_widths = [row["bbox_width_px"] for row in associated]
    merged_heights = [row["bbox_height_px"] for row in associated]
    merge_rows = [row for row in associated if row.get("fragment_merge")]
    window_merge_rows = [row for row in window_associated if row.get("fragment_merge")]
    decisions_by_reason: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in parameter_decisions:
        decisions_by_reason[decision["decision"]].append(decision)
    applied = decisions_by_reason.get("merged", [])
    window_decisions_by_reason: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in window_parameter_decisions:
        window_decisions_by_reason[decision["decision"]].append(decision)
    window_applied = window_decisions_by_reason.get("merged", [])
    def gate_margin_summary(by_reason: dict[str, list[dict[str, Any]]],
                            applied_decisions: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "decision_count": sum(len(items) for items in by_reason.values()),
            "applied_merge_count": len(applied_decisions),
            "applied_merge_gap_distance_px": number_summary(
                [d["gap_distance"] for d in applied_decisions]),
            "applied_merge_gap_remaining_px": number_summary(
                [d["gap_margin_px"] for d in applied_decisions]),
            "applied_merge_single_intact_area_margin_px": number_summary(
                [d["single_intact_margin_px"] for d in applied_decisions]),
            "applied_merge_area_remaining_px": number_summary(
                [d["merge_area_margin_px"] for d in applied_decisions]),
            "applied_merge_span_remaining_px": number_summary(
                [d["merge_span_margin_px"] for d in applied_decisions]),
            "blocked_both_intact_candidates": len(by_reason.get("blocked_both_intact", [])),
            "blocked_merge_area_candidates": len(by_reason.get("blocked_merge_area", [])),
            "blocked_merge_span_candidates": len(by_reason.get("blocked_merge_span", [])),
        }
    merge_summary = {
        "physical_total_confirmed_by_operator": EXPECTED_PHYSICAL_COUNT,
        "count_events": len(replay_events), "algorithm_track_ids": sorted({t for _, t in replay_events}),
        "track_ids_are_physical_ids": False,
        "full_track_scope": "all actual association records for each of the 12 count-event track IDs during the 300 captured frames; missed/predicted frames excluded",
        "event_window_scope": "each count event frame +/- 5 frames, clipped to captured range",
        "associated_observations": len(associated),
        "full_track_associated_observations": len(associated),
        "event_window_associated_observations": len(window_associated),
        "full_track_association_counts_by_track": {
            str(event["track_id"]): event["full_track_associated_observation_count"]
            for event in event_summaries},
        "merged_detection_area_px": number_summary(merged_areas),
        "merged_detection_bbox_width_px": number_summary(merged_widths),
        "merged_detection_bbox_height_px": number_summary(merged_heights),
        "raw_component_area_px": number_summary(raw_areas),
        "largest_raw_ccl_component_px": max(raw_areas) if raw_areas else None,
        "max_merge_area_cap_applies_to_single_components": False,
        "merge_area_cap_note": "260 px limits a proposed merged group. A single raw CCL component can exceed 260 while remaining eligible under the separate max_area=900 limit.",
        "fragment_merge_observation_count": len(merge_rows),
        "full_track_fragment_merge_observation_count": len(merge_rows),
        "event_window_fragment_merge_observation_count": len(window_merge_rows),
        "full_track_fragment_merge_union_operation_count": sum(
            int(row["raw_component_count"]) - 1 for row in merge_rows),
        "event_window_fragment_merge_union_operation_count": sum(
            int(row["raw_component_count"]) - 1 for row in window_merge_rows),
        "full_track_fragment_merge_counts_by_track": {
            str(event["track_id"]): event["full_track"]["fragment_merge_frame_count"]
            for event in event_summaries},
        "event_window_fragment_merge_counts_by_track": {
            str(event["track_id"]): event["whole_window"]["fragment_merge_frame_count"]
            for event in event_summaries},
        "fragment_merge_frames": [
            {"track_id": row["track_id"], "frame_id": row["frame_id"],
             "raw_component_areas_px": row.get("raw_component_areas_px", []),
             "merged_area_px": row.get("area_px")}
            for row in merge_rows],
        "parameters": {
            "component_cluster_gap_px": config.get("component_cluster_gap"),
            "single_intact_area_px": config.get("single_intact_area"),
            "max_merge_area_px": config.get("max_merge_area"),
            "max_merged_span_px": config.get("max_merged_span"),
            "max_raw_detection_area_px": recorded_counter_config.get("max_area"),
        },
        "observed_gate_margins": {
            "full_lifecycle_associations": gate_margin_summary(decisions_by_reason, applied),
            "event_window_pm5_associations": gate_margin_summary(
                window_decisions_by_reason, window_applied),
        },
        "recorded_algorithm_config": config_provenance,
        "interpretation": "Observed margins cover the 157 actual association records for the 12 count-event algorithm tracks (separately reporting the +/-5-frame subset); missed/predicted frames are excluded. The 260 px cap applies to proposed merged groups, not single raw CCL components; a raw component may exceed 260 while remaining below max_area=900. With no per-item human labels, track IDs are not independent physical-object identities, so this finite run does not establish general safety or accuracy.",
    }
    (output / "feature-summary.json").write_text(
        json.dumps(merge_summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "analysis-inputs.json").write_text(json.dumps({
        "bundle_root": str(bundle), "capture_manifest_sha256": sha256_file(capture / "manifest.json"),
        "evidence_sha256": sha256_file(evidence_path),
        "frame_results_sha256": sha256_file(results_path),
        "frame_records_sha256": records_hash,
        "raw_npz_sha256": sha256_file(capture / RAW_NAME),
        "source_npz_sha256": sha256_file(capture / SOURCE_NAME),
        "sync_verification": sync_check,
        "algorithm_config_provenance": config_provenance,
        "analysis_script": str(Path(__file__).resolve()),
        "analysis_script_sha256": sha256_file(Path(__file__).resolve()),
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    full_margins = merge_summary["observed_gate_margins"]["full_lifecycle_associations"]
    window_margins = merge_summary["observed_gate_margins"]["event_window_pm5_associations"]
    report = [
        "# WO-0017 -10 离线几何与轨迹特征分析",
        "",
        f"- Capture: {manifest.get('status')}，{manifest.get('captured_frames')}/300 帧，丢帧 {manifest.get('dropped_frames')}。",
        f"- Baseline replay: {replay_check['count_total']} 计数，{replay_check['actual_count_events']} 个过线事件；逐帧一致={replay_check['frames_match']}，事件一致={replay_check['event_rows_match']}。",
        f"- 原始相机帧：{len(source_ids)} 帧，ID={source_ids}；映射帧={len(gray)}。",
        f"- 图像几何检查：{geometry['analysis_status']}；正式 physical_calibration_status={geometry['physical_calibration_status']}，pixels_per_mm_y={geometry['pixels_per_mm_y']}。300帧灰度/二值上下padding全零={padding['all_300_gray8_and_binary8_padding_zero']}，18张源帧至映射ROI逐点一致={mapping_validation['exact_sample_mapping']}。200 行 active ROI 属于 letterbox 映射，未作为 180 mm 物理标尺。",
        f"- 过线轨迹数：{len(event_summaries)}；跟踪 ID 不等同于物理物料身份。",
        f"- 完整生命周期：{len(associated)} 条实际关联记录；±5 帧窗口：{len(window_associated)} 条实际关联记录。fulltrack-observations.csv 与 track-summary.csv 分别保留观测和每个 track 的完整生命周期/过线子窗口统计，含面积、宽高、质心Y、bbox Y、边界触碰及合并次数。",
        f"- 完整生命周期多组件观测帧：{len(merge_rows)}，Σ(raw_component_count−1)={merge_summary['full_track_fragment_merge_union_operation_count']} 次union；±5帧为{len(window_merge_rows)}帧/Σ={merge_summary['event_window_fragment_merge_union_operation_count']}次。每track帧数/union数见track-summary.csv。",
        f"- 实际合并门禁裕度：完整生命周期 applied={full_margins['applied_merge_count']}，gap/area/span最小剩余={full_margins['applied_merge_gap_remaining_px']['min']}/{full_margins['applied_merge_area_remaining_px']['min']}/{full_margins['applied_merge_span_remaining_px']['min']} px；±5帧 applied={window_margins['applied_merge_count']}，最小剩余={window_margins['applied_merge_gap_remaining_px']['min']}/{window_margins['applied_merge_area_remaining_px']['min']}/{window_margins['applied_merge_span_remaining_px']['min']} px。",
        f"- 合并门禁按每帧记录配置（含 backward_tolerance={recorded_counter_config.get('backward_tolerance')}）逐帧一致地重放。",
        "",
        "## 几何边界人工复核",
        "",
        "请查看 contact-source640x400.png 与 contact-mapped320x240.png，并结合两份 row-profile CSV/PNG 核验图像几何。当前正式物理标定状态保持 NOT_MEASURED / pixels_per_mm_y=null：原图未显示可确认的皮带两端或180 mm标记，不能把200行映射有效区间解释为实测物理宽度。",
        "",
        "## 轨迹特征",
        "",
        f"- 合并检测面积 min/mean/max: {merge_summary['merged_detection_area_px']}",
        f"- 合并框宽 min/mean/max: {merge_summary['merged_detection_bbox_width_px']}",
        f"- 合并框高 min/mean/max: {merge_summary['merged_detection_bbox_height_px']}",
        f"- raw CCL 前景面积 min/mean/max: {merge_summary['raw_component_area_px']}",
        "",
        "参数的 observed_gate_margins 覆盖本次12个算法过线track的157条实际关联（并单列±5帧窗口），不构成独立物理样本的普遍安全性证明。260 px限制的是拟合并组，不限制单个raw CCL；max_area=900是单个检测面积上限。实际物料数12为操作员确认，无逐物料人工标注。",
    ]
    (output / "analysis-summary.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "ANALYSIS_COMPLETE",
        "output_dir": str(output),
        "contact_source": str(output / "contact-source640x400.png"),
        "contact_mapped": str(output / "contact-mapped320x240.png"),
        "profile_plot": str(output / "row-profile-plot.png"),
        "baseline_replay": replay_check,
        "geometry_status": geometry["status"],
        "event_tracks": len(event_summaries),
        "full_track_associations": len(fulltrack_observations),
        "window_associations": len([row for row in observations if row["observation"] == "ASSOCIATED"]),
    }, indent=2, ensure_ascii=False))
    return 0


def _group_matches_detection(members: list[int], raw: list[Any],
                             target: dict[str, Any], config: dict[str, Any]) -> bool:
    selected = [det_dict(raw[index]) for index in members]
    if not selected:
        return False
    area = sum(int(item["area"]) for item in selected)
    box = (min(int(item["x_min"]) for item in selected),
           max(int(item["x_max"]) for item in selected),
           min(int(item["y_min"]) for item in selected),
           max(int(item["y_max"]) for item in selected))
    cx = sum(int(item["area"]) * float(item["centroid_x"]) for item in selected) / area
    cy = sum(int(item["area"]) * float(item["centroid_y"]) for item in selected) / area
    return (
        area == int(target["area"])
        and box == detection_bbox(target)
        and math.isclose(cx, float(target["centroid_x"]), rel_tol=0, abs_tol=1e-7)
        and math.isclose(cy, float(target["centroid_y"]), rel_tol=0, abs_tol=1e-7)
    )


if __name__ == "__main__":
    raise SystemExit(main())
