"""Focused tests for the isolated WO-0011 morphology and tracking ablations."""

from pathlib import Path
import sys

import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[2]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from refmodel.experiments.wo0011_supplements import (
    ROOT,
    STRATEGIES,
    _validate_output_path,
    run_supplements,
)


@pytest.fixture(scope="module")
def result():
    return run_supplements()


def strategy_row(result, case_id, strategy):
    case = next(row for row in result["cases"] if row["case_id"] == case_id)
    return case["strategies"][strategy]


def test_ablation_modes_are_explicit_and_include_offline_only_candidates():
    assert STRATEGIES == ("bypass", "erosion", "dilation", "opening", "hole_fill")
    result = run_supplements()
    assert result["parameters"]["threshold"] == 128
    assert result["parameters"]["min_area"] == 16
    assert result["parameters"]["random_seed"] == 20261013
    assert result["protocol"]["event_frame_rule"] == "first visible frame at or after strict hysteresis qualification"
    assert result["protocol"]["gt_identity_to_tracker"] is False


def test_thin_bridge_opening_splits_components_and_improves_counting(result):
    bypass = strategy_row(result, "thin_bridge", "bypass")
    opening = strategy_row(result, "thin_bridge", "opening")

    assert bypass["first_observed_region_count"] == 1
    assert opening["first_observed_region_count"] == 2
    assert (bypass["event_tp"], bypass["event_fn"]) == (0, 2)
    assert bypass["spurious_fp"] == 1
    assert (opening["event_tp"], opening["event_fn"]) == (2, 0)
    assert opening["duplicate_fp"] == opening["spurious_fp"] == 0


def test_opening_does_not_split_fully_contacting_rectangles(result):
    bypass = strategy_row(result, "contact_rectangles", "bypass")
    opening = strategy_row(result, "contact_rectangles", "opening")

    assert bypass["first_observed_region_count"] == opening["first_observed_region_count"] == 1
    assert (bypass["event_tp"], bypass["event_fn"], bypass["spurious_fp"]) == (0, 2, 1)
    assert (opening["event_tp"], opening["event_fn"], opening["spurious_fp"]) == (0, 2, 1)


def test_hole_fill_is_an_experimental_candidate_and_preserves_raw_ring_evidence(result):
    bypass = strategy_row(result, "thick_ring", "bypass")
    filled = strategy_row(result, "thick_ring", "hole_fill")

    assert bypass["first_observed_hole_count"] == 1
    assert bypass["first_observed_hole_area"] > 0
    assert filled["first_observed_hole_count"] == 0
    assert filled["first_observed_hole_area"] == 0
    assert filled["candidate_chain_status"] == "offline_only_not_in_production_chain"


def test_thin_ring_erosion_disappearance_is_counted_as_a_real_failure(result):
    bypass = strategy_row(result, "thin_ring", "bypass")
    erosion = strategy_row(result, "thin_ring", "erosion")

    assert bypass["first_observed_region_count"] == 1
    assert erosion["first_observed_region_count"] == 0
    assert strategy_row(result, "thin_ring", "opening")["first_observed_region_count"] == 0
    assert erosion["event_fn"] == 1


@pytest.mark.parametrize(("case_id", "dilated_count"), [("gap_1px", 1), ("gap_2px", 1), ("gap_3px", 2)])
def test_dilation_merge_risk_depends_on_gap(case_id, dilated_count, result):
    bypass = strategy_row(result, case_id, "bypass")
    dilation = strategy_row(result, case_id, "dilation")

    assert bypass["first_observed_region_count"] == 2
    assert dilation["first_observed_region_count"] == dilated_count
    if dilated_count == 1:
        assert dilation["event_fn"] >= 1


def test_boundary_noise_and_projection_cases_are_explicit(result):
    edge = strategy_row(result, "boundary_target", "bypass")
    small = strategy_row(result, "below_min_area", "bypass")
    projection = strategy_row(result, "projection_overlap_rows", "bypass")

    assert edge["first_observed_edge_region_count"] == 1
    assert small["first_observed_region_count"] == 0
    assert small["event_fn"] == 1
    assert projection["first_observed_region_count"] == 2
    assert (projection["event_tp"], projection["event_fn"]) == (2, 0)


@pytest.mark.parametrize(("missing", "expected_tp"), [(1, 1), (2, 1), (3, 0)])
def test_one_two_three_frame_observation_interruptions(missing, expected_tp, result):
    row = strategy_row(result, f"interrupt_{missing}frames", "bypass")

    assert row["qualified_truth_count"] == 1
    assert row["event_tp"] == expected_tp
    assert row["event_fn"] == 1 - expected_tp
    assert row["duplicate_fp"] == 0
    assert row["spurious_fp"] == 0


def test_generated_results_are_deterministic_and_exclude_gt_ids_from_features(result):
    repeated = run_supplements()

    assert result["trace_sha256"] == repeated["trace_sha256"]
    assert result["cases"] == repeated["cases"]
    for case in result["cases"]:
        for row in case["strategies"].values():
            assert "ground_truth_object_id" not in row
            assert row["event_tp"] + row["event_fn"] == row["qualified_truth_count"]
            assert row["qualified_precision"] in (0.0, 1.0, None)


def test_result_writer_rejects_frozen_synthetic_dataset_paths_without_creating_them():
    forbidden = ROOT / "data" / "synthetic" / "__wo0011_supplement_must_not_exist__"

    with pytest.raises(ValueError, match="inside the frozen synthetic data tree"):
        _validate_output_path(forbidden)

    assert not forbidden.exists()
