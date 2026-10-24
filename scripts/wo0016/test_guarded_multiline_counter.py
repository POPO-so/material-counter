from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stream_counter import Detection, MaskCounter


def _box(x_min, y_min, width, height, area):
    x_max = x_min + width - 1
    y_max = y_min + height - 1
    return Detection(
        x_min=x_min,
        x_max=x_max,
        y_min=y_min,
        y_max=y_max,
        area=area,
        centroid_x=(x_min + x_max) / 2,
        centroid_y=(y_min + y_max) / 2,
    )


def _parts(mask, x, rows, *, size=5):
    half = size // 2
    for center_y in rows:
        mask[center_y-half:center_y+half+1, x-half:x+half+1] = 0xFF


def _counter(**kwargs):
    defaults = {
        "width": 320,
        "height": 240,
        "count_axis": "x",
        "line_x": 160,
        "max_step": 48,
        "max_lateral_step": 10,
        "max_missing": 3,
        "backward_tolerance": 4,
        "confirm_hits": 2,
        "hysteresis": 2,
        "roi_mode": "full",
        "guarded_merge": True,
        "component_cluster_gap": 14,
        "strict_lateral_gate": True,
    }
    defaults.update(kwargs)
    return MaskCounter(**defaults)


def test_multiline_options_have_safe_defaults_and_strict_validation():
    legacy = MaskCounter()
    assert legacy.roi_mode == "lane"
    assert legacy.guarded_merge is False
    assert legacy.strict_lateral_gate is False
    assert legacy.single_intact_area == 95
    assert legacy.max_merge_area == 260
    assert legacy.max_split_area == 900
    assert MaskCounter(max_split_area=None).max_split_area == 260
    assert legacy.max_merged_span == 24

    configured = MaskCounter(
        roi_mode="full", guarded_merge=True, strict_lateral_gate=True,
        single_intact_area=95, max_merge_area=260, max_merged_span=24,
    )
    assert configured.roi_mode == "full"
    assert configured.guarded_merge is True
    assert configured.strict_lateral_gate is True

    for options in (
        {"roi_mode": "crop"},
        {"roi_mode": 1},
        {"guarded_merge": 1},
        {"strict_lateral_gate": 0},
        {"single_intact_area": True},
        {"max_merge_area": 260.0},
        {"max_merged_span": False},
        {"single_intact_area": 0},
        {"max_merge_area": -1},
        {"max_split_area": 0},
        {"max_merged_span": 0},
    ):
        with pytest.raises(ValueError):
            MaskCounter(**options)


def test_full_roi_includes_top_and_bottom_while_default_lane_does_not():
    mask = np.zeros((240, 320), dtype=np.uint8)
    _parts(mask, 80, (15, 225))

    lane_result = MaskCounter().update(0, mask)
    assert (lane_result.roi_top, lane_result.roi_bottom) == (96, 145)
    assert lane_result.detections == ()

    full_result = MaskCounter(roi_mode="full").update(0, mask)
    assert (full_result.roi_top, full_result.roi_bottom) == (0, 240)
    assert [round(d.centroid_y) for d in full_result.detections] == [15, 225]


def test_guarded_merge_rejects_two_intact_neighbors_and_accepts_exact_caps():
    intact = _counter().update_detections(0, [
        _box(0, 0, 10, 10, 95),
        _box(14, 0, 10, 10, 95),
    ])
    assert len(intact.detections) == 2

    # The first two 85-pixel fragments form 170; a 90-pixel fragment then
    # brings the root to exactly 260 pixels and a 24 by 24 union box.
    exact = _counter().update_detections(0, [
        _box(0, 0, 10, 10, 85),
        _box(14, 0, 10, 10, 85),
        _box(14, 14, 10, 10, 90),
    ])
    assert len(exact.detections) == 1
    merged = exact.detections[0]
    assert merged.area == 260
    assert (merged.x_max - merged.x_min + 1,
            merged.y_max - merged.y_min + 1) == (24, 24)
    assert merged.centroid_x == pytest.approx(
        sum(d.area * d.centroid_x for d in exact.raw_detections) / 260
    )
    assert merged.centroid_y == pytest.approx(
        sum(d.area * d.centroid_y for d in exact.raw_detections) / 260
    )

    gap_counter = _counter(max_merged_span=30)
    at_gap_limit = gap_counter.update_detections(0, [
        _box(0, 30, 5, 5, 25),
        _box(19, 30, 5, 5, 25),
    ])
    beyond_gap_limit = _counter(max_merged_span=30).update_detections(0, [
        _box(0, 30, 5, 5, 25),
        _box(20, 30, 5, 5, 25),
    ])
    assert len(at_gap_limit.detections) == 1
    assert len(beyond_gap_limit.detections) == 2


