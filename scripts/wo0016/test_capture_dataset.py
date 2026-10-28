import json
import hashlib
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture_dataset import (  # noqa: E402
    CaptureError,
    CaptureWriter,
    DEFAULT_MAX_BYTES,
    estimated_capture_bytes,
    evaluate_entities,
    load_dataset,
)



def _frame_metadata(frame_id):
    return {
        "frame_id": frame_id,
        "source_sequence": frame_id,
        "capture_monotonic_ns": 1000 + frame_id,
        "capture_wall_time_ns": 3000 + frame_id,
        "camera_read_ms": 0.5,
        "capture_timestamp_kind": "host-after-read",
        "dma_complete_monotonic_ns": 2000 + frame_id,
        "dma_slot_index": frame_id % 2,
        "software_pairing": {"input_sequence_equals_dma_frame_id": True,
                             "input_and_output_use_same_slot": True,
                             "mask_echoes_frame_id": False},
        "resolution": {"width": 320, "height": 240},
        "threshold": 128,
        "algorithm_config": {"line_y": 120, "min_area": 12},
        "roi": {"top": 96, "bottom_exclusive": 145},
        "dma_status": {"frame_id": frame_id, "state": "IDLE"},
        "packer_status": {"frame_id": frame_id, "validation": "PASS"},
        "raw_detections": [], "detections": [], "associations": [], "tracks": [],
        "events": [], "suppressed_events": [], "total_count": 0,
        "frame_result": {},
    }


def _session(tmp_path, *, frames=2, max_bytes=2_000_000, queue_depth=2):
    return CaptureWriter(
        tmp_path / "capture",
        expected_frames=frames,
        config={"line_y": 120, "min_area": 12},
        max_bytes=max_bytes,
        queue_depth=queue_depth,
    )


def test_deferred_300_frames_do_not_touch_storage_or_queue_until_close(tmp_path, monkeypatch):
    writer = CaptureWriter(tmp_path / "capture", expected_frames=300, config={},
                           defer_writes_until_close=True, source_frame_ids=(0, 30, 299))
    started, release = threading.Event(), threading.Event()
    original = writer._write_frame

    def stalled_disk(item):
        started.set()
        assert release.wait(10), "test must release simulated stalled SD card"
        original(item)

    def unexpected_io(*args, **kwargs):
        raise AssertionError("sampling must not enqueue or fsync")

    monkeypatch.setattr(writer, "_write_frame", stalled_disk)
    with monkeypatch.context() as sampling:
        sampling.setattr(writer._queue, "put_nowait", unexpected_io)
        sampling.setattr("capture_dataset.os.fsync", unexpected_io)
        for frame_id in range(300):
            gray = np.full((240, 320), frame_id % 256, dtype=np.uint8)
            mask = np.full((240, 320), 255 if frame_id % 2 else 0, dtype=np.uint8)
            if frame_id in writer.source_frame_ids:
                writer.record_source_frame(frame_id, np.full((400, 640), frame_id % 256, dtype=np.uint8))
            writer.submit(frame_id, gray, mask, _frame_metadata(frame_id))
            gray.fill(99)
        assert not started.is_set()
        assert not list((tmp_path / "capture" / "frames").iterdir())
        assert not (tmp_path / "capture" / "frame-records.jsonl").exists()
        assert writer._raw_frames_recorded == writer._accepted == 300
    result = {}
    closer = threading.Thread(target=lambda: result.update(writer.close()))
    closer.start()
    try:
        assert started.wait(5)
        assert closer.is_alive(), "flush must wait for the slow disk after sampling"
    finally:
        release.set()
        closer.join(30)
    assert not closer.is_alive()
    assert result["status"] == "COMPLETE"
    assert result["captured_frames"] == 300 and result["dropped_frames"] == 0
    with np.load(tmp_path / "capture" / "frames_gray8_binary8.npz") as data:
        for frame_id in (0, 35, 299):
            assert np.all(data["raw_gray8"][frame_id] == frame_id % 256)
            assert np.all(data["raw_binary8"][frame_id] == (255 if frame_id % 2 else 0))
    assert result["source_frame_archive"]["frame_ids"] == [0, 30, 299]


