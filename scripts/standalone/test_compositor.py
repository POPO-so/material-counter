import binascii
import importlib.util
import math
from multiprocessing import shared_memory
import uuid
from pathlib import Path

import numpy as np
import pytest

if importlib.util.find_spec("scripts.standalone.compositor") is None:
    compositor = None
else:
    from scripts.standalone import compositor


FONT = Path("C:/Windows/Fonts/msyh.ttc")


def _api():
    if compositor is None:
        pytest.fail("compositor implementation is missing")
    return compositor


def _composer():
    api = _api()
    if not FONT.is_file():
        pytest.skip("local CJK font is unavailable")
    return api.GrayComposer(FONT)


def _decode_marker(pixels):
    bits = (pixels == 255).astype(np.uint8)
    data = np.packbits(bits, bitorder="big").tobytes()
    payload, crc_bytes = data[:-2], data[-2:]
    assert int.from_bytes(crc_bytes, "big") == binascii.crc_hqx(payload, 0xFFFF)
    return uuid.UUID(bytes=payload[2:18]), int.from_bytes(payload[18:26], "big")


def test_horizontal_count_line_uses_same_source_coordinates_as_y_counter():
    composer=_composer()
    gray=np.full((400,640),80,np.uint8)
    binary=np.zeros_like(gray)
    image=composer.render(gray,binary,uuid.uuid4(),1,line_y=200)
    assert np.all(image[540,32:608]==255)
    assert image[400,320]==0
    assert np.all(image[:360,32:608]==80)


def test_horizontal_count_line_requires_integer_source_coordinate():
    composer=_composer();gray=np.zeros((400,640),np.uint8)
    with pytest.raises(TypeError,match='line_y'):
        composer.render(gray,gray,uuid.uuid4(),1,line_y=200.5)


def test_marker_encoder_has_fixed_layout_crc_and_known_crc_vector():
    encode = _api().encode_marker
    session = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
    frame_id = 0x0102030405060708

    encoded = encode(session, frame_id)

    assert len(encoded) == 28
    assert encoded[:2] == b"MC"
    assert encoded[2:18] == session.bytes
    assert encoded[18:26] == frame_id.to_bytes(8, "big")
    assert int.from_bytes(encoded[26:], "big") == binascii.crc_hqx(encoded[:26], 0xFFFF)
    assert binascii.crc_hqx(b"123456789", 0xFFFF) == 0x29B1
    with pytest.raises((TypeError, ValueError)):
        encode(session, 1 << 64)
    with pytest.raises(TypeError):
        encode("not-a-uuid", 0)


def test_render_preserves_fixed_geometry_scaling_padding_and_binary_values():
    composer = _composer()
    session = uuid.uuid4()
    gray = np.full((400, 640), 100, dtype=np.uint8)
    binary = np.zeros((400, 640), dtype=np.uint8)
    binary[20:23, 10:13] = 255

    canvas = composer.render(gray, binary, session, 8, boxes=((10, 20, 12, 22),), line_x=320)

    assert canvas.shape == (720, 1280)
    assert canvas.dtype == np.uint8
    assert canvas is composer.canvas
    assert np.all(canvas[:, :32] == 0)
    assert np.all(canvas[:, 608:640] == 0)
    assert canvas[100, 32 + 90] == 100
    assert set(np.unique(canvas[360:720, 32:608])).issubset({0, 255})
    assert canvas[360 + 18, 32 + 9] == 255
    assert canvas[360 + 20, 32 + 11] == 255
    assert canvas[360 + 20, 32 + 288] in (0, 255)
    assert _decode_marker(canvas[0, 640:864]) == (session, 8)
    assert _decode_marker(canvas[719, 640:864]) == (session, 8)


def test_render_exposes_per_frame_scale_and_draw_timings(monkeypatch):
    api = _api()
    composer = _composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    binary = np.zeros_like(gray)
    ticks = iter((0, 10_000_000, 20_000_000, 50_000_000))
    monkeypatch.setattr(api, "perf_counter_ns", lambda: next(ticks), raising=False)

    composer.render(gray, binary, uuid.uuid4(), 1, status_lines=("更新状态",))

    timings = composer.last_timings_ms
    assert set(timings) == {"scale_ms", "draw_ms"}
    assert timings["scale_ms"] == 10
    assert timings["draw_ms"] == 30
    assert all(value >= 0 for value in timings.values())

    ticks = iter((100_000_000, 105_000_000, 110_000_000, 118_000_000))
    composer.render(gray, binary, uuid.uuid4(), 2, status_lines=("更新状态",))
    assert composer.last_timings_ms == {"scale_ms": 5, "draw_ms": 8}


def test_render_can_write_complete_frame_directly_into_shared_slot():
    composer = _composer()
    session = uuid.uuid4()
    gray = np.full((400, 640), 96, dtype=np.uint8)
    binary = np.zeros_like(gray)
    options = {"boxes": ((12, 20, 80, 140),), "tracks": (("7", 20, 30),),
               "status_lines": ("共享帧状态", "第二行遥测")}
    expected = composer.render(gray, binary, session, 57, **options).copy()
    target = np.full((720, 1280), 31, dtype=np.uint8)

    actual = composer.render(gray, binary, session, 57, output=target, **options)

    assert actual is target
    assert np.array_equal(target, expected)


def test_render_uses_configured_gray_interpolation_and_keeps_binary_nearest():
    api = _api()
    gray = np.tile(np.arange(640, dtype=np.uint8), (400, 1))
    binary = np.where(gray >= 128, 255, 0).astype(np.uint8)
    session = uuid.uuid4()

    assert _composer().gray_interpolation == api.cv2.INTER_AREA
    for interpolation in (api.cv2.INTER_AREA, api.cv2.INTER_LINEAR):
        composer = api.GrayComposer(FONT, gray_interpolation=interpolation)

        canvas = composer.render(gray, binary, session, 31)

        expected_gray = api.cv2.resize(
            gray, (api.VIEW_WIDTH, api.VIEW_HEIGHT), interpolation=interpolation
        )
        expected_binary = api.cv2.resize(
            binary, (api.VIEW_WIDTH, api.VIEW_HEIGHT), interpolation=api.cv2.INTER_NEAREST
        )
        actual_binary = canvas[api.BOTTOM_Y:, api.VIEW_X:api.VIEW_X + api.VIEW_WIDTH]
        unmarked = np.ones(expected_binary.shape, dtype=bool)
        line_x = api._scale_coordinate(320)
        unmarked[:, line_x - 3:line_x + 4] = False
        assert np.array_equal(canvas[:api.VIEW_HEIGHT, api.VIEW_X:api.VIEW_X + api.VIEW_WIDTH],
                              expected_gray)
        assert np.array_equal(actual_binary[unmarked], expected_binary[unmarked])


