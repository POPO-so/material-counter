"""Diagnostic capture must include visible targets even without count events."""
from dataclasses import replace
import json
import threading

import cv2
import numpy as np
import pytest

from scripts.rle.protocol import encode_mask, decode_packet
from scripts.wo0016.stream_counter import MaskCounter


def counter(**kwargs):
    return MaskCounter(width=640, height=400, count_axis='x', line_x=320,
                       roi_mode='full', min_area=12, max_area=3600,
                       max_step=80, discard_finished_tracks=True, **kwargs)


def observe(recorder, tracker, number, x, *, epoch=0, paused=False):
    mask = np.zeros((400, 640), np.uint8)
    mask[100:112, x:x+12] = 255
    packet = decode_packet(encode_mask(mask, frame_id=number))
    result = tracker.update(number, mask)
    if paused:
        result = replace(result, events=(), suppressed_events=result.events)
    gray = np.full_like(mask, number + 30)
    recorder.observe(gray, packet, result, tracker, trajectory_epoch=epoch,
                     capture_monotonic_ns=(number+1)*100_000_000,
                     camera_sequence=number, paused=paused, commands=[])
    return gray, result


def recorder(tmp_path, **kwargs):
    from scripts.standalone.crossing_snapshots import CrossingSnapshots
    return CrossingSnapshots(tmp_path/'captures', identity={'boot_id':'boot', 'session_id':'session'},
                             min_free_bytes=0, **kwargs)


def records(tmp_path):
    return [json.loads(p.read_text()) for p in sorted((tmp_path/'captures').glob('event-*/event.json'))]


def test_unarmed_visible_box_is_saved_with_exact_images_and_context(tmp_path):
    capture = recorder(tmp_path)
    tracker = counter()
    gray, result = observe(capture, tracker, 0, 315)
    assert not result.events and result.total_count == 0
    gray.fill(0)  # camera buffers may be reused after observe returns
    observe(capture, tracker, 1, 325)
    observe(capture, tracker, 2, 335)
    assert capture.close(timeout=10)
    saved = records(tmp_path)
    assert len(saved) == 1
    assert saved[0]['triggers'][0]['kind'] == 'box_at_line'
    assert saved[0]['triggers'][0]['track_key'] == '0:1'
    assert saved[0]['frames'][0]['tracks'][0]['armed'] is False
    assert saved[0]['frames'][0]['total_count'] == 0
    assert saved[0]['context_complete'] is True
    assert [f['frame_id'] for f in saved[0]['frames']] == [0, 1, 2]
    directory = next((tmp_path/'captures').glob('event-*'))
    assert np.all(cv2.imread(str(directory/'frame-0000000000.gray.png'), 0) == 30)
    binary = cv2.imread(str(directory/'frame-0000000000.binary.png'), 0)
    assert np.count_nonzero(binary) == 144
    assert (directory/'frame-0000000000.annotated.png').is_file()
    assert (directory/'frame-0000000000.rle.npz').is_file()
    assert saved[0]['frames'][0]['tracker_parameters']['guarded_merge'] is False
    assert saved[0]['frames'][0]['packet']['threshold'] == 128
    assert capture.status()['saved'] == 1


def test_centroid_jump_over_line_without_box_touch_and_count_is_captured(tmp_path):
    capture = recorder(tmp_path)
    tracker = counter()
    for i, x in enumerate((250, 275, 335, 355, 375)):
        observe(capture, tracker, i, x)
    assert capture.close(timeout=10)
    saved = records(tmp_path)
    assert len(saved) == 1
    assert {'centroid_crossing', 'counted'} <= {t['kind'] for t in saved[0]['triggers']}
    assert [f['frame_id'] for f in saved[0]['frames']] == [0, 1, 2, 3, 4]
    assert saved[0]['frames'][2]['events'][0]['track_id'] == 1