@pytest.mark.parametrize("disk_failure", [False, True])
def test_deferred_flush_partial_or_failed_disk_never_claims_complete(tmp_path, monkeypatch, disk_failure):
    writer = CaptureWriter(tmp_path / "capture", expected_frames=2, config={},
                           defer_writes_until_close=True)
    image = np.zeros((240, 320), dtype=np.uint8)
    writer.submit(0, image, image, _frame_metadata(0))
    if disk_failure:
        writer.submit(1, image, image, _frame_metadata(1))
        def fail(item):
            raise OSError("simulated SD write failure")
        monkeypatch.setattr(writer, "_write_frame", fail)
    result = writer.close()
    assert result["status"] == "INCOMPLETE"
    assert result["raw_frame_archive"] is None
    assert result["writer_active"] is False
    assert writer.close() == result


def test_default_capture_capacity_accounts_for_220_frame_npz():
    assert estimated_capture_bytes(220) < DEFAULT_MAX_BYTES


def test_lossless_gray_and_raw_mask_round_trip(tmp_path):
    writer = _session(tmp_path)
    gray0 = np.arange(240 * 320, dtype=np.uint32).reshape(240, 320).astype(np.uint8)
    gray1 = np.flipud(gray0).copy()
    mask0 = np.zeros((240, 320), dtype=np.uint8)
    mask0[101:108, 33:44] = 255
    mask1 = np.zeros((240, 320), dtype=np.uint8)
    mask1[110:120, 200:212] = 255
    for frame_id, gray, mask in ((0, gray0, mask0), (1, gray1, mask1)):
        writer.submit(frame_id, gray, mask, _frame_metadata(frame_id))
    manifest = writer.close()

    assert manifest["status"] == "COMPLETE"
    stored_manifest, frames = load_dataset(tmp_path / "capture")
    assert stored_manifest["captured_frames"] == 2
    assert [frame.frame_id for frame in frames] == [0, 1]
    np.testing.assert_array_equal(frames[0].gray, gray0)
    np.testing.assert_array_equal(frames[1].gray, gray1)
    np.testing.assert_array_equal(frames[0].mask, mask0)
    np.testing.assert_array_equal(frames[1].mask, mask1)


def test_writer_preallocates_and_seals_combined_raw_frame_archive(tmp_path):
    writer = _session(tmp_path, frames=2)
    assert writer.raw_gray8.shape == (2, 240, 320)
    assert writer.raw_binary8.shape == (2, 240, 320)
    assert writer.raw_gray8.dtype == writer.raw_binary8.dtype == np.uint8
    assert writer.raw_gray8.flags.c_contiguous and writer.raw_binary8.flags.c_contiguous
    assert not hasattr(writer.raw_gray8, "physical_address")
    assert not hasattr(writer.raw_binary8, "physical_address")

    gray = np.arange(240 * 320, dtype=np.uint32).reshape(240, 320).astype(np.uint8)
    mask = np.zeros((240, 320), dtype=np.uint8)
    mask[100:108, 120:129] = 0xFF
    for frame_id in range(2):
        frame_gray = np.roll(gray, frame_id, axis=0)
        frame_mask = np.roll(mask, frame_id, axis=1)
        writer.record_raw_frame(frame_id, frame_gray, frame_mask)
        writer.submit(frame_id, frame_gray, frame_mask, _frame_metadata(frame_id))
    archive_path = tmp_path / "capture" / "frames_gray8_binary8.npz"
    assert not archive_path.exists(), "the archive must be written after sampling closes"

    manifest = writer.close()

    assert manifest["status"] == "COMPLETE"
    archive_info = manifest["raw_frame_archive"]
    archive_bytes = archive_path.read_bytes()
    assert hashlib.sha256(archive_bytes).hexdigest() == archive_info["sha256"]
    assert archive_info["raw_gray8_sha256"]
    assert archive_info["raw_binary8_sha256"]
    sidecar = archive_path.with_suffix(".npz.sha256").read_text(encoding="ascii").strip()
    assert sidecar == f"{archive_info['sha256']}  {archive_path.name}"
    with np.load(archive_path, allow_pickle=False) as stored:
        assert stored["raw_gray8"].shape == (2, 240, 320)
        assert stored["raw_binary8"].shape == (2, 240, 320)
        assert stored["raw_gray8"].dtype == stored["raw_binary8"].dtype == np.uint8
        for frame_id in range(2):
            np.testing.assert_array_equal(stored["raw_gray8"][frame_id], writer.raw_gray8[frame_id])
            np.testing.assert_array_equal(stored["raw_binary8"][frame_id], writer.raw_binary8[frame_id])
    loaded_manifest, _ = load_dataset(tmp_path / "capture")
    assert loaded_manifest["raw_frame_archive"] == archive_info


