import argparse
from pathlib import Path
import statistics
import sys
import time

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from binary_stats import Binary8RoiValidator


def test_validator_accepts_read_only_noncontiguous_roi_without_changing_input():
    source = np.zeros((12, 20), dtype=np.uint8)
    source[::2, ::2] = 255
    roi = source[:, ::2]
    roi.setflags(write=False)
    before = roi.copy()
    validator = Binary8RoiValidator()

    validator.validate(roi)

    np.testing.assert_array_equal(roi, before)
    assert not roi.flags.writeable
    assert validator._scratch.shape == roi.shape
    assert validator._scratch.dtype == np.uint8


@pytest.mark.parametrize("invalid", [1, 127, 254])
def test_validator_rejects_non_binary8_values(invalid):
    mask = np.zeros((3, 7), dtype=np.uint8)
    mask[1, 4] = invalid

    with pytest.raises(ValueError, match="Binary8"):
        Binary8RoiValidator().validate(mask)


def test_validator_reuses_scratch_until_roi_shape_changes():
    validator = Binary8RoiValidator()
    validator.validate(np.zeros((8, 6), dtype=np.uint8))
    first_scratch = validator._scratch

    validator.validate(np.full((8, 6), 255, dtype=np.uint8))

    assert validator._scratch is first_scratch
    validator.validate(np.zeros((9, 6), dtype=np.uint8))
    assert validator._scratch.shape == (9, 6)
    assert validator._scratch is not first_scratch


def _legacy_check(roi):
    if np.any((roi != 0) & (roi != 255)):
        raise ValueError("mask values must be Binary8")


def _benchmark_case(width, height, case, iterations):
    rng = np.random.default_rng(20261028 + width + height)
    if case == "blank":
        roi = np.zeros((height, width), dtype=np.uint8)
    else:
        roi = (rng.integers(0, 2, size=(height, width), dtype=np.uint8) * 255)

    validator = Binary8RoiValidator()
    validator.validate(roi)
    _legacy_check(roi)

    def measure(call):
        samples = []
        for _ in range(iterations):
            started = time.perf_counter()
            call()
            samples.append((time.perf_counter() - started) * 1000.0)
        return statistics.median(samples)

    new_ms = measure(lambda: validator.validate(roi))
    old_ms = measure(lambda: _legacy_check(roi))
    return old_ms, new_ms


def _run_benchmark(iterations):
    print("case,width,height,legacy_ms,inrange_reused_ms,speedup")
    for height, width in ((240, 320), (180, 320), (400, 640)):
        for case in ("blank", "noise"):
            legacy_ms, reused_ms = _benchmark_case(width, height, case, iterations)
            print(f"{case},{width},{height},{legacy_ms:.6f},{reused_ms:.6f},"
                  f"{legacy_ms / reused_ms:.2f}x")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark Binary8 validation alternatives")
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--iterations", type=int, default=500)
    args = parser.parse_args()
    if args.benchmark:
        _run_benchmark(args.iterations)
