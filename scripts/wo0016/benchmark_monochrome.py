"""Offline timing of direct MJPEG-to-Gray8 decode and current Gray8 preprocessing."""
import argparse
import json
import platform
import time
from pathlib import Path

import cv2
import numpy as np

from live_calibration import MonochromePreprocessor


def benchmark(iterations=500):
    gray_source = np.random.default_rng(17).integers(0, 256, (400, 640), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", gray_source)
    if not ok:
        raise RuntimeError("could not encode the synthetic Gray8 source frame")
    packet = encoded.reshape(-1)
    processor = MonochromePreprocessor()

    def decode_and_process():
        gray = cv2.imdecode(packet, cv2.IMREAD_GRAYSCALE)
        if gray is None or gray.dtype != np.uint8 or gray.shape != (400, 640):
            raise RuntimeError("synthetic MJPEG did not decode to Gray8 640x400")
        return processor(gray)

    for _ in range(30):
        decode_and_process()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        decode_and_process()
        samples.append((time.perf_counter() - started) * 1000)
    return {
        "mode": "offline-synthetic-direct-mjpeg-gray8-preprocessing",
        "platform": platform.platform(),
        "opencv": cv2.__version__,
        "iterations": iterations,
        "source_geometry": [640, 400],
        "decoded_format": "Gray8",
        "packet_bytes": int(packet.nbytes),
        "camera_accessed": False,
        "rgb_intermediate": False,
        "timing_ms": {
            "median": float(np.median(samples)),
            "p95": float(np.percentile(samples, 95)),
            "mean": float(np.mean(samples)),
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--iterations", type=int, default=500)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    text = json.dumps(benchmark(args.iterations), indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text)