def test_calibration_writer_seals_selected_full_resolution_source_frames(tmp_path):
    writer = CaptureWriter(
        tmp_path / "capture", expected_frames=2,
        config={"line_y": 120, "min_area": 12},
        max_bytes=2_000_000, source_frame_ids=(0, 1),
    )
    raw_sources = [np.full((400, 640), value, dtype=np.uint8) for value in (31, 97)]
    gray = np.full((240, 320), 31, dtype=np.uint8)
    mask = np.zeros((240, 320), dtype=np.uint8)
    for frame_id, source in enumerate(raw_sources):
        writer.record_source_frame(frame_id, source)
        writer.record_raw_frame(frame_id, gray, mask)
        writer.submit(frame_id, gray, mask, _frame_metadata(frame_id))

    manifest = writer.close()
    archive = tmp_path / "capture" / "frames_source640x400.npz"
    source_info = manifest["source_frame_archive"]
    assert manifest["status"] == "COMPLETE"
    assert source_info["frame_ids"] == [0, 1]
    assert source_info["shape"] == [2, 400, 640]
    with np.load(archive, allow_pickle=False) as stored:
        np.testing.assert_array_equal(stored["source_gray8"], np.stack(raw_sources))
        np.testing.assert_array_equal(stored["source_frame_ids"], [0, 1])
    loaded_manifest, _ = load_dataset(tmp_path / "capture")
    assert loaded_manifest["source_frame_archive"] == source_info


def test_bounded_calibration_writer_seals_selected_source_frames(tmp_path):
    source_ids = (0, 1)
    required_bytes = estimated_capture_bytes(
        2, source_frame_count=len(source_ids), write_mode="bounded_stream")
    no_source_bytes = estimated_capture_bytes(2, write_mode="bounded_stream")
    assert required_bytes == no_source_bytes + len(source_ids) * (400 * 640 + 4096)

    writer = CaptureWriter(
        tmp_path / "bounded-calibration", expected_frames=2,
        config={"line_y": 120, "min_area": 12}, max_bytes=required_bytes,
        source_frame_ids=source_ids, write_mode="bounded_stream", ring_depth=2)
    gray = np.full((240, 320), 31, np.uint8)
    mask = np.zeros((240, 320), np.uint8)
    for frame_id, value in enumerate((31, 97)):
        writer.record_source_frame(
            frame_id, np.full((400, 640), value, dtype=np.uint8))
        writer.submit(frame_id, gray, mask, _frame_metadata(frame_id))

    manifest = writer.close()
    assert manifest["status"] == "COMPLETE"
    assert manifest["write_mode"] == "bounded_stream"
    assert manifest["source_frame_archive"]["frame_ids"] == [0, 1]
    with np.load(tmp_path / "bounded-calibration" / "frames_source640x400.npz",
                 allow_pickle=False) as stored:
        assert stored["source_gray8"].shape == (2, 400, 640)
        assert stored["source_gray8"][:, 0, 0].tolist() == [31, 97]
    loaded_manifest, _ = load_dataset(tmp_path / "bounded-calibration")
    assert loaded_manifest["source_frame_archive"] == manifest["source_frame_archive"]


def test_bounded_calibration_capture_fails_closed_if_a_source_frame_is_missing(tmp_path):
    writer = CaptureWriter(
        tmp_path / "missing-source", expected_frames=1, config={},
        source_frame_ids=(0,), write_mode="bounded_stream", ring_depth=1)
    gray = np.zeros((240, 320), np.uint8)
    writer.submit(0, gray, gray, _frame_metadata(0))

    manifest = writer.close()
    assert manifest["status"] == "INCOMPLETE"
    assert "source frame" in manifest["error"]
    assert manifest["source_frame_archive"] is None


def test_dataset_loader_rejects_tampered_original_source_frame_archive(tmp_path):
    writer = CaptureWriter(
        tmp_path / "capture", expected_frames=1,
        config={"line_y": 120, "min_area": 12},
        max_bytes=2_000_000, source_frame_ids=(0,),
    )
    gray = np.zeros((240, 320), dtype=np.uint8)
    writer.record_source_frame(0, np.full((400, 640), 41, dtype=np.uint8))
    writer.record_raw_frame(0, gray, gray)
    writer.submit(0, gray, gray, _frame_metadata(0))
    assert writer.close()["status"] == "COMPLETE"

    archive_path = tmp_path / "capture" / "frames_source640x400.npz"
    archive_path.write_bytes(archive_path.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="source frame NPZ full-file SHA-256"):
        load_dataset(tmp_path / "capture")