def test_render_keeps_reusable_canvas_panel_current_after_direct_output():
    composer = _composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    binary = np.zeros_like(gray)
    target = np.empty((720, 1280), dtype=np.uint8)
    session = uuid.uuid4()
    composer.render(gray, binary, session, 1, status_lines=("最新遥测",), output=target)

    reusable = composer.render(gray, binary, session, 2, status_lines=("最新遥测",))

    assert np.array_equal(reusable[1:-1, 640:], target[1:-1, 640:])


@pytest.mark.parametrize("output", [
    np.empty((720, 1279), dtype=np.uint8),
    np.empty((720, 1280), dtype=np.uint16),
    np.empty((1440, 1280), dtype=np.uint8)[::2],
    np.empty((720, 1280), dtype=np.uint8),
])
def test_render_rejects_incompatible_shared_slot_output(output):
    composer = _composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    binary = np.zeros_like(gray)
    if output.shape == (720, 1280) and output.dtype == np.uint8:
        output.flags.writeable = False

    with pytest.raises((TypeError, ValueError), match="output must"):
        composer.render(gray, binary, uuid.uuid4(), 1, output=output)


def test_gray_background_mode_uses_gray_work_and_keeps_frame_identity():
    composer = _composer()
    session = uuid.uuid4()
    gray = np.full((400, 640), 128, dtype=np.uint8)
    binary = np.zeros((400, 640), dtype=np.uint8)

    canvas = composer.render(
        gray,
        binary,
        session,
        0xA5,
        boxes=((10, 20, 12, 22),),
        binary_background=False,
    )

    assert canvas[100, 32 + 90] == 128
    assert canvas[360 + 90, 32 + 90] == 128
    assert canvas[360 + 18, 32 + 9] == 255
    assert np.all(binary == 0)
    assert _decode_marker(canvas[0, 640:864]) == (session, 0xA5)
    assert _decode_marker(canvas[719, 640:864]) == (session, 0xA5)


def test_gray_background_mode_still_rejects_nonbinary_input_without_canvas_changes():
    composer = _composer()
    gray = np.full((400, 640), 128, dtype=np.uint8)
    binary = np.zeros((400, 640), dtype=np.uint8)
    binary[0, 0] = 128
    before = composer.canvas.copy()

    with pytest.raises(ValueError):
        composer.render(gray, binary, uuid.uuid4(), 0, binary_background=False)

    assert np.array_equal(composer.canvas, before)


def test_render_reuses_canvas_workspaces_and_redraws_panel_only_for_changed_status():
    composer = _composer()
    canvas_id = id(composer.canvas)
    gray_work_id = id(composer._gray_work)
    binary_work_id = id(composer._binary_work)
    gray = np.zeros((400, 640), dtype=np.uint8)
    binary = np.zeros_like(gray)
    session = uuid.uuid4()

    composer.render(gray, binary, session, 1, status_lines=("运行", "帧 1"))
    first_redraws = composer.panel_redraws
    composer.render(gray, binary, session, 2, status_lines=("运行", "帧 1"))
    assert composer.panel_redraws == first_redraws
    composer.render(gray, binary, session, 3, status_lines=("暂停", "帧 3"))

    assert composer.panel_redraws == first_redraws + 1
    assert id(composer.canvas) == canvas_id
    assert id(composer._gray_work) == gray_work_id
    assert id(composer._binary_work) == binary_work_id


def test_panel_pixels_and_status_changes_are_visible_in_numpy_canvas():
    composer = _composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    binary = np.zeros_like(gray)
    session = uuid.uuid4()

    canvas = composer.render(gray, binary, session, 10, status_lines=("运行状态", "首个状态"))
    first_status_pixels = canvas[120:220, 640:1120].copy()

    assert canvas[80, 850] == 128
    assert np.any(canvas[16:60, 656:960] > 16)

    composer.render(gray, binary, session, 11, status_lines=("运行状态", "另一个状态"))
    second_status_pixels = canvas[120:220, 640:1120]
    assert not np.array_equal(first_status_pixels, second_status_pixels)


@pytest.mark.parametrize("bad_input", ["shape", "dtype", "binary"])
def test_invalid_frames_are_rejected_without_modifying_canvas(bad_input):
    composer = _composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    binary = np.zeros_like(gray)
    if bad_input == "shape":
        gray = np.zeros((400, 639), dtype=np.uint8)
    elif bad_input == "dtype":
        gray = np.zeros((400, 640), dtype=np.float32)
    else:
        binary[0, 0] = 128
    before = composer.canvas.copy()

    with pytest.raises((TypeError, ValueError)):
        composer.render(gray, binary, uuid.uuid4(), 0)

    assert np.array_equal(composer.canvas, before)


@pytest.mark.parametrize("session,frame", [("bad-session", 0), (uuid.uuid4(), -1), (uuid.uuid4(), 1 << 64)])
def test_invalid_identity_is_rejected_without_modifying_canvas(session, frame):
    composer = _composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    before = composer.canvas.copy()

    with pytest.raises((TypeError, ValueError)):
        composer.render(gray, np.zeros_like(gray), session, frame)

    assert np.array_equal(composer.canvas, before)


def test_track_ids_must_be_ascii():
    composer = _composer()
    gray = np.zeros((400, 640), dtype=np.uint8)

    with pytest.raises(ValueError):
        composer.render(gray, np.zeros_like(gray), uuid.uuid4(), 0, tracks=(("轨迹", 10, 10),))



