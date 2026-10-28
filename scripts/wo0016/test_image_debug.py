import sys
from pathlib import Path
from types import SimpleNamespace
import json
import base64
from urllib.request import Request, urlopen

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from image_debug import DebugSnapshots
from mjpeg_preview import MjpegPreviewServer


def test_debug_disabled_then_enabled_then_cleared():
    debug = DebugSnapshots(interval_s=0)
    gray = np.zeros((240, 320), np.uint8)
    result = SimpleNamespace(frame_id=12, roi_top=20, roi_bottom=220,
                             detections=(), tracks=(), total_count=0)
    debug.capture(result, gray, gray, source="software", source_gray=gray)
    assert debug.snapshot() == {"enabled": False, "frame_id": None, "source": None, "steps": []}
    debug.set_enabled(True)
    debug.capture(result, gray, gray, source="software", source_gray=gray)
    snapshot = debug.snapshot()
    assert snapshot["frame_id"] == 12 and snapshot["source"] == "software"
    assert len(snapshot["steps"]) == 5
    assert all(step["before"].startswith("data:image/jpeg;base64,") for step in snapshot["steps"])
    debug.set_enabled(False)
    assert debug.snapshot()["steps"] == []


def test_board_missing_source_is_not_substituted():
    debug = DebugSnapshots(interval_s=0)
    debug.set_enabled(True)
    image = np.zeros((240, 320), np.uint8)
    result = SimpleNamespace(frame_id=1, roi_top=0, roi_bottom=240,
                             detections=(), tracks=(), total_count=0)
    debug.capture(result, image, image, source="pl_dma")
    assert debug.snapshot()["steps"][0]["before"] is None


def test_disabled_never_encodes_and_enabled_preserves_frame_pixels(monkeypatch):
    debug = DebugSnapshots(interval_s=0)
    gray = np.full((240, 320), 60, np.uint8)
    mask = np.full((240, 320), 255, np.uint8)
    result = SimpleNamespace(frame_id=8, roi_top=0, roi_bottom=240,
                             detections=(), tracks=(), total_count=0)
    original = cv2.imencode
    def forbidden(*args, **kwargs):
        raise AssertionError("disabled debug encoded an image")
    monkeypatch.setattr(cv2, "imencode", forbidden)
    debug.capture(result, gray, mask, source="pl_dma")
    monkeypatch.setattr(cv2, "imencode", original)
    debug.set_enabled(True)
    debug.capture(result, gray, mask, source="pl_dma")
    step = debug.snapshot()["steps"][1]
    def decode(url):
        return cv2.imdecode(np.frombuffer(base64.b64decode(url.split(",", 1)[1]), np.uint8), cv2.IMREAD_GRAYSCALE)
    assert abs(int(decode(step["before"])[100, 100]) - 60) <= 2  # JPEG is lossy.
    assert int(decode(step["after"])[100, 100]) == 255


def test_server_exposes_debug_toggle_and_snapshot():
    debug = DebugSnapshots()
    with MjpegPreviewServer(host="127.0.0.1", port=0, debug_snapshots=debug) as server:
        base = f"http://127.0.0.1:{server.port}"
        request = Request(base + "/api/debug-mode", data=b'{"enabled":true}',
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request) as response:
            assert json.load(response) == {"enabled": True}
        with urlopen(base + "/api/debug-snapshot") as response:
            assert json.load(response)["enabled"] is True
        with urlopen(base + "/status.json") as response:
            assert json.load(response)["debug"] == {"enabled": True}
        request = Request(base + "/api/debug-mode", data=b'{"enabled":false}',
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request) as response:
            assert json.load(response) == {"enabled": False}
        with urlopen(base + "/api/debug-snapshot") as response:
            assert json.load(response)["steps"] == []
