"""Bounded RAM capture mode tests (no camera, no disk writes in the loop)."""
from __future__ import annotations

import hashlib
import json
import time
import types
from pathlib import Path

import cv2
import numpy as np
import pytest

from scripts.wo0016 import capture_touching_camera_only as capture

JPEG = cv2.imencode(".jpg", np.full((400, 640), 128, np.uint8))[1].tobytes()
BIG_DISK = types.SimpleNamespace(total=10**12, used=0, free=10**12)


class FakeCamera:
    def __init__(self, events_dir, *, fail_at=None, markers=None, probe_at=None):
        self.events_dir = events_dir
        self.fail_at = fail_at
        self.markers = markers or {}
        self.probe_at = probe_at
        self.probed = None
        self.reads = 0

    def isOpened(self):
        return True

    def set(self, *args):
        return True

    def get(self, prop):
        return {cv2.CAP_PROP_FRAME_WIDTH: 640.0, cv2.CAP_PROP_FRAME_HEIGHT: 400.0,
                cv2.CAP_PROP_FPS: 60.0, cv2.CAP_PROP_FOURCC: 0.0}.get(prop, 0.0)

    def read(self):
        recording = self.reads - 2
        if self.probe_at is not None and recording == self.probe_at:
            self.probed = sorted(path.name for path in self.events_dir.parent.iterdir())
        for name in self.markers.get(recording, []):
            (self.events_dir / name).touch()
        if self.fail_at is not None and recording == self.fail_at:
            self.reads += 1
            return False, None
        self.reads += 1
        return True, np.frombuffer(JPEG, np.uint8)

    def release(self):
        pass


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(capture, "_disk_usage", lambda path: BIG_DISK)
    monkeypatch.setattr(capture, "_read_meminfo", lambda path="/proc/meminfo": 4_000_000)
    monkeypatch.setattr(capture, "_save_npz", np.savez)
    monkeypatch.setattr(capture, "_monotonic_ns", time.perf_counter_ns)


def _run(tmp_path, *, camera_kwargs=None, **kwargs):
    output = tmp_path / "ram-session"
    camera = FakeCamera(output / "events", **(camera_kwargs or {}))
    kwargs.setdefault("frames", 10)
    kwargs.setdefault("start_delay_s", 0.0)
    kwargs.setdefault("output_format", "ram")
    manifest = capture.capture_session(output, camera_factory=lambda path: camera,
                                       **kwargs)
    return manifest, output, camera


def test_ram_complete_session(tmp_path):
    manifest, output, _ = _run(tmp_path, frames=10, diagnostics=True)
    assert manifest["status"] == "COMPLETE"
    assert manifest["ram_only"] is True and manifest["preview_saved"] is False
    assert manifest["ram_limit_frames"] == 300
    assert manifest["archive"] == "camera_ram_capture.npz"
    assert manifest["tail_post_ms"] is not None
    assert manifest["timing"]["non_positive_gaps"] == 0
    with np.load(output / "camera_ram_capture.npz") as data:
        assert data["source_gray640x400"].shape == (10, 400, 640)
        assert "input_gray320x240" not in data.files
        assert len(data["monotonic_ns"]) == 10
    with np.load(output / "diagnostics.npz") as data:
        assert set(data.files) == {"read_ms", "decode_ms", "ram_write_ms",
                                   "frame_total_ms"}
    entry = manifest["data_files"][0]
    path = output / entry["name"]
    assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_ram_loop_does_not_write_data_files(tmp_path):
    manifest, _, camera = _run(tmp_path, frames=10, camera_kwargs={"probe_at": 4})
    assert manifest["status"] == "COMPLETE"
    assert camera.probed is not None
    assert not [name for name in camera.probed if name.endswith((".npz", ".npy"))]
    assert (tmp_path / "ram-session" / "camera_ram_capture.npz").exists()


def test_ram_memory_precheck_accounts_for_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "_read_meminfo", lambda path="/proc/meminfo": 200_000)
    with pytest.raises(capture.PrecheckError, match="insufficient memory"):
        capture.capture_session(tmp_path / "ram-a", frames=300, start_delay_s=0.0,
                                output_format="ram", camera_factory=lambda path: None)
    monkeypatch.setattr(capture, "_read_meminfo", lambda path="/proc/meminfo": 300_000)
    manifest = capture.capture_session(
        tmp_path / "ram-b", frames=300, start_delay_s=0.0, output_format="ram",
        camera_factory=lambda path: FakeCamera((tmp_path / "ram-b" / "events")))
    assert manifest["status"] == "COMPLETE"


def test_ram_rejects_windows_over_the_cap(tmp_path):
    with pytest.raises(capture.PrecheckError, match="not silently truncated"):
        capture.capture_session(tmp_path / "ram-big", frames=301, start_delay_s=0.0,
                                output_format="ram", camera_factory=lambda path: None)


def test_ram_rejects_formal_mode(tmp_path):
    with pytest.raises(capture.PrecheckError, match="600"):
        capture.capture_session(tmp_path / "ram-formal", frames=300, formal=True,
                                start_delay_s=0.0, output_format="ram",
                                camera_factory=lambda path: None)


def test_ram_partial_capture_on_read_failure(tmp_path):
    with pytest.raises(capture.CaptureError, match="INCOMPLETE"):
        _run(tmp_path, frames=10, camera_kwargs={"fail_at": 4})
    manifest = json.loads((tmp_path / "ram-session" / "manifest.json").read_text())
    assert manifest["status"] == "INCOMPLETE" and manifest["frame_count"] == 4
    with np.load(tmp_path / "ram-session" / "camera_ram_capture.npz") as data:
        assert data["source_gray640x400"].shape[0] == 4


def test_ram_save_failure_marks_incomplete(tmp_path, monkeypatch):
    def broken(stream, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(capture, "_save_npz", broken)
    with pytest.raises(capture.CaptureError, match="INCOMPLETE"):
        _run(tmp_path, frames=6)
    manifest = json.loads((tmp_path / "ram-session" / "manifest.json").read_text())
    assert manifest["status"] == "INCOMPLETE"
    assert "save_failed" in manifest["failure_reason"]


def test_ram_events_and_baseline(tmp_path):
    manifest, _, _ = _run(tmp_path, frames=10, baseline_frames=3,
                          camera_kwargs={"markers": {4: ["drop_start"], 7: ["drop_end"]}})
    assert manifest["events"]["drop_start"]["frame"] == 4
    assert manifest["events"]["drop_end"]["frame"] == 7
    assert manifest["events"]["baseline_end"]["frame"] == 2
    assert manifest["frames_after_drop_end"] == 2
    assert manifest["status"] == "COMPLETE"


def test_ram_tail_post_covers_last_frame(tmp_path):
    manifest, _, _ = _run(tmp_path, frames=6, diagnostics=True)
    assert manifest["tail_post_ms"] is not None
    assert manifest["tail_post_ms"] >= 0.0