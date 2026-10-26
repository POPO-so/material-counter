"""Tests for reference-area scaling and threshold conversion helpers."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("scale_parameters.py")


def _load_scale_parameters():
    assert MODULE_PATH.is_file(), "scale_parameters.py has not been implemented"
    spec = importlib.util.spec_from_file_location("scale_parameters", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_parameters_preserve_all_reference_values():
    scale_parameters = _load_scale_parameters()
    assert scale_parameters.REFERENCE_AREA == 111.0
    assert scale_parameters.DEFAULT_THRESHOLD == 100
    assert scale_parameters.PL_THRESHOLD == 128
    assert scale_parameters.derive_lighting_parameters() == {
        "min_area": 15,
        "min_split_child_area": 48,
        "single_intact_area": 64,
        "split_recursive_item_area": 80,
        "max_area": 540,
        "max_split_area": 540,
        "max_merge_area": 540,
        "split_min_hole_area": 16,
        "source_min_net_area": 60,
        "split_min_hole_distance": 10,
        "max_merged_span": 48,
        "max_bbox_width": 85,
        "max_bbox_height": 85,
        "hysteresis": 2.0,
    }


@pytest.mark.parametrize(
    ("reference_area", "expected"),
    [
        (75.0, {
            "min_area": 10,
            "min_split_child_area": 32,
            "single_intact_area": 43,
            "split_recursive_item_area": 54,
            "max_area": 365,
            "max_split_area": 365,
            "max_merge_area": 365,
            "split_min_hole_area": 11,
            "source_min_net_area": 41,
            "split_min_hole_distance": 8,
            "max_merged_span": 39,
            "max_bbox_width": 70,
            "max_bbox_height": 70,
            "hysteresis": 2.0,
        }),
        (160.0, {
            "min_area": 22,
            "min_split_child_area": 69,
            "single_intact_area": 92,
            "split_recursive_item_area": 115,
            "max_area": 778,
            "max_split_area": 778,
            "max_merge_area": 778,
            "split_min_hole_area": 23,
            "source_min_net_area": 86,
            "split_min_hole_distance": 12,
            "max_merged_span": 58,
            "max_bbox_width": 102,
            "max_bbox_height": 102,
            "hysteresis": 2.0,
        }),
    ],
)
def test_area_and_length_parameters_use_area_and_square_root_scaling(
    reference_area, expected
):
    scale_parameters = _load_scale_parameters()
    assert scale_parameters.derive_lighting_parameters(reference_area) == expected


def test_scaled_area_thresholds_keep_strict_split_hierarchy():
    scale_parameters = _load_scale_parameters()
    for reference_area in (75.0, 111.0, 160.0):
        config = scale_parameters.derive_lighting_parameters(reference_area)
        assert config["min_area"] < config["min_split_child_area"]
        assert config["min_split_child_area"] < config["single_intact_area"]
        assert config["single_intact_area"] < config["split_recursive_item_area"]


def test_too_small_reference_area_that_collapses_hierarchy_is_rejected():
    scale_parameters = _load_scale_parameters()
    with pytest.raises(ValueError, match="hierarchy"):
        scale_parameters.derive_lighting_parameters(reference_area=1.0)


@pytest.mark.parametrize(
    "reference_area",
    [True, False, 0, -1, float("inf"), float("-inf"), float("nan"), "111", None],
)
def test_invalid_reference_area_is_rejected(reference_area):
    scale_parameters = _load_scale_parameters()
    with pytest.raises(ValueError):
        scale_parameters.derive_lighting_parameters(reference_area=reference_area)


def test_positive_half_values_round_up_and_lengths_round_by_square_root():
    scale_parameters = _load_scale_parameters()
    config = scale_parameters.derive_lighting_parameters(reference_area=77.7)
    assert config["min_area"] == 11
    assert type(config["min_area"]) is int
    assert type(config["hysteresis"]) is float


def test_hysteresis_uses_scaled_length_and_is_rounded_back_to_float():
    scale_parameters = _load_scale_parameters()
    config = scale_parameters.derive_lighting_parameters(reference_area=222.0)
    assert config["split_min_hole_distance"] == 14
    assert config["max_merged_span"] == 68
    assert config["hysteresis"] == 3.0
    assert type(config["hysteresis"]) is float


def test_large_finite_reference_area_does_not_overflow_scaled_parameters():
    scale_parameters = _load_scale_parameters()
    config = scale_parameters.derive_lighting_parameters(reference_area=1e308)
    assert type(config["max_area"]) is int
    assert config["min_area"] < config["min_split_child_area"]
    assert config["min_split_child_area"] < config["single_intact_area"]
    assert config["single_intact_area"] < config["split_recursive_item_area"]


@pytest.mark.parametrize(
    ("threshold", "expected_offset"),
    [(0, 127), (100, 27), (127, 0), (128, -1), (255, -128)],
)
def test_threshold_validation_and_pl_offset_cover_signed_offsets(
    threshold, expected_offset
):
    scale_parameters = _load_scale_parameters()
    assert scale_parameters.validate_threshold(threshold) == threshold
    assert scale_parameters.threshold_offset(threshold) == expected_offset


@pytest.mark.parametrize("threshold", [True, False, 100.0, 100.5, -1, 256, "100", None])
def test_invalid_threshold_is_rejected(threshold):
    scale_parameters = _load_scale_parameters()
    with pytest.raises(ValueError):
        scale_parameters.validate_threshold(threshold)
    with pytest.raises(ValueError):
        scale_parameters.threshold_offset(threshold)
