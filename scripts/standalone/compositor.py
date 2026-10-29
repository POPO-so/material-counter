"""Fixed-geometry Gray8 display composition for the standalone G1 path."""

from __future__ import annotations

import binascii
import math
from time import perf_counter_ns
import uuid
from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


WIDTH = 1280
HEIGHT = 720
SOURCE_WIDTH = 640
SOURCE_HEIGHT = 400
VIEW_WIDTH = 576
VIEW_HEIGHT = 360
VIEW_X = 32
TOP_Y = 0
BOTTOM_Y = 360
MARKER_X = 640
MARKER_WIDTH = 224
UINT64_MAX = (1 << 64) - 1


def encode_marker(session_id: uuid.UUID, frame_id: int) -> bytes:
    """Encode the 224-bit MSB-first frame marker, including CCITT-FALSE CRC."""
    if not isinstance(session_id, uuid.UUID):
        raise TypeError("session_id must be uuid.UUID")
    if isinstance(frame_id, bool) or not isinstance(frame_id, (int, np.integer)):
        raise TypeError("frame_id must be an unsigned 64-bit integer")
    frame_value = int(frame_id)
    if not 0 <= frame_value <= UINT64_MAX:
        raise ValueError("frame_id is outside uint64 range")
    payload = b"MC" + session_id.bytes + frame_value.to_bytes(8, "big")
    return payload + binascii.crc_hqx(payload, 0xFFFF).to_bytes(2, "big")


