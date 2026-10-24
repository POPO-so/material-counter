"""Local-only, lossless -10 replay and interleaved host timing comparison.

The baseline is the deployed source preserved inside the sealed evidence, not
another instance of the modified implementation. No hardware modules are used.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import platform
import statistics
import sys
import time

import cv2
import numpy as np

from capture_dataset import load_dataset
from stream_counter import MaskCounter


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()
                if key != "roi_subtimings_ms"}
    if isinstance(value, (tuple, list)):
        return [canonical(item) for item in value]
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rounds", type=int, default=7)
    args = parser.parse_args()
    if args.rounds < 3:
        parser.error("at least three timing rounds are required")
    root = args.evidence.resolve(strict=True)
    output = args.output.resolve()
    if output == root or root in output.parents:
        parser.error("output must be outside the sealed evidence")
    output.mkdir(parents=True, exist_ok=True)
    manifest, frames = load_dataset(root / "capture")
    assert len(frames) == 300
    config = {key: value for key, value in manifest["config"].items()
              if key in inspect.signature(MaskCounter).parameters}
    assert "active_rows" not in config
    baseline_source = root / "scripts/wo0016/stream_counter.py"
    locked_runtime = json.loads((root / "_board-control/calibration-lock.json").read_text())["runtime_sha256"]
    assert digest(baseline_source) == locked_runtime["stream_counter.py"]
    spec = importlib.util.spec_from_file_location("sealed_counter10", baseline_source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    recorded = json.loads(next(root.glob("standby-reopen-*/frame-results.json")).read_text())
    if isinstance(recorded, dict):
        recorded = recorded["frames"]
    assert len(recorded) == 300
    original = module.MaskCounter(**config)
    optimized = MaskCounter(**config, active_rows=(20, 220))
    results, board_differences, frame_digests = [], [], []
    padding_nonzero = 0
    for index, frame in enumerate(frames):
        padding_nonzero += int(np.count_nonzero(frame.mask[:20]))
        padding_nonzero += int(np.count_nonzero(frame.mask[220:]))
        left = canonical(original.update(frame.frame_id, frame.mask))
        right = canonical(optimized.update(frame.frame_id, frame.mask))
        assert left == right, f"result mismatch frame {frame.frame_id}"
        assert original.last_associations == optimized.last_associations
        assert canonical(original.rejected_detections) == canonical(optimized.rejected_detections)
        assert len(original.rejected_contours) == len(optimized.rejected_contours)
        for a, b in zip(original.rejected_contours, optimized.rejected_contours):
            np.testing.assert_array_equal(a, b)
        if left != canonical(recorded[index]):
            board_differences.append(frame.frame_id)
        results.append(right)
        frame_digests.append({"frame_id": frame.frame_id, "sha256": fingerprint(right)})
    assert padding_nonzero == 0
    assert not board_differences, f"sealed board records differ: {board_differences}"
    assert original.total_count == optimized.total_count == 12
    events = [event for result in results for event in result["events"]]
    assert len(events) == 12
    # Warm both variants, then alternate run order to reduce host drift bias.
    def timing(factory, options):
        counter = factory(**options)
        stages = {key: [] for key in ("validate_ms", "projection_ms", "ccl_ms", "track_ms")}
        started = time.perf_counter()
        for frame in frames:
            result = counter.update(frame.frame_id, frame.mask)
            for key in stages:
                stages[key].append(result.roi_subtimings_ms[key])
        elapsed = time.perf_counter() - started
        return {"ms_per_frame": elapsed * 1000 / len(frames),
                "total_count": counter.total_count,
                "stages_mean_ms": {key: statistics.mean(values) for key, values in stages.items()}}
    variants = {"baseline": (module.MaskCounter, config),
                "active_rows": (MaskCounter, dict(config, active_rows=(20, 220)))}
    for factory, options in variants.values():
        timing(factory, options)
    samples = {name: [] for name in variants}
    for round_id in range(args.rounds):
        names = list(variants) if round_id % 2 == 0 else list(reversed(variants))
        for name in names:
            samples[name].append(timing(*variants[name]))
    medians = {name: statistics.median(item["ms_per_frame"] for item in values)
               for name, values in samples.items()}
    evidence = json.loads((root / "evidence.json").read_text())
    receipt = {
        "scope": "LOCAL_OFFLINE_ONLY; host timing is not board FPS acceptance",
        "frames": len(frames), "padding_nonzero_pixels": padding_nonzero,
        "exact_baseline_optimized_frames": 300,
        "comparison": "all FrameResult fields except timings, associations, rejection geometry/contours",
        "board_record_different_frame_ids": board_differences,
        "baseline_count": original.total_count, "optimized_count": optimized.total_count,
        "operator_reported_total": 12, "events": events, "frame_fingerprints": frame_digests,
        "baseline_source_sha256": digest(baseline_source),
        "optimized_source_sha256": digest(Path(__file__).with_name("stream_counter.py")),
        "runner_source_sha256": digest(Path(__file__).with_name("run_stream_counter.py")),
        "raw_npz_sha256": digest(root / "capture/frames_gray8_binary8.npz"),
        "config": config, "optimized_config": dict(config, active_rows=[20, 220]),
        "environment": {"platform": platform.platform(), "processor": platform.processor(),
                        "python": platform.python_version(), "opencv": cv2.__version__,
                        "opencv_threads": cv2.getNumThreads(), "numpy": np.__version__},
        "timing_rounds": args.rounds, "timing_samples": samples,
        "median_ms_per_frame": medians,
        "median_saved_ms": medians["baseline"] - medians["active_rows"],
        "board_original_fps": evidence.get("throughput_fps"),
        "note": "No DMA, camera sampling, lock changes or board deployment. Full board throughput needs a new authorized measurement."
    }
    (output / "replay-receipt.json").write_text(json.dumps(receipt, indent=2)+"\n", encoding="utf-8")
    (output / "optimized-frame-results.json").write_text(json.dumps(results, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({key: receipt[key] for key in ("frames", "baseline_count", "optimized_count",
        "board_record_different_frame_ids", "median_ms_per_frame", "median_saved_ms")}))


if __name__ == "__main__":
    main()
