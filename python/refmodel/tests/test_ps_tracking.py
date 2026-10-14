from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from refmodel.ps_reference.regions import Region
from refmodel.ps_reference.tracking import Tracker, TrackerConfig


def feature(x, y=50, flags=4):
    return Region(int(x - 3), int(x + 3), int(y - 3), int(y + 3), 49, float(x), float(y), flags=flags)


def tracker(**kwargs):
    return Tracker(TrackerConfig(line_x=100, **kwargs))


def test_confirmed_crossing_and_once_only():
    t = tracker()
    events = [e for f, x in enumerate([90, 94, 98, 100, 102, 104, 106, 105, 108]) for e in t.update(f, [feature(x)])]
    assert len(events) == t.count == 1
    assert events[0].frame_id == 5
    assert events[0].track_id == 1


def test_hysteresis_thresholds_are_strict():
    t = tracker()
    assert not t.update(0, [feature(98)])  # equality does not arm
    assert not t.update(1, [feature(97)])
    assert not t.update(2, [feature(102)])  # equality does not count
    assert len(t.update(3, [feature(103)])) == 1


def test_birth_downstream_never_counts():
    t = tracker()
    for f, x in enumerate([101, 105, 109, 113]):
        assert t.update(f, [feature(x)]) == []
    assert t.count == 0
    assert t.tracks[1].born_counted


def test_two_missing_frames_recover_without_prediction_count():
    t = tracker()
    assert not t.update(0, [feature(90)])
    assert not t.update(1, [feature(94)])
    assert not t.update(2, [])
    assert not t.update(3, [])
    assert len(t.update(4, [feature(106)])) == 1
    assert t.last_assignments == [(1, 0)]


def test_lost_after_count_reappears_without_duplicate():
    t = tracker()
    for f, x in enumerate([90, 96, 103]):
        t.update(f, [feature(x)])
    assert t.count == 1
    t.update(3, [])
    t.update(4, [])
    assert not t.update(5, [feature(119)])
    assert t.count == 1


def test_long_absence_crossing_is_known_miss_not_invented_event():
    t = tracker()
    t.update(0, [feature(90)])
    t.update(1, [feature(94)])
    for f in range(2, 8):
        assert not t.update(f, [])
    assert not t.update(8, [feature(122)])
    assert not t.update(9, [feature(126)])
    assert t.count == 0
    assert t.tracks[1].state == 'finished'


def test_disappear_before_line_and_reappear_can_still_count():
    t = tracker()
    t.update(0, [feature(80)])
    t.update(1, [feature(82)])
    for f in range(2, 7):
        t.update(f, [])
    for f, x in enumerate([92, 96, 100, 104], 7):
        t.update(f, [feature(x)])
    assert t.count == 1


def test_duplicate_records_and_duplicate_frame_rejection():
    t = tracker()
    for f, x in enumerate([90, 96, 103]):
        t.update(f, [feature(x), feature(x)])
    assert t.count == 1
    assert t.duplicate_detections == 3
    with pytest.raises(ValueError):
        t.update(2, [feature(110)])
    assert t.count == 1


@pytest.mark.parametrize('step', [0.5, 1.0, 1.5, 4.0])
def test_speed_changes_and_pause(step):
    t = tracker()
    x = 80.0
    for f in range(100):
        if f % 11 != 0:
            x += step if f < 30 else step / 2
        t.update(f, [feature(x)])
    assert t.count == 1


def test_same_x_separate_rows_keep_identity_when_order_reverses():
    t = tracker()
    for f, x in enumerate([90, 94, 98, 104]):
        items = [feature(x, 40), feature(x, 70)]
        t.update(f, items if f % 2 == 0 else items[::-1])
    assert t.count == 2
    assert t.tracks[1].region.centroid_y == 40
    assert t.tracks[2].region.centroid_y == 70


def test_short_lived_noise_and_merge_flag_do_not_count():
    t = tracker()
    t.update(0, [feature(95)])
    assert not t.update(1, [])
    assert not t.update(2, [feature(105, flags=5)])
    assert t.count == 0
    assert t.merge_observations == 1


def test_reverse_direction():
    t = tracker(direction=-1)
    for f, x in enumerate([110, 106, 102, 98, 94]):
        t.update(f, [feature(x)])
    assert t.count == 1


def test_gt_identity_cannot_be_input():
    t = tracker()
    with pytest.raises(TypeError):
        t.update(0, [{'object_id': 1, 'centroid_x': 90}])
    assert t.last_frame_id is None


def test_invalid_configuration():
    for kwargs in ({'direction': 0}, {'confirm_hits': 1}, {'hysteresis': -1}, {'max_missing': -1}, {'max_step': 0}):
        with pytest.raises(ValueError):
            Tracker(TrackerConfig(**kwargs))


@pytest.mark.parametrize('flags_order', [(5, 4), (4, 5)])
def test_same_geometry_different_flags_is_not_an_exact_duplicate(flags_order):
    t = tracker()
    for f, x in enumerate([90, 96, 103]):
        t.update(f, [feature(x, flags=flag) for flag in flags_order])
    assert t.count == 1
    assert t.duplicate_detections == 0
    assert t.merge_observations == 3


def test_counted_track_smoothly_recrosses_without_second_event():
    t = tracker()
    for f, x in enumerate([90, 94, 98, 101, 104, 102, 100, 98, 100, 102, 104]):
        t.update(f, [feature(x)])
    assert t.count == 1
    assert t.next_id == 2


def test_skipped_frame_ids_use_elapsed_time_and_old_frame_is_rejected():
    t = tracker()
    t.update(0, [feature(90)])
    t.update(1, [feature(94)])
    assert len(t.update(4, [feature(106)])) == 1
    assert t.last_assignments == [(1, 0)]
    with pytest.raises(ValueError):
        t.update(3, [])
    assert t.count == 1


def test_confirmation_needs_consecutive_observations():
    t = tracker()
    t.update(0, [feature(90)])
    t.update(2, [feature(96)])
    assert not t.tracks[1].confirmed
    assert len(t.update(3, [feature(103)])) == 1


def test_expired_downstream_reappearance_gets_new_suppressed_identity():
    t = tracker()
    for f, x in enumerate([90, 96, 103]):
        t.update(f, [feature(x)])
    for f in range(3, 8):
        t.update(f, [])
    assert not t.update(8, [feature(122)])
    assert t.last_assignments == [(2, 0)]
    assert t.tracks[2].born_counted
    assert t.count == 1


def test_unsupported_reverse_reentry_can_duplicate_physical_object():
    # The same physical target cannot be recognized after this reverse jump
    # from geometry alone. Preserve this failure, not an artificial GT identity.
    t = tracker()
    for f, x in enumerate([90, 96, 103, 80, 86, 92, 98, 104]):
        t.update(f, [feature(x)])
    assert t.count == 2
