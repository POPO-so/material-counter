"""WO-0012 bounded row-run labeler contracts, isolated from production RTL."""
from pathlib import Path
import sys

import numpy as np
import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[2]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))


def _api():
    try:
        from refmodel.experiments import wo0012_bounded_runs
    except ImportError:
        pytest.fail("WO-0012 bounded run model is not implemented yet")
    return wo0012_bounded_runs


def _extract(binary, *, row=32, active=32, records=32, min_area=1):
    api = _api()
    return api.extract_run_frame(
        binary,
        caps=api.RunCaps(row_run_cap=row, active_cap=active, record_cap=records),
        min_area=min_area,
    )


def _mask(height, width, pixels):
    result = np.zeros((height, width), dtype=np.uint8)
    for y, x0, x1 in pixels:
        result[y, x0:x1 + 1] = 0xFF
    return result


def test_a_hole_can_split_and_rejoin_one_predecessor_identity():
    binary = _mask(3, 9, [
        (0, 1, 7),
        (1, 1, 2), (1, 6, 7),
        (2, 1, 7),
    ])

    result = _extract(binary)

    assert result.accepted
    assert result.rejection_reason is None
    assert len(result.regions) == 1
    region = result.regions[0]
    assert region.bbox == (1, 7, 0, 2)
    assert region.area == 18
    assert (region.centroid_x, region.centroid_y) == (4.0, 1.0)
    assert result.component_sums == [{"sum_x": 72, "sum_y": 18}]


def test_two_independent_roots_converging_into_one_run_rejects_whole_frame():
    binary = _mask(2, 9, [
        (0, 1, 2), (0, 6, 7),
        (1, 1, 7),
    ])

    result = _extract(binary)

    assert not result.accepted
    assert result.rejection_reason == "multi_root_merge"
    assert result.regions == []
    assert result.component_sums == []


def test_diagonal_pixels_are_connected_with_eight_connectivity():
    binary = _mask(3, 5, [(0, 1, 1), (1, 2, 2), (2, 3, 3)])

    result = _extract(binary)

    assert result.accepted
    assert len(result.regions) == 1
    assert result.regions[0].area == 3
    assert result.regions[0].bbox == (1, 3, 0, 2)
    assert result.component_sums == [{"sum_x": 6, "sum_y": 3}]


def test_row_tail_and_last_image_row_are_flushed_with_edge_flag():
    binary = _mask(2, 7, [(1, 3, 6)])

    result = _extract(binary)

    assert result.accepted
    assert len(result.regions) == 1
    assert result.regions[0].bbox == (3, 6, 1, 1)
    assert result.regions[0].area == 4
    assert result.regions[0].flags == 6


def test_completed_label_slot_is_reused_after_a_component_ends():
    binary = _mask(5, 8, [(0, 1, 2), (4, 5, 6)])

    result = _extract(binary, active=1, records=2)

    assert result.accepted
    assert [region.bbox for region in result.regions] == [
        (1, 2, 0, 0), (5, 6, 4, 4),
    ]
    assert result.stats["active_peak"] == 1
    assert result.stats["completed_regions_per_frame"] == 2


def test_packet_order_is_component_completion_order_without_a_hardware_sorter():
    binary = _mask(5, 30, [(0, 20, 22), (3, 1, 3)])
    result = _extract(binary, active=1, records=2)
    assert result.accepted
    assert [r.bbox for r in result.regions] == [(20, 22, 0, 0), (1, 3, 3, 3)]


def test_old_and_new_label_groups_on_adjacent_rows_share_the_same_active_slots():
    binary = np.zeros((2, 240), dtype=np.uint8)
    binary[0, 0:40:2] = 0xFF
    binary[1, 200:240:2] = 0xFF

    result = _extract(binary, row=32, active=32, records=64, min_area=1)

    assert result.accepted
    assert len(result.regions) == 40
    assert result.stats["active_peak"] == 20
    assert result.stats["completed_regions_per_frame"] == 40


