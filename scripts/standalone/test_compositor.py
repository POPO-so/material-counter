import binascii
import importlib.util
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
    binary[100, 100] = 255

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
