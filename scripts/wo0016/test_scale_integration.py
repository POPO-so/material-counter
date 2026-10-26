"""Scale/photometric contracts of the lighting profile, independent of hardware."""
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lighting_profile as lighting
import run_stream_counter as runner
import run_live_monitor as live
import live_calibration as calibration


def test_reference_profile_is_exact_recorded_dictionary():
    import json
    root = Path(__file__).parents[2] / "data/results/lighting-calibration-20261025/board-approval/b2-attempt-03/evidence/b2-capture-b2-m6touch-220-20261025-03"
    if not (root / "frame-records.jsonl").is_file():
        pytest.skip("local B2-03 paired capture is not available")
    record = json.loads((root / "frame-records.jsonl").read_text(encoding="utf-8").splitlines()[0])
    args = runner._parser().parse_args(["--counting-profile", "lighting-calibrated", "--reference-area", "111"])
    assert runner.counting_configuration(args) == record["metadata"]["algorithm_config"]


@pytest.mark.parametrize("area", [75, 160])
def test_reference_area_reaches_counter_and_geometry(area):
    from scale_parameters import derive_lighting_parameters
    args = live.parser().parse_args(["--reference-area", str(area), "--lighting-area-filter", "output-roi"])
    expected = derive_lighting_parameters(area)
    config = runner.counting_configuration(args)
    for name in ("min_area", "min_split_child_area", "single_intact_area",
                 "split_recursive_item_area", "max_area", "split_min_hole_area",
                 "max_merged_span", "max_bbox_width", "max_bbox_height", "hysteresis"):
        assert config[name] == expected[name]
    assert config.get("split_min_hole_distance", 10) == expected["split_min_hole_distance"]
    assert config.get("split_hole_area_cap", 16) == expected["split_min_hole_area"]
    geometry = runner.preprocessing_configuration(args)
    assert geometry["runtime_min_foreground_pixels"] == expected["min_area"]
    assert geometry["source_min_net_contour_area_configured"] == expected["source_min_net_area"]


def test_scaled_small_hole_gate_does_not_reintroduce_unscaled_16_pixel_cap():
    from split_touching import _hole_area_threshold
    assert _hole_area_threshold(365, 11, relative_cap=11) == 11


@pytest.mark.parametrize("threshold", [0, 50, 100, 127, 128, 200, 254, 255])
def test_all_gray_values_match_fixed_pl_at_configured_threshold(threshold):
    values = np.arange(256, dtype=np.uint8)
    gray = np.zeros((240, 320), np.uint8)
    gray[40, :256] = values
    packet = lighting.encode(gray, threshold=threshold)
    pixels = packet[16:-16].reshape(240, 320, 3)
    np.testing.assert_array_equal(pixels[40, :256, 0] >= 128, values > threshold)
    np.testing.assert_array_equal(gray[40, :256], values)


def test_threshold_reaches_source_filter_and_software_preview():
    source = np.zeros((400, 640), np.uint8)
    source[100:108, 100:108] = 150  # net contour 49: would be removed at the old T=100
    processor = calibration.MonochromePreprocessor(mapping="lighting-calibrated", threshold=180)
    gray, binary = processor(source)
    assert not binary.any()
    assert gray.max() == 150
    args = live.parser().parse_args(["--lighting-threshold", "180"])
    assert runner.effective_threshold(args) == 180
    assert runner.preprocessing_configuration(args)["binary_thresh"] == 180


def test_runtime_packet_encoders_bind_one_threshold_without_global_mutation():
    args = live.parser().parse_args(["--lighting-threshold", "180"])
    encode, encode_into = runner.packet_encoders(args)
    gray = np.zeros((240, 320), np.uint8)
    gray[40, :256] = np.arange(256, dtype=np.uint8)
    packet = encode(gray)
    output = np.empty_like(packet)
    encode_into(gray, output)
    np.testing.assert_array_equal(packet, output)
    np.testing.assert_array_equal(packet[16:-16].reshape(240, 320, 3)[40, :256, 0] >= 128,
                                  np.arange(256) > 180)
    assert lighting.THRESHOLD == 100


def test_explicit_default_pixel_values_override_scaled_defaults():
    args = runner._parser().parse_args(["--counting-profile", "lighting-calibrated",
                                       "--reference-area", "75", "--max-bbox-width", "85",
                                       "--hysteresis", "2"])
    config = runner.counting_configuration(args)
    assert config["max_bbox_width"] == 85
    assert config["max_bbox_height"] == 70
    assert config["hysteresis"] == 2.0


def test_mock_summary_reports_requested_geometry_and_threshold(capsys):
    import json
    assert runner.main(["--mode", "mock", "--counting-profile", "lighting-calibrated",
                        "--reference-area", "75", "--lighting-threshold", "180"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["preprocessing_config"]["reference_area"] == 75
    assert summary["preprocessing_config"]["binary_thresh"] == 180


@pytest.mark.parametrize("value", [True, 100.0, 256, -1])
def test_invalid_threshold_is_not_accepted_even_after_lut_is_cached(value):
    lighting.gray_to_pl_lut(100)
    with pytest.raises(ValueError):
        lighting.gray_to_pl_lut(value)


def test_runtime_identity_includes_the_new_scale_parameter_source():
    import hashlib
    source = Path(__file__).with_name("scale_parameters.py")
    assert runner.formal_runtime_sha256()["scale_parameters.py"] == hashlib.sha256(source.read_bytes()).hexdigest()
