"""Focused tests for the offline Gray8-to-region reference API."""

from dataclasses import FrozenInstanceError
from pathlib import Path
import sys

import numpy as np
import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[2]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from refmodel.check_morphology_output import recompute_frame
from refmodel.ps_reference.regions import Region, extract_regions, preprocess


def test_threshold_uses_greater_than_or_equal_and_ff00_encoding():
    gray = np.array([[127, 128, 129]], dtype=np.uint8)

    actual = preprocess(gray, threshold=128, morph_mode=0)

    np.testing.assert_array_equal(actual, np.array([[0x00, 0xFF, 0xFF]], dtype=np.uint8))


@pytest.mark.parametrize("threshold", [0, 255])
def test_threshold_accepts_both_byte_endpoints(threshold):
    gray = np.array([[0, 127, 128, 255]], dtype=np.uint8)

    actual = preprocess(gray, threshold=threshold)

    expected = np.where(gray >= threshold, 0xFF, 0x00).astype(np.uint8)
    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("threshold", [-1, 256, 1.5, "128"])
def test_threshold_rejects_values_outside_byte_integer_range(threshold):
    with pytest.raises(ValueError):
        preprocess(np.zeros((2, 3), dtype=np.uint8), threshold=threshold)


@pytest.mark.parametrize("gray", [np.zeros(3, dtype=np.uint8), np.zeros((2, 3), dtype=np.int16)])
def test_gray_input_must_be_two_dimensional_uint8(gray):
    with pytest.raises(ValueError):
        preprocess(gray)


def test_extract_regions_filters_small_noise_and_reports_inclusive_coordinates():
    gray = np.zeros((8, 12), dtype=np.uint8)
    gray[0, 0] = 255  # One-pixel noise is filtered by min_area.
    gray[5:7, 2:4] = 255
    gray[1:3, 7:9] = 255

    regions = extract_regions(gray, min_area=2, max_width=None)

    assert len(regions) == 2
    left, right = regions
    assert left.bbox == (2, 3, 5, 6)
    assert (left.x_min, left.x_max, left.row_min, left.row_max) == (2, 3, 5, 6)
    assert left.area == 4
    assert (left.centroid_x, left.centroid_y) == (2.5, 5.5)
    assert right.bbox == (7, 8, 1, 2)
    assert (right.centroid_x, right.centroid_y) == (7.5, 1.5)
    assert left.flags == right.flags == 4
    assert left.peak == right.peak == 0


def test_region_records_are_immutable():
    region = Region(1, 2, 3, 4, 4, 1.5, 3.5)

    with pytest.raises(FrozenInstanceError):
        region.area = 9


def test_zero_padded_morphology_handles_single_pixel_image():
    gray = np.array([[128]], dtype=np.uint8)

    np.testing.assert_array_equal(preprocess(gray, morph_mode=1), [[0]])
    np.testing.assert_array_equal(preprocess(gray, morph_mode=2), [[255]])
    np.testing.assert_array_equal(preprocess(gray, morph_mode=0), [[255]])


def test_hole_remains_background_inside_one_foreground_region():
    gray = np.full((5, 5), 128, dtype=np.uint8)
    gray[2, 2] = 127

    regions = extract_regions(gray, threshold=128, min_area=1, max_width=None)

    assert len(regions) == 1
    assert regions[0].bbox == (0, 4, 0, 4)
    assert regions[0].area == 24
    assert (regions[0].centroid_x, regions[0].centroid_y) == (2.0, 2.0)
    assert regions[0].flags == 6  # 2D component plus image-edge contact.


def test_dilation_can_join_targets_separated_by_one_column():
    gray = np.zeros((7, 10), dtype=np.uint8)
    gray[2:5, 1:4] = 128
    gray[2:5, 5:8] = 128

    separate = extract_regions(gray, min_area=1, morph_mode=0, max_width=None)
    joined = extract_regions(gray, min_area=1, morph_mode=2, max_width=None)

    assert len(separate) == 2
    assert len(joined) == 1
    assert joined[0].bbox == (0, 8, 1, 5)


def test_width_and_edge_flags_are_independent_and_can_disable_width_check():
    gray = np.zeros((5, 9), dtype=np.uint8)
    gray[3:5, 2:6] = 255

    flagged = extract_regions(gray, min_area=1, max_width=3)[0]
    no_width_limit = extract_regions(gray, min_area=1, max_width=0)[0]
    no_width_limit_none = extract_regions(gray, min_area=1, max_width=None)[0]

    assert flagged.flags == 7  # 2D, width exceeds limit, and touches the frame edge.
    assert no_width_limit.flags == no_width_limit_none.flags == 6


def test_large_component_area_is_not_truncated_to_sixteen_bits():
    gray = np.full((300, 300), 255, dtype=np.uint8)

    regions = extract_regions(gray, min_area=1, max_width=None)

    assert len(regions) == 1
    assert regions[0].area == 90_000


@pytest.mark.parametrize("morph_mode", [0, 1, 2])
def test_preprocess_matches_existing_python_morphology_checker_and_is_deterministic(morph_mode):
    rng = np.random.default_rng(20261013)
    binary = rng.choice(np.array([0, 255], dtype=np.uint8), size=(7, 9), p=[0.58, 0.42])
    gray = np.where(binary == 255, 128, 127).astype(np.uint8)
    gray_before = gray.copy()
    width = gray.shape[1]
    expected = recompute_frame(binary.reshape(-1).tolist(), width, gray.shape[0], morph_mode)

    actual = preprocess(gray, threshold=128, morph_mode=morph_mode)
    repeated = preprocess(gray, threshold=128, morph_mode=morph_mode)

    np.testing.assert_array_equal(actual.reshape(-1), np.asarray(expected, dtype=np.uint8))
    np.testing.assert_array_equal(repeated, actual)
    np.testing.assert_array_equal(gray, gray_before)


def test_preprocess_rejects_unknown_morphology_mode():
    with pytest.raises(ValueError):
        preprocess(np.zeros((2, 3), dtype=np.uint8), morph_mode=3)
