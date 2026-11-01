from pathlib import Path
import uuid

import numpy as np
import pytest
from PIL import ImageFont

from scripts.standalone.compositor import DashboardComposer


def _font_path():
    candidates = (
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    for path in candidates:
        if path.is_file():
            return path
    pytest.skip("No system TrueType font is available for the dashboard renderer")


def test_button_status_does_not_overpaint_detection_count():
    font_path = _font_path()
    composer = DashboardComposer(font_path, latin_font_path=font_path)
    gray = np.zeros((400, 640), dtype=np.uint8)
    binary = np.zeros_like(gray)
    boxes = [(0, 0, 1, 1)] * 53

    def render(button_status):
        return composer.render(
            gray, binary, uuid.UUID(int=1), 1, boxes=boxes,
            dashboard={"button_status": button_status},
        ).copy()

    count_only = render("")
    with_button_status = render("IRQ READY / SW0 ON")
    count_region = (slice(184, 212), slice(874, 984))
    count_pixels = count_only[count_region] > 100

    assert count_pixels.any()
    assert np.array_equal(
        with_button_status[count_region][count_pixels],
        count_only[count_region][count_pixels],
    ), "button status text must leave the detection count pixels unobstructed"
