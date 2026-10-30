"""Qt desktop behavior: one frame, capability routing, and safe close."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pytest
from PIL import Image
from qt_view import DesktopWindow, QtRootAdapter
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt


@pytest.fixture(scope="session")
def application():
    return QApplication.instance() or QApplication([])


class FakeController:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, args))


def frame():
    gray = np.zeros((240, 320), np.uint8)
    gray[80:100, 120:140] = 200
    return {"session_id": "test", "frame_id": 12, "gray": gray,
            "mask": np.where(gray > 128, 255, 0).astype(np.uint8),
            "source": "software", "result": {"total_count": 5,
            "detections": [{"x_min": 120, "y_min": 80, "x_max": 139, "y_max": 99}],
            "tracks": []}, "config": {"count_axis": "x", "line_x": 160,
            "roi_top": 96, "roi_bottom": 144}}


def test_frame_previews_and_count_are_rendered_together(application):
    window = DesktopWindow(FakeController())
    window.show_frame(frame())
    assert window.count_value.text() == "5"
    assert window.metric_values["detections"].text() == "1"
    assert window.preview_image.pixmap() is not None
    assert window.binary_image.pixmap() is not None
    assert window.current_frame_id == 12
    window.show_status({"connected": True, "telemetry": {"source": "software",
        "phase": "running", "stale": False, "last_frame_age_seconds": 0,
        "total_count": 99, "detections": 9}, "capabilities": {}})
    assert window.count_value.text() == "5"
    assert window.metric_values["detections"].text() == "1"
    window.deleteLater()


def test_finished_tracks_are_not_drawn_in_tracking_overlay(application):
    window = DesktopWindow(FakeController())
    base = np.zeros((240, 320), np.uint8)
    result = {"detections": [], "tracks": [
        {"track_id": 1, "finished": True,
         "detection": {"x_min": 20, "y_min": 20, "x_max": 40, "y_max": 40}},
        {"track_id": 2, "finished": False,
         "detection": {"x_min": 80, "y_min": 80, "x_max": 100, "y_max": 100}},
    ]}

    overlay = window._annotate(Image.fromarray(base), result, {}, tracks=True)

    assert overlay.getpixel((20, 20)) == 0
    assert overlay.getpixel((80, 80)) != 0
    window.deleteLater()


def test_pause_preserves_last_frame_and_sends_control(application):
    controller = FakeController()
    window = DesktopWindow(controller)
    window.show_frame(frame())
    previous = window.preview_image.pixmap().cacheKey()
    window.pause_button.click()
    assert ("set_preview_paused", (True,)) in controller.calls
    assert window.preview_image.pixmap().cacheKey() == previous
    assert "暂停" in window.source_label.text()
    window.show_status({"connected": True, "telemetry": {"source": "software"}, "capabilities": {}})
    assert "暂停" in window.source_label.text()
    window.deleteLater()


def test_controls_and_calibration_follow_backend_capabilities(application):
    controller = FakeController()
    window = DesktopWindow(controller)
    window.show_status({"connected": True, "telemetry": {"source": "software",
        "phase": "running", "stale": False, "last_frame_age_seconds": 0},
        "camera_controls": {"exposure_time_absolute": 50, "brightness": 0},
        "capabilities": {"stop": True, "reset": True, "calibration": True},
        "debug": {"enabled": False}, "resource_monitor": {"enabled": False, "mode": "normal"}})
    window.reset_button.click()
    assert ("reset", ()) in controller.calls
    window.exposure_input.setValue(50)
    window.brightness_input.setValue(1)
    window.apply_button.click()
    assert ("calibrate", ({"exposure_time_absolute": 50, "brightness": 1},)) in controller.calls
    window.deleteLater()


def test_status_row_reports_only_states_this_session_can_change(application):
    window = DesktopWindow(FakeController())
    window.show_status({"connected": True, "telemetry": {"source": "software",
        "phase": "running", "camera": "frames_received", "fpga": "disabled",
        "dma": "disabled", "stale": False, "last_frame_age_seconds": 0,
        "total_count": 4}, "capabilities": {}})
    assert set(window.devices) == {"camera", "system"}
    texts = [label.text() for label in window.devices.values()]
    assert "相机：有图像" in texts
    assert "系统：运行中" in texts
    assert all("未启用" not in text for text in texts)
    window.deleteLater()


def test_start_is_available_while_disconnected(application):
    window = DesktopWindow(FakeController())
    window.show_status({"connected": False, "capabilities": {"start": True, "stop": True}})
    assert window.start_button.isEnabled()
    assert not window.stop_button.isEnabled()
    assert "未连接" in window.connection_label.text()
    window.deleteLater()


def test_debug_and_resource_controls_remain_available(application):
    controller = FakeController()
    window = DesktopWindow(controller)
    window.show_status({"connected": True, "telemetry": {"source": "software", "phase": "running"},
        "capabilities": {}, "debug": {"enabled": False},
        "resource_monitor": {"enabled": False, "mode": "normal"}})
    window.show_frame(frame())
    window.debug_button.click()
    assert ("set_debug", (True,)) in controller.calls
    assert len(window.debug_steps) == 5
    assert "12" in window.debug_meta.text()
    window.resource_button.click()
    assert ("set_resource", (True, "normal")) in controller.calls
    window.deleteLater()


def test_close_failure_reenables_controls(application):
    class RejectedController(FakeController):
        def close(self): return False
    window = DesktopWindow(RejectedController())
    window.show_status({"connected": False, "capabilities": {"start": True}})
    window.show()
    window.close()
    assert not window.closing
    assert window.isVisible()
    assert window.start_button.isEnabled()
    assert window.pause_button.isEnabled()
    window.allow_close = True
    window.close()


def test_close_waits_for_controller_confirmation(application):
    controller = FakeController()
    window = DesktopWindow(controller)
    window.show()
    window.close()
    assert ("close", ()) in controller.calls
    assert window.closing is True and window.isVisible()
    adapter = QtRootAdapter()
    adapter.bind(window)
    adapter.destroy()
    application.processEvents()
    assert not window.isVisible()


def test_qt_adapter_runs_callbacks_on_main_thread(application):
    adapter = QtRootAdapter()
    called = []
    adapter.after(0, lambda: called.append(True))
    application.processEvents()
    assert called == [True]


def test_real_controller_delivers_frame_and_closes_without_session(application):
    from controller import DesktopController, load_config
    from PySide6.QtTest import QTest
    adapter = QtRootAdapter()
    controller = DesktopController(adapter, load_config())
    window = DesktopWindow(controller)
    adapter.bind(window)
    controller.attach(window)
    window.show()
    with controller.frame_lock:
        controller.latest_frame = frame()
    QTest.qWait(120)
    assert window.current_frame_id == 12
    assert window.count_value.text() == "5"
    window.close()
    QTest.qWait(120)
    assert controller.shutdown.is_set()
    assert not window.isVisible()
