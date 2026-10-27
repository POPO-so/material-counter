"""Offline Gray8 packet microbenchmark: allocate-return versus reusable TX buffer."""
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
GRAY8_DIR = SCRIPT_DIR.parent / "gray8"
for path in (SCRIPT_DIR, GRAY8_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from packet_encoder import (TX_BYTES, gray320x240_to_gray_packet,
                            gray320x240_to_gray_packet_into)
from packet_codec import HEIGHT, WIDTH, validate_packet


def benchmark(iterations: int) -> dict:
    if iterations < 1:
        raise ValueError("iterations must be positive")
    gray = np.random.default_rng(20261027).integers(0, 256, (HEIGHT, WIDTH), dtype=np.uint8)
    frame_id = 0x12345678
    output = np.empty(TX_BYTES, dtype=np.uint8)
    expected = gray320x240_to_gray_packet(gray, frame_id=frame_id)
    gray320x240_to_gray_packet_into(gray, output, frame_id=frame_id)
    if output.tobytes() != expected:
        raise AssertionError("allocated and reusable Gray8 packet encoders differ")
    np.testing.assert_array_equal(validate_packet(expected, frame_id=frame_id), gray)

    gray320x240_to_gray_packet(gray, frame_id=frame_id)
    gray320x240_to_gray_packet_into(gray, output, frame_id=frame_id)
    samples = {"allocate_return": [], "reusable_buffer": []}
    for _ in range(iterations):
        started = time.perf_counter()
        gray320x240_to_gray_packet(gray, frame_id=frame_id)
        samples["allocate_return"].append((time.perf_counter() - started) * 1000.0)
        started = time.perf_counter()
        gray320x240_to_gray_packet_into(gray, output, frame_id=frame_id)
        samples["reusable_buffer"].append((time.perf_counter() - started) * 1000.0)

    allocated_ms = statistics.mean(samples["allocate_return"])
    reused_ms = statistics.mean(samples["reusable_buffer"])
    return {
        "mode": "offline-gray8-packet-encoder-microbenchmark",
        "board_accessed": False,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "geometry": [HEIGHT, WIDTH],
        "packet_bytes": TX_BYTES,
        "frame_id": frame_id,
        "iterations": iterations,
        "exact_packet_match": True,
        "gray8_payload_verified": True,
        "allocate_return_mean_ms": round(allocated_ms, 3),
        "reusable_buffer_mean_ms": round(reused_ms, 3),
        "speedup": round(allocated_ms / reused_ms, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.iterations), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
