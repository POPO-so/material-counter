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
    assert legacy.max_split_area == 260
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


def test_confirmed_crossed_cluster_parent_recovers_stable_children_once():
    counter = _counter(cluster_parent_recovery=True)

    def parent(x):
        return _box(x, 40, 30, 12, 240)

    def child(x):
        return _box(x, 42, 8, 8, 64)

    for frame_id, x in enumerate((100, 120, 140, 150)):
        events = counter._associate_and_update(
            frame_id, [], cluster_parent_observations=((parent(x), 2),)
        )
        assert not events
    assert counter.total_count == 0
    assert any(row["state"] == "crossed"
               for row in counter.last_cluster_parent_diagnostics)

    first = counter._associate_and_update(4, [child(162), child(178)])
    recovered = counter._associate_and_update(5, [child(172), child(188)])
    repeated = counter._associate_and_update(6, [child(182), child(198)])

    assert not first
    assert len(recovered) == 2
    assert all(event.inferred_gap for event in recovered)
    assert counter.total_count == 2
    assert not repeated
    assert counter.total_count == 2


def test_parent_prediction_uses_capture_time_across_slow_frames():
    counter = _counter(cluster_parent_recovery=True, motion_timebase_s=.1)
    parent = lambda x: _box(x, 40, 30, 12, 240)
    for frame_id, x, stamp in ((0, 100, 100_000_000), (1, 110, 200_000_000),
                               (2, 140, 500_000_000)):
        counter._associate_and_update(frame_id, [], stamp,
                                     cluster_parent_observations=((parent(x), 2),))
    state = next(iter(counter._cluster_parents.values()))
    counter._associate_and_update(3, [], 700_000_000)
    left, right, _, _ = counter._parent_predicted_box(state, 3)
    assert (left + right) / 2 == pytest.approx(parent(160).centroid_x)


def test_parent_can_confirm_and_cross_across_bounded_frame_id_gap():
    counter = _counter(cluster_parent_recovery=True, motion_timebase_s=.1)
    parent = lambda x: _box(x, 40, 30, 12, 240)
    for frame_id, x, stamp in ((0, 100, 100_000_000), (3, 120, 300_000_000),
                               (5, 150, 600_000_000)):
        counter._associate_and_update(frame_id, [], stamp,
                                     cluster_parent_observations=((parent(x), 2),))
    assert any(p.crossed for p in counter._cluster_parents.values())


def test_parent_wall_time_timeout_expires_even_with_adjacent_frame_ids():
    counter = _counter(cluster_parent_recovery=True, motion_timebase_s=.1)
    counter._associate_and_update(0, [], 100_000_000,
        cluster_parent_observations=((_box(100, 40, 30, 12, 240), 2),))
    counter._associate_and_update(1, [], 1_400_000_001)
    assert not counter._cluster_parents


def test_irregular_capture_intervals_recover_each_child_once():
    counter = _counter(cluster_parent_recovery=True, motion_timebase_s=.1)
    parent = lambda x: _box(x, 40, 30, 12, 240)
    for frame_id, x, stamp in ((0, 100, 100_000_000), (1, 120, 300_000_000),
                               (2, 140, 500_000_000), (3, 150, 600_000_000)):
        counter._associate_and_update(frame_id, [], stamp,
                                     cluster_parent_observations=((parent(x), 2),))
    events = []
    for frame_id, x, stamp in ((4, 162, 750_000_000), (5, 172, 850_000_000),
                               (6, 182, 950_000_000)):
        events.extend(counter._associate_and_update(frame_id,
            [_box(x, 42, 8, 8, 64), _box(x+16, 42, 8, 8, 64)], stamp))
    assert len(events) == counter.total_count == 2
    assert len({event.track_id for event in events}) == 2
    assert all(event.inferred_gap for event in events)


def test_recovered_cluster_excludes_previously_counted_child_track():
    counter = _counter(cluster_parent_recovery=True)

    def child(x):
        return _box(x, 42, 8, 8, 64)

    for frame_id, x in enumerate((140, 150, 160)):
        counter._associate_and_update(frame_id, [child(x)])
    assert counter.total_count == 1

    def parent(x):
        return _box(x, 36, 40, 20, 400)

    for frame_id, x in ((3, 130), (4, 140), (5, 150)):
        counter._associate_and_update(
            frame_id, [], cluster_parent_observations=((parent(x), 2),)
        )
    assert counter.total_count == 1

    counter._associate_and_update(6, [child(170), child(188)])
    recovered = counter._associate_and_update(7, [child(180), child(198)])
    assert len(recovered) == 1
    assert counter.total_count == 2
    assert recovered[0].track_id != 1


