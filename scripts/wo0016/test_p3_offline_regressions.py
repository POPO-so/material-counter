"""P3 offline regressions: association gaps, touching splits and ROI edges.

These tests document the current `field-v1` behaviour on synthetic but
geometry-calibrated cases. They do not change the counting algorithm; a failure
means the documented behaviour moved and must be reviewed, not that the test
should be relaxed.
"""

import numpy as np
import cv2
import pytest

from scripts.wo0016.split_touching import (
    is_single_connected_component, split_touching_component,
)
from scripts.wo0016.stream_counter import Detection, MaskCounter


FIELD_V1 = dict(
    width=640, height=400,
    count_axis="x", line_x=320, direction=1,
    roi_mode="full", roi_top=0, roi_bottom=400,
    predictive_association=True, motion_timebase_s=0.1,
    max_step=200, max_lateral_step=12, strict_lateral_gate=True,
    max_association_interval_s=0.5, guarded_merge=True,
    single_intact_area=380, max_split_area=1200, min_split_child_area=80,
)
SPLIT = dict(min_child_area=80, min_hole_area=30, recursive_item_area=None)


def _detection(cx, cy=200):
    x0, x1 = max(0, cx - 10), min(639, cx + 10)
    y0, y1 = cy - 7, cy + 7
    return Detection(x_min=x0, x_max=x1, y_min=y0, y_max=y1,
                     area=min(300, (x1 - x0 + 1) * (y1 - y0 + 1)),
                     centroid_x=cx, centroid_y=cy)


def _replay(frames):
    """frames = [(frame_id, x_or_None, t_seconds), ...]"""
    counter = MaskCounter(**FIELD_V1)
    base = 1_000_000_000
    for frame_id, x, t in frames:
        counter.update_detections(
            frame_id, [_detection(x)] if x is not None else [],
            capture_monotonic_ns=base + int(t * 1e9))
    return counter.total_count


# ---------------------------------------------------------------- association

def test_gap_049s_still_associates_and_counts():
    assert _replay([(10, 60, 0.0), (11, 200, 0.1), (12, 340, 0.59)]) == 1


def test_gap_051s_finishes_the_track_and_loses_the_count():
    # 0.51 s exceeds max_association_interval_s=0.5; the new track is born
    # after the line and starts de-duplicated, so no event is emitted.
    assert _replay([(10, 60, 0.0), (11, 200, 0.1), (12, 340, 0.61)]) == 0


def test_gap_1s_finishes_the_track_and_loses_the_count():
    assert _replay([(10, 60, 0.0), (11, 200, 0.1), (12, 340, 1.1)]) == 0


# ------------------------------------------------------------------ ROI edges

def test_target_born_after_the_line_is_not_counted():
    assert _replay([(10, 340, 0.0), (11, 360, 0.1), (12, 380, 0.2)]) == 0


def test_target_that_disappears_before_the_line_is_not_counted():
    assert _replay([(10, 100, 0.0), (11, None, 0.1), (12, None, 0.2),
                    (13, 340, 0.3)]) == 0


def test_reentry_from_the_left_starts_a_new_count():
    # Documented current semantics: a target that vanished after the line and a
    # new target entering from the left are indistinguishable, so it counts.
    # This is the known duplicate risk if the same physical object bounces back.
    assert _replay([(10, 100, 0.0), (11, 200, 0.1), (12, 340, 0.2),
                    (13, None, 0.3), (14, None, 0.4), (15, None, 0.5),
                    (16, 0, 0.6), (17, 60, 0.7), (18, 200, 0.8),
                    (19, 340, 0.9)]) == 2


# ------------------------------------------------------------- touching split

def _ring(center, outer=12, inner=6, shape=(60, 90)):
    mask = np.zeros(shape, np.uint8)
    cv2.circle(mask, center, outer, 1, -1)
    cv2.circle(mask, center, inner, 0, -1)
    return mask


def _square(x0, y0, x1, y1, shape=(40, 100)):
    mask = np.zeros(shape, np.uint8)
    mask[y0:y1, x0:x1] = 1
    return mask


def _assert_partition(mask, pieces, min_area=80):
    assert len(pieces) >= 1
    union = np.logical_or.reduce(pieces)
    assert np.array_equal(union, mask != 0), "pieces must cover the input exactly"
    assert sum(int(piece.sum()) for piece in pieces) == int((mask != 0).sum()), \
        "pieces must not overlap"
    for piece in pieces:
        assert int(piece.sum()) >= min_area
        assert is_single_connected_component(piece)


def test_single_ring_is_not_split():
    mask = _ring((45, 30))
    pieces = split_touching_component(mask, **SPLIT)
    assert len(pieces) == 1
    assert np.array_equal(np.asarray(pieces[0]), mask != 0)


def test_single_square_is_not_split():
    mask = _square(10, 10, 40, 35)
    assert len(split_touching_component(mask, **SPLIT)) == 1


def test_two_touching_rings_split_into_two():
    mask = np.maximum(_ring((30, 30)), _ring((52, 30)))
    pieces = split_touching_component(mask, **SPLIT)
    assert len(pieces) == 2
    _assert_partition(mask, pieces)


def test_overlapping_m6_style_rings_split_into_two():
    mask = np.maximum(_ring((32, 30)), _ring((50, 30)))
    pieces = split_touching_component(mask, **SPLIT)
    assert len(pieces) == 2
    _assert_partition(mask, pieces)


def test_dense_three_rings_split_into_three():
    mask = np.maximum(np.maximum(_ring((20, 30)), _ring((42, 30))),
                      _ring((64, 30)))
    pieces = split_touching_component(mask, **SPLIT)
    assert len(pieces) == 3
    _assert_partition(mask, pieces)


def test_narrow_neck_splits_two_solids():
    mask = _square(5, 10, 35, 30)
    mask[18:20, 35:40] = 1
    mask[10:30, 40:60] = 1
    pieces = split_touching_component(mask, **SPLIT)
    assert len(pieces) == 2
    _assert_partition(mask, pieces)


def test_shared_edge_between_solids_does_not_split():
    mask = np.maximum(_square(10, 10, 35, 35), _square(35, 10, 60, 35))
    assert len(split_touching_component(mask, **SPLIT)) == 1


def test_wide_bridge_does_not_split():
    mask = _square(5, 10, 30, 30)
    mask[10:30, 30:50] = 1
    mask[10:30, 50:70] = 1
    assert len(split_touching_component(mask, **SPLIT)) == 1


def test_empty_and_tiny_components_are_returned_unchanged():
    for mask in (np.zeros((20, 20), np.uint8), _square(5, 5, 8, 8, shape=(20, 20))):
        pieces = split_touching_component(mask, **SPLIT)
        assert len(pieces) == 1