def test_cjk_font_accepts_separate_latin_font_for_numeric_parameter_labels(monkeypatch):
    api = _api()
    real = api.ImageFont.truetype
    seen = []
    def track(path, size):
        seen.append((str(path), size))
        return real(path, size)
    monkeypatch.setattr(api.ImageFont, "truetype", track)
    c = api.GrayComposer(FONT, latin_font_path=FONT)
    assert hasattr(c, "_small_latin_font")
    gray = np.zeros((400,640), dtype=np.uint8)
    c.render(gray,gray,uuid.uuid4(),1,status_lines=("numeric 0123456789 FPS 20.0",))
    assert c.panel_redraws >= 1
    assert any(size == 24 for _,size in seen)


def test_long_status_wraps_and_uses_readable_24_pixel_font():
    c = _composer()
    assert c._small_font.size == 24
    gray = np.zeros((400,640), dtype=np.uint8)
    a = c.render(gray,gray,uuid.uuid4(),5,status_lines=("ABCDEFGHIJKLMN" * 9,))
    assert np.any(a[164:190, 664:1260] != 16), "long status must wrap to another line"


def test_status_updates_reuse_warmed_glyphs_without_drawing_static_header(monkeypatch):
    c = _composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    session = uuid.uuid4()
    c.render(gray, gray, session, 1, status_lines=("CPU 0123456789 / 运行",))
    draws = []
    real_text = compositor.ImageDraw.ImageDraw.text

    def record_text(self, xy, text, *args, **kwargs):
        draws.append(text)
        return real_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(compositor.ImageDraw.ImageDraw, "text", record_text)
    canvas = c.render(gray, gray, session, 2, status_lines=("CPU 9876543210 / 运行",))
    assert draws == [], "cached status glyphs must not be rasterized again"
    assert _decode_marker(canvas[0, 640:864]) == (session, 2)
    assert _decode_marker(canvas[719, 640:864]) == (session, 2)


@pytest.mark.parametrize("final_status", [("CPU 1 / 运行", "底行 gyp"), (), ("", "新行")])
def test_shorter_wrapped_status_removes_old_text_and_preserves_other_rows(final_status):
    c = _composer()
    expected = _composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    session = uuid.uuid4()
    c.render(gray, gray, session, 1,
             status_lines=("CPU 0123456789 / 运行" * 8, "底行 gyp", "应当擦除"))
    actual = c.render(gray, gray, session, 2, status_lines=final_status).copy()
    clean = expected.render(gray, gray, session, 2, status_lines=final_status)
    assert np.array_equal(actual, clean)


def test_status_glyph_cache_has_a_fixed_memory_bound():
    c = _composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    session = uuid.uuid4()
    for index in range(300):
        c.render(gray, gray, session, index, status_lines=(chr(0x4E00 + index),))
    assert len(c._glyph_cache) <= 256


def test_cached_glyphs_preserve_mixed_font_pixels_and_wrapping():
    c = _composer()
    latin = Path("C:/Windows/Fonts/consola.ttf")
    if not latin.is_file():
        pytest.skip("distinct Latin font is unavailable")
    c = compositor.GrayComposer(FONT, latin_font_path=latin)
    lines = ("运行 CPU 12.5% / gypj_", "帧 0000123456789 " * 8, "\u0301尾行")
    # Independent Pillow rasterization checks the established glyph anchors,
    # antialiasing and font selection without any timing or hardware comparison.
    expected = compositor.Image.fromarray(c.canvas[:, 640:].copy())
    draw = compositor.ImageDraw.Draw(expected)
    y = 126
    for line in lines:
        if y + 32 >= 708:
            break
        x = 24.0
        for ch in line:
            font = c._small_latin_font if ord(ch) < 256 else c._small_font
            advance = font.getlength(ch)
            if x + advance > 616:
                x, y = 24.0, y + 38
            if y + 32 >= 708:
                break
            draw.text((round(x), y), ch, font=font, fill=255)
            x += advance
        y += 38
    gray = np.zeros((400, 640), dtype=np.uint8)
    actual = c.render(gray, gray, uuid.uuid4(), 1, status_lines=lines)
    assert np.array_equal(actual[1:719, 640:], np.asarray(expected)[1:719])


def _dashboard_composer():
    api = _api()
    latin = Path("C:/Windows/Fonts/arial.ttf")
    if not FONT.is_file() or not latin.is_file():
        pytest.skip("local CJK and Latin fonts are required")
    return api.DashboardComposer(FONT, latin_font_path=latin)


def _collect_dashboard_text(monkeypatch, api):
    texts = []
    for name in ("_text", "_whole_latin_text"):
        original = getattr(api.DashboardComposer, name)

        def collect(composer, draw, xy, text, *args, _original=original, **kwargs):
            texts.append(str(text))
            return _original(composer, draw, xy, text, *args, **kwargs)

        monkeypatch.setattr(api.DashboardComposer, name, collect)
    return texts


def test_dashboard_gutter_wrap_measures_character_widths_linearly(monkeypatch):
    c = _dashboard_composer()
    c._gutter_wrap_cache.clear()
    text = "处理性能 CPU0 91.5% / CPU1 92.3% RLE 等待 123.4 ms"
    measurements = []
    fonts = list(c._fonts.values()) + list(c._latin.values())
    for font in fonts:
        original = font.getlength

        def track_length(value, *args, _original=original, **kwargs):
            measurements.append(value)
            return _original(value, *args, **kwargs)

        monkeypatch.setattr(font, "getlength", track_length)

    rows = c._wrap_gutter(text, max_width=150, size=12, max_rows=2)

    assert rows and rows[-1].endswith("…")
    assert len(measurements) <= 2 * len(text) + 5


def test_dashboard_top_cards_use_link_metrics(monkeypatch):
    api = _api()
    c = _dashboard_composer()
    texts = _collect_dashboard_text(monkeypatch, api)
    gray = np.zeros((400, 640), dtype=np.uint8)
    session = uuid.uuid4()

    canvas = c.render(gray, gray, session, 1, dashboard={
        "count": 12, "camera_fps": 29.5, "e2e_ms": {"mean": 41.2},
        "drop_total": 7, "detail_page": 0,
    })

    assert canvas.shape == (720, 1280)
    assert _decode_marker(canvas[0, 640:864]) == (session, 1)
    assert "显示 FPS" not in texts
    assert "处理延迟 / RLE + 计数" not in texts
    assert "相机帧率" in texts
    assert "端到端延迟" in texts


