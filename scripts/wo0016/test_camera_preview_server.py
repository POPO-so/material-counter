import asyncio
import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import camera_preview_server as preview  # noqa: E402


def _install_preview_gates(monkeypatch):
    monkeypatch.setattr(preview.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(preview.preflight, "read_boot_id",
                        lambda: preview.preflight.AUTHORIZED_BOOT_ID)
    monkeypatch.setattr(preview.preflight, "read_fpga_manager",
                        lambda: {"state": "operating", "flags": "0"})
    monkeypatch.setattr(preview.preflight, "audit_process_ownership",
                        lambda: {"findings": [], "denied": []})
    monkeypatch.setattr(preview.preflight, "read_ov9281_gate",
                        lambda device: {"status": "PASS", "device": device})


def test_camera_preview_publishes_live_frames_and_closes_cleanly(tmp_path, monkeypatch):
    _install_preview_gates(monkeypatch)
    hub = types.SimpleNamespace(frames=[], closed=False)
    hub.publish = lambda jpeg: hub.frames.append(jpeg)
    hub.close = lambda: setattr(hub, "closed", True)
    server = types.SimpleNamespace(started=False, closed=False)
    server.start = lambda: setattr(server, "started", True)
    def close_server():
        server.closed = True
        hub.close()
    server.close = close_server
    monkeypatch.setattr(preview, "MjpegFrameHub", lambda: hub)
    monkeypatch.setattr(preview, "MjpegPreviewServer", lambda **kwargs: server)

    async def fake_camera_frames(_device, *, max_frames, **_kwargs):
        assert max_frames == preview.MAX_PREVIEW_FRAMES
        yield np.zeros((240, 320), dtype=np.uint8)
        yield np.full((240, 320), 127, dtype=np.uint8)
        stop_event.set()

    monkeypatch.setattr(preview, "camera_gray_frames", fake_camera_frames)
    stop_event = asyncio.Event()
    status_path = tmp_path / "preview-status.json"

    result = asyncio.run(preview.serve_camera_preview(
        expected_boot_id=preview.preflight.AUTHORIZED_BOOT_ID,
        device="/dev/video0", host="0.0.0.0", port=8080,
        status_json=status_path, stop_event=stop_event,
    ))

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert server.started and server.closed
    assert hub.closed
    assert len(hub.frames) == 2
    assert all(frame.startswith(b"\xff\xd8") for frame in hub.frames)
    assert result["status"] == status["status"] == "PREVIEW_STOPPED"
    assert status["preview_mode"] == "camera-only; no Overlay or DMA"
    assert status["frames_published"] == 2


def test_camera_preview_refuses_boot_mismatch_before_opening_camera(tmp_path, monkeypatch):
    _install_preview_gates(monkeypatch)
    monkeypatch.setattr(preview.preflight, "read_boot_id", lambda: "different-boot")
    monkeypatch.setattr(preview, "camera_gray_frames",
                        lambda *args, **kwargs: pytest.fail("camera must stay closed"))
    status_path = tmp_path / "preview-status.json"

    with pytest.raises(preview.preflight.PreflightError, match="live boot_id"):
        asyncio.run(preview.serve_camera_preview(
            expected_boot_id=preview.preflight.AUTHORIZED_BOOT_ID,
            device="/dev/video0", host="0.0.0.0", port=8080,
            status_json=status_path,
        ))

    assert not status_path.exists()
