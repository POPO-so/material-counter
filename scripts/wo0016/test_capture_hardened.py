"""Fake-camera tests for the hardened camera-only capture tool."""
from __future__ import annotations

import hashlib
import json
import types
from pathlib import Path

import cv2
import numpy as np
import pytest

from scripts.wo0016 import capture_touching_camera_only as capture

JPEG = cv2.imencode(".jpg", np.full((400, 640), 128, np.uint8))[1].tobytes()
BIG_DISK = types.SimpleNamespace(total=10**12, used=0, free=10**12)


class FakeCamera:
    def __init__(self, events_dir: Path, *, fail_at=None, bad_jpeg_at=None,
                 markers=None, opened=True, width=640, height=400):
        self.events_dir = events_dir
        self.fail_at = fail_at
        self.bad_jpeg_at = bad_jpeg_at
        self.markers = markers or {}
        self.opened = opened
        self.width = width
        self.height = height
        self.reads = 0
        self.released = False

    def isOpened(self):
        return self.opened

    def set(self, *args):
        return True

    def get(self, prop):
        return {cv2.CAP_PROP_FRAME_WIDTH: float(self.width),
                cv2.CAP_PROP_FRAME_HEIGHT: float(self.height),
                cv2.CAP_PROP_FPS: 60.0,
                cv2.CAP_PROP_FOURCC: 0.0}.get(prop, 0.0)

    def read(self):
        recording = self.reads - 2  # two warmup reads before RECORDING
        for name in self.markers.get(recording, []):
            (self.events_dir / name).touch()
        failing = self.fail_at is not None and recording == self.fail_at
        bad = self.bad_jpeg_at is not None and recording == self.bad_jpeg_at
        self.reads += 1
        if failing:
            return False, None
        if bad:
            return True, np.frombuffer(b"not-a-jpeg", np.uint8)
        return True, np.frombuffer(JPEG, np.uint8)

    def release(self):
        self.released = True


@pytest.fixture(autouse=True)
def _precheck_env(monkeypatch):
    monkeypatch.setattr(capture, "_disk_usage", lambda path: BIG_DISK)
    monkeypatch.setattr(capture, "_read_meminfo", lambda path="/proc/meminfo": 4_000_000)
    monkeypatch.setattr(capture, "_save_npz", np.savez)


def _run(tmp_path, camera_kwargs=None, **kwargs):
    output = tmp_path / "session-01"
    holder = {}

    def factory(path):
        holder["camera"] = FakeCamera(output / "events", **(camera_kwargs or {}))
        return holder["camera"]

    kwargs.setdefault("frames", 10)
    kwargs.setdefault("start_delay_s", 0.0)
    return capture.capture_session(output, camera_factory=factory, **kwargs), holder["camera"]


def _manifest(output):
    return json.loads((output / "manifest.json").read_text(encoding="utf-8"))


def test_complete_session_records_events_and_hashes(tmp_path):
    manifest, camera = _run(tmp_path, camera_kwargs={"markers": {4: ["drop_start"], 7: ["drop_end"]}},
                            baseline_frames=3, tail_frames=2)
    output = tmp_path / "session-01"
    assert manifest["status"] == "COMPLETE"
    assert manifest["frame_count"] == 10 and manifest["decoded_frames"] == 10
    assert manifest["events"]["drop_start"]["frame"] == 4
    assert manifest["events"]["drop_end"]["frame"] == 7
    assert manifest["events"]["baseline_end"]["frame"] == 2
    assert manifest["frames_after_drop_end"] == 2
    assert manifest["raw_mjpeg_saved"] is False
    states = [entry["state"] for entry in manifest["state_history"]]
    assert states[:4] == ["PRECHECK", "PRECHECK", "CAMERA_READY", "RECORDING"]
    assert states[-1] == "COMPLETE"
    stored = _manifest(output)
    archive = output / stored["archive"]
    assert stored["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    with np.load(archive) as data:
        assert data["source_gray640x400"].shape == (10, 400, 640)
        assert data["input_gray320x240"].shape == (10, 240, 320)
        assert len(data["monotonic_ns"]) == 10
    truth = json.loads((output / "truth-template.json").read_text())
    assert truth["material"] == "UNKNOWN" and truth["archive_sha256"] == "UNKNOWN"
    assert camera.released is True


def test_camera_cannot_open_writes_failed_manifest(tmp_path):
    output = tmp_path / "session-01"
    with pytest.raises(capture.CaptureError, match="could not be opened"):
        capture.capture_session(output, frames=5, start_delay_s=0.0,
                                camera_factory=lambda path: FakeCamera(output, opened=False))
    manifest = _manifest(output)
    assert manifest["status"] == "FAILED"
    assert manifest["frame_count"] == 0
    assert not (output / "camera_gray_only.npz").exists()


def test_mid_read_failure_saves_partial_prefix(tmp_path):
    with pytest.raises(capture.CaptureError, match="INCOMPLETE"):
        _run(tmp_path, camera_kwargs={"fail_at": 5}, frames=10)
    output = tmp_path / "session-01"
    manifest = _manifest(output)
    assert manifest["status"] == "INCOMPLETE" and manifest["frame_count"] == 5
    with np.load(output / "camera_gray_only.npz") as data:
        assert data["source_gray640x400"].shape[0] == 5
    assert "read failed" in manifest["failure_reason"]


def test_decode_failure_saves_partial_prefix(tmp_path):
    with pytest.raises(capture.CaptureError, match="INCOMPLETE"):
        _run(tmp_path, camera_kwargs={"bad_jpeg_at": 3}, frames=10)
    manifest = _manifest(tmp_path / "session-01")
    assert manifest["status"] == "INCOMPLETE" and manifest["frame_count"] == 3
    assert "JPEG" in manifest["failure_reason"] or "decode" in manifest["failure_reason"]


def test_disk_precheck_fails_before_camera(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "_disk_usage",
                        lambda path: types.SimpleNamespace(total=10**6, used=10**6, free=0))
    called = []
    with pytest.raises(capture.PrecheckError, match="insufficient disk"):
        capture.capture_session(tmp_path / "session-01", frames=10, start_delay_s=0.0,
                                camera_factory=lambda path: called.append(path))
    assert called == []
    manifest = _manifest(tmp_path / "session-01")
    assert manifest["status"] == "FAILED" and "insufficient disk" in manifest["failure_reason"]


def test_memory_precheck_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "_read_meminfo", lambda path="/proc/meminfo": 1000)
    with pytest.raises(capture.PrecheckError, match="insufficient memory"):
        capture.capture_session(tmp_path / "session-01", frames=100, start_delay_s=0.0,
                                camera_factory=lambda path: None)


