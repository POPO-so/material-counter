"""Offline comparison of attempt-03 preprocessing and reusable OV9281 path."""
import argparse
import json
import platform
import time
from pathlib import Path

import cv2
import numpy as np

from live_calibration import MonochromePreprocessor


def benchmark(iterations=500):
    rng = np.random.RandomState(17)
    gray_source = rng.randint(0, 256, (400, 640), dtype=np.uint8)
    frame = np.repeat(gray_source[:, :, None], 3, axis=2)
    processor = MonochromePreprocessor()

    def old():
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (320, 240), interpolation=cv2.INTER_AREA)
        return cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY)

    def new():
        return processor(frame)

    for _ in range(30):
        old()
        new()
    timings = {"attempt03_cvtColor_area": [], "first_channel_nearest_reused": []}
    for _ in range(iterations):
        for name, run in zip(timings, (old, new)):
            started = time.perf_counter()
            run()
            timings[name].append((time.perf_counter()-started)*1000)
    result = {"platform": platform.platform(), "opencv": cv2.__version__,
              "iterations": iterations, "source_shape": list(frame.shape),
              "kind": "offline synthetic repeated-monochrome channels; no camera capture",
              "interpolation": "new path uses formal counter INTER_NEAREST; not pixel-equivalent to old AREA",
              "timings_ms": {name: {"median": float(np.median(values)),
                                    "p95": float(np.percentile(values, 95))}
                             for name, values in timings.items()}}
    result["speedup"] = (result["timings_ms"]["attempt03_cvtColor_area"]["median"] /
                         result["timings_ms"]["first_channel_nearest_reused"]["median"])
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    text = json.dumps(benchmark(), indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text)
