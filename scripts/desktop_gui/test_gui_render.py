"""Real Tk layout regression for pause and paired-frame display."""
import sys
from pathlib import Path

import numpy as np
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parent))
from view import DesktopWindow


class Controller:
    def set_preview_paused(self, paused):
        self.paused = paused


def test_pause_keeps_preview_geometry_stable_and_freezes_last_frame():
    root = tk.Tk()
    root.withdraw()
    try:
        controller = Controller()
        view = DesktopWindow(root, controller)
        frame = {"frame_id": 1, "gray": np.zeros((240, 320), np.uint8),
                 "mask": np.zeros((240, 320), np.uint8), "source": "software",
                 "result": {"total_count": 3, "detections": [], "tracks": []},
                 "config": {"count_axis": "x", "line_x": 160}}
        view.show_frame(frame)
        root.update_idletasks()
        height = view.body.winfo_reqheight()
        preview_image = view.preview_image.cget("image")
        binary_image = view.binary_image.cget("image")
        view._toggle_pause()
        root.update_idletasks()
        assert controller.paused is True
        assert view.body.winfo_reqheight() == height
        assert view.preview_image.cget("image") == preview_image
        assert view.binary_image.cget("image") == binary_image
        assert "暂停" in view.source_var.get()
    finally:
        root.destroy()
