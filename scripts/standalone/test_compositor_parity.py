"""Pixel-parity tests for the dashboard composer optimisations.

The reference object recomputes the pre-optimisation drawing sequence
(scratch-buffer resize, per-dash ``cv2.line``, full chrome redraw) so every
output can be compared byte for byte with the optimised implementation.
"""
import importlib.util
import math
import uuid
from pathlib import Path

import numpy as np
import pytest

if importlib.util.find_spec("scripts.standalone.compositor") is None:
    compositor = None
else:
    from scripts.standalone import compositor


FONT = Path("C:/Windows/Fonts/msyh.ttc")
HEIGHT = 720
WIDTH = 1280


def _api():
    if compositor is None:
        pytest.fail("compositor implementation is missing")
    return compositor


class _Reference:
    """Pre-optimisation renderer kept verbatim for pixel comparison."""

    def __init__(self, font):
        self.helper = compositor.DashboardComposer(font, latin_font_path=font)
        self.canvas = np.zeros((HEIGHT, WIDTH), np.uint8)
        self._chrome = np.zeros_like(self.canvas)
        self._key = None
        self._slot_chrome_key = {}
        self._work = np.empty((480, 768), np.uint8)
        self.last_timings_ms = {"scale_ms": 0.0, "draw_ms": 0.0}

    def _redraw_chrome(self, dashboard, binary_background, target_count, lines):
        helper = self.helper
        values = (
            helper._number(dashboard.get("count"), 0),
            helper._number(dashboard.get("display_fps")),
            helper._number(dashboard.get("processing_ms"), 1, " ms"),
            helper._number(dashboard.get("cpu_percent"), 0, "%"),
            helper._number(dashboard.get("memory_percent"), 0, "%"),
        )
        state = str(dashboard.get("state", "RUNNING"))
        detail = tuple(lines[:3]) if dashboard.get("detail_page") else ()
        key = (values, state, binary_background, target_count, detail)
        if key == self._key:
            return False
        from PIL import Image, ImageDraw
        image = Image.new("L", (WIDTH, HEIGHT), 14)
        draw = ImageDraw.Draw(image)
        helper._whole_latin_text(draw, (24, 12), "NUT VISION COUNTER", 28, 242)
        draw.ellipse((1060, 25, 1070, 35), fill=224)
        helper._whole_latin_text(draw, (1082, 19), state[:15], 18, 208)
        for x, label, value in zip((24, 440, 856),
                                   ("本次累计计数", "显示 FPS", "处理延迟 / RLE + 计数"),
                                   values[:3]):
            draw.rounded_rectangle((x, 58, x + 400, 126), radius=10, fill=42)
            helper._text(draw, (x + 16, 62), label, 18, 170)
            helper._whole_latin_text(draw, (x + 16, 84), value, 34, 248)
        draw.rounded_rectangle((24, 136, 1256, 632), radius=10, fill=25, outline=58)
        helper._whole_latin_text(draw, (42, 157), "CAM 01", 24, 232)
        helper._whole_latin_text(draw, (42, 194), "640 x 400", 18, 158)
        helper._text(draw, (42, 222), "二值预览" if binary_background else "实时灰度画面", 20, 212)
        draw.line((44, 284, 100, 284), fill=240, width=2)
        helper._text(draw, (42, 298), "实际计数线", 18, 175)
        helper._text(draw, (42, 328), "从左向右计数", 18, 212)
        for y, text in zip((448, 480, 512, 544),
                           ("BTN0  暂停 / 恢复", "BTN1  长按 2 秒清零",
                            "BTN2  灰度 / 二值", "BTN3  详细状态")):
            helper._text(draw, (42, y), text, 18, 144)
        helper._text(draw, (1044, 158), "检测目标", 20, 170)
        helper._whole_latin_text(draw, (1044, 190), str(target_count), 34, 242)
        helper._text(draw, (1044, 248), "跟踪 ID / 检测框", 18, 170)
        helper._text(draw, (1044, 279), "计数真值待人工核对", 18, 144)
        for i, text in enumerate(detail):
            for row in range(0, min(len(text), 30), 10):
                helper._text(draw, (1044, 344 + i * 78 + row // 10 * 24),
                             text[row:row + 10], 18, 170)
        for x, width, label, text, field in (
                (24, 600, "PS CPU 使用率", values[3], "cpu_percent"),
                (640, 616, "DDR 内存占用", values[4], "memory_percent")):
            draw.rounded_rectangle((x, 646, x + width, 704), radius=9, fill=40)
            helper._text(draw, (x + 16, 650), label, 18, 178)
            helper._whole_latin_text(draw, (x + width - 74, 674), text, 18, 238)
            left, right = x + 16, x + width - 92
            draw.rounded_rectangle((left, 682, right, 689), radius=3, fill=82)
            percent = dashboard.get(field)
            if (isinstance(percent, (int, float)) and not isinstance(percent, bool)
                    and math.isfinite(percent) and percent > 0):
                end = left + round((right - left) * min(100, percent) / 100)
                if end > left:
                    draw.rounded_rectangle((left, 682, end, 689), radius=3, fill=218)
        self._chrome[:] = np.asarray(image)
        self._key = key
        return True

    def render(self, gray, binary, session_id, frame_id, boxes=(), tracks=(),
               line_x=320, status_lines=(), *, binary_background=False, output=None,
               line_y=None, dashboard=None, slot_key=None):
        import cv2
        helper = self.helper
        compositor.GrayComposer._validate_image(gray, "gray")
        compositor.GrayComposer._validate_image(binary, "binary")
        target = self.canvas if output is None else output
        boxes, tracks, line_x, lines = compositor.GrayComposer._validate_annotations(
            helper, boxes, tracks, line_x, status_lines)
        if line_y is not None:
            line_y = compositor._integer(line_y, "line_y")
        marker = compositor.encode_marker(session_id, frame_id)
        chrome_redrawn = self._redraw_chrome(dict(dashboard or {}), binary_background,
                                             len(boxes), lines)
        if slot_key is None:
            np.copyto(target, self._chrome)
        elif chrome_redrawn or self._slot_chrome_key.get(slot_key) != self._key:
            np.copyto(target, self._chrome)
        cv2.resize(binary if binary_background else gray, (768, 480), dst=self._work,
                   interpolation=cv2.INTER_NEAREST if binary_background
                   else helper.gray_interpolation)
        x0, y0, width, height = compositor.DashboardComposer.VIEW
        image = target[y0:y0 + height, x0:x0 + width]
        np.copyto(image, self._work)
        sx = lambda x: min(width - 1, max(0, math.floor(x * width / compositor.SOURCE_WIDTH + 0.5)))
        sy = lambda y: min(height - 1, max(0, math.floor(y * height / compositor.SOURCE_HEIGHT + 0.5)))
        horizontal = line_y is not None
        fixed = sy(line_y) if horizontal else sx(line_x)
        extent = width if horizontal else height
        for start in range(0, extent, 24):
            end = min(extent - 1, start + 13)
            a, b = ((start, fixed), (end, fixed)) if horizontal else ((fixed, start), (fixed, end))
            cv2.line(image, a, b, 0, 5, cv2.LINE_8)
            cv2.line(image, a, b, 248, 2, cv2.LINE_8)
        label_at = (12, max(22, fixed - 12)) if horizontal else (min(width - 140, fixed + 12), 24)
        cv2.putText(image, "COUNT LINE", label_at, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 0, 4, cv2.LINE_8)
        cv2.putText(image, "COUNT LINE", label_at, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 248, 1, cv2.LINE_8)
        for x1, y1, x2, y2 in boxes:
            a, b = (sx(x1), sy(y1)), (sx(x2), sy(y2))
            cv2.rectangle(image, a, b, 0, 5, cv2.LINE_8)
            cv2.rectangle(image, a, b, 245, 2, cv2.LINE_8)
        for track_id, x, y in tracks:
            origin = (min(width - 100, sx(x)), max(21, sy(y) - 10))
            cv2.putText(image, "ID " + track_id, origin, cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, 0, 4, cv2.LINE_8)
            cv2.putText(image, "ID " + track_id, origin, cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, 248, 1, cv2.LINE_8)
        bits = np.unpackbits(np.frombuffer(marker, dtype=np.uint8), bitorder="big") * np.uint8(255)
        target[0, compositor.MARKER_X:compositor.MARKER_X + compositor.MARKER_WIDTH] = bits
        target[HEIGHT - 1, compositor.MARKER_X:compositor.MARKER_X + compositor.MARKER_WIDTH] = bits
        if slot_key is not None:
            self._slot_chrome_key[slot_key] = self._key
        return target


def _pair():
    if not FONT.is_file():
        pytest.skip("local CJK font is unavailable")
    api = _api()
    return (api.DashboardComposer(FONT, latin_font_path=FONT), _Reference(FONT), api)


def _frame(seed=0, fill=None):
    if fill is not None:
        return np.full((400, 640), fill, np.uint8)
    return np.random.default_rng(seed).integers(0, 256, size=(400, 640), dtype=np.uint8)


def _dashboard(**overrides):
    base = dict(count=12, display_fps=13.4, processing_ms=71.2, cpu_percent=93.0,
                memory_percent=48.5, state="RUNNING", detail_page=False)
    base.update(overrides)
    return base


SCENARIOS = [
    dict(name="initial-gray", dashboard=_dashboard(), boxes=(), tracks=()),
    dict(name="targets-present", dashboard=_dashboard(count=3, detail_page=True),
         boxes=((40, 60, 120, 160), (300, 200, 380, 300)),
         tracks=(("7", 80, 110), ("8", 340, 250)),
         status_lines=("L1 line", "L2 line")),
    dict(name="targets-disappear", dashboard=_dashboard(count=3),
         boxes=(), tracks=()),
    dict(name="number-shortening", dashboard=dict(count=None, display_fps=float("nan"),
                                                  processing_ms=-1, cpu_percent=None,
                                                  memory_percent=float("inf"),
                                                  state="PAUSED")),
    dict(name="binary-mode", dashboard=_dashboard(count=5), binary_background=True,
         boxes=((10, 10, 60, 70),)),
    dict(name="horizontal-line", dashboard=_dashboard(), line_y=200,
         boxes=((100, 100, 200, 200),), tracks=(("2", 150, 150),)),
    dict(name="detail-page", dashboard=_dashboard(detail_page=True),
         status_lines=("ID 12 x=340 y=120", "队列 3", "计数 42")),
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["name"] for s in SCENARIOS])
def test_optimised_render_matches_reference_pixels(scenario):
    new, ref, api = _pair()
    frame = _frame(1)
    binary = (frame > 128).astype(np.uint8) * 255
    session = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
    kwargs = dict(boxes=scenario.get("boxes", ()), tracks=scenario.get("tracks", ()),
                  status_lines=scenario.get("status_lines", ()),
                  binary_background=scenario.get("binary_background", False),
                  dashboard=scenario["dashboard"], line_y=scenario.get("line_y"))
    new_out = np.zeros((api.HEIGHT, api.WIDTH), np.uint8)
    ref_out = np.zeros((api.HEIGHT, api.WIDTH), np.uint8)
    new.render(frame, binary, session, 5, output=new_out, **kwargs)
    ref.render(frame, binary, session, 5, output=ref_out, **kwargs)
    assert np.array_equal(new_out, ref_out)


