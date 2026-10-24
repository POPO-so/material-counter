"""Replay camera Gray8 with the isolated main-derived counter, never FPGA data."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np
from verify_split_package import verify

CAPTURES = (
    ("wo_touching_pair_motion_20261024_03", "0b55c6e1d5a43a4c69702dd89ef14be09c1673cdbc7b8d22b7bf5b8fbe40ce9e", 901, 2),
    ("wo_touching_three_motion_20261024_02", "7edb8fcc077a0592b957f485ae6d5b99989bdf61b2d0c17cff7d71afaa2ad9fa", 100, 3),
    ("wo_touching_three_motion_20261024_04", "c534ad4334afa725337a03d5ed849e5ae81857b5c8f5bf1c1686b465884ed59d", 80, 3),
)


def optical_mask(gray):
    """Main's software optical threshold; this does not claim PL equivalence."""
    return cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY)[1]


def replay(package, capture_root, output, manifest_sha256):
    package, root, out = Path(package).resolve(strict=True), Path(capture_root).resolve(strict=True), Path(output).resolve()
    if out.exists() or out.is_symlink():
        raise FileExistsError("replay output must be new")
    if root == out or root in out.parents:
        raise ValueError("derived outputs must be outside the original captures")
    verify(package, manifest_sha256)
    manifest = json.loads((package / "PACKAGE.json").read_text())
    sys.path[:0] = [str(package / "scripts/wo0016"), str(package / "scripts/wo0015")]
    import run_stream_counter as runner
    import stream_counter as stream
    from split_touching import split_touching_component
    for module in (runner, stream, sys.modules[split_touching_component.__module__]):
        if Path(module.__file__).resolve().parent != package / "scripts/wo0016":
            raise ValueError("counter module imported outside the isolated package")
    if "pynq" in sys.modules:
        raise RuntimeError("offline import unexpectedly loaded PYNQ")
    config = runner.counting_configuration(runner._parser().parse_args([
        "--counting-profile", "full-width-mixed"]))
    runtime = runner.formal_runtime_sha256()
    assert runtime["split_touching.py"] == hashlib.sha256(
        (package / "scripts/wo0016/split_touching.py").read_bytes()).hexdigest()
    out.mkdir(parents=True, exist_ok=False)
    reports = []
    for name, expected_hash, stop, expected_count in CAPTURES:
        source = root / name / "camera_gray_only.npz"
        actual_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise ValueError("camera archive SHA-256 mismatch: " + str(source))
        with np.load(source, allow_pickle=False) as stored:
            grays = stored["input_gray320x240"]
            timestamps = stored["monotonic_ns"]
        assert grays.shape == (1800, 240, 320) and grays.dtype == np.uint8
        counter = stream.MaskCounter(**config)
        rows, latencies, connectivity_warnings = [], [], []
        for frame_id in range(stop):
            # Match main's OpenCV THRESH_BINARY optical path, not FPGA proof.
            mask = optical_mask(grays[frame_id])
            count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
            child_connectivity = []
            for label in range(1, count):
                x, y, width, height, area = map(int, stats[label])
                if (counter.single_intact_area < area <= counter.max_split_area
                        and not counter._component_exceeds_limits(area, width, height)):
                    pieces = split_touching_component(labels[y:y+height, x:x+width] == label,
                                                      min_child_area=counter.min_area)
                    if len(pieces) in (2, 3):
                        connected = [cv2.connectedComponents(piece.astype(np.uint8), connectivity=8)[0] - 1
                                     for piece in pieces]
                        child_connectivity.append(connected)
                        if any(value != 1 for value in connected):
                            connectivity_warnings.append(frame_id)
            started = time.perf_counter_ns()
            result = counter.update(frame_id, mask)
            latencies.append((time.perf_counter_ns() - started) / 1e6)
            rows.append({"frame_id": frame_id, "camera_monotonic_ns": int(timestamps[frame_id]),
                         "pre_split_qualified_ccl": int(np.count_nonzero(stats[1:, cv2.CC_STAT_AREA] >= counter.min_area)),
                         "post_split_precluster_count": len(result.raw_detections),
                         "post_cluster_count": len(result.detections),
                         "detections": [asdict(d) for d in result.raw_detections],
                         "tracks": list(counter.tracking_snapshot()),
                         "crossing_events": [asdict(event) for event in result.events],
                         "total_count": result.total_count,
                         "child_pixel_component_counts": child_connectivity,
                         "counter_host_ms": latencies[-1], "dma_status": None})
        count_status = "OFFLINE_PASS" if counter.total_count == expected_count else "FAIL"
        geometry_status = "OFFLINE_PASS" if not connectivity_warnings else "FAIL"
        status = "OFFLINE_PASS" if count_status == geometry_status == "OFFLINE_PASS" else "FAIL"
        report = {"capture": name, "input_sha256": actual_hash, "frames": stop,
                  "status": status, "count_status": count_status, "geometry_status": geometry_status,
                  "expected": expected_count, "actual": counter.total_count,
                  "crossing_events": [event for row in rows for event in row["crossing_events"]],
                  "disconnected_child_frames": sorted(set(connectivity_warnings)),
                  "host_counter_ms": {"mean": float(np.mean(latencies)), "median": float(np.median(latencies)),
                                      "p95": float(np.percentile(latencies, 95))},
                  "frames_detail": rows}
        target = out / (name + ".json")
        target.write_text(json.dumps(report, indent=2) + "\n")
        reports.append({key: value for key, value in report.items() if key != "frames_detail"})
    controls = []
    for kind in ("round", "hex"):
        counter = stream.MaskCounter(**config)
        false_splits = []
        for frame_id, x in enumerate(range(120, 201, 2)):
            mask = np.zeros((240, 320), np.uint8)
            if kind == "round":
                cv2.circle(mask, (x, 120), 12, 255, -1)
            else:
                cv2.fillPoly(mask, [np.array([(x-12,120),(x-6,110),(x+6,110),(x+12,120),(x+6,130),(x-6,130)])], 255)
            cv2.circle(mask, (x, 120), 4, 0, -1)
            result = counter.update(frame_id, mask)
            if len(result.raw_detections) != 1:
                false_splits.append(frame_id)
        controls.append({"kind": kind, "source": "SYNTHETIC_SINGLE_NUT_SEQUENCE", "frames": 41,
                         "false_split_frames": false_splits, "expected": 1, "actual": counter.total_count})
    summary = {"status": "OFFLINE_PASS" if all(r["status"] == "OFFLINE_PASS" for r in reports)
               and all(not r["false_split_frames"] and r["actual"] == 1 for r in controls) else "FAIL",
               "count_regression_status": "OFFLINE_PASS" if all(r["count_status"] == "OFFLINE_PASS" for r in reports) else "FAIL",
               "input_type": "CAMERA_GRAY8_SOFTWARE_THRESHOLD_GT128_NOT_FPGA_BINARY8",
               "source_main": manifest["upstream_main_commit"], "runtime_sha256": runtime,
               "config": config, "captures": reports, "single_nut_controls": controls,
               "hardware_status": "NOT_VERIFIED", "board_fps": None, "board_cma": None,
               "independent_physical_truth": "NOT_VERIFIED", "hardware_actions": 0,
               "evidence_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.glob("*.json"))}}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--capture-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = replay(args.package, args.capture_root, args.output, args.manifest_sha256)
    print(json.dumps({k: result[k] for k in ("status", "input_type", "source_main", "hardware_status")}))
    raise SystemExit(0 if result["status"] == "OFFLINE_PASS" else 1)