def _integer(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError(f"{name} must be an integer")
    return int(value)


def _scale_coordinate(value: int) -> int:
    return math.floor(0.9 * value + 0.5)


class GrayComposer:
    """Reuse one Gray8 canvas and two scaled work areas for each display frame."""

    def __init__(self, font_path: str | Path, *, latin_font_path=None):
        self.canvas = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
        self._gray_work = np.empty((VIEW_HEIGHT, VIEW_WIDTH), dtype=np.uint8)
        self._binary_work = np.empty((VIEW_HEIGHT, VIEW_WIDTH), dtype=np.uint8)
        self._font = ImageFont.truetype(str(font_path), 32)
        self._small_font = ImageFont.truetype(str(font_path), 24)
        self._small_latin_font = ImageFont.truetype(str(latin_font_path or font_path), 24)
        self._panel_image = Image.new("L", (WIDTH - 640, HEIGHT), 16)
        self._panel_draw = ImageDraw.Draw(self._panel_image)
        self._status_key = None
        self._glyph_cache = OrderedDict()
        self._status_glyphs = []
        self.panel_redraws = 0
        self.last_timings_ms = {"scale_ms": 0.0, "draw_ms": 0.0}
        self._panel_draw.text((24, 22), "运行状态", font=self._font, fill=255)
        for index, value in enumerate((0, 128, 255)):
            x = 24 + index * 180
            self._panel_draw.rectangle((x, 70, x + 95, 98), fill=value)
            self._panel_draw.text((x + 104, 73), str(value), font=self._small_latin_font, fill=255)
        self._panel_base = self._panel_image.copy()
        np.copyto(self.canvas[:, 640:], np.asarray(self._panel_base))
        self._redraw_panel(())

    def _glyph(self, ch):
        """Keep font measurement and rasterization off recurring status updates."""
        cached = self._glyph_cache.get(ch)
        if cached is not None:
            self._glyph_cache.move_to_end(ch)
            return cached
        font = self._small_latin_font if ord(ch) < 256 else self._small_font
        left, top, right, bottom = font.getbbox(ch)
        mask = Image.new("L", (right - left, bottom - top))
        if mask.width and mask.height:
            ImageDraw.Draw(mask).text((-left, -top), ch, font=font, fill=255)
        cached = (font.getlength(ch), left, top, mask)
        self._glyph_cache[ch] = cached
        if len(self._glyph_cache) > 256:
            self._glyph_cache.popitem(last=False)
        return cached

    def _layout_status(self, status_lines):
        glyphs = []
        y = 126
        for line in status_lines:
            if y + 32 >= HEIGHT - 12:
                break
            x = 24.0
            for ch in line:
                advance, left, top, mask = self._glyph(ch)
                if x + advance > 616:
                    x, y = 24.0, y + 38
                if y + 32 >= HEIGHT - 12:
                    break
                if mask.width and mask.height:
                    glyphs.append((round(x) + left, y + top, ch, mask))
                x += advance
            y += 38
        return glyphs

    def _redraw_panel(self, status_lines: tuple[str, ...]) -> None:
        glyphs = self._layout_status(status_lines)
        previous = self._status_glyphs
        shared = 0
        # The unchanged prefix needs no repaint. Include both removed and added
        # suffixes so shortened or differently wrapped text leaves no old pixels.
        while (shared < min(len(previous), len(glyphs))
               and previous[shared][:3] == glyphs[shared][:3]):
            shared += 1
        changed = previous[shared:] + glyphs[shared:]
        if changed:
            x0 = max(0, min(x for x, y, ch, mask in changed))
            y0 = max(0, min(y for x, y, ch, mask in changed))
            x1 = min(WIDTH - 640, max(x + mask.width for x, y, ch, mask in changed))
            y1 = min(HEIGHT, max(y + mask.height for x, y, ch, mask in changed))
            if x0 < x1 and y0 < y1:
                dirty = (x0, y0, x1, y1)
                self._panel_image.paste(self._panel_base.crop(dirty), (x0, y0))
                # Redraw any retained glyph overlapping the dirty area, too.
                # Clip masks to avoid blending unchanged antialiased edges twice.
                for x, y, ch, mask in glyphs:
                    left, top = max(x, x0), max(y, y0)
                    right, bottom = min(x + mask.width, x1), min(y + mask.height, y1)
                    if left < right and top < bottom:
                        clipped = mask.crop((left - x, top - y, right - x, bottom - y))
                        self._panel_image.paste(255, (left, top, right, bottom), clipped)
                np.copyto(self.canvas[y0:y1, 640+x0:640+x1],
                          np.asarray(self._panel_image.crop(dirty)))
        self._status_glyphs = glyphs
        self._status_key = status_lines
        self.panel_redraws += 1

    @staticmethod
    def _validate_image(image, name: str) -> None:
        if not isinstance(image, np.ndarray):
            raise TypeError(f"{name} must be a numpy.ndarray")
        if image.shape != (SOURCE_HEIGHT, SOURCE_WIDTH):
            raise ValueError(f"{name} must have shape (400, 640)")
        if image.dtype != np.uint8:
            raise TypeError(f"{name} must have dtype uint8")

    def _validate_annotations(self, boxes, tracks, line_x, status_lines):
        try:
            box_items = tuple(boxes)
            track_items = tuple(tracks)
            status_items = tuple(status_lines)
        except TypeError as exc:
            raise TypeError("boxes, tracks, and status_lines must be iterable") from exc
        normalized_boxes = []
        for box in box_items:
            if len(box) != 4:
                raise ValueError("each box must be (x1, y1, x2, y2)")
            x1, y1, x2, y2 = (_integer(value, "box coordinate") for value in box)
            if x2 < x1 or y2 < y1:
                raise ValueError("box end coordinates must not precede start coordinates")
            normalized_boxes.append((x1, y1, x2, y2))

        normalized_tracks = []
        for track in track_items:
            if len(track) != 3:
                raise ValueError("each track must be (track_id, x, y)")
            track_id, x, y = track
            text = str(track_id)
            if not text.isascii():
                raise ValueError("track IDs must be ASCII")
            normalized_tracks.append((text, _integer(x, "track x"), _integer(y, "track y")))

        if any(not isinstance(line, str) for line in status_items):
            raise TypeError("status_lines must contain strings")
        normalized_status = tuple(status_items)
        return normalized_boxes, normalized_tracks, _integer(line_x, "line_x"), normalized_status

    def _draw_overlays(self, boxes, tracks, line_x: int) -> None:
        image = self.canvas[BOTTOM_Y:HEIGHT, VIEW_X:VIEW_X + VIEW_WIDTH]
        x = _scale_coordinate(min(max(line_x, 0), SOURCE_WIDTH - 1))
        cv2.line(image, (x, 0), (x, VIEW_HEIGHT - 1), 0, 3, cv2.LINE_8)
        cv2.line(image, (x, 0), (x, VIEW_HEIGHT - 1), 255, 1, cv2.LINE_8)

        for x1, y1, x2, y2 in boxes:
            x1 = _scale_coordinate(min(max(x1, 0), SOURCE_WIDTH - 1))
            x2 = _scale_coordinate(min(max(x2, 0), SOURCE_WIDTH - 1))
            y1 = _scale_coordinate(min(max(y1, 0), SOURCE_HEIGHT - 1))
            y2 = _scale_coordinate(min(max(y2, 0), SOURCE_HEIGHT - 1))
            first = (x1, y1)
            last = (x2, y2)
            cv2.rectangle(image, first, last, 0, 4, cv2.LINE_8)
            cv2.rectangle(image, first, last, 255, 2, cv2.LINE_8)

        for track_id, source_x, source_y in tracks:
            x = _scale_coordinate(min(max(source_x, 0), SOURCE_WIDTH - 1))
            y = _scale_coordinate(min(max(source_y, 0), SOURCE_HEIGHT - 1))
            origin = (x, max(13, y - 4))
            cv2.putText(image, track_id, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 0, 3, cv2.LINE_8)
            cv2.putText(image, track_id, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 255, 1, cv2.LINE_8)

    def _write_markers(self, marker: bytes) -> None:
        bits = np.unpackbits(np.frombuffer(marker, dtype=np.uint8), bitorder="big")
        pixels = bits * np.uint8(255)
        self.canvas[TOP_Y, MARKER_X:MARKER_X + MARKER_WIDTH] = pixels
        self.canvas[HEIGHT - 1, MARKER_X:MARKER_X + MARKER_WIDTH] = pixels

    def render(
        self,
        gray,
        binary,
        session_id: uuid.UUID,
        frame_id: int,
        boxes=(),
        tracks=(),
        line_x=320,
        status_lines=(),
        *,
        binary_background: bool = True,
    ) -> np.ndarray:
        """Render a same-identity gray/binary pair into the reusable canvas."""
        if not isinstance(binary_background, bool):
            raise TypeError("binary_background must be bool")
        self._validate_image(gray, "gray")
        self._validate_image(binary, "binary")
        if not np.all((binary == 0) | (binary == 255)):
            raise ValueError("binary input may contain only 0 and 255")
        marker = encode_marker(session_id, frame_id)
        normalized_boxes, normalized_tracks, normalized_line, normalized_status = self._validate_annotations(
            boxes, tracks, line_x, status_lines
        )

        self.canvas[:VIEW_HEIGHT, :SOURCE_WIDTH].fill(0)
        self.canvas[BOTTOM_Y:HEIGHT, :SOURCE_WIDTH].fill(0)
        scale_start_ns = perf_counter_ns()
        cv2.resize(gray, (VIEW_WIDTH, VIEW_HEIGHT), dst=self._gray_work, interpolation=cv2.INTER_AREA)
        self.canvas[TOP_Y:TOP_Y + VIEW_HEIGHT, VIEW_X:VIEW_X + VIEW_WIDTH] = self._gray_work
        if binary_background:
            cv2.resize(binary, (VIEW_WIDTH, VIEW_HEIGHT), dst=self._binary_work, interpolation=cv2.INTER_NEAREST)
            self.canvas[BOTTOM_Y:HEIGHT, VIEW_X:VIEW_X + VIEW_WIDTH] = self._binary_work
        else:
            self.canvas[BOTTOM_Y:HEIGHT, VIEW_X:VIEW_X + VIEW_WIDTH] = self._gray_work
        scale_ms = (perf_counter_ns() - scale_start_ns) / 1_000_000

        draw_start_ns = perf_counter_ns()
        self._draw_overlays(normalized_boxes, normalized_tracks, normalized_line)

        if normalized_status != self._status_key:
            self._redraw_panel(normalized_status)
        self._write_markers(marker)
        draw_ms = (perf_counter_ns() - draw_start_ns) / 1_000_000
        self.last_timings_ms = {"scale_ms": scale_ms, "draw_ms": draw_ms}
        return self.canvas
