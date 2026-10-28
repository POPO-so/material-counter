"""Reproduce host-only helper timings; does not access the camera or FPGA."""
import argparse
import json
from pathlib import Path
import platform
import statistics
import sys
import time

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/wo0016"))
sys.path.insert(0, str(ROOT / "scripts/gray8"))
from binary_stats import Binary8RoiValidator
from split_touching import is_single_connected_component


def old_validate(image):
    if np.any((image != 0) & (image != 255)):
        raise ValueError("invalid Binary8")


def old_connected(image):
    image = np.asarray(image)
    if image.ndim != 2 or not np.any(image):
        return False
    count, _, _, _ = cv2.connectedComponentsWithStats(
        (image != 0).astype(np.uint8), connectivity=8)
    return count == 2


def measure_pair(old, new, *, calls, repeats=7):
    samples = {"old_ms": [], "new_ms": []}
    for _ in range(25):
        old()
        new()
    for repeat in range(repeats):
        pairs = [("old_ms", old), ("new_ms", new)]
        if repeat % 2:
            pairs.reverse()
        for name, call in pairs:
            start = time.perf_counter()
            for _ in range(calls):
                call()
            samples[name].append((time.perf_counter() - start) * 1000 / calls)
    old_ms, new_ms = (statistics.median(samples[key]) for key in ("old_ms", "new_ms"))
    return dict(**samples, median_old_ms=old_ms, median_new_ms=new_ms,
                local_speedup=old_ms / new_ms, calls_per_batch=calls, repeats=repeats)


def benchmark(calls):
    report = dict(scope="Windows host helper microbenchmarks only; not board or full-system FPS",
                  python=platform.python_version(), platform=platform.platform(),
                  opencv=cv2.__version__, validation=[], connectivity={})
    random = np.random.default_rng(20261028)
    for height, width in ((49, 320), (180, 320), (240, 320), (400, 640)):
        for case in ("blank", "noise"):
            image = (np.zeros((height, width), np.uint8) if case == "blank" else
                     random.integers(0, 2, (height, width), dtype=np.uint8) * 255)
            validator = Binary8RoiValidator()
            result = measure_pair(lambda: old_validate(image), lambda: validator.validate(image),
                                  calls=calls)
            report["validation"].append(dict(shape=[height, width], case=case, **result))
    fixture = ROOT / "scripts/wo0016/fixtures/lighting_b2_m6_touch_masks.npz"
    masks = []
    with np.load(fixture, allow_pickle=False) as sample:
        for frame in sample["masks"]:
            count, labels, stats, _ = cv2.connectedComponentsWithStats(frame, connectivity=8)
            for label in range(1, count):
                x, y, width, height, area = stats[label]
                if 100 < area < 1000:
                    masks.append(labels[y:y + height, x:x + width] == label)
    if not masks:
        raise RuntimeError("recorded fixture has no candidate components")
    assert all(old_connected(image) == is_single_connected_component(image) for image in masks)
    def old_batch():
        return [old_connected(image) for image in masks]
    def new_batch():
        return [is_single_connected_component(image) for image in masks]
    report["connectivity"] = dict(components=len(masks),
        **measure_pair(old_batch, new_batch, calls=max(50, calls // 10)))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--calls", type=int, default=2000)
    args = parser.parse_args()
    if args.calls < 1:
        parser.error("calls must be positive")
    if args.output.exists():
        raise FileExistsError(args.output)
    result = benchmark(args.calls)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
    print(json.dumps({"validation_speedups": [r["local_speedup"] for r in result["validation"]],
                      "connectivity_speedup": result["connectivity"]["local_speedup"]}))