def test_dashboard_unknown_metrics_render_placeholder(monkeypatch):
    api = _api()
    c = _dashboard_composer()
    texts = _collect_dashboard_text(monkeypatch, api)
    gray = np.zeros((400, 640), dtype=np.uint8)

    c.render(gray, gray, uuid.uuid4(), 1, dashboard={
        "count": 1, "camera_fps": None,
        "e2e_ms": {"mean": float("nan")}, "drop_total": "stale",
        "cpu_percent": -1,
    })

    assert texts.count("--") >= 4


def test_dashboard_subprecision_telemetry_hits_key_without_panel_pixel_changes():
    c = _dashboard_composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    session = uuid.uuid4()
    first = c.render(gray, gray, session, 1, dashboard={
        "camera_fps": 29.51, "cpu_percent": 20.01,
    }).copy()
    second = c.render(gray, gray, session, 2, dashboard={
        "camera_fps": 29.54, "cpu_percent": 20.04,
    }).copy()

    assert c.key_stats == {
        "frames": 2, "key_hits": 1, "key_misses": 1, "hit_rate": 0.5,
    }
    assert np.array_equal(first[1:719, :], second[1:719, :])


def test_dashboard_display_precision_boundary_updates_dynamic_value_without_chrome_miss():
    c = _dashboard_composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    session = uuid.uuid4()
    before = c.render(gray, gray, session, 1,
                      dashboard={"camera_fps": 29.54}).copy()
    after = c.render(gray, gray, session, 2,
                     dashboard={"camera_fps": 29.56}).copy()

    assert c.key_stats == {
        "frames": 2, "key_hits": 1, "key_misses": 1, "hit_rate": 0.5,
    }
    assert not np.array_equal(before[84:126, 340:640], after[84:126, 340:640])


def test_dashboard_page_lines_and_low_level_diagnostics_are_scoped_to_ps_page(monkeypatch):
    api = _api()
    c = _dashboard_composer()
    texts = _collect_dashboard_text(monkeypatch, api)
    gray = np.zeros((400, 640), dtype=np.uint8)
    dashboard = {
        "count": 12, "camera_fps": 29.5, "drop_total": 4,
        "processing_fps": 28.2, "display_fps": 27.9,
        "cpu_percent": 35, "memory_percent": 42,
        "pl": {"aggregate": {"active_cycles": 123, "total_cycles": 456}},
        "rle_fresh": True, "rle_activity_pct": 22,
        "page": 0, "page_lines": (),
        "cpu_source_text": "CPU 来源 camera 2%",
        "queue_summary_text": "队列 相机丢1 覆盖True",
        "thread_text": "threads capture=1",
        "button_status": "BTN3 PS/丢帧",
    }

    c.render(gray, gray, uuid.uuid4(), 1, dashboard=dashboard)
    overview = " ".join(texts)
    assert "本次累计计数" in overview and "相机帧率" in overview
    assert "camera 2%" not in overview
    assert "相机丢1 覆盖True" not in overview
    assert "threads capture" not in overview

    texts.clear()
    c.render(gray, gray, uuid.uuid4(), 2,
             dashboard={**dashboard, "page": 2,
                        "page_lines": ("PS 与丢帧", "RLE 5.5 / TRK 0.3 ms")})
    detail = " ".join(texts)
    assert "PS 与丢帧" in detail
    assert "CPU 来源" in detail and "队列" in detail
    assert "camera 2%" in detail and "相机丢1 覆盖True" in detail
    assert "capture=1" in detail


def test_dashboard_key_ignores_page_lines_and_diagnostics_hidden_on_overview():
    c = _dashboard_composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    session = uuid.uuid4()
    base = {"page": 0, "count": 12, "camera_fps": 29.5}
    c.render(gray, gray, session, 1, dashboard={
        **base, "page_lines": ("hidden page text A",),
        "cpu_source_text": "CPU 来源 camera 2%",
        "queue_summary_text": "队列 相机丢1", "thread_text": "threads capture=1",
    })
    c.render(gray, gray, session, 2, dashboard={
        **base, "page_lines": ("hidden page text B",),
        "cpu_source_text": "CPU 来源 camera 3%",
        "queue_summary_text": "队列 相机丢2", "thread_text": "threads capture=2",
    })

    assert c.key_stats == {
        "frames": 2, "key_hits": 1, "key_misses": 1, "hit_rate": 0.5,
    }


def test_dashboard_key_stats_report_first_redraw_and_hits():
    c = _dashboard_composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    session = uuid.uuid4()
    c.render(gray, gray, session, 1, dashboard={"camera_fps": 29.5})
    assert c.key_stats == {
        "frames": 1, "key_hits": 0, "key_misses": 1, "hit_rate": 0.0,
    }
    c.render(gray, gray, session, 2, dashboard={"camera_fps": 29.5})
    assert c.key_stats == {
        "frames": 2, "key_hits": 1, "key_misses": 1, "hit_rate": 0.5,
    }


def test_v12_dashboard_help_keeps_btn3_page_behavior(monkeypatch):
    api = _api()
    c = _dashboard_composer()
    texts = _collect_dashboard_text(monkeypatch, api)
    gray = np.zeros((400, 640), dtype=np.uint8)

    c.render(gray, gray, uuid.uuid4(), 1, dashboard={"page": 0})

    rendered_text = "\n".join(texts)
    assert "BTN3 循环切换信息页" in rendered_text
    assert "BTN3 预留" not in rendered_text
    assert "SW0" not in rendered_text


def test_dashboard_gutter_wrap_honors_size_and_row_limit():
    c = _dashboard_composer()

    rows = c._wrap_gutter("CPU 来源 camera process", 72, 12, 1)

    assert len(rows) == 1
    assert rows[0].endswith("…")


def test_dashboard_detection_count_changes_update_display_without_chrome_miss(monkeypatch):
    api = _api()
    c = _dashboard_composer()
    texts = _collect_dashboard_text(monkeypatch, api)
    gray = np.zeros((400, 640), dtype=np.uint8)
    session = uuid.uuid4()
    dashboard = {"count": 12, "camera_fps": 29.5, "page": 0}

    c.render(gray, gray, session, 1, boxes=(), dashboard=dashboard)
    c.render(gray, gray, session, 2, boxes=((10, 10, 30, 30),),
             dashboard=dashboard)

    assert c.key_stats == {
        "frames": 2, "key_hits": 1, "key_misses": 1, "hit_rate": 0.5,
    }
    assert "0" in texts and "1" in texts