def test_three_slot_rotation_matches_reference_pixels():
    new, ref, api = _pair()
    session = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
    new_slots = [np.zeros((api.HEIGHT, api.WIDTH), np.uint8) for _ in range(3)]
    ref_slots = [np.zeros((api.HEIGHT, api.WIDTH), np.uint8) for _ in range(3)]
    for index in range(9):
        frame = _frame(10 + index)
        binary = (frame > 128).astype(np.uint8) * 255
        slot = index % 3
        dashboard = _dashboard(count=index // 2, display_fps=10 + index * 0.5)
        boxes = () if index % 4 == 3 else ((10 + index, 20, 90 + index, 120),)
        tracks = () if index % 4 == 3 else ((str(index), 40, 60),)
        kwargs = dict(boxes=boxes, tracks=tracks, dashboard=dashboard, slot_key=slot)
        new.render(frame, binary, session, index, output=new_slots[slot], **kwargs)
        ref.render(frame, binary, session, index, output=ref_slots[slot], **kwargs)
        assert np.array_equal(new_slots[slot], ref_slots[slot]), f"slot {slot} frame {index}"


def test_mode_switch_back_redraws_identical_pixels():
    new, ref, api = _pair()
    session = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
    frame = _frame(3)
    binary = (frame > 128).astype(np.uint8) * 255
    for index, mode in enumerate((False, True, False, True)):
        out_new = np.zeros((api.HEIGHT, api.WIDTH), np.uint8)
        out_ref = np.zeros((api.HEIGHT, api.WIDTH), np.uint8)
        new.render(frame, binary, session, index, output=out_new,
                   binary_background=mode, dashboard=_dashboard(count=index))
        ref.render(frame, binary, session, index, output=out_ref,
                   binary_background=mode, dashboard=_dashboard(count=index))
        assert np.array_equal(out_new, out_ref)


def test_reused_slot_with_unchanged_chrome_keeps_reference_pixels():
    new, ref, api = _pair()
    session = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
    frame = _frame(4)
    binary = np.zeros_like(frame)
    dashboard = _dashboard()
    out_new = np.zeros((api.HEIGHT, api.WIDTH), np.uint8)
    out_ref = np.zeros((api.HEIGHT, api.WIDTH), np.uint8)
    for frame_id in range(3):
        new.render(frame, binary, session, frame_id, output=out_new,
                   dashboard=dashboard, slot_key=0)
        ref.render(frame, binary, session, frame_id, output=out_ref,
                   dashboard=dashboard, slot_key=0)
        assert np.array_equal(out_new, out_ref)