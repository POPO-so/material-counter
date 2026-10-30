"""Wire codecs and validation for the 640x400 Gray8/RLE pipeline."""

from __future__ import annotations

from dataclasses import dataclass
import numbers
import struct
from typing import Any

import numpy as np


WIDTH = 640
HEIGHT = 400
PIXELS = WIDTH * HEIGHT
MAX_RUNS = 16384
PROJECTION_WORDS = (WIDTH + HEIGHT + 1) // 2
TX_HEADER_BYTES = 16
TX_TRAILER_BYTES = 16
TX_BYTES = TX_HEADER_BYTES + PIXELS + TX_TRAILER_BYTES
RX_CAPACITY = (16 + MAX_RUNS + PROJECTION_WORDS + 16) * 4

GRAY_HEADER_MAGIC = 0x47525938
GRAY_TRAILER_MAGIC = 0x454E4438
GRAY_END_MAGIC = 0xC0DEC018
HEADER_MAGIC = 0x31454C52
TRAILER_MAGIC = 0x31444E45
END_MAGIC = 0xC0DEC0DE
VERSION = 1
STATUS_OVERFLOW = 1
STATUS_INPUT_ERROR = 2
_WORD = struct.Struct("<I")
_U32_MAX = 0xFFFFFFFF
_U64_MAX = 0xFFFFFFFFFFFFFFFF


@dataclass(frozen=True)
class RleFrame:
    """Validated immutable frame. Overflow/error frames are inspectable, invalid for counting."""

    width: int
    height: int
    frame_id: int
    params_version: int
    roi: tuple[int, int, int, int]
    hw_timestamp: int
    threshold: int
    intensity_offset: int
    morph_mode: int
    runs: np.ndarray
    row_projection: np.ndarray
    column_projection: np.ndarray
    total_fg: int
    total_run_count: int
    status: int
    input_error_code: int
    accepted_pixels: int
    actual_bytes: int

    @property
    def run_count(self) -> int:
        return int(self.runs.size)

    @property
    def valid(self) -> bool:
        return self.status == 0


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise TypeError(f"{name} must be an integer")
    value = int(value)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be in {minimum}..{maximum}")
    return value


def _gray_array(gray: Any) -> np.ndarray:
    array = np.asarray(gray)
    if array.dtype != np.uint8 or array.shape != (HEIGHT, WIDTH):
        raise ValueError(f"gray must be uint8 with shape {(HEIGHT, WIDTH)}")
    return array


def _frame_id(value: Any) -> int:
    return _integer(value, "frame_id", 0, _U32_MAX)


def _writable_bytes(output: Any, expected: int) -> memoryview:
    if not isinstance(output, (bytearray, memoryview, np.ndarray)):
        raise TypeError("output must be writable bytearray, memoryview, or ndarray")
    try:
        view = memoryview(output).cast("B")
    except (TypeError, ValueError) as exc:
        raise TypeError("output must expose a contiguous writable byte buffer") from exc
    if view.readonly:
        raise TypeError("output must be writable")
    if view.nbytes != expected:
        raise ValueError(f"output must be exactly {expected} bytes")
    return view


def encode_gray_into(gray: Any, output: Any, *, frame_id: int = 0) -> None:
    """Encode a raw 640x400 Gray8 frame using the existing 16-byte envelope."""
    array = _gray_array(gray)
    frame_id = _frame_id(frame_id)
    view = _writable_bytes(output, TX_BYTES)
    struct.pack_into("<4I", view, 0, GRAY_HEADER_MAGIC,
                     (HEIGHT << 16) | WIDTH, PIXELS, frame_id)
    payload = memoryview(np.ascontiguousarray(array)).cast("B")
    view[16:16 + PIXELS] = payload
    checksum = int(array.sum(dtype=np.uint64)) & _U32_MAX
    struct.pack_into("<4I", view, 16 + PIXELS,
                     GRAY_TRAILER_MAGIC, frame_id, checksum, GRAY_END_MAGIC)


def encode_gray(gray: Any, *, frame_id: int = 0) -> bytes:
    output = bytearray(TX_BYTES)
    encode_gray_into(gray, output, frame_id=frame_id)
    return bytes(output)


