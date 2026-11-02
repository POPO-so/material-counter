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

from .shared_frames import SLOT_COUNT


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

    def __init__(self, font_path: str | Path, *, latin_font_path=None,
                 gray_interpolation=cv2.INTER_AREA):
        if (isinstance(gray_interpolation, bool) or not isinstance(gray_interpolation, int)
                or gray_interpolation not in (cv2.INTER_AREA, cv2.INTER_LINEAR)):
            raise ValueError("gray_interpolation must be INTER_AREA or INTER_LINEAR")
        self.canvas = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
        self.gray_interpolation = gray_interpolation
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
        self._canvas_panel_stale = False
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

    def _draw_overlays(self, boxes, tracks, line_x: int, line_y=None) -> None:
        image = self.canvas[BOTTOM_Y:HEIGHT, VIEW_X:VIEW_X + VIEW_WIDTH]
        if line_y is None:
            x = _scale_coordinate(min(max(line_x, 0), SOURCE_WIDTH - 1))
            first, last = (x, 0), (x, VIEW_HEIGHT - 1)
        else:
            y = _scale_coordinate(min(max(line_y, 0), SOURCE_HEIGHT - 1))
            first, last = (0, y), (VIEW_WIDTH - 1, y)
        cv2.line(image, first, last, 0, 3, cv2.LINE_8)
        cv2.line(image, first, last, 255, 1, cv2.LINE_8)

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
        output=None,
        line_y=None,
    ) -> np.ndarray:
        """Render a same-identity gray/binary pair into the reusable canvas or output."""
        if not isinstance(binary_background, bool):
            raise TypeError("binary_background must be bool")
        if output is not None and (
            not isinstance(output, np.ndarray)
            or output.shape != (HEIGHT, WIDTH)
            or output.dtype != np.uint8
            or not output.flags.writeable
            or not output.flags.c_contiguous
        ):
            raise TypeError("output must be a writable C-contiguous (720,1280) uint8 array")
        self._validate_image(gray, "gray")
        self._validate_image(binary, "binary")
        if not np.all((binary == 0) | (binary == 255)):
            raise ValueError("binary input may contain only 0 and 255")
        marker = encode_marker(session_id, frame_id)
        normalized_boxes, normalized_tracks, normalized_line, normalized_status = self._validate_annotations(
            boxes, tracks, line_x, status_lines
        )

        if line_y is not None:
            line_y = _integer(line_y, "line_y")
        reusable_canvas = self.canvas
        if output is not None:
            self.canvas = output
        try:
            self.canvas[:VIEW_HEIGHT, :SOURCE_WIDTH].fill(0)
            self.canvas[BOTTOM_Y:HEIGHT, :SOURCE_WIDTH].fill(0)
            scale_start_ns = perf_counter_ns()
            cv2.resize(gray, (VIEW_WIDTH, VIEW_HEIGHT), dst=self._gray_work,
                       interpolation=self.gray_interpolation)
            self.canvas[TOP_Y:TOP_Y + VIEW_HEIGHT, VIEW_X:VIEW_X + VIEW_WIDTH] = self._gray_work
            if binary_background:
                cv2.resize(binary, (VIEW_WIDTH, VIEW_HEIGHT), dst=self._binary_work, interpolation=cv2.INTER_NEAREST)
                self.canvas[BOTTOM_Y: BOTTOM_Y + VIEW_HEIGHT,
                            VIEW_X: VIEW_X + VIEW_WIDTH] = self._binary_work
            else:
                self.canvas[BOTTOM_Y: BOTTOM_Y + VIEW_HEIGHT,
                            VIEW_X: VIEW_X + VIEW_WIDTH] = self._gray_work
            scale_ms = (perf_counter_ns() - scale_start_ns) / 1_000_000

            draw_start_ns = perf_counter_ns()
            self._draw_overlays(normalized_boxes, normalized_tracks, normalized_line, line_y)

            if normalized_status != self._status_key:
                self._redraw_panel(normalized_status)
            if output is not None:
                np.copyto(self.canvas[:, SOURCE_WIDTH:], np.asarray(self._panel_image))
            elif self._canvas_panel_stale:
                np.copyto(self.canvas[:, SOURCE_WIDTH:], np.asarray(self._panel_image))
                self._canvas_panel_stale = False
            self._write_markers(marker)
            draw_ms = (perf_counter_ns() - draw_start_ns) / 1_000_000
            self.last_timings_ms = {"scale_ms": scale_ms, "draw_ms": draw_ms}
            return self.canvas
        finally:
            if output is not None:
                if output is not reusable_canvas:
                    self._canvas_panel_stale = True
                self.canvas = reusable_canvas