def test_new_downstream_tracks_without_a_cluster_parent_are_not_counted():
    counter = _counter(cluster_parent_recovery=True)
    results = [counter._associate_and_update(
        frame_id, [_box(x, 40, 8, 8, 64)]
    ) for frame_id, x in enumerate((170, 180, 190))]

    assert all(not events for events in results)
    assert counter.total_count == 0


def test_un_crossed_cluster_parent_does_not_recover_children():
    counter = _counter(cluster_parent_recovery=True)
    parent = lambda x: _box(x, 40, 30, 12, 240)
    counter._associate_and_update(
        0, [], cluster_parent_observations=((parent(100), 2),)
    )
    counter._associate_and_update(
        1, [], cluster_parent_observations=((parent(110), 2),)
    )
    first = counter._associate_and_update(
        2, [_box(120, 42, 8, 8, 64), _box(136, 42, 8, 8, 64)]
    )
    second = counter._associate_and_update(
        3, [_box(130, 42, 8, 8, 64), _box(146, 42, 8, 8, 64)]
    )

    assert not first and not second
    assert counter.total_count == 0


def test_overlapping_crossed_parents_do_not_claim_the_same_child_tracks():
    counter = _counter(cluster_parent_recovery=True)

    def parent(x, y):
        return _box(x, y, 30, 20, 400)

    for frame_id, x in enumerate((100, 120, 140, 150)):
        observations = ((parent(x, 40), 2), (parent(x, 60), 2))
        counter._associate_and_update(
            frame_id, [], cluster_parent_observations=observations
        )

    def child(x, y):
        return _box(x, y, 8, 8, 64)

    counter._associate_and_update(4, [child(164, 53), child(180, 60)])
    conflict_recorded = any(
        row["state"] == "child_association_conflict"
        for row in counter.last_cluster_parent_diagnostics
    )
    counter._associate_and_update(5, [child(174, 53), child(190, 60)])

    assert counter.total_count == 0
    assert conflict_recorded


def test_crossed_parent_timeout_discards_pending_children_without_counting():
    counter = _counter(cluster_parent_recovery=True)
    parent = lambda x: _box(x, 40, 30, 12, 240)
    for frame_id, x in enumerate((100, 120, 140, 150)):
        counter._associate_and_update(
            frame_id, [], cluster_parent_observations=((parent(x), 2),)
        )
    assert counter._cluster_parents

    counter._associate_and_update(16, [])
    assert not counter._cluster_parents
    assert any(row["state"] == "expired"
               for row in counter.last_cluster_parent_diagnostics)

    first = counter._associate_and_update(17, [_box(170, 42, 8, 8, 64),
                                                _box(188, 42, 8, 8, 64)])
    second = counter._associate_and_update(18, [_box(180, 42, 8, 8, 64),
                                                 _box(198, 42, 8, 8, 64)])
    assert not first and not second
    assert counter.total_count == 0


def test_clear_discards_unresolved_cluster_parent_recovery():
    counter = _counter(cluster_parent_recovery=True)
    parent = lambda x: _box(x, 40, 30, 12, 240)
    for frame_id, x in enumerate((100, 120, 140, 150)):
        counter._associate_and_update(
            frame_id, [], cluster_parent_observations=((parent(x), 2),)
        )
    assert counter._cluster_parents

    counter.clear_pending_cluster_parents()
    first = counter._associate_and_update(4, [_box(170, 42, 8, 8, 64),
                                               _box(188, 42, 8, 8, 64)])
    clear_recorded = any(
        row["state"] == "cleared" for row in counter.last_cluster_parent_diagnostics
    )
    second = counter._associate_and_update(5, [_box(180, 42, 8, 8, 64),
                                                _box(198, 42, 8, 8, 64)])

    assert not first and not second
    assert counter.total_count == 0
    assert clear_recorded