def test_dataset_loader_rejects_tampered_combined_raw_frame_archive(tmp_path):
    writer = _session(tmp_path, frames=1)
    gray = np.full((240, 320), 37, dtype=np.uint8)
    mask = np.zeros((240, 320), dtype=np.uint8)
    writer.record_raw_frame(0, gray, mask)
    writer.submit(0, gray, mask, _frame_metadata(0))
    assert writer.close()["status"] == "COMPLETE"

    archive_path = tmp_path / "capture" / "frames_gray8_binary8.npz"
    archive_path.write_bytes(archive_path.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="full-file SHA-256"):
        load_dataset(tmp_path / "capture")


def test_dataset_validator_rejects_missing_or_mismatched_frame_metadata(tmp_path):
    writer = _session(tmp_path, frames=1)
    writer.submit(0, np.zeros((240, 320), np.uint8),
                  np.zeros((240, 320), np.uint8), _frame_metadata(0))
    writer.close()
    records_path = tmp_path / "capture" / "frame-records.jsonl"
    row = json.loads(records_path.read_text(encoding="utf-8"))
    row["frame_id"] = 9
    records_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    manifest_path = tmp_path / "capture" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    import hashlib
    manifest["frame_records_sha256"] = hashlib.sha256(records_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="frame ID|frame_id"):
        load_dataset(tmp_path / "capture")


def test_capacity_preflight_and_write_failure_are_fail_closed(tmp_path, monkeypatch):
    too_small = tmp_path / "too-small"
    with pytest.raises(CaptureError, match="capacity"):
        CaptureWriter(too_small, expected_frames=2, config={}, max_bytes=100)
    assert not too_small.exists()

    writer = CaptureWriter(tmp_path / "broken", expected_frames=1, config={},
                           max_bytes=1_000_000)
    def fail_write(*args, **kwargs):
        raise OSError("simulated disk full")
    monkeypatch.setattr(writer, "_write_frame", fail_write)
    writer.submit(0, np.zeros((240, 320), np.uint8),
                  np.zeros((240, 320), np.uint8), _frame_metadata(0))
    manifest = writer.close()
    assert manifest["status"] == "INCOMPLETE"
    assert "simulated disk full" in manifest["error"]


def test_bounded_queue_overflow_is_reported_not_silently_dropped(tmp_path, monkeypatch):
    writer = CaptureWriter(tmp_path / "overflow", expected_frames=3, config={},
                           max_bytes=2_000_000, queue_depth=1)
    entered = threading.Event()
    release = threading.Event()
    original = writer._write_frame
    def blocked_write(item):
        entered.set()
        release.wait(timeout=2)
        return original(item)
    monkeypatch.setattr(writer, "_write_frame", blocked_write)
    gray = np.zeros((240, 320), np.uint8)
    mask = np.zeros_like(gray)
    writer.submit(0, gray, mask, _frame_metadata(0))
    assert entered.wait(timeout=2)
    writer.submit(1, gray, mask, _frame_metadata(1))
    with pytest.raises(CaptureError, match="queue|overflow"):
        writer.submit(2, gray, mask, _frame_metadata(2))
    release.set()
    manifest = writer.close()
    assert manifest["status"] == "INCOMPLETE"
    assert manifest["dropped_frames"] >= 1
    assert manifest["missing_frames"] == 0


