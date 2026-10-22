import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
import json
import asyncio
from types import SimpleNamespace
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parent))
import camera_only_preview as camera
import live_calibration as calibration
import mjpeg_preview as preview


def test_monochrome_channel_is_zero_copy_and_does_not_mix_channels(monkeypatch):
    frame = np.zeros((400, 640, 3), np.uint8)
    frame[:, :, 0] = 139
    monkeypatch.setattr(cv2, "cvtColor", lambda *a, **k: pytest.fail("redundant conversion"))
    gray = calibration.monochrome_view(frame)
    assert np.shares_memory(gray, frame)
    assert gray.shape == (400, 640)
    output, mask = camera.software_gray_binary(frame)
    assert np.all(output == 139)
    assert np.all(mask == 255)


def test_preprocessor_reuses_buffers_and_accepts_native_gray():
    processor = calibration.MonochromePreprocessor()
    frame = np.full((400, 640), 128, np.uint8)
    gray, mask = processor(frame)
    assert np.shares_memory(calibration.monochrome_view(frame), frame)
    assert not mask.any()
    frame[:] = 129
    next_gray, next_mask = processor(frame)
    assert next_gray is gray and next_mask is mask
    assert np.all(mask == 255)
    with pytest.raises(ValueError):
        processor(np.zeros((400, 640, 4), np.uint8))


def test_fixed_geometry_resize_matches_opencv_nearest_without_strided_opencv_copy(monkeypatch):
    frame = np.random.RandomState(17).randint(0,256,(400,640,3),dtype=np.uint8)
    expected = cv2.resize(frame[:,:,0], (320,240), interpolation=cv2.INTER_NEAREST)
    monkeypatch.setattr(cv2, "resize", lambda *a, **k: pytest.fail("strided OpenCV copy is too slow on ARM"))
    gray, mask = calibration.MonochromePreprocessor()(frame)
    assert np.array_equal(gray, expected)
    assert np.array_equal(mask, np.where(expected>128,255,0))


def test_calibration_metrics_use_exact_roi_and_exclude_nuts_from_background():
    gray = np.full((240, 320), 30, np.uint8)
    gray[100:110, 30:40] = 200
    gray[0:90] = 255  # outside ROI
    counter = camera.MaskCounter(max_area=900)
    result = counter.update(0, np.where(gray > 128, 255, 0).astype(np.uint8))
    metrics = calibration.optical_metrics(gray, result, counter, fps=42.0)
    assert metrics["bg_mean"] == 30
    assert metrics["roi_mean"] > 30
    assert metrics["over_128_ratio"] == pytest.approx(100 / (49 * 320))
    assert metrics["candidate_nut_blobs"] == 1
    assert metrics["candidate_areas_px"] == [100]
    assert metrics["glare_area_px"] == 0
    assert metrics["status"] == "OPTICS READY (TH=128 OK)"
    assert metrics["fps"] == 42


def test_calibration_glare_metrics_and_hud_line():
    gray = np.full((240, 320), 30, np.uint8)
    gray[100:140, 50:100] = 200
    counter = camera.MaskCounter(max_area=900)
    result = counter.update(0, np.where(gray > 128, 255, 0).astype(np.uint8))
    metrics = calibration.optical_metrics(gray, result, counter, fps=12)
    assert metrics["glare_area_px"] == 2000
    assert metrics["glare_rejected_count"] == 1
    assert result.glare_rejected_count == 1
    assert result.glare_area_px == 2000
    assert metrics["candidate_nut_blobs"] == 0
    assert metrics["status"] == "GLARE >128: ADJUST LIGHT/EXPOSURE"
    assert camera.COUNT_LINE_X == 160 and camera.HYSTERESIS == 15


def test_controls_are_opt_in_bounded_and_reset_to_standard():
    calls = []
    def apply(values):
        calls.append(values)
        return dict(values)
    locked = calibration.CalibrationControls(enabled=False, apply=apply)
    with pytest.raises(PermissionError):
        locked.update({"brightness": 1})
    controls = calibration.CalibrationControls(enabled=True, apply=apply)
    for invalid in ({"auto_exposure": 3}, {"brightness": True},
                    {"brightness": 65}, {"exposure_time_absolute": 49},
                    {"reset": True, "brightness": 2}, {"reset": 1}, {}):
        with pytest.raises(ValueError):
            controls.update(invalid)
    assert not calls
    assert controls.update({"brightness": -1})["brightness"] == -1
    assert controls.update({"reset": True}) == {
        "exposure_time_absolute": 50, "brightness": 0}


