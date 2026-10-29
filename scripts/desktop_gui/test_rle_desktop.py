"""Hardware-free checks for the RLE desktop runtime boundary."""
from pathlib import Path
import sys
from dataclasses import replace
from types import SimpleNamespace
import subprocess

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent / "wo0016"), str(HERE.parent / "gray8"), str(ROOT)]

from frame_protocol import LatestFrameHub
from rle_desktop_board import RleDesktopRuntime, read_camera_frame
from scripts.rle.protocol import decode_packet, encode_mask
from stream_counter import Detection, FrameResult


class FakeTransport:
    def __init__(self):
        self.frames = []
        self.dma_may_be_active = False

    def transfer(self, gray, frame_id, **kwargs):
        mask = np.zeros_like(gray)
        mask[100:110, 200:220] = 255
        frame = decode_packet(
            encode_mask(mask, frame_id=frame_id, params_version=1)
        )
        self.frames.append(frame)
        return frame

    def snapshot(self):
        return {
            "mm2s_status": 0x1002,
            "s2mm_status": 0x1002,
            "actual_dma_bytes": 2208,
        }


class FakeCounter:
    width = 640
    height = 400
    line_x = 320
    line_y = 200
    count_axis = "x"
    total_count = 0

    def update_rle(self, frame, *, capture_monotonic_ns=None):
        detection = Detection(200, 219, 100, 109, 200, 209.5, 104.5)
        return FrameResult(
            frame_id=frame.frame_id,
            total_count=self.total_count,
            detections=(detection,),
            events=(),
            roi_top=0,
            roi_bottom=400,
            row_projection=frame.row_projection.tolist(),
            column_projection=frame.column_projection.tolist(),
            roi_foreground_pixels=frame.total_fg,
            ccl_executed=True,
            tracks=(),
        )


class CaptureHub(LatestFrameHub):
    def __init__(self):
        super().__init__()
        self.captured = []

    @property
    def client_count(self):
        return 1

    def publish(self, frame):
        self.captured.append({**frame, "gray": frame["gray"].copy(),
                              "mask": frame["mask"].copy()})


def test_rle_runtime_publishes_full_resolution_pl_derived_mask():
    hub = CaptureHub()
    runtime = RleDesktopRuntime(
        "session-rle", FakeCounter(), FakeTransport(), hub,
        threshold=128,
    )
    gray = np.full((400, 640), 17, dtype=np.uint8)
    result = runtime.process_frame(gray)
    frame = hub.captured[-1]

    assert result.frame_id == 0
    assert frame["source"] == "pl_rle"
    assert frame["gray"].shape == frame["mask"].shape == (400, 640)
    assert frame["config"]["width"] == 640
    assert frame["config"]["height"] == 400
    assert np.count_nonzero(frame["mask"]) == 200
    status = runtime.status()
    assert status["preview"] == {
        "width": 640, "height": 400, "pipeline": "RLE_PACKET_V1"
    }
    assert status["telemetry"]["source"] == "pl_rle"
    assert status["telemetry"]["rle"]["valid"] is True
    hub.close()


def test_invalid_rle_frame_never_reaches_counter_or_preview():
    transport = FakeTransport()
    original = transport.transfer
    transport.transfer = lambda *a, **kw: replace(original(*a, **kw), status=1)
    counter = FakeCounter()
    counter.update_rle = lambda *_a, **_kw: pytest.fail("invalid frame reached counter")
    hub = CaptureHub()
    runtime = RleDesktopRuntime("invalid", counter, transport, hub)
    assert runtime.process_frame(np.zeros((400, 640), np.uint8)) is None
    assert runtime.invalid_frames == 1
    assert runtime.frame_id == 1
    assert hub.captured == []


def test_no_subscriber_does_not_reconstruct_full_frame(monkeypatch):
    import scripts.rle.protocol as protocol
    monkeypatch.setattr(protocol, "reconstruct", lambda *_a: pytest.fail("rasterized"))
    hub = LatestFrameHub()
    runtime = RleDesktopRuntime("no-viewer", FakeCounter(), FakeTransport(), hub)
    assert runtime.process_frame(np.zeros((400, 640), np.uint8)).frame_id == 0
    assert hub.wait_next(0, 0) is None


def test_camera_intake_keeps_original_same_frame_jpeg():
    import cv2
    original = np.full((400, 640), 35, np.uint8)
    ok, jpeg = cv2.imencode(".jpg", original)
    assert ok
    capture = SimpleNamespace(read=lambda: (True, jpeg.reshape(1, -1)))
    gray, preview = read_camera_frame(capture)
    assert gray.shape == (400, 640)
    assert preview == jpeg.tobytes()
    assert np.array_equal(gray, cv2.imdecode(jpeg, cv2.IMREAD_GRAYSCALE))


def test_preview_projection_metadata_does_not_require_recursive_copy():
    from frame_protocol import read_frame
    import io

    class ProjectionValue(int):
        def __deepcopy__(self, memo):
            pytest.fail("projection metadata was recursively copied")

    counter = FakeCounter()
    original = counter.update_rle
    counter.update_rle = lambda *a, **kw: replace(
        original(*a, **kw), row_projection=tuple(ProjectionValue(0) for _ in range(400)))
    hub = LatestFrameHub()
    runtime = RleDesktopRuntime("bounded-preview", counter, FakeTransport(), hub)
    with hub.client():
        runtime.process_frame(np.zeros((400, 640), np.uint8))
    _, packet = hub.wait_next(0, 0)
    frame = read_frame(io.BytesIO(packet))
    assert frame["result"]["detections"][0]["area"] == 200
    assert frame["result"]["row_projection"] == [0] * 400


def test_self_test_timeout_reports_uncertain_hardware_ownership(tmp_path, monkeypatch):
    import board_entry
    preflight = SimpleNamespace(
        audit_process_ownership=lambda: None,
        inspect_pynq_global_state=lambda *_a, **_kw: {
            "pl_state_status": "COLD_BOOT_NO_GLOBAL_STATE"},
    )
    monkeypatch.setitem(sys.modules, "board_preflight", preflight)
    class Process:
        def wait(self, timeout):
            raise subprocess.TimeoutExpired("self-test", timeout)
    monkeypatch.setattr(board_entry.subprocess, "Popen", lambda *_a, **_kw: Process())
    config = {"runtime_root": str(tmp_path), "pynq_init": "pynq.py",
              "protocol": "RLE_PACKET_V1", "python": sys.executable,
              "manifest": "manifest.json", "manifest_sha256": "0" * 64}
    with pytest.raises(RuntimeError) as error:
        board_entry.self_test(config, tmp_path, "boot")
    assert type(error.value).__name__ == "SelfTestOwnershipUncertain"
