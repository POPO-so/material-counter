"""Offline byte-exact benchmark for the legacy and vectorized WO-0015 packet encoders."""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
WO15_DIR = SCRIPT_DIR.parent / "wo0015"
for path in (SCRIPT_DIR, WO15_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from camera_adapter import gray320x240_to_gray_packet
from packet_encoder import gray320x240_to_gray_packet_into
from prepare_vectors import HEIGHT, WIDTH


def legacy_packet(gray: np.ndarray, frame_id: int) -> bytes:
    """Call the untouched WO-0015 encoder, which uses the original Beat-list path."""
    return gray320x240_to_gray_packet(gray, frame_id=frame_id)


def vectorized_packet(gray: np.ndarray, output: np.ndarray, frame_id: int) -> None:
    gray320x240_to_gray_packet_into(gray, output, frame_id=frame_id)


def benchmark(iterations: int) -> dict:
    if iterations < 1:
        raise ValueError("iterations must be positive")
    gray = np.random.default_rng(20261018).integers(
        0, 256, (HEIGHT, WIDTH), dtype=np.uint8
    )
    frame_id = 0x12345678
    output = np.empty((16 + HEIGHT * WIDTH * 3 + 16,), dtype=np.uint8)
    expected = legacy_packet(gray, frame_id)
    vectorized_packet(gray, output, frame_id)
    actual = output.tobytes()
    if expected != actual:
        raise AssertionError("vectorized packet differs from the legacy packet bytes")

    # Warm the NumPy/JIT-independent paths, then time identical fixed input frames.
    legacy_packet(gray, frame_id)
    vectorized_packet(gray, output, frame_id)
    samples = {"legacy": [], "vectorized": []}
    for _ in range(iterations):
        started = time.perf_counter()
        legacy_packet(gray, frame_id)
        samples["legacy"].append((time.perf_counter() - started) * 1000.0)
        started = time.perf_counter()
        vectorized_packet(gray, output, frame_id)
        samples["vectorized"].append((time.perf_counter() - started) * 1000.0)

    legacy_ms = statistics.mean(samples["legacy"])
    vectorized_ms = statistics.mean(samples["vectorized"])
    return {
        "mode": "offline-host-encoder-microbenchmark",
        "board_accessed": False,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "geometry": [HEIGHT, WIDTH],
        "packet_bytes": len(expected),
        "frame_id": frame_id,
        "iterations": iterations,
        "byte_exact_match": True,
        "legacy_mean_ms": round(legacy_ms, 3),
        "vectorized_mean_ms": round(vectorized_ms, 3),
        "speedup": round(legacy_ms / vectorized_ms, 2),
        "legacy_samples_ms": [round(value, 3) for value in samples["legacy"]],
        "vectorized_samples_ms": [round(value, 3) for value in samples["vectorized"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.iterations), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