class DashboardComposer:
    """Gray8 operator dashboard with an undistorted 640x400 camera viewport.

    The diagnostic composer above remains available to the G1/G2 tools. Both
    layouts retain the same top/bottom session markers for the PL observer.
    """

    VIEW = (398, 154, 460, 288)

    def __init__(self, font_path, *, latin_font_path=None,
                 gray_interpolation=cv2.INTER_AREA):
        if gray_interpolation not in (cv2.INTER_AREA, cv2.INTER_LINEAR):
            raise ValueError("gray_interpolation must be INTER_AREA or INTER_LINEAR")
        self.gray_interpolation = gray_interpolation
        self.canvas = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
        self._work = np.empty((288, 460), dtype=np.uint8)
        self._fonts = {size: ImageFont.truetype(str(font_path), size)
                       for size in (12, 14, 16, 18, 20, 24, 34)}
        self._latin = {size: ImageFont.truetype(str(latin_font_path or font_path), size)
                       for size in (12, 14, 16, 18, 20, 24, 28, 34)}
        self._text_cache = OrderedDict()
        self._latin_advance_cache = {
            (size, chr(codepoint)): self._latin[size].getlength(chr(codepoint))
            for size in (12, 20) for codepoint in range(32, 127)
        }
        self._latin_glyph_cache = OrderedDict()
        self._gutter_wrap_cache = OrderedDict()
        self._key = None
        self._chrome = np.zeros_like(self.canvas)
        self._chrome_version = 0
        self._output_chrome_versions = [None] * SLOT_COUNT
        self._output_dynamic_keys = [None] * SLOT_COUNT
        self._output_detection_counts = [None] * SLOT_COUNT
        self.last_timings_ms = {"scale_ms": 0.0, "draw_ms": 0.0}
        self._frames = 0
        self._key_hits = 0
        self._key_misses = 0

    @property
    def key_stats(self):
        frames = self._frames
        return {
            "frames": frames,
            "key_hits": self._key_hits,
            "key_misses": self._key_misses,
            "hit_rate": self._key_hits / frames if frames else 0.0,
        }

    @staticmethod
    def _number(value, digits=1, suffix=""):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0):
            return "--"
        return f"{value:,.{digits}f}{suffix}"

    def _latin_advance(self, character, size):
        key = (size, character)
        advance = self._latin_advance_cache.get(key)
        if advance is None:
            advance = self._latin[size].getlength(character)
            self._latin_advance_cache[key] = advance
        return advance

    def _latin_glyph(self, character, size, phase):
        key = (size, character, phase)
        cached = self._latin_glyph_cache.get(key)
        if cached is not None:
            self._latin_glyph_cache.move_to_end(key)
            return cached

        margin = 64
        font = self._latin[size]
        advance = self._latin_advance(character, size)
        glyph = Image.new(
            "L", (max(1, margin * 2 + math.ceil(advance) + 16), size * 4 + margin * 2), 0)
        ImageDraw.Draw(glyph).text(
            (margin + phase / 64, margin + size), character,
            font=font, fill=255, anchor="ls")
        bounds = glyph.getbbox()
        if bounds is None:
            cached = (0, 0, glyph.crop((0, 0, 1, 1)))
        else:
            left, top, right, bottom = bounds
            cached = (left - margin, top - margin - size,
                      glyph.crop((left, top, right, bottom)))

        self._latin_glyph_cache[key] = cached
        if len(self._latin_glyph_cache) > 512:
            self._latin_glyph_cache.popitem(last=False)
        return cached

    def _latin_text_mask(self, text, size, margin):
        advances = [self._latin_advance(character, size) for character in text]
        width = margin * 2 + sum(advances)
        pixels = np.zeros((size * 4 + margin * 2,
                           max(1, math.ceil(width))), dtype=np.uint8)
        x = float(margin)
        for character, advance in zip(text, advances):
            base_x = math.floor(x)
            phase = int(round((x - base_x) * 64))
            if phase == 64:
                base_x += 1
                phase = 0
            offset_x, offset_y, glyph = self._latin_glyph(character, size, phase)
            glyph_pixels = np.asarray(glyph)
            glyph_x = base_x + offset_x
            glyph_y = margin + size + offset_y
            left, top = max(0, glyph_x), max(0, glyph_y)
            right = min(pixels.shape[1], glyph_x + glyph_pixels.shape[1])
            bottom = min(pixels.shape[0], glyph_y + glyph_pixels.shape[0])
            if left < right and top < bottom:
                source = glyph_pixels[top - glyph_y:bottom - glyph_y,
                                      left - glyph_x:right - glyph_x]
                destination = pixels[top:bottom, left:right]
                np.copyto(destination, source, where=source != 0)
            x += advance

        mask = Image.fromarray(pixels)
        bounds = mask.getbbox()
        if bounds is None:
            return 0, 0, mask.crop((0, 0, 1, 1))
        left, top, right, bottom = bounds
        return left - margin, top - margin, mask.crop(bounds)

    def _text(self, draw, xy, text, size, fill):
        key = ("mixed", text, size)
        cached = self._text_cache.get(key)
        if cached is None:
            # Render at an integer offset so glyph bearings and fractional
            # advances match the original per-character drawing path exactly.
            margin = 64
            if all(ord(character) < 256 for character in text):
                cached = self._latin_text_mask(text, size, margin)
            else:
                width = margin * 2 + sum(
                    (self._latin[size] if ord(character) < 256 else self._fonts[size])
                    .getlength(character) for character in text)
                mask = Image.new("L", (max(1, math.ceil(width)), size * 4 + margin * 2), 0)
                mask_draw = ImageDraw.Draw(mask)
                x = float(margin)
                for character in text:
                    font = self._latin[size] if ord(character) < 256 else self._fonts[size]
                    mask_draw.text((x, margin + size), character, font=font,
                                   fill=255, anchor="ls")
                    x += font.getlength(character)
                bounds = mask.getbbox()
                if bounds is None:
                    cached = (0, 0, mask.crop((0, 0, 1, 1)))
                else:
                    left, top, right, bottom = bounds
                    cached = (left - margin, top - margin,
                              mask.crop((left, top, right, bottom)))
        else:
            self._text_cache.move_to_end(key)
        self._cache_text(key, cached)
        offset_x, offset_y, mask = cached
        draw.bitmap((xy[0] + offset_x, xy[1] + offset_y), mask, fill=fill)

    def _cache_text(self, key, cached):
        self._text_cache[key] = cached
        if len(self._text_cache) > 128:
            self._text_cache.popitem(last=False)

    def _whole_latin_text(self, draw, xy, text, size, fill):
        """Draw cached complete Latin strings with Pillow's default text layout."""
        key = ("latin", text, size)
        cached = self._text_cache.get(key)
        if cached is None:
            font = self._latin[size]
            left, top, right, bottom = font.getbbox(text)
            mask = Image.new("L", (max(1, right - left), max(1, bottom - top)), 0)
            ImageDraw.Draw(mask).text((-left, -top), text, font=font, fill=255)
            cached = (left, top, mask)
        else:
            self._text_cache.move_to_end(key)
        self._cache_text(key, cached)
        offset_x, offset_y, mask = cached
        draw.bitmap((xy[0] + offset_x, xy[1] + offset_y), mask, fill=fill)

    def _resource_pie(self, draw, x, center_y, label, value, *, static=False):
        """Draw a compact gray8 donut; invalid readings remain an empty ring."""
        center_x = x + 24
        radius = 22
        bounds = (center_x - radius, center_y - radius,
                  center_x + radius, center_y + radius)
        draw.ellipse(bounds, fill=76)
        valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
                 and math.isfinite(value) and value >= 0)
        if valid and value > 0:
            sweep = 360.0 * min(100.0, value) / 100.0
            draw.pieslice(bounds, start=-90, end=-90 + sweep, fill=218)
        inner = radius - 8
        draw.ellipse((center_x - inner, center_y - inner,
                      center_x + inner, center_y + inner), fill=40)
        suffix = " · ST" if static else ""
        self._text(draw, (x + 55, center_y - 18), label + suffix, 18, 202)
        value_text = self._number(value, 1, "%")
        self._whole_latin_text(draw, (x + 55, center_y + 2), value_text, 18,
                               226 if valid else 142)

    def _wrap_gutter(self, text, max_width=286, size=18, max_rows=None):
        key = (str(text), max_width, size, max_rows)
        rows = self._gutter_wrap_cache.get(key)
        if rows is not None:
            self._gutter_wrap_cache.move_to_end(key)
            return rows
        rows, row_prefix_widths = [], []
        current, current_width = '', 0.0
        current_prefix_widths = []
        for character in key[0]:
            font = self._latin[size] if ord(character) < 256 else self._fonts[size]
            character_width = (self._latin_advance(character, size)
                               if ord(character) < 256 else font.getlength(character))
            if current and current_width + character_width > max_width:
                rows.append(current)
                row_prefix_widths.append(current_prefix_widths)
                current, current_width = character, character_width
                current_prefix_widths = [current_width]
            else:
                current += character
                current_width += character_width
                current_prefix_widths.append(current_width)
        if current:
            rows.append(current)
            row_prefix_widths.append(current_prefix_widths)
        if max_rows is not None and len(rows) > max_rows:
            rows = rows[:max_rows]
            last = rows[-1]
            prefix_widths = row_prefix_widths[max_rows - 1]
            ellipsis_width = self._fonts[size].getlength("…")
            while last and prefix_widths[-1] + ellipsis_width > max_width:
                last = last[:-1]
                prefix_widths.pop()
            rows[-1] = last + "…"
        rows = tuple(rows)
        self._gutter_wrap_cache[key] = rows
        if len(self._gutter_wrap_cache) > 128:
            self._gutter_wrap_cache.popitem(last=False)
        return rows

    def _draw_wrapped(self, draw, text, x, y, width, size, fill, max_rows, line_height):
        rows = self._wrap_gutter(text, width, size, max_rows)
        for row in rows:
            self._text(draw, (x, y), row, size, fill)
            y += line_height
        return y

    def _redraw_chrome(self, dashboard, binary_background, lines):
        def strip_prefix(text, prefix):
            value = str(text).strip()
            return value[len(prefix):].strip(" :：") if value.startswith(prefix) else value

        e2e = dashboard.get("e2e_ms")
        if isinstance(e2e, dict):
            e2e = e2e.get("mean")
        values = (
            self._number(dashboard.get("count"), 0),
            self._number(dashboard.get("camera_fps"), 1, " FPS"),
            self._number(e2e, 1, " ms"),
            self._number(dashboard.get("drop_total"), 0),
        )
        raw_live_pies = tuple(dashboard.get(name) for name in (
            "cpu_percent", "cpu0_percent", "cpu1_percent", "memory_percent"))
        raw_static_pies = tuple(dashboard.get(name) for name in (
            "static_lut_percent", "static_ff_percent",
            "static_bram_percent", "static_dsp_percent"))
        def display_percent(value):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                return None
            return float(f"{value:.1f}")
        live_pies = tuple(display_percent(value) for value in raw_live_pies)
        static_pies = tuple(display_percent(value) for value in raw_static_pies)
        state = str(dashboard.get("state", "RUNNING"))
        chain = dashboard.get("resolution_chain")
        if isinstance(chain, (tuple, list)) and chain:
            input_resolution = str(chain[0])
        elif isinstance(chain, str) and chain:
            input_resolution = chain.split("->", 1)[0].strip()
        else:
            input_resolution = "640x400"
        pl = dashboard.get("pl")
        aggregate = pl.get("aggregate") if isinstance(pl, dict) else None
        if isinstance(aggregate, dict):
            active = aggregate.get("active_cycles")
            total = aggregate.get("total_cycles")
            active_text = self._number(active, 0)
            total_text = self._number(total, 0)
            pl_text = (f"PL {active_text}/{total_text} cyc"
                       if active_text != "--" and total_text != "--" else "PL --")
        else:
            pl_text = "PL --"

        feature_pl_text = str(dashboard.get("pl_text", "PL rates unavailable"))
        if "unavailable" in feature_pl_text.lower():
            pl_summary = pl_text
        else:
            pl_summary = feature_pl_text

        regions = dashboard.get("regions_ms")
        regions_mean = regions.get("mean") if isinstance(regions, dict) else None
        track = dashboard.get("track_ms")
        track_mean = track.get("mean") if isinstance(track, dict) else None
        mode = str(dashboard.get("mode", "NORMAL"))
        exposure_text = str(dashboard.get("exposure_text", "曝光不可用"))
        exposure_range_text = str(dashboard.get("exposure_range_text", "范围 --"))
        thread_text = str(dashboard.get("thread_text", "线程概要暂不可用"))
        cpu_source_text = str(dashboard.get("cpu_source_text", "CPU 来源 unknown"))
        queue_summary_text = str(dashboard.get("queue_summary_text", "队列 unknown"))
        button_status = str(dashboard.get("button_status", "IRQ READY"))
        preview_status = str(dashboard.get("preview_status", "预览 READY"))
        exposure_latency_text = str(dashboard.get("exposure_latency_text", ""))
        page = dashboard.get("page")
        if page is None:
            page = dashboard.get("detail_page", 0)
            if page is True:
                page = 1
        if isinstance(page, bool) or not isinstance(page, int):
            page = 0
        raw_page_lines = dashboard.get("page_lines", ())
        if isinstance(raw_page_lines, str):
            page_lines = (raw_page_lines,) if raw_page_lines else ()
        elif isinstance(raw_page_lines, (tuple, list)):
            page_lines = tuple(str(line) for line in raw_page_lines if str(line))
        else:
            page_lines = ()
        count_line = dashboard.get("count_line")
        if not isinstance(count_line, dict):
            count_line = {"axis": "x", "position": 320, "direction": 1}
        axis = count_line.get("axis")
        direction = count_line.get("direction")
        position = count_line.get("position")
        if axis not in ("x", "y") or direction not in (-1, 1):
            axis, direction, position = "x", 1, 320
        if isinstance(position, bool) or not isinstance(position, int):
            position = 320
        count_line_key = (axis, position, direction)
        submitted_fps = self._number(
            dashboard.get("preview_submitted_fps", dashboard.get("binary_fps")), 1, " FPS")
        processing_ms = self._number(dashboard.get("processing_ms"), 1, " ms")
        effective_input_fps = self._number(dashboard.get("effective_input_fps"), 1, " FPS")
        fresh = bool(dashboard.get("rle_fresh"))
        raw_rle_values = (dashboard.get("rle_activity_pct"),
                          dashboard.get("input_mb_s"), dashboard.get("output_mb_s"),
                          dashboard.get("input_stall_pct"), dashboard.get("output_stall_pct"))
        rle_values = tuple(self._number(value, 1) for value in raw_rle_values)
        # Count, run state, and processing FPS already have dedicated slots.
        detail = tuple(line for line in lines
                       if not line.startswith("本次会话累计 ")
                       and line not in ("运行中", "暂停计数")
                       and not (line.startswith("处理 ") and " FPS" in line))[:3]
        visible_details = page_lines if page and page_lines else detail
        visible_details = tuple(self._wrap_gutter(line, 334, 12, 1)[0]
                                for line in visible_details)
        visible_cpu_source = (self._wrap_gutter(
            strip_prefix(cpu_source_text, "CPU 来源"), 262, 12, 1)[0]
            if page == 2 else "")
        visible_queue = (self._wrap_gutter(
            strip_prefix(queue_summary_text, "队列"), 262, 12, 1)[0]
            if page == 2 else "")
        visible_thread = (self._wrap_gutter(
            strip_prefix(thread_text, "threads"), 262, 12, 1)[0]
            if page == 2 else "")
        pl_summary_display = self._wrap_gutter(pl_summary, 206, 12, 1)[0]
        performance_values = (
            pl_summary_display,
            f"{self._number(regions_mean)} / {self._number(track_mean)} ms",
            f"{self._number(dashboard.get('processing_fps'))} / "
            f"{self._number(dashboard.get('display_fps'))} FPS",
            f"{effective_input_fps} / {submitted_fps}",
            processing_ms,
        )
        control_status = " · ".join(
            text for text in (button_status, exposure_latency_text) if text)
        self._dynamic_state = {
            "top_values": values,
            "live_pies": live_pies,
            "performance_values": performance_values,
            "control_status": control_status,
            "visible_cpu_source": visible_cpu_source,
            "visible_queue": visible_queue,
            "visible_thread": visible_thread,
            "visible_details": visible_details,
            "page": page,
            "rle_fresh": fresh,
            "rle_values": rle_values,
        }
        self._dynamic_key = (
            values, live_pies, performance_values, control_status,
            visible_cpu_source, visible_queue, visible_thread, visible_details,
            fresh, rle_values if fresh else ("--",) * len(rle_values),
        )
        key = (state, binary_background, input_resolution, mode,
               exposure_text, exposure_range_text, page, preview_status,
               static_pies, count_line_key)
        self._frames += 1
        if key == self._key:
            self._key_hits += 1
            return
        self._key_misses += 1
        image = Image.new("L", (WIDTH, HEIGHT), 14)
        draw = ImageDraw.Draw(image)
        self._whole_latin_text(draw, (24, 12), "NUT VISION COUNTER", 28, 242)
        draw.ellipse((1060, 25, 1070, 35), fill=224)
        self._whole_latin_text(draw, (1082, 19), state[:15], 18, 208)
        for x, label, value in zip((24, 340, 656, 972),
                                   ("本次累计计数", "相机帧率", "端到端延迟", "实时丢帧"),
                                   values[:4]):
            draw.rounded_rectangle((x, 58, x + 300, 126), radius=10, fill=42)
            self._text(draw, (x + 16, 62), label, 18, 170)
        draw.rounded_rectangle((24, 136, 1256, 474), radius=10, fill=25, outline=66)
        axis_label = "X" if axis == "x" else "Y"
        direction_label = (("左→右" if direction == 1 else "右→左") if axis == "x"
                           else ("上→下" if direction == 1 else "下→上"))
        def card(x, y, width, height, title):
            draw.rounded_rectangle((x, y, x + width, y + height), radius=8,
                                   fill=43, outline=94, width=1)
            draw.rounded_rectangle((x + 1, y + 1, x + width - 1, y + 27),
                                   radius=7, fill=76)
            draw.rectangle((x + 1, y + 20, x + width - 1, y + 27), fill=76)
            draw.rectangle((x + 8, y + 7, x + 11, y + 20), fill=218)
            self._text(draw, (x + 18, y + 5), title, 16, 248)

        def aligned_row(label, value, x, y, width, *, label_width=82,
                        size=14, value_size=None, value_fill=244):
            self._text(draw, (x, y), label, size, 174)
            value_x = x + label_width
            value_width = width - label_width
            rows = self._wrap_gutter(value, value_width, value_size or size, 1)
            self._text(draw, (value_x, y), rows[0], value_size or size, value_fill)

        def card_line(text, x, y, width, *, size=12, fill=218):
            rows = self._wrap_gutter(text, width, size, 1)
            self._text(draw, (x, y), rows[0], size, fill)

        def centered(text, x, y, width, size, fill, max_rows=1, line_height=None):
            rows = self._wrap_gutter(text, width, size, max_rows)
            line_height = line_height or size + 1
            for index, line in enumerate(rows):
                line_width = sum(
                    (self._latin[size] if ord(ch) < 256 else self._fonts[size])
                    .getlength(ch) for ch in line)
                self._text(draw, (x + (width - line_width) / 2,
                                  y + index * line_height), line, size, fill)
            return y + len(rows) * line_height

        # Left gutter: camera/count rules, exposure controls, then performance.
        left_x, left_width = 38, 342
        right_x, right_width = 874, 368
        card(left_x, 145, left_width, 92, "CAM 01 · 画面与计数规则")
        aligned_row("输入 / 预览", f"{input_resolution} / 640x400",
                    left_x + 12, 176, left_width - 24, label_width=92, size=14)
        aligned_row("输出 / 画面", "1280x720 / {}".format(
            "二值" if binary_background else "灰度"),
                    left_x + 12, 195, left_width - 24, label_width=92, size=14)
        aligned_row("计数线", f"{axis_label}={position} · {direction_label} · {mode}",
                    left_x + 12, 214, left_width - 24, label_width=92, size=14)

        card(left_x, 244, left_width, 65, "曝光控制")
        aligned_row("当前", strip_prefix(exposure_text, "曝光"), left_x + 12, 275,
                    left_width - 24, label_width=64, size=14)
        aligned_row("范围 / 步进", strip_prefix(exposure_range_text, "范围"),
                    left_x + 12, 292, left_width - 24, label_width=82, size=14)

        card(left_x, 316, left_width, 116, "处理性能")
        performance_labels = (
            "PL / RLE", "PS RLE / TRK", "处理 / 显示",
            "有效 / 二值提交 FPS", "处理延迟",
        )
        for index, label in enumerate(performance_labels):
            self._text(draw, (left_x + 12, 346 + index * 16), label, 12, 174)

        # Right gutter: detection, preview path, and runtime state.
        card(right_x, 145, right_width, 76, "检测结果")
        self._text(draw, (right_x + 12, 177), "检测目标", 14, 174)
        card_line("跟踪 ID / 检测框 · 计数真值待人工核对",
                  right_x + 12, 201, right_width - 24, size=12, fill=218)

        card(right_x, 229, right_width, 54, "预览链路")
        card_line(preview_status, right_x + 12, 259,
                  right_width - 24, size=14, fill=246)

        card(right_x, 291, right_width, 141, f"运行状态 · 第{page}页")
        control_status = " · ".join(
            text for text in (button_status, exposure_latency_text) if text)
        if page == 2:
            runtime_rows = (
                ("按键 / SW0", control_status),
                ("CPU 来源", strip_prefix(cpu_source_text, "CPU 来源")),
                ("队列", strip_prefix(queue_summary_text, "队列")),
                ("线程", strip_prefix(thread_text, "threads")),
            )
        else:
            runtime_rows = (("按键 / SW0", control_status), ("", ""), ("", ""), ("", ""))
        for index, (label, value) in enumerate(runtime_rows):
            if not label and not value:
                continue
            aligned_row(label, value, right_x + 12, 321 + index * 16,
                        right_width - 24, label_width=82, size=12,
                        value_size=12, value_fill=246 if index == 0 else 226)
        for index, line in enumerate(visible_details[:3]):
            y = 386 + index * 15
            draw.ellipse((right_x + 12, y + 5, right_x + 16, y + 9), fill=182)
        draw.line((left_x, 440, left_x + left_width, 440), fill=100, width=1)
        draw.line((right_x, 440, right_x + right_width, 440), fill=100, width=1)
        centered("BTN0 清零累计计数 · BTN1 切换诊断模式 · BTN2 调整曝光 · BTN3 预留",
                 42, 442, 1190, 16, 250)
        centered("SW0 ON + BTN2：增加曝光 · SW0 OFF + BTN2：减少曝光",
                 42, 459, 1190, 16, 246)
        for top, bottom, center_y, title, labels, percentages, is_static in (
                (480, 552, 516, "LIVE", ("PS CPU", "CPU0", "CPU1", "内存"),
                 (None,) * len(live_pies), False),
                (558, 630, 594, "STATIC", ("LUT", "FF", "BRAM", "DSP"),
                 static_pies, True)):
            draw.rounded_rectangle((24, top, 1256, bottom), radius=9, fill=40)
            self._whole_latin_text(draw, (36, center_y - 12), title, 18, 232)
            for index, (label, percent) in enumerate(zip(labels, percentages)):
                self._resource_pie(draw, 116 + index * 280, center_y, label,
                                   percent, static=is_static)
        top,bottom,center_y=636,708,672
        draw.rounded_rectangle((24,top,1256,bottom),radius=9,fill=40)
        self._whole_latin_text(draw,(36,center_y-12),"RLE",18,232)
        rle_labels=("活动(含等待)","IN MB/s","OUT MB/s","IN 阻塞","OUT 阻塞")
        for index,(label,value_text) in enumerate(zip(rle_labels,rle_values)):
            x=184+index*210
            self._text(draw,(x,center_y-23),label,18,170)
        self._chrome[:] = np.asarray(image)
        self._key = key
        self._chrome_version += 1

    @staticmethod
    def _target_draw(target):
        """Create a writable Pillow draw surface over the full frame buffer."""
        image = Image.frombuffer("L", (WIDTH, HEIGHT), target, "raw", "L", 0, 1)
        if image.readonly:
            image.readonly = 0
        return ImageDraw.Draw(image)

    def _draw_dynamic_region(self, target, bounds, draw_content, draw=None):
        x0, y0, x1, y1 = bounds
        pixels = target[y0:y1, x0:x1]
        np.copyto(pixels, self._chrome[y0:y1, x0:x1])
        draw = self._target_draw(target) if draw is None else draw
        # The shared draw surface is full-frame, so dynamic content uses absolute coordinates.
        draw_content(draw, 0, 0)

    def _draw_dynamic_overlays(self, target, draw=None):
        state = self._dynamic_state
        draw = self._target_draw(target) if draw is None else draw

        def top_values(draw, x0, y0):
            for x, value in zip((24, 340, 656, 972), state["top_values"]):
                self._whole_latin_text(draw, (x + 16 - x0, 84 - y0), value, 34, 248)

        self._draw_dynamic_region(target, (24, 58, 1256, 126), top_values, draw)

        def performance_values(draw, x0, y0):
            for index, value in enumerate(state["performance_values"]):
                row = self._wrap_gutter(value, 206, 12, 1)[0]
                self._text(draw, (162 - x0, 346 + index * 16 - y0), row, 12, 238)

        self._draw_dynamic_region(target, (38, 316, 380, 432), performance_values, draw)

        def live_resource_values(draw, x0, y0):
            labels = ("PS CPU", "CPU0", "CPU1", "内存")
            for index, (label, value) in enumerate(zip(labels, state["live_pies"])):
                self._resource_pie(draw, 116 + index * 280 - x0,
                                   516 - y0, label, value)

        self._draw_dynamic_region(target, (24, 480, 1256, 552), live_resource_values, draw)

        def runtime_values(draw, x0, y0):
            values = [(state["control_status"], 321, 246)]
            if state["page"] == 2:
                values.extend((
                    (state["visible_cpu_source"], 337, 226),
                    (state["visible_queue"], 353, 226),
                    (state["visible_thread"], 369, 226),
                ))
            for value, y, fill in values:
                row = self._wrap_gutter(value, 262, 12, 1)[0]
                self._text(draw, (968 - x0, y - y0), row, 12, fill)
            for index, line in enumerate(state["visible_details"][:3]):
                self._text(draw, (908 - x0, 386 + index * 15 - y0), line, 12, 206)

        self._draw_dynamic_region(target, (874, 291, 1242, 432), runtime_values, draw)

        def rle_values(draw, x0, y0):
            labels = ("活动(含等待)", "IN MB/s", "OUT MB/s", "IN 阻塞", "OUT 阻塞")
            for index, value_text in enumerate(state["rle_values"]):
                shown = (value_text + ("%" if index in (0, 3, 4) and value_text != "--" else "")
                         if state["rle_fresh"] else "--")
                x = 184 + index * 210
                self._whole_latin_text(draw, (x - x0, 672 - y0), shown, 24,
                                       226 if state["rle_fresh"] else 142)

        self._draw_dynamic_region(target, (24, 636, 1256, 708), rle_values, draw)

    def _draw_detection_count(self, target, target_count, draw=None):
        x0, y0, x1, y1 = 974, 174, 1230, 201
        pixels = target[y0:y1, x0:x1]
        pixels.fill(43)
        draw = self._target_draw(target) if draw is None else draw
        self._text(draw, (x0, 177), str(target_count), 20, 255)

    def render(self, gray, binary, session_id, frame_id, boxes=(), tracks=(),
               line_x=320, status_lines=(), *, binary_background=False, output=None,
               line_y=None, dashboard=None, pl_preview=False, output_slot=None):
        GrayComposer._validate_image(gray, "gray")
        GrayComposer._validate_image(binary, "binary")
        if not isinstance(binary_background, bool):
            raise TypeError("binary_background must be bool")
        if output_slot is not None:
            if (isinstance(output_slot, bool) or not isinstance(output_slot, int)
                    or not 0 <= output_slot < SLOT_COUNT):
                raise ValueError("output_slot must be an integer from 0 to {}".format(
                    SLOT_COUNT - 1))
            if output is None:
                raise ValueError("output_slot requires an explicit output buffer")
        if not np.all((binary == 0) | (binary == 255)):
            raise ValueError("binary input may contain only 0 and 255")
        target = self.canvas if output is None else output
        if (not isinstance(target, np.ndarray) or target.shape != (HEIGHT, WIDTH)
                or target.dtype != np.uint8 or not target.flags.c_contiguous
                or not target.flags.writeable):
            raise TypeError("output must be a writable C-contiguous (720,1280) uint8 array")
        boxes, tracks, line_x, lines = GrayComposer._validate_annotations(
            self, boxes, tracks, line_x, status_lines)
        if line_y is not None:
            line_y = _integer(line_y, "line_y")
        marker = encode_marker(session_id, frame_id)
        started = perf_counter_ns()
        self._redraw_chrome(dict(dashboard or {}), binary_background, lines)
        chrome_sync = (output_slot is None
                       or self._output_chrome_versions[output_slot] != self._chrome_version)
        if chrome_sync:
            np.copyto(target, self._chrome)
            if output_slot is not None:
                self._output_chrome_versions[output_slot] = self._chrome_version
                self._output_dynamic_keys[output_slot] = None
                self._output_detection_counts[output_slot] = None
        dynamic_sync = (output_slot is None
                        or self._output_dynamic_keys[output_slot] != self._dynamic_key)
        target_count = len(boxes)
        detection_sync = (output_slot is None
                          or self._output_detection_counts[output_slot] != target_count)
        draw = self._target_draw(target) if dynamic_sync or detection_sync else None
        if dynamic_sync:
            self._draw_dynamic_overlays(target, draw)
            if output_slot is not None:
                self._output_dynamic_keys[output_slot] = self._dynamic_key
        if detection_sync:
            self._draw_detection_count(target, target_count, draw)
            if output_slot is not None:
                self._output_detection_counts[output_slot] = target_count
        draw_ms = (perf_counter_ns() - started) / 1_000_000
        started = perf_counter_ns()
        x0, y0, width, height = self.VIEW
        image = target[y0:y0 + height, x0:x0 + width]
        if pl_preview:
            image.fill(0)
        else:
            cv2.resize(binary, (width, height), dst=self._work,
                       interpolation=cv2.INTER_NEAREST)
            np.copyto(image, self._work)
        scale_ms = (perf_counter_ns() - started) / 1_000_000
        started = perf_counter_ns()
        if not pl_preview:
            sx = lambda x: min(width - 1, max(0, math.floor(x * width / SOURCE_WIDTH + 0.5)))
            sy = lambda y: min(height - 1, max(0, math.floor(y * height / SOURCE_HEIGHT + 0.5)))
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
        target[0, MARKER_X:MARKER_X + MARKER_WIDTH] = bits
        target[HEIGHT - 1, MARKER_X:MARKER_X + MARKER_WIDTH] = bits
        self.last_timings_ms = {"scale_ms": scale_ms,
                                "draw_ms": draw_ms + (perf_counter_ns() - started) / 1_000_000}
        return target