def test_dashboard_telemetry_updates_change_pixels_without_chrome_miss(monkeypatch):
    api = _api()
    c = _dashboard_composer()
    texts = _collect_dashboard_text(monkeypatch, api)
    gray = np.zeros((400, 640), dtype=np.uint8)
    session = uuid.uuid4()
    first_dashboard = {
        "count": 12, "camera_fps": 29.5, "e2e_ms": {"mean": 1200.04},
        "drop_total": 1, "cpu_percent": 90.02, "processing_fps": 21.5,
        "display_fps": 0.5, "effective_input_fps": 24.3,
        "preview_submitted_fps": 25.1, "processing_ms": 4.3,
        "regions_ms": {"mean": 4.0}, "track_ms": {"mean": 0.3},
        "rle_fresh": True, "rle_activity_pct": 3.0, "input_mb_s": 1.1,
        "output_mb_s": 0.4, "input_stall_pct": 2.3, "output_stall_pct": 0.0,
    }
    second_dashboard = {
        **first_dashboard, "camera_fps": 30.5, "e2e_ms": {"mean": 1300.04},
        "drop_total": 2, "cpu_percent": 91.2, "processing_fps": 22.0,
        "display_fps": 0.6, "effective_input_fps": 25.0,
        "preview_submitted_fps": 24.4, "processing_ms": 4.6,
        "regions_ms": {"mean": 4.2}, "track_ms": {"mean": 0.4},
        "rle_activity_pct": 3.5, "input_mb_s": 1.2,
    }

    first = c.render(gray, gray, session, 1, dashboard=first_dashboard).copy()
    second = c.render(gray, gray, session, 2, dashboard=second_dashboard).copy()

    assert c.key_stats == {
        "frames": 2, "key_hits": 1, "key_misses": 1, "hit_rate": 0.5,
    }
    assert not np.array_equal(first[58:126, 340:640], second[58:126, 340:640])
    assert "29.5 FPS" in texts and "30.5 FPS" in texts
    assert "1,200.0 ms" in texts and "1,300.0 ms" in texts
def test_dashboard_output_slots_match_full_render_when_slots_and_chrome_are_reused(monkeypatch):
    api = _api()
    slot_composer = _dashboard_composer()
    reference_composer = _dashboard_composer()
    outputs = [np.empty((720, 1280), dtype=np.uint8) for _ in range(3)]
    slot_copies = []
    original_copyto = api.np.copyto

    def track_slot_copy(destination, source, *args, **kwargs):
        for slot, output in enumerate(outputs):
            if destination is output:
                slot_copies.append(slot)
                break
        return original_copyto(destination, source, *args, **kwargs)

    monkeypatch.setattr(api.np, "copyto", track_slot_copy)
    session = uuid.uuid4()
    y, x = np.indices((400, 640))
    cases = (
        (0, 20.0, False, 0),
        (1, 20.0, False, 1),
        (2, 20.0, True, 2),
        (0, 21.1, False, 3),
        (1, 21.1, True, 0),
        (2, 21.1, False, 4),
        (0, 21.1, True, 2),
        (1, 22.2, False, 1),
        (2, 22.2, True, 0),
        (0, 22.2, False, 1),
    )

    for frame_id, (slot, cpu_percent, pl_preview, target_count) in enumerate(cases, 1):
        gray = np.full((400, 640), frame_id * 9, dtype=np.uint8)
        binary = (((x + y + frame_id) % 11) < 4).astype(np.uint8) * 255
        boxes = tuple((20 + i, 30, 100 + i, 120) for i in range(target_count))
        dashboard = {
            "count": 7,
            "camera_fps": 29.5,
            "cpu_percent": cpu_percent,
            "page": 0,
        }
        annotations = {
            "boxes": boxes,
            "tracks": ((str(frame_id), 180 + frame_id, 210),),
            "line_x": 160 + frame_id,
            "line_y": 90 + frame_id if frame_id % 2 else None,
            "dashboard": dashboard,
            "pl_preview": pl_preview,
        }
        actual = slot_composer.render(
            gray, binary, session, frame_id, output=outputs[slot],
            output_slot=slot, **annotations)
        expected = reference_composer.render(
            gray, binary, session, frame_id, **annotations)

        assert actual is outputs[slot]
        assert np.array_equal(actual, expected), f"frame {frame_id}, slot {slot}"

    assert slot_composer._output_chrome_versions == [
        slot_composer._chrome_version,
        slot_composer._chrome_version,
        slot_composer._chrome_version,
    ]
    assert slot_copies == [0, 1, 2]