def test_save_failure_marks_incomplete(tmp_path, monkeypatch):
    def broken(stream, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(capture, "_save_npz", broken)
    with pytest.raises(capture.CaptureError, match="INCOMPLETE"):
        _run(tmp_path, frames=6)
    manifest = _manifest(tmp_path / "session-01")
    assert manifest["status"] == "INCOMPLETE"
    assert "save_failed" in manifest["failure_reason"]


def test_stop_marker_ends_with_incomplete(tmp_path):
    with pytest.raises(capture.CaptureError, match="INCOMPLETE"):
        _run(tmp_path, camera_kwargs={"markers": {4: ["stop"]}}, frames=10)
    manifest = _manifest(tmp_path / "session-01")
    assert manifest["status"] == "INCOMPLETE"
    assert manifest["failure_reason"] == "stopped_by_marker"
    assert manifest["frame_count"] == 5
    assert manifest["events"]["stop"]["frame"] == 5


def test_timestamp_anomaly_is_reported(tmp_path, monkeypatch):
    values = iter([100, 100, 90, 200, 300, 400, 500, 600, 700, 800, 900, 1000])
    monkeypatch.setattr(capture, "_monotonic_ns", lambda: next(values))
    manifest, _ = _run(tmp_path, frames=10)
    assert manifest["status"] == "COMPLETE"
    assert manifest["timing"]["non_positive_gaps"] == 1
    assert manifest["timing"]["median_gap_ms"] is not None


def test_existing_output_directory_is_not_overwritten(tmp_path):
    output = tmp_path / "session-01"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("old", encoding="utf-8")
    with pytest.raises(capture.PrecheckError, match="already exists"):
        capture.capture_session(output, frames=5, start_delay_s=0.0,
                                camera_factory=lambda path: None)
    assert marker.read_text(encoding="utf-8") == "old"
    assert not (output / "manifest.json").exists()


def test_formal_rejects_compact_and_short_windows(tmp_path):
    with pytest.raises(capture.PrecheckError, match="640x400"):
        capture.capture_session(tmp_path / "a", frames=600, compact=True,
                                formal=True, start_delay_s=0.0,
                                camera_factory=lambda path: None)
    with pytest.raises(capture.PrecheckError, match="at least 600"):
        capture.capture_session(tmp_path / "b", frames=300, formal=True,
                                start_delay_s=0.0, camera_factory=lambda path: None)


def test_formal_requires_drop_end_and_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "MIN_FORMAL_FRAMES", 10)
    with pytest.raises(capture.CaptureError, match="INCOMPLETE"):
        _run(tmp_path, camera_kwargs={"markers": {4: ["drop_start"]}},
             frames=12, formal=True, output_format="memmap", tail_frames=5)
    manifest = _manifest(tmp_path / "session-01")
    assert manifest["status"] == "INCOMPLETE"
    assert manifest["failure_reason"] == "drop_end_marker_missing"


def test_memmap_session_writes_npy_files(tmp_path):
    manifest, _ = _run(tmp_path, frames=8, output_format="memmap")
    output = tmp_path / "session-01"
    names = {entry["name"] for entry in manifest["data_files"]}
    assert {"source_gray640x400.npy", "input_gray320x240.npy",
            "monotonic_ns.npy"} <= names
    for entry in manifest["data_files"]:
        path = output / entry["name"]
        assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_legacy_capture_returns_old_keys(tmp_path, monkeypatch):
    output = tmp_path / "legacy"
    monkeypatch.setattr(capture, "_open_camera",
                        lambda path: FakeCamera(output / "events"))
    manifest = capture.capture(output, frames=5, start_delay_s=0.0)
    for key in ("status", "scope", "frame_count", "archive_sha256",
                "archive_bytes", "effective_fps"):
        assert key in manifest
    assert manifest["scope"] == "CAMERA_ONLY_NOT_PL_BINARY8"