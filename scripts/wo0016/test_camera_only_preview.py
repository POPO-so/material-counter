import sys
from pathlib import Path

import numpy as np
import pytest
import camera_only_preview as preview

sys.path.insert(0, str(Path(__file__).resolve().parent))

from camera_only_preview import profile_matches, software_gray_binary  # noqa: E402


def test_camera_preview_generates_same_threshold_128_binary_pip():
    for value, expected in ((127, 0), (128, 0), (129, 255)):
        camera_bgr = np.full((400, 640, 3), value, dtype=np.uint8)

        gray, mask = software_gray_binary(camera_bgr)

        assert gray.shape == (240, 320)
        assert gray.dtype == np.uint8
        assert mask.shape == (240, 320)
        assert mask.dtype == np.uint8
        assert np.all(mask == expected)


def test_camera_preview_requires_exact_live_manual_v4l2_profile():
    profile = """Width/Height      : 640/400
Pixel Format      : 'MJPG'
Frames per second: 60.000 (60/1)
brightness: 0
auto_exposure: 1 (Manual Mode)
exposure_time_absolute: 50
"""

    assert profile_matches(profile)
    assert not profile_matches(profile.replace("brightness: 0", "brightness: 1"))
    assert not profile_matches(profile.replace("exposure_time_absolute: 50",
                                               "exposure_time_absolute: 51"))


def test_calibration_memory_policy_warns_for_dma_reserve_but_checks_real_memory():
    snapshot = {"cma_free_kib": 30000, "mem_available_kib": 200000, "process_rss_kib": 60000}
    assert preview.require_preview_memory(snapshot, calibration_mode=True) == "CMA_LOW: STOP CALIBRATION BEFORE DMA"
    with pytest.raises(RuntimeError, match="CMA"):
        preview.require_preview_memory(snapshot, calibration_mode=False)
    with pytest.raises(RuntimeError, match="CMA"):
        preview.require_preview_memory(snapshot, calibration_mode=True, startup=True)


def test_calibration_still_has_a_cma_emergency_floor():
    snapshot = {"cma_free_kib": 16383, "mem_available_kib": 200000, "process_rss_kib": 60000}
    with pytest.raises(RuntimeError, match="CMA"):
        preview.require_preview_memory(snapshot, calibration_mode=True)
    snapshot["cma_free_kib"] = 16384
    assert preview.require_preview_memory(snapshot, calibration_mode=True).startswith("CMA_LOW")


@pytest.mark.parametrize("field,value,error", [
    ("mem_available_kib", 65535, "MemAvailable"),
    ("process_rss_kib", 196609, "RSS"),
    ("cma_free_kib", -1, "invalid"),
])
def test_calibration_memory_policy_stops_on_actual_pressure(field, value, error):
    snapshot = {"cma_free_kib": 40000, "mem_available_kib": 200000, "process_rss_kib": 60000}
    snapshot[field] = value
    with pytest.raises(RuntimeError, match=error):
        preview.require_preview_memory(snapshot, calibration_mode=True)


def test_preview_memory_snapshot_reads_cma_available_and_process_rss(monkeypatch):
    texts = {"/proc/meminfo": "MemAvailable: 200000 kB\nCmaFree: 40000 kB\n",
             "/proc/self/status": "Name: python3\nVmRSS: 60000 kB\n"}
    monkeypatch.setattr(preview.Path, "read_text", lambda path, **kw: texts[str(path).replace('\\','/')])
    assert preview.preview_memory_snapshot() == {
        "cma_free_kib": 40000, "mem_available_kib": 200000, "process_rss_kib": 60000}