def test_dashboard_draws_dynamic_regions_in_output_buffer_without_region_copies(monkeypatch):
    api = _api()
    composer = _dashboard_composer()
    reference = _dashboard_composer()
    gray = np.full((400, 640), 87, dtype=np.uint8)
    binary = np.zeros_like(gray)
    session = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
    memory = shared_memory.SharedMemory(create=True, size=720 * 1280)
    actual = np.ndarray((720, 1280), dtype=np.uint8, buffer=memory.buf)
    expected = np.empty_like(actual)
    strided_materializations = []
    real_fromarray = api.Image.fromarray

    def track_fromarray(array, *args, **kwargs):
        if isinstance(array, np.ndarray) and array.ndim == 2 and not array.flags.c_contiguous:
            strided_materializations.append(array.shape)
        return real_fromarray(array, *args, **kwargs)

    try:
        monkeypatch.setattr(api.Image, "fromarray", track_fromarray)
        composer.render(
            gray, binary, session, 1, boxes=((12, 18, 120, 150), (210, 30, 300, 200)),
            tracks=(("7", 240, 90),), dashboard={
                "count": 42, "camera_fps": 29.7, "e2e_ms": {"mean": 1234.5},
                "drop_total": 5, "cpu_percent": 81.2, "cpu0_percent": 77.1,
                "cpu1_percent": 84.4, "memory_percent": 49.3,
                "regions_ms": {"mean": 2.8}, "track_ms": {"mean": 0.6},
                "processing_fps": 27.4, "display_fps": 28.8,
                "effective_input_fps": 30.0, "preview_submitted_fps": 29.1,
                "processing_ms": 36.5,
            }, pl_preview=True, output=actual, output_slot=0)
        actual_image = actual.copy()
        assert strided_materializations == []

        def legacy_region(target, bounds, draw_content, draw=None):
            x0, y0, x1, y1 = bounds
            pixels = target[y0:y1, x0:x1]
            np.copyto(pixels, reference._chrome[y0:y1, x0:x1])
            image = real_fromarray(pixels)
            draw_content(api.ImageDraw.Draw(image), x0, y0)
            np.copyto(pixels, np.asarray(image))

        def legacy_detection_count(target, target_count, draw=None):
            x0, y0, x1, y1 = 974, 174, 1230, 201
            pixels = target[y0:y1, x0:x1]
            pixels.fill(43)
            image = real_fromarray(pixels)
            reference._text(api.ImageDraw.Draw(image), (0, 177 - y0),
                            str(target_count), 20, 255)
            np.copyto(pixels, np.asarray(image))

        monkeypatch.setattr(reference, "_draw_dynamic_region", legacy_region)
        monkeypatch.setattr(reference, "_draw_detection_count", legacy_detection_count)
        monkeypatch.setattr(api.Image, "fromarray", real_fromarray)
        reference.render(
            gray, binary, session, 1, boxes=((12, 18, 120, 150), (210, 30, 300, 200)),
            tracks=(("7", 240, 90),), dashboard={
                "count": 42, "camera_fps": 29.7, "e2e_ms": {"mean": 1234.5},
                "drop_total": 5, "cpu_percent": 81.2, "cpu0_percent": 77.1,
                "cpu1_percent": 84.4, "memory_percent": 49.3,
                "regions_ms": {"mean": 2.8}, "track_ms": {"mean": 0.6},
                "processing_fps": 27.4, "display_fps": 28.8,
                "effective_input_fps": 30.0, "preview_submitted_fps": 29.1,
                "processing_ms": 36.5,
            }, pl_preview=True, output=expected)

        assert np.array_equal(actual_image, expected)
    finally:
        del actual
        memory.close()
        memory.unlink()


def test_dashboard_output_slot_requires_a_valid_explicit_output():
    c = _dashboard_composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    output = np.empty((720, 1280), dtype=np.uint8)

    with pytest.raises(ValueError, match="output_slot"):
        c.render(gray, gray, uuid.uuid4(), 1, output=output, output_slot=3)
    with pytest.raises(ValueError, match="output_slot"):
        c.render(gray, gray, uuid.uuid4(), 1, output_slot=0)


@pytest.mark.parametrize(("page", "page_lines", "expected"), [
    (1, ("第一页详情",), "第一页详情"),
    (2, ("第二页详情",), "第二页详情"),
    (3, ("第三页详情",), "第三页详情"),
])
def test_dashboard_three_pages_show_distinct_lines(monkeypatch, page, page_lines, expected):
    api = _api()
    c = _dashboard_composer()
    texts = _collect_dashboard_text(monkeypatch, api)
    gray = np.zeros((400, 640), dtype=np.uint8)

    c.render(gray, gray, uuid.uuid4(), page,
             dashboard={"detail_page": page, "page_lines": page_lines})

    assert any(expected in text for text in texts)


def test_dashboard_left_summary_shows_pl_ps_throughput(monkeypatch):
    api = _api()
    c = _dashboard_composer()
    texts = _collect_dashboard_text(monkeypatch, api)
    gray = np.zeros((400, 640), dtype=np.uint8)

    c.render(gray, gray, uuid.uuid4(), 1, dashboard={
        "pl": {"aggregate": {"active_cycles": 123, "total_cycles": 456}},
        "regions_ms": {"mean": 2.3}, "track_ms": {"mean": 1.4},
        "processing_fps": 28.2, "display_fps": 27.9,
    })

    joined = " ".join(texts)
    assert "PL" in joined
    assert "PS" in joined
    assert "处理" in joined


def test_dashboard_text_cache_avoids_static_label_rasterization_on_value_updates(monkeypatch):
    api = _api()
    c = _dashboard_composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    session = uuid.uuid4()
    c.render(gray, gray, session, 1, boxes=((10, 10, 20, 20),),
             dashboard={"count": 3, "camera_fps": 29.5, "e2e_ms": {"mean": 4.2},
                        "drop_total": 0, "processing_fps": 28.0,
                        "display_fps": 30},
             status_lines=())

    calls = []
    real_text = api.ImageDraw.ImageDraw.text
    def track_text(draw, xy, text, *args, **kwargs):
        calls.append(text)
        return real_text(draw, xy, text, *args, **kwargs)
    monkeypatch.setattr(api.ImageDraw.ImageDraw, "text", track_text)
    metric_calls = []
    fonts = list(c._fonts.values()) + list(c._latin.values())
    originals = [(font, font.getlength) for font in fonts]
    for font, original in originals:
        def track_length(text, *args, _original=original, **kwargs):
            metric_calls.append(text)
            return _original(text, *args, **kwargs)
        monkeypatch.setattr(font, "getlength", track_length)

    c.render(gray, gray, session, 2, boxes=((10, 10, 20, 20),),
             dashboard={"count": 4, "camera_fps": 29.5, "e2e_ms": {"mean": 4.2},
                        "drop_total": 0, "processing_fps": 28.0,
                        "display_fps": 30},
             status_lines=())
    c.render(gray, gray, session, 3, boxes=((10, 10, 20, 20), (30, 30, 40, 40)),
             dashboard={"count": 4, "camera_fps": 29.5, "e2e_ms": {"mean": 4.2},
                        "drop_total": 0, "processing_fps": 28.0,
                        "display_fps": 30},
             status_lines=())

    assert not set("本次累计计数").intersection(calls)
    assert metric_calls == []


def test_dashboard_labels_preview_rate_as_submission_rate(monkeypatch):
    c = _dashboard_composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    labels = []
    original = c._text
    def capture_text(draw, xy, text, size, fill):
        labels.append(text)
        return original(draw, xy, text, size, fill)
    monkeypatch.setattr(c, "_text", capture_text)

    c.render(gray, gray, uuid.uuid4(), 1,
             dashboard={"preview_submitted_fps": 23.5},
             binary_background=True)

    assert any("二值提交 FPS" in label for label in labels)


