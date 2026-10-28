"""Render the full-resolution RLE session and its pinned build resources."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from PySide6.QtWidgets import QApplication
from controller import load_config
from qt_view import DesktopWindow


def test_rle_view_uses_actual_dimensions_source_and_build_resources():
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow(object())
    window.show_status({
        "connected": True,
        "preview": {"width": 640, "height": 400, "fps": 17.5},
        "telemetry": {"source": "pl_rle", "phase": "running", "stale": False,
                      "last_frame_age_seconds": 0, "total_count": 7},
        "resource_monitor": {"enabled": True, "mode": "detail", "metrics": {},
                             "detail": {"fpga": load_config()["fpga_resources"]}},
    })
    assert "640 × 400" in window.preview_meta.text()
    assert window.source_label.text() == "pl_rle"
    assert window.count_value.text() == "7"
    assert "5809 / 53200 · 10.92%" in window.resource_details.toPlainText()
    gray = np.zeros((400, 640), np.uint8)
    window.show_frame({
        "frame_id": 9, "source": "pl_rle", "gray": gray, "mask": gray.copy(),
        "result": {"total_count": 8, "detections": [], "tracks": []},
        "config": {"width": 640, "height": 400, "count_axis": "x", "line_x": 320},
    })
    assert window._images()[0].size == (640, 400)
    assert window.count_value.text() == "8"
    window.deleteLater()
    app.processEvents()