def test_calibration_hud_draws_status_metrics_and_dashed_glare_contour(monkeypatch):
    labels = []
    original = cv2.putText
    def text(frame, label, *args, **kwargs):
        labels.append(label)
        return original(frame, label, *args, **kwargs)
    monkeypatch.setattr(cv2, "putText", text)
    metrics = {"bg_mean": 30, "over_128_ratio": .1, "glare_area_px": 1500,
               "candidate_nut_blobs": 1, "candidate_areas_px": [100], "fps": 42,
               "status": "GLARE >128: ADJUST LIGHT/EXPOSURE",
               "glare_contours": [[[40, 100], [80, 100], [80, 140], [40, 140]]]}
    frame = preview.render_preview_frame(np.zeros((240, 320), np.uint8),
                                        total_count=0, line_y=120, calibration=metrics)
    assert metrics["status"] in labels
    assert any("BG Mean" in label for label in labels)
    assert any("FPS" in label for label in labels)
    assert np.any(np.all(frame[99:142, 39:82] == (0, 0, 255), axis=2))


def test_calibration_api_state_and_controls_require_same_origin_json():
    changes = []
    server = preview.MjpegPreviewServer(host="127.0.0.1", port=0,
        state_provider=lambda: {"calibration_mode": True},
        control_callback=lambda value: changes.append(value) or value).start()
    url = f"http://127.0.0.1:{server.port}"
    try:
        with urlopen(url + "/state.json") as response:
            assert json.load(response)["calibration_mode"]
        for headers in ({"Content-Type": "text/plain"},
                        {"Content-Type": "application/json", "Origin": "https://evil.example"}):
            request = Request(url + "/api/calibration", data=b'{"brightness":1}', headers=headers)
            with pytest.raises(HTTPError):
                urlopen(request)
        assert not changes
        request = Request(url + "/api/calibration", data=b'{"reset":true}',
                          headers={"Content-Type": "application/json", "Origin": url})
        with urlopen(request) as response:
            assert json.load(response) == {"reset": True}
        assert changes == [{"reset": True}]
    finally:
        server.close()


def test_formal_counter_cli_binds_glare_limits_in_counting_configuration():
    import run_stream_counter as runner
    config = runner.counting_configuration(runner._parser().parse_args([]))
    assert config["max_area"] == 900
    assert config["max_bbox_width"] == 85
    assert config["max_bbox_height"] == 85


def test_fully_bright_roi_is_not_optics_ready():
    gray = np.full((240, 320), 200, np.uint8)
    counter = camera.MaskCounter()
    result = counter.update(0, np.full_like(gray, 255))
    metrics = calibration.optical_metrics(gray, result, counter, fps=1)
    assert metrics["bg_mean"] is None
    assert metrics["over_128_ratio"] == 1
    assert metrics["glare_area_px"] == 49 * 320
    assert not metrics["status"].startswith("OPTICS READY")


def test_calibration_close_restores_readback_and_disables_future_writes():
    applied = []
    controls = calibration.CalibrationControls(enabled=True,
        apply=lambda values: applied.append(dict(values)) or dict(values))
    controls.update({"brightness": 4})
    controls.close_and_restore()
    assert applied[-1] == controls.STANDARD
    assert controls.values == controls.STANDARD
    with pytest.raises(PermissionError):
        controls.update({"brightness": 5})


def test_formal_profile_gate_rejects_nonstandard_controls(monkeypatch):
    output = "brightness: 1\nauto_exposure: 1 (Manual Mode)\nexposure_time_absolute: 50\n"
    monkeypatch.setattr(calibration.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=output))
    with pytest.raises(RuntimeError, match="standard camera controls"):
        calibration.require_standard_camera_controls("/dev/video0")


def test_formal_camera_rejects_control_drift_before_open(monkeypatch):
    import run_stream_counter as runner
    import operator_gate_launcher as gate_module
    gate = gate_module.OperatorGate()
    monkeypatch.setattr(gate_module, "is_registered_operator_gate_session", lambda gate: True)
    def reject(device):
        raise RuntimeError("nonstandard camera controls")
    monkeypatch.setattr(runner, "require_standard_camera_controls", reject, raising=False)
    async def consume():
        return [item async for item in runner.camera_gray_frames("/dev/video0", max_frames=1, operator_gate=gate)]
    with pytest.raises(RuntimeError, match="nonstandard camera controls"):
        asyncio.run(consume())


def test_formal_preview_server_has_no_control_endpoint():
    server = preview.MjpegPreviewServer(host="127.0.0.1", port=0).start()
    try:
        request = Request(f"http://127.0.0.1:{server.port}/api/calibration",
                          data=b'{"reset":true}', headers={"Content-Type": "application/json"})
        with pytest.raises(HTTPError) as error:
            urlopen(request)
        assert error.value.code == 403
    finally:
        server.close()