def test_dashboard_cpu_source_and_queue_summaries_refresh_on_change(monkeypatch):
    c = _dashboard_composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    drawn = []
    original = c._text

    def capture_text(draw, xy, text, size, fill):
        if text.startswith(("CPU 来源", "队列", "camera", "相机丢")):
            drawn.append((xy, text, size))
        return original(draw, xy, text, size, fill)

    monkeypatch.setattr(c, "_text", capture_text)
    base = {
        "page": 2,
        "cpu_source_text": "CPU 来源 camera 2%",
        "queue_summary_text": "队列 相机丢1 覆盖True",
    }
    c.render(gray, gray, uuid.uuid4(), 1, dashboard=base)
    assert any(text == "camera 2%" for _, text, _ in drawn)
    assert any(text == "相机丢1 覆盖True" for _, text, _ in drawn)
    first_count = len(drawn)

    c.render(gray, gray, uuid.uuid4(), 2,
             dashboard={**base, "cpu_source_text": "CPU 来源 camera 3%"})
    assert any(text == "camera 3%" for _, text, _ in drawn)
    assert len(drawn) > first_count


def test_dashboard_chrome_warmed_latin_strings_skip_all_text_rasterization(monkeypatch):
    api = _api()
    c = _dashboard_composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    session = uuid.uuid4()
    warm = {"count": 3, "display_fps": 30, "processing_ms": 4.2,
            "cpu_percent": 20, "memory_percent": 41}
    changed = {"count": 4, "display_fps": 31, "processing_ms": 4.2,
               "cpu_percent": 21, "memory_percent": 41}
    c.render(gray, gray, session, 1, dashboard=warm)
    c.render(gray, gray, session, 2, dashboard=changed, boxes=((10, 10, 20, 20),))

    calls = []
    real_text = api.ImageDraw.ImageDraw.text
    def track_text(draw, xy, text, *args, **kwargs):
        calls.append(text)
        return real_text(draw, xy, text, *args, **kwargs)
    monkeypatch.setattr(api.ImageDraw.ImageDraw, "text", track_text)
    metric_calls = []
    fonts = list(c._fonts.values()) + list(c._latin.values())
    for font in fonts:
        for name in ("getlength", "getbbox"):
            original = getattr(font, name)
            def track_metric(text, *args, _original=original, **kwargs):
                metric_calls.append(text)
                return _original(text, *args, **kwargs)
            monkeypatch.setattr(font, name, track_metric)

    c.render(gray, gray, session, 3, dashboard=warm)
    c.render(gray, gray, session, 4, dashboard=changed, boxes=((10, 10, 20, 20),))

    assert calls == []
    assert metric_calls == []


@pytest.mark.parametrize("size", [12, 18, 20, 24, 34])
@pytest.mark.parametrize("background,fill", [(12, 242), (238, 35)])
def test_dashboard_cached_whole_latin_text_matches_default_pillow_pixels(
        size, background, fill):
    api = _api()
    c = _dashboard_composer()
    actual = api.Image.new("L", (420, 100), background)
    expected = api.Image.new("L", (420, 100), background)
    text = "NUT VISION 0123456789 x 400"
    draw_actual = api.ImageDraw.Draw(actual)
    draw_expected = api.ImageDraw.Draw(expected)
    c._whole_latin_text(draw_actual, (17, 19), text, size, fill)
    draw_expected.text((17, 19), text, font=c._latin[size], fill=fill)
    first = np.asarray(actual).copy()
    assert np.array_equal(first, np.asarray(expected))

    # Same cache entry, new fill and destination: the independent default
    # Pillow call retains its normal kerning and anchor behavior.
    c._whole_latin_text(draw_actual, (103, 53), text, size, 255 - fill)
    draw_expected.text((103, 53), text, font=c._latin[size], fill=255 - fill)

    assert np.array_equal(np.asarray(actual), np.asarray(expected))


@pytest.mark.parametrize("size", [18, 20, 24, 34])
@pytest.mark.parametrize("background,fills", [(12, (45, 225)), (238, (35, 210))])
def test_dashboard_cached_mixed_text_matches_independent_legacy_pillow_pixels(
        size, background, fills):
    api = _api()
    c = _dashboard_composer()
    actual = api.Image.new("L", (800, 150), background)
    expected = api.Image.new("L", (800, 150), background)
    text = "混合 CPU 12.5% / gypj_ e\u0301，测试。"
    # First draw populates the cache; the second hits the same text/size key
    # while changing fill to check mask compositing on both dark and light gray.
    for y0, fill in ((12, fills[0]), (78, fills[1])):
        c._text(api.ImageDraw.Draw(actual), (27, y0), text, size, fill)

        # Independent copy of the original per-character Pillow drawing path.
        draw = api.ImageDraw.Draw(expected)
        x, y = 27, y0
        for character in text:
            font = c._latin[size] if ord(character) < 256 else c._fonts[size]
            draw.text((x, y + size), character, font=font, fill=fill, anchor="ls")
            x += font.getlength(character)

    assert np.array_equal(np.asarray(actual), np.asarray(expected))


def test_dashboard_ascii_text_reuses_glyph_rasterization_without_pixel_changes(monkeypatch):
    api = _api()
    c = _dashboard_composer()
    font = c._latin[12]
    text = "0" * 128
    next_text = text + "0"

    def legacy_image(value):
        image = api.Image.new("L", (2400, 80), 12)
        draw = api.ImageDraw.Draw(image)
        x = 10.0
        for character in value:
            draw.text((x, 24), character, font=font, fill=238, anchor="ls")
            x += font.getlength(character)
        return image

    expected = legacy_image(text)
    expected_next = legacy_image(next_text)
    actual = api.Image.new("L", (2400, 80), 12)
    calls = []
    real_text = api.ImageDraw.ImageDraw.text

    def track_text(draw, xy, value, *args, **kwargs):
        calls.append(value)
        return real_text(draw, xy, value, *args, **kwargs)

    monkeypatch.setattr(api.ImageDraw.ImageDraw, "text", track_text)
    c._text(api.ImageDraw.Draw(actual), (10, 12), text, 12, 238)

    assert np.array_equal(np.asarray(actual), np.asarray(expected))
    assert len(calls) < len(text)

    c._text_cache.clear()
    calls.clear()
    actual_next = api.Image.new("L", (2400, 80), 12)
    c._text(api.ImageDraw.Draw(actual_next), (10, 12), next_text, 12, 238)

    assert calls == []
    assert np.array_equal(np.asarray(actual_next), np.asarray(expected_next))