def _roi(value: Any, width: int, height: int) -> tuple[int, int, int, int]:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise ValueError("roi must be (x0, y0, x1, y1)")
    x0, y0, x1, y1 = (_integer(v, "roi coordinate", 0, 0xFFFF) for v in value)
    if x0 >= x1 or y0 >= y1 or x1 > width or y1 > height:
        raise ValueError("roi must be a non-empty rectangle inside the frame")
    return x0, y0, x1, y1


def _projection_words(rows: np.ndarray, columns: np.ndarray) -> list[int]:
    values = [int(v) for v in rows]
    values.extend(int(v) for v in columns)
    if len(values) & 1:
        values.append(0)
    return [values[i] | (values[i + 1] << 16) for i in range(0, len(values), 2)]


def _signed_u32(value: int) -> int:
    return value & _U32_MAX


def encode_mask(mask: Any, *, frame_id: int = 0, params_version: int = 0,
                roi: tuple[int, int, int, int] = (0, 0, WIDTH, HEIGHT),
                timestamp: int = 0, max_runs: int = MAX_RUNS,
                threshold: int = 128, intensity_offset: int = 0,
                morph_mode: int = 0) -> bytes:
    """Reference-encode a Binary8 mask, including bounded RLE and full projections."""
    image = np.asarray(mask)
    if image.dtype != np.uint8 or image.shape != (HEIGHT, WIDTH):
        raise ValueError(f"mask must be uint8 with shape {(HEIGHT, WIDTH)}")
    if np.any((image != 0) & (image != 255)):
        raise ValueError("mask values must be 0 or 255")
    frame_id = _frame_id(frame_id)
    params_version = _integer(params_version, "params_version", 0, _U32_MAX)
    timestamp = _integer(timestamp, "timestamp", 0, _U64_MAX)
    max_runs = _integer(max_runs, "max_runs", 1, MAX_RUNS)
    threshold = _integer(threshold, "threshold", 0, 255)
    intensity_offset = _integer(intensity_offset, "intensity_offset", -255, 255)
    morph_mode = _integer(morph_mode, "morph_mode", 0, 2)
    x0, y0, x1, y1 = _roi(roi, WIDTH, HEIGHT)

    active = image[y0:y1, x0:x1] == 255
    row_projection = np.zeros(HEIGHT, dtype=np.uint16)
    column_projection = np.zeros(WIDTH, dtype=np.uint16)
    row_projection[y0:y1] = active.sum(axis=1, dtype=np.uint32).astype(np.uint16)
    column_projection[x0:x1] = active.sum(axis=0, dtype=np.uint32).astype(np.uint16)
    total_fg = int(active.sum(dtype=np.uint64))

    runs: list[int] = []
    total_run_count = 0
    for local_y, row in enumerate(active):
        indices = np.flatnonzero(row)
        if indices.size == 0:
            continue
        breaks = np.flatnonzero(np.diff(indices) > 1) + 1
        starts = np.concatenate((indices[:1], indices[breaks]))
        ends = np.concatenate((indices[breaks - 1], indices[-1:]))
        total_run_count += int(starts.size)
        available = max_runs - len(runs)
        if available > 0:
            absolute_y = y0 + local_y
            for start, end in zip(starts[:available], ends[:available]):
                runs.append((absolute_y << 20) | ((x0 + int(start)) << 10) | (x0 + int(end)))

    status = STATUS_OVERFLOW if total_run_count > max_runs else 0
    projection = _projection_words(row_projection, column_projection)
    header = [
        HEADER_MAGIC, VERSION, (HEIGHT << 16) | WIDTH, frame_id, params_version,
        (y0 << 16) | x0, (y1 << 16) | x1,
        timestamp & _U32_MAX, timestamp >> 32, 1, len(projection), max_runs,
        threshold, _signed_u32(intensity_offset), morph_mode, 0,
    ]
    checksum = (sum(header) + sum(runs) + sum(projection)) & _U32_MAX
    actual_bytes = (len(header) + len(runs) + len(projection) + 16) * 4
    trailer = [
        TRAILER_MAGIC, frame_id, params_version, total_fg, total_run_count,
        len(runs), status, actual_bytes, checksum, 0, PIXELS,
        0, 0, 0, 0, END_MAGIC,
    ]
    packet = bytearray(actual_bytes)
    offset = 0
    for word in header:
        _WORD.pack_into(packet, offset, word)
        offset += 4
    for word in runs:
        _WORD.pack_into(packet, offset, word)
        offset += 4
    for word in projection:
        _WORD.pack_into(packet, offset, word)
        offset += 4
    for word in trailer:
        _WORD.pack_into(packet, offset, word)
        offset += 4
    if len(packet) > RX_CAPACITY:
        raise AssertionError("encoded RLE packet exceeds its declared capacity")
    return bytes(packet)