def test_entity_report_requires_independent_ground_truth_and_manual_identity():
    algorithm = [{"frame_id": 5, "total_count": 1,
                  "raw_detections": [{"x_min": 85, "x_max": 95, "y_min": 110,
                                       "area": 81}],
                  "detections": [{"x_min": 85, "x_max": 95, "y_min": 110,
                                  "y_max": 120, "area": 81,
                                  "centroid_x": 90.0, "centroid_y": 115.0}],
                  "associations": [{"track_id": 7, "detection_index": 0}],
                  "events": [{"track_id": 7, "frame_id": 5}]}]
    unreviewed = {
        "status": "CONFIRMED", "provenance": "independent_video",
        "observer": "reviewer", "independent_record_reference": "video.mp4",
        "actual_window_entity_count": 1, "window_count_confirmed": True,
        "entities": [{"entity_id": "E1", "entered_detection_window": True,
                      "insertion_order": 1, "put_in_monotonic_ns": 100,
                      "first_visible_frame": 5, "last_visible_frame": 5,
                      "fully_visible": True, "completed_pass": True,
                      "identity_evidence": "reviewed video", "track_ids": [7],
                      "identity_reviewed": False}],
    }
    assert evaluate_entities(unreviewed, algorithm)["status"] == "NOT VERIFIED"

    reviewed = json.loads(json.dumps(unreviewed))
    reviewed["entities"][0]["identity_reviewed"] = True
    report = evaluate_entities(reviewed, algorithm)
    assert report["status"] == "PASS"
    assert report["entities"][0]["event_count"] == 1
    assert report["entities"][0]["detected_frame_range"] == [5, 5]
    assert report["entities"][0]["key_evidence_frames"] == [5]
    assert report["duplicate_count"] == 0

def test_dataset_validator_rejects_missing_array_file(tmp_path):
    writer = _session(tmp_path, frames=1)
    writer.submit(0, np.zeros((240, 320), np.uint8),
                  np.zeros((240, 320), np.uint8), _frame_metadata(0))
    writer.close()
    (tmp_path / "capture" / "frames" / "000000.mask.npy").unlink()
    with pytest.raises(FileNotFoundError):
        load_dataset(tmp_path / "capture")


def test_dataset_validator_rejects_non_binary_mask(tmp_path):
    writer = _session(tmp_path, frames=1)
    mask = np.zeros((240, 320), np.uint8)
    mask[100, 100] = 1
    with pytest.raises(CaptureError, match="Binary8"):
        writer.submit(0, np.zeros_like(mask), mask, _frame_metadata(0))
    manifest = writer.close()
    assert manifest["status"] == "INCOMPLETE"


def test_independent_entity_report_flags_duplicate_and_wrong_merge():
    ground_truth = {
        "status": "CONFIRMED", "provenance": "independent_video",
        "observer": "reviewer", "independent_record_reference": "video.mp4",
        "actual_window_entity_count": 2, "window_count_confirmed": True,
        "entities": [
            {"entity_id": "E1", "entered_detection_window": True,
             "insertion_order": 1, "put_in_monotonic_ns": 100,
             "first_visible_frame": 1, "last_visible_frame": 9,
             "fully_visible": True, "completed_pass": True,
             "identity_evidence": "video frames", "track_ids": [7],
             "identity_reviewed": True},
            {"entity_id": "E2", "entered_detection_window": True,
             "insertion_order": 2, "put_in_monotonic_ns": 200,
             "first_visible_frame": 10, "last_visible_frame": 19,
             "fully_visible": True, "completed_pass": True,
             "identity_evidence": "video frames", "track_ids": [7],
             "identity_reviewed": True},
        ],
    }
    frames = [{"frame_id": frame_id, "raw_detections": [],
               "detections": [], "associations": [],
               "events": ([{"track_id": 7, "frame_id": frame_id}]
                          if frame_id in (4, 5) else [])}
              for frame_id in range(20)]
    report = evaluate_entities(ground_truth, frames)
    assert report["status"] == "FAIL"
    assert report["duplicate_count"] == 1
    assert report["missed_count"] == 1
    assert report["wrong_merge_count"] == 1


def test_baseline_replay_reads_raw_masks_and_does_not_infer_ground_truth(tmp_path):
    from replay_capture_dataset import replay_capture_dataset

    writer = CaptureWriter(tmp_path / "capture", expected_frames=1,
                           config={"line_y": 120, "roi_half_height": 24, "min_area": 12},
                           max_bytes=1_000_000)
    gray = np.full((240, 320), 37, np.uint8)
    mask = np.zeros((240, 320), np.uint8)
    mask[116:125, 86:95] = 255
    metadata = _frame_metadata(0)
    metadata["algorithm_config"]["roi_half_height"] = 24
    writer.submit(0, gray, mask, metadata)
    assert writer.close()["status"] == "COMPLETE"

    report = replay_capture_dataset(tmp_path / "capture")

    assert report["status"] == "REPLAY_COMPLETE"
    assert report["frame_count"] == 1
    assert report["frames"][0]["raw_detections"][0]["area"] == 81
    assert report["entity_report"]["status"] == "NOT VERIFIED"


