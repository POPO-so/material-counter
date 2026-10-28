"""Qt pause must preserve the preview and avoid collapsing its layout."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from PySide6.QtWidgets import QApplication
from qt_view import DesktopWindow


class Controller:
    def set_preview_paused(self, paused):
        self.paused = paused


def test_pause_keeps_preview_geometry_stable_and_freezes_last_frame():
    app = QApplication.instance() or QApplication([])
    controller = Controller()
    window = DesktopWindow(controller)
    window.show()
    frame = {"frame_id": 1, "gray": np.zeros((240, 320), np.uint8),
             "mask": np.zeros((240, 320), np.uint8), "source": "software",
             "result": {"total_count": 3, "detections": [], "tracks": []},
             "config": {"count_axis": "x", "line_x": 160}}
    window.show_frame(frame)
    app.processEvents()
    geometry = window.preview_image.geometry()
    preview = window.preview_image.pixmap().cacheKey()
    binary = window.binary_image.pixmap().cacheKey()
    window.pause_button.click()
    app.processEvents()
    assert controller.paused is True
    assert window.preview_image.geometry() == geometry
    assert window.preview_image.pixmap().cacheKey() == preview
    assert window.binary_image.pixmap().cacheKey() == binary
    assert "暂停" in window.source_label.text()
    window.allow_close = True
    window.close()
