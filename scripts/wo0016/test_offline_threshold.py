"""CPU masks are offline comparison evidence, never a replacement board mode."""
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.mark.parametrize("threshold", [0, 100, 127, 200, 255])
def test_cpu_threshold_matches_pl_comparison_and_preserves_gray(threshold):
    from offline_threshold import software_binary8
    gray = np.arange(240 * 320, dtype=np.uint8).reshape(240, 320)
    before = gray.copy()
    destination = np.full_like(gray, 17)
    result = software_binary8(gray, threshold=threshold, output=destination)
    assert result is destination
    expected = np.zeros_like(gray)
    expected[30:210] = (gray[30:210] > threshold).astype(np.uint8) * 255
    np.testing.assert_array_equal(result, expected)
    np.testing.assert_array_equal(gray, before)


def test_cpu_threshold_accepts_strided_gray_and_rejects_alias():
    from offline_threshold import software_binary8
    gray = np.arange(240 * 640, dtype=np.uint8).reshape(240, 640)[:, ::2]
    assert not gray.flags.c_contiguous
    assert software_binary8(gray).shape == (240, 320)
    with pytest.raises(ValueError, match="contiguous"):
        software_binary8(gray.copy(), output=gray)  # output is not contiguous
    contiguous = gray.copy()
    with pytest.raises(ValueError, match="overlap"):
        software_binary8(contiguous, output=contiguous)


@pytest.mark.parametrize("threshold", [True, 100.0, -1, 256])
def test_cpu_threshold_rejects_invalid_threshold(threshold):
    from offline_threshold import software_binary8
    with pytest.raises(ValueError):
        software_binary8(np.zeros((240, 320), np.uint8), threshold=threshold)
