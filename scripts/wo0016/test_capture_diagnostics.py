"""Stage-timing and gap-attribution tests for the camera-only capture tool."""
from __future__ import annotations

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
    def __init__(self, events_dir: Path, *, read_delay_at=None, read_delay_s=0.0):
        self.events_dir = events_dir
        self.read_delay_at = read_delay_at
        self.read_delay_s = read_delay_s
        self.reads = 0
        self.released = False

    def isOpened(self):
        return True

    def set(self, *args):
        return True

    def get(self, prop):
        return {cv2.CAP_PROP_FRAME_WIDTH: 640.0, cv2.CAP_PROP_FRAME_HEIGHT: 400.0,
                cv2.CAP_PROP_FPS: 60.0, cv2.CAP_PROP_FOURCC: 0.0}.get(prop, 0.0)

    def read(self):
        recording = self.reads - 2
        if self.read_delay_at is not None and recording == self.read_delay_at:
            time.sleep(self.read_delay_s)
        self.reads += 1
        return True, np.frombuffer(JPEG, np.uint8)

    def release(self):
        self.released = True


class SlowArray:
    def __init__(self, array, delay_at, delay_s):
        self.array = array
        self.delay_at = delay_at
        self.delay_s = delay_s

    def __setitem__(self, key, value):
        if key == self.delay_at:
            time.sleep(self.delay_s)
        self.array[key] = value

    def __getitem__(self, key):
        return self.array[key]

    def flush(self):
        return self.array.flush()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(capture, "_disk_usage", lambda path: BIG_DISK)
    monkeypatch.setattr(capture, "_read_meminfo", lambda path="/proc/meminfo": 4_000_000)
    monkeypatch.setattr(capture, "_save_npz", np.savez)
    # Windows' monotonic_ns has ~15.6 ms resolution; the board clock does not.
    # Use the high-resolution counter so the fake capture has real frame gaps.
    monkeypatch.setattr(capture, "_monotonic_ns", time.perf_counter_ns)


def _run(tmp_path, *, camera_kwargs=None, **kwargs):
    output = tmp_path / "session"
    camera = FakeCamera(output / "events", **(camera_kwargs or {}))
    kwargs.setdefault("frames", 10)
    kwargs.setdefault("start_delay_s", 0.0)
    manifest = capture.capture_session(output, camera_factory=lambda path: camera, **kwargs)
    return manifest, output


def test_memmap_timing_is_computed_from_saved_timestamps(tmp_path):
    manifest, output = _run(tmp_path, frames=10, output_format="memmap")
    assert manifest["timing"]["median_gap_ms"] is not None
    assert manifest["timing"]["max_gap_ms"] is not None
    saved = np.load(output / "monotonic_ns.npy")
    expected = np.median(np.diff(saved) / 1e6)
    assert abs(manifest["timing"]["median_gap_ms"] - expected) < 0.01


def test_npz_and_memmap_share_timing_semantics(tmp_path):
    npz_manifest, _ = _run(tmp_path / "a", frames=8, output_format="npz")
    memmap_manifest, _ = _run(tmp_path / "b", frames=8, output_format="memmap")
    for manifest in (npz_manifest, memmap_manifest):
        assert manifest["timing"]["median_gap_ms"] is not None
        assert manifest["timing"]["non_positive_gaps"] == 0


def test_diagnostics_artifact_and_manifest(tmp_path):
    manifest, output = _run(tmp_path, frames=10, output_format="memmap", diagnostics=True)
    assert manifest["diagnostics"]["enabled"] is True
    assert manifest["diagnostics"]["file"] == "diagnostics.npz"
    assert any(entry["name"] == "diagnostics.npz" for entry in manifest["data_files"])
    with np.load(output / "diagnostics.npz") as data:
        for name in ("read_ms", "decode_ms", "resize_ms", "source_write_ms",
                     "preview_write_ms", "frame_total_ms"):
            assert data[name].shape == (10,)


def test_diagnostics_off_keeps_legacy_artifacts(tmp_path):
    manifest, output = _run(tmp_path, frames=6)
    assert manifest["diagnostics"]["enabled"] is False
    assert manifest["diagnostics"]["file"] is None
    assert not (output / "diagnostics.npz").exists()


def test_read_delay_is_attributed(tmp_path):
    manifest, _ = _run(tmp_path, frames=10, output_format="memmap", diagnostics=True,
                       camera_kwargs={"read_delay_at": 3, "read_delay_s": 0.045})
    anomalies = manifest["diagnostics"]["anomalies"]
    match = [item for item in anomalies if item["before_frame"] == 3]
    assert match and match[0]["read_ms_next"] >= 40
    assert abs(match[0]["unexplained_ms"]) < 15


def test_decode_delay_is_attributed(tmp_path, monkeypatch):
    original = capture._decode_gray
    state = {"calls": 0}

    def slow(raw):
        index = state["calls"]
        state["calls"] += 1
        if index == 5:  # two warmup calls + recording frame 3
            time.sleep(0.045)
        return original(raw)

    monkeypatch.setattr(capture, "_decode_gray", slow)
    manifest, _ = _run(tmp_path, frames=10, output_format="memmap", diagnostics=True)
    match = [item for item in manifest["diagnostics"]["anomalies"]
             if item["before_frame"] == 3]
    assert match and match[0]["decode_ms_next"] >= 40


def test_memmap_write_delay_is_attributed(tmp_path, monkeypatch):
    original = capture._open_memmap
    frames = 10

    def slow_open(path, mode, dtype, shape):
        array = original(path, mode=mode, dtype=dtype, shape=shape)
        if tuple(shape) == (frames, 400, 640):
            return SlowArray(array, 2, 0.04)
        return array

    monkeypatch.setattr(capture, "_open_memmap", slow_open)
    manifest, _ = _run(tmp_path, frames=frames, output_format="memmap", diagnostics=True)
    match = [item for item in manifest["diagnostics"]["anomalies"]
             if item["after_frame"] == 2]
    assert match and match[0]["source_write_ms_prev"] >= 35
    assert abs(match[0]["unexplained_ms"]) < 15


def test_incomplete_session_still_has_timing(tmp_path):
    output = tmp_path / "session"
    camera = FakeCamera(output / "events")
    original = camera.read
    state = {"calls": 0}

    def flaky():
        state["calls"] += 1
        if state["calls"] == 7:  # two warmups + four recorded frames
            return False, None
        return original()

    camera.read = flaky
    with pytest.raises(capture.CaptureError, match="INCOMPLETE"):
        capture.capture_session(output, frames=10, start_delay_s=0.0,
                                output_format="memmap", diagnostics=True,
                                camera_factory=lambda path: camera)
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "INCOMPLETE" and manifest["frame_count"] == 4
    assert manifest["timing"]["median_gap_ms"] is not None
    assert manifest["diagnostics"]["enabled"] is True


def test_finishing_timings_are_recorded(tmp_path):
    manifest, _ = _run(tmp_path, frames=6, output_format="memmap")
    finishing = manifest["finishing"]
    assert finishing["flush_ms"] is not None
    assert finishing["hash_ms"] is not None
    assert finishing["manifest_ms"] is not None