def test_empty_belt_ground_truth_can_pass_only_when_independently_confirmed():
    truth = {"status": "CONFIRMED", "provenance": "human_observation",
             "observer": "observer", "independent_record_reference": "log-1",
             "actual_window_entity_count": 0, "window_count_confirmed": True,
             "empty_belt_first_frame": 0, "empty_belt_last_frame": 0,
             "empty_belt_observed_frames": 1, "entities": []}
    empty_frame = {"frame_id": 0, "raw_detections": [], "detections": [],
                   "associations": [], "events": []}
    assert evaluate_entities(truth, [empty_frame])["status"] == "PASS"
    truth["window_count_confirmed"] = False
    assert evaluate_entities(truth, [empty_frame])["status"] == "NOT VERIFIED"


def test_empty_belt_fails_on_raw_component_or_detection_without_count_event():
    truth = {"status": "CONFIRMED", "provenance": "human_observation",
             "observer": "observer", "independent_record_reference": "empty-belt-log",
             "actual_window_entity_count": 0, "window_count_confirmed": True,
             "empty_belt_first_frame": 0, "empty_belt_last_frame": 0,
             "empty_belt_observed_frames": 1, "entities": []}
    frame = {"frame_id": 0, "raw_detections": [{"area": 12}],
             "detections": [], "associations": [], "events": []}
    report = evaluate_entities(truth, [frame])
    assert report["status"] == "FAIL"
    assert report["false_positive_raw_component_count"] == 1
    assert report["unmatched_event_count"] == 0


def test_ground_truth_missing_independent_fields_and_duplicate_ids_are_not_verified():
    from copy import deepcopy

    good = {
        "status": "CONFIRMED", "provenance": "independent_video",
        "observer": "reviewer", "independent_record_reference": "video.mp4",
        "actual_window_entity_count": 1, "window_count_confirmed": True,
        "entities": [{"entity_id": "E1", "entered_detection_window": True,
                      "insertion_order": 1, "put_in_monotonic_ns": 100,
                      "first_visible_frame": 0, "last_visible_frame": 5,
                      "fully_visible": True, "completed_pass": True,
                      "identity_evidence": "video frame review", "track_ids": [7],
                      "identity_reviewed": True}],
    }
    replay = [{"frame_id": 0, "raw_detections": [], "detections": [],
               "associations": [], "events": []},
              {"frame_id": 1, "raw_detections": [], "detections": [], "associations": [],
               "events": [{"track_id": 7, "frame_id": 1}]}]
    missing_record = deepcopy(good)
    missing_record["observer"] = " "
    assert evaluate_entities(missing_record, replay)["status"] == "NOT VERIFIED"
    duplicate_rows = deepcopy(good)
    duplicate_rows["entities"].append(deepcopy(duplicate_rows["entities"][0]))
    duplicate_rows["actual_window_entity_count"] = 2
    assert evaluate_entities(duplicate_rows, replay)["status"] == "NOT VERIFIED"


def test_event_outside_independent_entity_frame_range_cannot_pass():
    truth = {
        "status": "CONFIRMED", "provenance": "independent_video",
        "observer": "reviewer", "independent_record_reference": "video.mp4",
        "actual_window_entity_count": 1, "window_count_confirmed": True,
        "entities": [{"entity_id": "E1", "entered_detection_window": True,
                      "insertion_order": 1, "put_in_monotonic_ns": 100,
                      "first_visible_frame": 2, "last_visible_frame": 4,
                      "fully_visible": True, "completed_pass": True,
                      "identity_evidence": "video frame review", "track_ids": [7],
                      "identity_reviewed": True}],
    }
    replay = [{"frame_id": i, "raw_detections": [], "detections": [], "associations": [],
               "events": ([{"track_id": 7, "frame_id": i}] if i == 1 else [])}
              for i in range(5)]
    report = evaluate_entities(truth, replay)
    assert report["status"] == "FAIL"
    assert report["missed_count"] == 1
    assert report["unmatched_event_count"] == 1
    assert report["entities"][0]["out_of_window_event_frames"] == [1]


