"""Frame-level overload handling must preserve unresolved observations."""
import numpy as np
import pytest

from scripts.rle.counter import RleCounter
from scripts.rle.test_counter import library, frame


def rectangles():
    mask = np.zeros((400, 640), np.uint8)
    for x, y in ((100, 80), (300, 180), (500, 280)):
        mask[y:y+20, x:x+30] = 255
    return mask


def test_field_profile_enables_frame_budget_and_bounded_retry():
    from scripts.standalone.count_controls import make_runtime_counter
    counter = make_runtime_counter(None, profile='field-v1')
    assert counter.split_max_candidates == 2
    assert counter.split_max_pixels == 65536
    assert counter.split_budget_ms == 8.0
    assert counter.split_retry_interval_s == .15


def test_legacy_profile_keeps_unlimited_split_admission():
    from scripts.standalone.count_controls import make_runtime_counter
    counter = make_runtime_counter(None, profile='legacy')
    assert counter.split_max_candidates is None
    assert counter.split_budget_ms is None


def settings(library, **kwargs):
    return dict(library=library, guarded_merge=True, single_intact_area=380,
                max_split_area=8192, cluster_parent_recovery=True, **kwargs)


def test_candidate_budget_defers_without_treating_unknown_parent_as_single(library, monkeypatch):
    import scripts.rle.counter as module
    calls = []
    monkeypatch.setattr(module, 'split_touching_component',
                        lambda mask, **kw: calls.append(mask.shape) or (mask,))
    counter = RleCounter(**settings(library, split_max_candidates=1))
    result = counter.update_rle(frame(rectangles()), capture_monotonic_ns=100_000_000)
    assert len(calls) == 1
    assert len(result.raw_detections) == 1
    assert len(counter.rejected_detections) == 2
    assert counter.last_split_stats['deferred'] == 2
    assert not result.events


def test_waiting_candidates_are_eventually_attempted(library, monkeypatch):
    import scripts.rle.counter as module
    monkeypatch.setattr(module, 'split_touching_component', lambda mask, **kw: (mask,))
    counter = RleCounter(**settings(library, split_max_candidates=1, split_retry_interval_s=.5))
    attempted = set()
    for number in range(4):
        counter.update_rle(frame(rectangles(), number), capture_monotonic_ns=(number+1)*100_000_000)
        attempted.update(row['x_min'] for row in counter.last_split_diagnostics
                         if row['state'] == 'attempted')
    assert attempted == {100, 300, 500}


def test_elapsed_budget_stops_starting_more_candidates(library, monkeypatch):
    import scripts.rle.counter as module
    clock = [0.0]
    calls = []
    monkeypatch.setattr(module.time, 'perf_counter', lambda: clock[0])

    def expensive(mask, **kwargs):
        calls.append(1)
        clock[0] += .02
        return (mask,)

    monkeypatch.setattr(module, 'split_touching_component', expensive)
    counter = RleCounter(**settings(library, split_budget_ms=5))
    result = counter.update_rle(frame(rectangles()))
    assert len(calls) == 1
    assert result.roi_subtimings_ms['split_ms'] >= 20
    assert counter.last_split_stats['deferred'] == 2


def test_parent_pixel_budget_blocks_oversized_task(library):
    counter = RleCounter(**settings(library, split_max_pixels=500))
    result = counter.update_rle(frame(rectangles()))
    assert not result.detections
    assert counter.last_split_stats['attempted'] == 0
    assert counter.last_split_stats['deferred'] == 3


def test_unchanged_failed_parent_is_reobserved_without_rebuilding_mask(library, monkeypatch):
    import cv2
    import scripts.rle.counter as module
    mask = np.zeros((400, 640), np.uint8)
    for x in (290, 313):
        cv2.circle(mask, (x, 100), 13, 255, -1)
        cv2.circle(mask, (x, 100), 6, 0, -1)
    calls = []
    original = module.local_mask
    monkeypatch.setattr(module, 'local_mask', lambda *a, **kw: calls.append(1) or original(*a, **kw))
    monkeypatch.setattr(module, 'split_touching_component', lambda mask, **kw: (mask,))
    counter = RleCounter(**settings(library, split_retry_interval_s=.15))
    for number, stamp in enumerate((100_000_000, 150_000_000)):
        result = counter.update_rle(frame(mask, number), capture_monotonic_ns=stamp)
        assert not result.detections
    assert len(calls) == 1
    assert counter.last_split_stats['cached'] == 1
    assert next(iter(counter._cluster_parents.values())).seed_count == 2
    # Deformation invalidates the cached result before the retry interval.
    mask[87, 290] = 0
    counter.update_rle(frame(mask, 2), capture_monotonic_ns=180_000_000)
    assert len(calls) == 2


def test_verified_partition_is_reused_only_for_identical_current_runs(library, monkeypatch):
    import cv2
    import scripts.rle.counter as module
    mask = np.zeros((400, 640), np.uint8)
    for x in (260, 283):
        cv2.circle(mask, (x, 100), 13, 255, -1)
        cv2.circle(mask, (x, 100), 6, 0, -1)
    calls = []
    original = module.local_mask
    monkeypatch.setattr(module, 'local_mask', lambda *a, **kw: calls.append(1) or original(*a, **kw))
    counter = RleCounter(**settings(library, split_retry_interval_s=.15))
    first = counter.update_rle(frame(mask), capture_monotonic_ns=100_000_000)
    shifted = np.roll(mask, 5, axis=1)
    second = counter.update_rle(frame(shifted, 1), capture_monotonic_ns=150_000_000)
    assert len(first.detections) == len(second.detections) == 2
    assert len(calls) == 1
    assert [d.centroid_x for d in second.detections] == pytest.approx(
        [d.centroid_x+5 for d in first.detections])
    shifted[87, 265] = 0
    counter.update_rle(frame(shifted, 2), capture_monotonic_ns=180_000_000)
    assert len(calls) == 2


def test_ambiguous_parent_identity_never_borrows_cached_partition(library, monkeypatch):
    import cv2
    import scripts.rle.counter as module
    mask = np.zeros((400, 640), np.uint8)
    for x in (250, 273, 350, 373):
        cv2.circle(mask, (x, 100), 13, 255, -1)
        cv2.circle(mask, (x, 100), 6, 0, -1)
    calls = []
    original = module.local_mask
    monkeypatch.setattr(module, 'local_mask', lambda *a, **kw: calls.append(1) or original(*a, **kw))
    counter = RleCounter(**settings(library, count_axis='x', max_step=200, split_retry_interval_s=.3))
    counter.update_rle(frame(mask), capture_monotonic_ns=100_000_000)
    counter.update_rle(frame(mask, 1), capture_monotonic_ns=200_000_000)
    assert len(calls) == 4
    assert counter.last_split_stats['cached'] == 0


@pytest.mark.parametrize('setting,value', [('split_max_candidates', 0),
    ('split_max_pixels', True), ('split_budget_ms', float('nan')),
    ('split_retry_interval_s', -1)])
def test_invalid_split_settings_rejected(setting, value):
    with pytest.raises(ValueError):
        RleCounter(library=None, **{setting: value})