def test_empty_frame_is_valid_and_has_zero_high_water():
    result = _extract(np.zeros((4, 7), dtype=np.uint8))

    assert result.accepted
    assert result.regions == []
    assert result.stats["run_count"] == 0
    assert result.stats["max_runs_per_row"] == 0
    assert result.stats["active_peak"] == 0
    assert result.stats["completed_regions_per_frame"] == 0


def test_raw_160_run_row_exceeds_capacity_even_when_area_filter_would_remove_noise():
    binary = np.zeros((3, 320), dtype=np.uint8)
    binary[1, ::2] = 0xFF

    result = _extract(binary, row=32, active=200, records=200, min_area=16)

    assert not result.accepted
    assert result.rejection_reason == "row_run_cap_exceeded"
    assert result.regions == []
    assert result.stats["max_runs_per_row"] == 160


def test_active_slot_overflow_rejects_frame_without_partial_regions():
    binary = _mask(2, 9, [(0, 1, 1), (0, 4, 4), (0, 7, 7)])

    result = _extract(binary, row=8, active=2, records=8)

    assert not result.accepted
    assert result.rejection_reason == "active_cap_exceeded"
    assert result.regions == []
    assert result.stats["active_peak"] == 3


def test_record_output_overflow_rejects_frame_without_partial_regions():
    binary = _mask(3, 8, [(0, 1, 1), (2, 6, 6)])

    result = _extract(binary, row=8, active=8, records=1, min_area=1)

    assert not result.accepted
    assert result.rejection_reason == "record_cap_exceeded"
    assert result.regions == []
    assert result.stats["completed_regions_per_frame"] == 0
    assert result.stats["completed_before_reject"] == 1


@pytest.mark.parametrize(("height", "width", "area", "accepted", "reason"), [
    (255, 257, 65_535, True, None),
    (256, 256, 65_536, False, "area_overflow"),
])
def test_component_area_limit_is_explicit(height, width, area, accepted, reason):
    binary = np.full((height, width), 0xFF, dtype=np.uint8)

    result = _extract(binary, row=1, active=1, records=1)

    assert result.stats["foreground_pixels"] == area
    assert result.accepted is accepted
    assert result.rejection_reason == reason
    if accepted:
        assert result.regions[0].area == 65_535
    else:
        assert result.regions == []


def test_bounded_features_match_opencv_exact_integer_moments():
    cv2 = pytest.importorskip("cv2")
    binary = np.zeros((31, 47), dtype=np.uint8)
    binary[2:8, 1:6] = 0xFF
    binary[2:8, 11:16] = 0xFF
    binary[12:21, 1:10] = 0xFF
    binary[15:18, 4:7] = 0x00
    binary[10:19, 24:33] = 0xFF
    binary[13:16, 27:30] = 0x00
    binary[22:30, 38:45] = 0xFF
    result = _extract(binary, row=32, active=256, records=256, min_area=1)

    assert result.accepted
    comparison = _api().compare_with_opencv(binary, result)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8, ltype=cv2.CV_32S)
    assert comparison["exact"]
    assert comparison["opencv_components"] == count - 1
    assert comparison["candidate_components"] == count - 1
    assert len(result.component_sums) == count - 1
    assert all(isinstance(item["sum_x"], int) and isinstance(item["sum_y"], int)
               for item in result.component_sums)


def test_twenty_disjoint_hollow_targets_measure_forty_row_runs_not_twenty_labels():
    result = _api().evaluate_hollow_target_envelope()

    assert result["accepted"]
    assert result["observed_row_run_peak"] == 40
    assert result["observed_active_peak"] == 20
    assert result["observed_record_count"] == 20
    assert result["opencv_comparison"]["exact"]


@pytest.mark.parametrize("bad", [
    np.zeros((3, 4), dtype=np.uint8) + 1,
    np.zeros(4, dtype=np.uint8),
    np.zeros((0, 4), dtype=np.uint8),
])
def test_binary_frame_validation_rejects_non_binary_or_invalid_shapes(bad):
    api = _api()
    with pytest.raises(ValueError):
        api.extract_run_frame(bad, caps=api.RunCaps(8, 8, 8))
