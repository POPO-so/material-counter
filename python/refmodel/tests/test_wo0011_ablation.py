"""Isolated WO-0011 experiment contracts; frozen production reference unchanged."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
def _api():
    try:
        from refmodel.experiments import wo0011_ablation
    except ImportError:
        pytest.fail('WO-0011 ablation is not implemented yet')
    return wo0011_ablation


def image_features(*args, **kwargs):
    return _api().image_features(*args, **kwargs)


def transform(*args, **kwargs):
    return _api().transform(*args, **kwargs)


def test_direct_binary_equals_bypass_without_mutation():
    gray = np.random.default_rng(11).integers(0, 256, (29, 35), dtype=np.uint8)
    before = gray.copy()
    assert image_features(gray, 'binary_direct') == image_features(gray, 'bypass')
    assert np.array_equal(gray, before)


def test_opening_removes_thin_bridge_without_periodic_split():
    image = np.zeros((30, 50), np.uint8)
    image[6:23, 4:17] = image[6:23, 26:39] = 255
    image[14, 17:26] = 255
    assert len(image_features(image, 'bypass', max_width=0)) == 1
    assert len(image_features(image, 'opening', max_width=0)) == 2


def test_solid_touching_rectangles_have_no_invented_seam():
    image = np.zeros((30, 50), np.uint8)
    image[6:23, 4:17] = image[6:23, 17:30] = 255
    for strategy in ('bypass', 'erosion', 'dilation', 'opening', 'fill_holes'):
        assert len(image_features(image, strategy, max_width=0)) == 1


def test_hole_fill_preserves_exterior_and_fills_only_enclosed_background():
    image = np.zeros((20, 20), np.uint8)
    image[2:15, 2:15] = 255
    image[5:10, 5:10] = 0
    before = image.copy()
    filled = transform(image, 'fill_holes')
    assert filled[7, 7] == 255 and filled[0, 0] == 0
    assert np.count_nonzero(filled) == 169
    assert np.array_equal(image, before)


def test_hole_fill_with_foreground_at_top_left():
    image = np.full((15, 15), 255, np.uint8)
    image[4:8, 4:8] = 0
    assert np.all(transform(image, 'fill_holes') == 255)


def test_invalid_strategy_rejected():
    with pytest.raises(ValueError):
        transform(np.zeros((4, 4), np.uint8), 'invent_gt_split')


def test_integer_centroid_ablation_does_not_change_bbox():
    image = np.zeros((20, 20), np.uint8)
    image[3:10, 4:12] = 255
    original = image_features(image, 'bypass')[0]
    quantized = image_features(image, 'bypass', integer_centroid=True)[0]
    assert quantized.bbox == original.bbox
    assert quantized.centroid_x == int(original.centroid_x)