def test_guarded_merge_rechecks_aggregate_area_and_span_on_fragment_chains():
    too_much_area = _counter().update_detections(0, [
        _box(0, 0, 10, 10, 85),
        _box(14, 0, 10, 10, 85),
        _box(14, 14, 10, 10, 91),
    ])
    assert sorted(d.area for d in too_much_area.detections) == [91, 170]

    # A small middle fragment may join one intact component, but may not
    # bridge two intact components into one current root.
    bridge = [
        _box(0, 0, 10, 10, 95),
        _box(14, 0, 10, 10, 12),
        _box(28, 0, 10, 10, 95),
    ]
    outputs = []
    for order in (bridge, list(reversed(bridge)), [bridge[1], bridge[0], bridge[2]]):
        result = _counter().update_detections(0, order)
        outputs.append(result.detections)
        assert len(result.detections) == 2
        assert sorted(d.area for d in result.detections) == [95, 107]
    assert outputs[0] == outputs[1] == outputs[2]

    # Pairwise gaps pass, but the transitive root would exceed the 24-pixel span.
    too_wide = _counter().update_detections(0, [
        _box(0, 0, 10, 10, 40),
        _box(14, 0, 10, 10, 40),
        _box(28, 0, 10, 10, 40),
    ])
    assert sorted(d.area for d in too_wide.detections) == [40, 80]
    assert max(d.x_max - d.x_min + 1 for d in too_wide.detections) <= 24


def test_multiline_tracks_count_both_edge_rows_in_both_directions_only_once():
    for direction, xs in (
        (1, (140, 150, 170, 180, 190)),
        (-1, (180, 170, 150, 140, 130)),
    ):
        counter = _counter(direction=direction)
        totals = []
        frame_two_events = ()
        for frame_id, x in enumerate(xs):
            mask = np.zeros((240, 320), dtype=np.uint8)
            _parts(mask, x, (15, 225))
            result = counter.update(frame_id, mask)
            totals.append(result.total_count)
            if frame_id == 2:
                frame_two_events = result.events

        assert totals == [0, 0, 2, 2, 2]
        assert len(frame_two_events) == 2
        assert len({event.track_id for event in frame_two_events}) == 2


def test_strict_lateral_gate_does_not_expand_after_missing_frames():
    def detection(x, y):
        return _box(x - 2, y - 2, 5, 5, 25)

    strict = _counter()
    legacy = _counter(strict_lateral_gate=False)
    for counter in (strict, legacy):
        counter.update_detections(0, [detection(130, 40)])
        counter.update_detections(1, [detection(142, 40)])
        counter.update_detections(2, [])
        counter.update_detections(3, [])

    strict_result = strict.update_detections(4, [detection(174, 55)])
    legacy_result = legacy.update_detections(4, [detection(174, 55)])

    assert len(strict.last_associations) == 1
    assert strict.last_associations[0]["kind"] == "new"
    assert strict.last_associations[0]["previous_frame"] is None
    assert not strict_result.events
    assert strict.total_count == 0
    assert legacy.last_associations[0]["kind"] == "predicted_gap_inferred"
    assert len(legacy_result.events) == 1


def test_missing_observations_never_emit_a_prediction_only_count():
    counter = _counter(prediction_max_missing=2)

    def detection(x):
        return _box(x - 2, 38, 5, 5, 25)

    counter.update_detections(0, [detection(130)])
    counter.update_detections(1, [detection(142)])
    results = [counter.update_detections(frame_id, []) for frame_id in (2, 3, 4)]

    assert counter.total_count == 0
    assert all(not result.events for result in results)


def test_large_raw_component_stays_one_detection_and_counts_once():
    counter = _counter()

    def large(x):
        return _box(x - 10, 30, 20, 20, 400)

    totals = [counter.update_detections(frame_id, [large(x)]).total_count
              for frame_id, x in enumerate((140, 150, 170, 180))]

    assert totals == [0, 0, 1, 1]