def test_pause_and_epoch_do_not_hide_triggers_or_join_context(tmp_path):
    capture = recorder(tmp_path)
    tracker = counter()
    observe(capture, tracker, 0, 300)
    observe(capture, tracker, 1, 330, paused=True)
    tracker = counter()
    observe(capture, tracker, 2, 315, epoch=1)
    assert capture.close(timeout=10)
    saved = records(tmp_path)
    assert len(saved) == 2
    assert 'suppressed' in {t['kind'] for t in saved[0]['triggers']}
    assert saved[0]['context_complete'] is False
    assert saved[0]['context_end_reason'] == 'trajectory_reset'
    assert {f['trajectory_epoch'] for f in saved[1]['frames']} == {1}
    assert saved[1]['triggers'][0]['track_key'] == '1:1'


def test_rejected_component_near_line_is_captured(tmp_path):
    capture = recorder(tmp_path)
    tracker = counter()
    mask = np.zeros((400, 640), np.uint8)
    mask[90:190, 290:350] = 255  # above max_area, no accepted target box
    result = tracker.update(0, mask)
    assert not result.detections
    packet = decode_packet(encode_mask(mask, frame_id=0))
    capture.observe(mask, packet, result, tracker, capture_monotonic_ns=1,
                    camera_sequence=0, paused=False, commands=[])
    assert capture.close(timeout=10)
    saved = records(tmp_path)
    assert saved[0]['triggers'][0]['kind'] == 'rejected_at_line'
    assert saved[0]['frames'][0]['rejected_detections'][0]['area'] == 6000


def test_new_id_already_beyond_line_is_saved_without_count_event(tmp_path):
    capture = recorder(tmp_path, context_frames=0)
    tracker = counter()
    observe(capture, tracker, 0, 240)
    # More than max_step: an existing visible object receives a fresh ID.
    _, result = observe(capture, tracker, 1, 340)
    assert result.total_count == 0 and not result.events
    assert capture.close(timeout=10)
    saved = records(tmp_path)
    assert len(saved) == 1
    assert 'new_track_after_line' in {t['kind'] for t in saved[0]['triggers']}
    assert saved[0]['frames'][0]['tracks'][-1]['armed'] is False


@pytest.mark.parametrize('limit', ['max_events', 'max_bytes', 'min_free_bytes'])
def test_storage_limits_are_reported_without_affecting_tracker(tmp_path, limit):
    settings = {limit: 1 if limit != 'min_free_bytes' else 1 << 60}
    if limit == 'min_free_bytes':
        from scripts.standalone.crossing_snapshots import CrossingSnapshots
        capture = CrossingSnapshots(tmp_path/'captures', identity={}, **settings)
    else:
        capture = recorder(tmp_path, **settings)
    tracker = counter()
    for i, x in enumerate((300, 330, 350, 370)):
        observe(capture, tracker, i, x)
    tracker = counter()
    for i, x in enumerate((315, 335, 355), 4):
        observe(capture, tracker, i, x, epoch=1)
    assert capture.close(timeout=10)
    status = capture.status()
    assert status['dropped_storage'] >= 1
    assert status['last_error']
    assert status['bytes_written'] <= status['max_bytes']


def test_slow_writer_has_bounded_queue_and_does_not_block_observe(tmp_path, monkeypatch):
    from scripts.standalone.crossing_snapshots import CrossingSnapshots
    entered, release = threading.Event(), threading.Event()
    original = CrossingSnapshots._write_job
    def slow(self, job):
        entered.set()
        assert release.wait(10)
        return original(self, job)
    monkeypatch.setattr(CrossingSnapshots, '_write_job', slow)
    capture = recorder(tmp_path, queue_size=1, context_frames=0)
    observe(capture, counter(), 0, 315)
    assert entered.wait(3)
    for i in range(1, 8):
        observe(capture, counter(), i, 315, epoch=i)
    assert capture.status()['dropped_queue'] >= 5
    assert capture.close(timeout=0) is False
    release.set()
    assert capture.close(timeout=10)


def test_write_failure_is_contained_and_reported(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('simulated SD failure')
    monkeypatch.setattr(cv2, 'imencode', fail)
    capture = recorder(tmp_path, context_frames=0)
    observe(capture, counter(), 0, 315)
    assert capture.close(timeout=10)
    assert capture.status()['write_errors'] == 1
    assert 'simulated SD failure' in capture.status()['last_error']
    assert not records(tmp_path)