def test_dashboard_text_cache_has_a_fixed_memory_bound():
    c = _dashboard_composer()
    image = _api().Image.new("L", (400, 40), 0)
    draw = _api().ImageDraw.Draw(image)
    for index in range(300):
        c._text(draw, (0, 0), f"标签{index}", 18, 170)
    assert len(c._text_cache) <= 128

def test_dashboard_resource_rows_change_pixels_and_preserve_markers():
    c = _dashboard_composer()
    gray = np.full((400, 640), 96, dtype=np.uint8)
    binary = np.zeros_like(gray)
    session = uuid.uuid4()
    first = dict(cpu_percent=20, cpu0_percent=12, cpu1_percent=28,
                 memory_percent=42, static_lut_percent=23,
                 static_ff_percent=15.2, static_bram_percent=3.2,
                 static_dsp_percent=4.1)
    second = dict(first, cpu_percent=65, static_bram_percent=8.4)

    image = c.render(gray, binary, session, 71, dashboard=first).copy()
    live_before = image[480:553, 24:1256].copy()
    static_before = image[558:631, 24:1256].copy()
    changed = c.render(gray, binary, session, 72, dashboard=second)

    assert not np.array_equal(live_before, changed[480:553, 24:1256])
    assert not np.array_equal(static_before, changed[558:631, 24:1256])
    assert ("latin", "LIVE", 18) in c._text_cache
    assert ("latin", "STATIC", 18) in c._text_cache
    assert ("mixed", "PS CPU", 18) in c._text_cache
    assert ("mixed", "LUT · ST", 18) in c._text_cache
    assert _decode_marker(changed[0, 640:864]) == (session, 72)
    assert _decode_marker(changed[719, 640:864]) == (session, 72)
    assert np.all(changed[717:719, :] == 14)


def test_dashboard_unavailable_percentages_show_dashes_and_empty_rings():
    c = _dashboard_composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    image = c.render(gray, gray, uuid.uuid4(), 1, dashboard={
        "cpu_percent": None, "cpu0_percent": float("nan"),
        "cpu1_percent": -1, "memory_percent": "stale",
        "static_lut_percent": None, "static_ff_percent": None,
        "static_bram_percent": None, "static_dsp_percent": None,
    })

    assert ("latin", "--", 18) in c._text_cache
    # The unavailable row starts at x=24 and leaves a gray, hollow ring at
    # the first metric's fixed center rather than drawing a zero-value wedge.
    assert image[599, 140] == 40
    assert image[599, 140 - 18] == 76


def test_dashboard_viewport_is_binary_460_by_288_dashboard_scale():
    c = _dashboard_composer()
    gray = np.full((400, 640), 103, dtype=np.uint8)
    binary = np.zeros_like(gray)
    image = c.render(gray, binary, uuid.uuid4(), 1)

    assert c.VIEW == (398, 154, 460, 288)
    assert image.shape == (720, 1280)
    assert image[200, 420] == 0
    assert image[300, 800] == 0
    x, y, width, height = c.VIEW
    assert image[y + height - 1, x + width - 1] == 0


def test_dashboard_mode_exposure_and_pl_status_change_without_hiding_rate_panels():
    c = _dashboard_composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    session = uuid.uuid4()
    common = dict(count=8, display_fps=15, processing_ms=30,
                  cpu_percent=24, cpu0_percent=20, cpu1_percent=28,
                  memory_percent=40, static_lut_percent=12,
                  static_ff_percent=14, static_bram_percent=3,
                  static_dsp_percent=4)
    normal = c.render(gray, gray, session, 1, dashboard=dict(
        common, mode="NORMAL", exposure_text="曝光 5..1000 / 5 ms / 1 ms",
        pl_text="PL 12% / IN 1.2 MB/s" )).copy()
    diagnostic = c.render(gray, gray, session, 2, dashboard=dict(
        common, mode="DIAGNOSTIC", exposure_text="曝光 5..1000 / 6 ms / 1 ms",
        pl_text="PL 14% / IN 1.4 MB/s" )).copy()

    assert not np.array_equal(normal[320:410, 34:370], diagnostic[320:410, 34:370])
    assert np.array_equal(normal[480:709, 24:1256], diagnostic[480:709, 24:1256])


def test_dashboard_binary_view_uses_four_fifths_nearest_golden():
    c = _dashboard_composer()
    gray = np.zeros((400, 640), dtype=np.uint8)
    binary = np.indices((400, 640)).sum(axis=0).astype(np.uint8) % 2 * 255
    image = c.render(gray, binary, uuid.uuid4(), 1, binary_background=True,
                     dashboard={"count": 1})
    x, y, width, height = c.VIEW
    expected = binary[np.arange(height) * 400 // height][:, np.arange(width) * 640 // width]
    assert c.VIEW == (398, 154, 460, 288)
    visible = np.ones((height, width), dtype=bool)
    line_x = math.floor(320 * width / 640 + 0.5)
    visible[:, max(0, line_x - 9):min(width, line_x + 10)] = False
    visible[:40, min(width, line_x + 12):] = False
    assert np.array_equal(image[y:y + height, x:x + width][visible], expected[visible])


def test_dashboard_pl_preview_skips_pixels_and_duplicate_count_line():
    c = _dashboard_composer()
    black = np.zeros((400, 640), dtype=np.uint8)
    image = c.render(black, black, uuid.uuid4(), 1, boxes=((20, 20, 100, 100),),
                     binary_background=True, pl_preview=True, dashboard={"count": 2})
    x, y, width, height = c.VIEW
    assert np.all(image[y:y + height, x:x + width] == 0)


def test_dashboard_rle_row_shows_activity_throughput_and_stalls():
    c = _dashboard_composer()
    black = np.zeros((400, 640), dtype=np.uint8)
    image = c.render(black, black, uuid.uuid4(), 1, dashboard={
        "rle_activity_pct": 22, "input_mb_s": 12.3, "output_mb_s": 0.7,
        "input_stall_pct": 1.5, "output_stall_pct": 2.5,
        "rle_fresh": True})
    assert np.any(image[636:708, 24:1256] != 14)