def _immutable(array: np.ndarray) -> np.ndarray:
    # Bytes-backed NumPy views cannot be made writable again by re-enabling
    # WRITEABLE, which preserves frozen-frame semantics for all consumers.
    return np.frombuffer(array.tobytes(), dtype=array.dtype).reshape(array.shape)


def decode_packet(packet: Any, expected_frame_id: int | None = None,
                  expected_params_version: int | None = None) -> RleFrame:
    """Parse a complete variable-length packet and reject malformed statistics."""
    try:
        view = memoryview(packet).cast("B")
    except (TypeError, ValueError) as exc:
        raise TypeError("packet must expose a contiguous byte buffer") from exc
    if view.nbytes % 4 or view.nbytes < (16 + 520 + 16) * 4 or view.nbytes > RX_CAPACITY:
        raise ValueError("RLE packet length is outside its valid bounds")
    words = np.frombuffer(view, dtype="<u4")
    h = [int(v) for v in words[:16]]
    if h[0] != HEADER_MAGIC or h[1] != VERSION:
        raise ValueError("RLE header magic or version is invalid")
    geometry = h[2]
    width, height = geometry & 0xFFFF, geometry >> 16
    if (width, height) != (WIDTH, HEIGHT):
        raise ValueError("RLE geometry must be 640x400")
    if h[5] >> 16 > 0xFFFF or h[6] >> 16 > 0xFFFF:
        raise ValueError("RLE ROI encoding is invalid")
    x0, y0 = h[5] & 0xFFFF, h[5] >> 16
    x1, y1 = h[6] & 0xFFFF, h[6] >> 16
    _roi((x0, y0, x1, y1), width, height)
    if h[9] != 1 or h[10] != (width + height + 1) // 2:
        raise ValueError("RLE flags or projection length are invalid")
    max_runs = h[11]
    if not 1 <= max_runs <= MAX_RUNS or h[12] > 255 or h[14] > 2 or h[15] != 0:
        raise ValueError("RLE configuration or reserved header word is invalid")
    frame_id, params_version = h[3], h[4]
    if expected_frame_id is not None and frame_id != _frame_id(expected_frame_id):
        raise ValueError("RLE frame ID does not match")
    if (expected_params_version is not None
            and params_version != _integer(expected_params_version, "params_version", 0, _U32_MAX)):
        raise ValueError("RLE parameter version does not match")
    timestamp = h[7] | (h[8] << 32)
    intensity_offset = h[13] if h[13] < 0x80000000 else h[13] - (1 << 32)
    if not -255 <= intensity_offset <= 255:
        raise ValueError("RLE intensity offset is outside -255..255")

    trailer_offset = len(words) - 16
    t = [int(v) for v in words[trailer_offset:]]
    if t[0] != TRAILER_MAGIC or t[1] != frame_id or t[2] != params_version or t[15] != END_MAGIC:
        raise ValueError("RLE trailer magic or identity is invalid")
    if any(t[i] != 0 for i in range(11, 15)):
        raise ValueError("RLE reserved trailer word is nonzero")
    run_count, total_run_count = t[5], t[4]
    if run_count > max_runs or run_count > total_run_count:
        raise ValueError("RLE emitted run count is invalid")
    overflow = bool(t[6] & STATUS_OVERFLOW)
    input_error = bool(t[6] & STATUS_INPUT_ERROR)
    if t[6] & ~(STATUS_OVERFLOW | STATUS_INPUT_ERROR):
        raise ValueError("RLE status has unknown flags")
    if overflow != (total_run_count > max_runs) or run_count != min(total_run_count, max_runs):
        raise ValueError("RLE overflow and run counts disagree")
    if input_error != (t[9] != 0) or t[10] > width * height:
        raise ValueError("RLE input error metadata is invalid")
    if not input_error and t[10] != width * height:
        raise ValueError("RLE accepted-pixel count is incomplete")
    roi_area = (x1 - x0) * (y1 - y0)
    if t[3] > roi_area or total_run_count > roi_area:
        raise ValueError("RLE foreground statistics exceed the ROI")
    if t[3] > t[10] or total_run_count > t[10]:
        raise ValueError("RLE foreground statistics exceed accepted pixels")
    projection_count = h[10]
    expected_bytes = (16 + run_count + projection_count + 16) * 4
    if t[7] != view.nbytes or t[7] != expected_bytes:
        raise ValueError("RLE actual length does not match the packet")
    checksum_offset = 16 + run_count + projection_count
    checksum = int(words[:checksum_offset].sum(dtype=np.uint64)) & _U32_MAX
    if checksum != t[8]:
        raise ValueError("RLE packet checksum is invalid")

    runs = np.array(words[16:16 + run_count], dtype=np.uint32, copy=True)
    run_rows = runs >> 20
    run_starts = (runs >> 10) & 0x3FF
    run_ends = runs & 0x3FF
    if np.any(runs & 0xE0000000):
        raise ValueError("RLE reserved run bits are nonzero")
    if (np.any(run_rows < y0) or np.any(run_rows >= y1)
            or np.any(run_starts < x0) or np.any(run_starts > run_ends)
            or np.any(run_ends >= x1)):
        raise ValueError("RLE run is outside the configured ROI")
    if (np.any(run_rows[1:] < run_rows[:-1]) or
            np.any((run_rows[1:] == run_rows[:-1]) &
                   (run_starts[1:] <= run_ends[:-1] + 1))):
        raise ValueError("RLE runs are not sorted and maximal")
    lengths = run_ends.astype(np.int64) - run_starts + 1
    run_foreground = int(lengths.sum(dtype=np.int64))
    run_row_counts = np.bincount(run_rows.astype(np.intp), weights=lengths,
                                  minlength=height).astype(np.uint32)
    run_column_deltas = np.zeros(width + 1, dtype=np.int32)
    np.add.at(run_column_deltas, run_starts, 1)
    np.add.at(run_column_deltas, run_ends + 1, -1)
    run_column_counts = np.cumsum(run_column_deltas[:-1], dtype=np.uint32)

    projection_start = 16 + run_count
    projection_data = words[projection_start:projection_start + projection_count]
    counts = projection_data.view("<u2")
    if (height + width) & 1 and int(counts[-1]) != 0:
        raise ValueError("RLE projection padding is nonzero")
    row_projection = counts[:height].copy()
    column_projection = counts[height:height + width].copy()
    if (np.any(row_projection > width) or np.any(column_projection > height)
            or np.any(row_projection[:y0]) or np.any(row_projection[y1:])
            or np.any(column_projection[:x0]) or np.any(column_projection[x1:])):
        raise ValueError("RLE projections exceed geometry or ROI")
    if int(row_projection.astype(np.uint64).sum()) != t[3] or int(column_projection.astype(np.uint64).sum()) != t[3]:
        raise ValueError("RLE projection totals disagree with foreground count")
    if np.any(run_row_counts > row_projection) or np.any(run_column_counts > column_projection):
        raise ValueError("RLE runs exceed their full-frame projections")
    if not overflow and not input_error and (run_foreground != t[3]
                         or not np.array_equal(run_row_counts, row_projection.astype(np.uint32))
                         or not np.array_equal(run_column_counts, column_projection.astype(np.uint32))):
        raise ValueError("RLE runs and projections disagree")

    return RleFrame(
        width=width, height=height, frame_id=frame_id, params_version=params_version,
        roi=(x0, y0, x1, y1), hw_timestamp=timestamp, threshold=h[12],
        intensity_offset=intensity_offset, morph_mode=h[14], runs=_immutable(runs),
        row_projection=_immutable(row_projection), column_projection=_immutable(column_projection),
        total_fg=t[3], total_run_count=total_run_count, status=t[6],
        input_error_code=t[9], accepted_pixels=t[10], actual_bytes=t[7],
    )


def reconstruct(frame: RleFrame) -> np.ndarray:
    """Expand a valid frame into a Binary8 mask for tests or candidate-only debugging."""
    if not isinstance(frame, RleFrame):
        raise TypeError("frame must be an RleFrame")
    if not frame.valid:
        raise ValueError("cannot reconstruct an overflow or input-error RLE frame")
    mask = np.zeros((frame.height, frame.width), dtype=np.uint8)
    for value in frame.runs:
        packed = int(value)
        row, start, end = packed >> 20, (packed >> 10) & 0x3FF, packed & 0x3FF
        mask[row, start:end + 1] = 255
    return mask