def test_dataset_validator_rejects_manifest_counter_config_tampering(tmp_path):
    writer = _session(tmp_path, frames=1)
    metadata = _frame_metadata(0)
    writer.submit(0, np.zeros((240, 320), np.uint8),
                  np.zeros((240, 320), np.uint8), metadata)
    writer.close()
    manifest_path = tmp_path / "capture" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["config"]["line_y"] = 121
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="config"):
        load_dataset(tmp_path / "capture")


def test_capture_path_rejects_protected_wo0016_archive(tmp_path):
    protected = tmp_path / "data" / "results" / "wo0016-sealed" / "new-child"
    protected.parent.mkdir(parents=True)
    with pytest.raises(CaptureError, match="protected WO-0016"):
        CaptureWriter(protected, expected_frames=1,
                      config={"line_y": 120, "min_area": 12},
                      max_bytes=1_000_000)
    assert not protected.exists()


def test_replay_records_ground_truth_sha256_when_annotation_is_present(tmp_path):
    import hashlib
    from replay_capture_dataset import replay_capture_dataset

    writer = CaptureWriter(tmp_path / "capture", expected_frames=1,
                           config={"line_y": 120, "min_area": 12},
                           max_bytes=1_000_000)
    metadata = _frame_metadata(0)
    writer.submit(0, np.zeros((240, 320), np.uint8),
                  np.zeros((240, 320), np.uint8), metadata)
    assert writer.close()["status"] == "COMPLETE"
    truth = {"status": "CONFIRMED", "provenance": "human_observation",
             "observer": "observer", "independent_record_reference": "empty-belt-log",
             "actual_window_entity_count": 0, "window_count_confirmed": True,
             "empty_belt_first_frame": 0, "empty_belt_last_frame": 0,
             "empty_belt_observed_frames": 1, "entities": []}
    truth_path = tmp_path / "capture" / "ground-truth.json"
    truth_path.write_text(json.dumps(truth), encoding="utf-8")
    source_hash = hashlib.sha256(truth_path.read_bytes()).hexdigest()

    report = replay_capture_dataset(tmp_path / "capture")

    assert report["ground_truth_source"]["sha256"] == source_hash
    assert report["ground_truth_source"]["status"] == "PRESENT"
    assert report["entity_report"]["status"] == "PASS"


def test_writer_timeout_remains_unreadable_even_if_worker_finishes_later(tmp_path, monkeypatch):
    writer = CaptureWriter(tmp_path / "unstable", expected_frames=1,
                           config={"line_y": 120, "min_area": 12},
                           max_bytes=1_000_000)
    entered = threading.Event()
    release = threading.Event()
    original_write = writer._write_frame
    def blocked_write(item):
        entered.set()
        release.wait(timeout=5)
        return original_write(item)
    monkeypatch.setattr(writer, "_write_frame", blocked_write)
    real_join = writer._thread.join
    writer.submit(0, np.zeros((240, 320), np.uint8),
                  np.zeros((240, 320), np.uint8), _frame_metadata(0))
    assert entered.wait(timeout=2)
    monkeypatch.setattr(writer._thread, "join", lambda timeout=None: None)
    manifest = writer.close()
    assert manifest["status"] == "INCOMPLETE"
    assert manifest["writer_active"] is True
    with pytest.raises(ValueError, match="not stable"):
        load_dataset(tmp_path / "unstable", require_complete=False)
    release.set()
    real_join(timeout=2)
    assert not writer._thread.is_alive()
    with pytest.raises(ValueError, match="not stable"):
        load_dataset(tmp_path / "unstable", require_complete=False)


def test_independently_confirmed_entity_without_track_is_reported_as_missed():
    truth = {"status": "CONFIRMED", "provenance": "independent_video",
             "observer": "reviewer", "independent_record_reference": "video.mp4",
             "actual_window_entity_count": 1, "window_count_confirmed": True,
             "entities": [{"entity_id": "E-missed", "entered_detection_window": True,
                           "insertion_order": 1, "put_in_monotonic_ns": 100,
                           "first_visible_frame": 0, "last_visible_frame": 2,
                           "fully_visible": True, "completed_pass": True,
                           "identity_evidence": "reviewer confirms no Track ID was assigned",
                           "track_ids": [], "identity_reviewed": True}],
    }
    replay = [{"frame_id": i, "raw_detections": [], "detections": [],
               "associations": [], "events": []} for i in range(3)]
    report = evaluate_entities(truth, replay)
    assert report["status"] == "FAIL"
    assert report["missed_count"] == 1
    assert report["entities"][0]["track_ids"] == []
