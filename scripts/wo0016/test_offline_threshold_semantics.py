"""Threshold semantics tests for the offline Gray8 -> Binary8 reference."""
from __future__ import annotations

import numpy as np

from scripts.wo0016.offline_threshold import software_binary8


def _gray():
    image = np.zeros((240, 320), dtype=np.uint8)
    image[100, 10] = 127
    image[100, 11] = 128
    image[100, 12] = 129
    return image


def test_default_uses_strictly_greater_than():
    out = software_binary8(_gray(), threshold=128, roi=(0, 240))
    assert out[100, 10] == 0
    assert out[100, 11] == 0   # cv2.THRESH_BINARY keeps > threshold
    assert out[100, 12] == 255


def test_inclusive_matches_the_pl_greater_or_equal_rule():
    out = software_binary8(_gray(), threshold=128, roi=(0, 240), inclusive=True)
    assert out[100, 10] == 0
    assert out[100, 11] == 255
    assert out[100, 12] == 255


def test_outside_roi_is_background_and_input_is_unchanged():
    image = _gray()
    before = image.copy()
    out = software_binary8(image, threshold=128, roi=(50, 150), inclusive=True)
    assert np.array_equal(image, before)
    assert out[40, 12] == 0 and out[150, 12] == 0
    assert out[100, 12] == 255