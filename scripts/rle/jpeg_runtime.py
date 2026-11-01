"""PS-side tracking and frame-matched HDMI publication for the JPEG PL path."""
from __future__ import annotations

from typing import Any

from scripts.rle.jpeg_transport import FRG_SIGNATURE
from scripts.wo0016.stream_counter import Detection


FRG_STATUS = 0x0204
FRG_FRAME_ID = 0x0208
FRG_RECORD_COUNT = 0x020C
FRG_MAX_RECORDS = 512
FRG_RECORD_BASE = 0x1000
FRG_WORDS_PER_RECORD = 8


def read_frg_detections(control: Any, frame_id: int) -> list[Detection]:
    """Read and validate the immutable FRG1 records for one completed PL frame."""
    frame_id = _uint(frame_id, "frame_id")
    actual_signature = int(control.read(0x0200))
    if actual_signature != FRG_SIGNATURE:
        raise RuntimeError(
            f"FRG signature mismatch: read 0x{actual_signature:08x}, "
            f"expected FRG1 0x{FRG_SIGNATURE:08x}")
    state = int(control.read(FRG_STATUS))
    actual_id = int(control.read(FRG_FRAME_ID))
    count = int(control.read(FRG_RECORD_COUNT))
    if actual_id != frame_id:
        raise RuntimeError(f"FRG frame ID mismatch: expected {frame_id}, got {actual_id}")
    if state & 0x2:
        raise RuntimeError("FRG overflow")
    if state & 0x4 or not state & 0x1:
        raise RuntimeError("FRG frame is not complete")
    if not 0 <= count <= FRG_MAX_RECORDS:
        raise RuntimeError("FRG record count is outside the ABI limit")

    detections = []
    for index in range(count):
        words = [int(control.read(FRG_RECORD_BASE + 4 * (index * FRG_WORDS_PER_RECORD + word)))
                 & 0xFFFFFFFF for word in range(FRG_WORDS_PER_RECORD)]
        area, sum_x, sum_y, packed_x, packed_y, record_id, reserved0, reserved1 = words
        x0, x1 = packed_x & 0x3FF, (packed_x >> 16) & 0x3FF
        y0, y1 = packed_y & 0x3FF, (packed_y >> 16) & 0x3FF
        if record_id != frame_id:
            raise RuntimeError(f"FRG record frame ID mismatch at index {index}")
        if (reserved0 or reserved1 or area == 0 or
                x0 > x1 or y0 > y1 or x1 >= 640 or y1 >= 400 or
                area > (x1 - x0 + 1) * (y1 - y0 + 1) or
                not x0 * area <= sum_x <= x1 * area or
                not y0 * area <= sum_y <= y1 * area):
            raise RuntimeError(f"FRG record {index} has invalid geometry or reserved fields")
        detections.append(Detection(x0, x1, y0, y1, area,
                                    sum_x / area, sum_y / area))

    # The single-port result RAM is stable until the next JPEG reaches PL. Check
    # the header again to fail closed if another producer changed it mid-read.
    final_state = int(control.read(FRG_STATUS))
    final_id = int(control.read(FRG_FRAME_ID))
    final_count = int(control.read(FRG_RECORD_COUNT))
    if final_id != frame_id or final_count != count or final_state & 0x6 or not final_state & 0x1:
        raise RuntimeError("FRG frame changed while records were being read")
    return detections


def process_jpeg_frame(transport: Any, tracker: Any, jpeg: Any, frame_id: int,
                       *, capture_monotonic_ns: int | None = None):
    """Decode one compressed frame in PL, validate FRG identity, then count via RLE split."""
    frame = transport.transfer(jpeg, frame_id, auto_accept=False)
    if not getattr(frame, "valid", False) or getattr(frame, "status", 1) != 0:
        raise RuntimeError("refusing to count an invalid JPEG/RLE frame")
    # FRG status/identity/geometry are still validated as a PL cross-check.
    # Counting runs the split-capable run-based path (update_rle), so the
    # 5feafe63 association and 3d466c6c 2-4 way split fixes stay in effect.
    read_frg_detections(transport.preview_control, frame_id)
    result = tracker.update_rle(frame, capture_monotonic_ns=capture_monotonic_ns)
    if result is None:
        raise RuntimeError("RLE frame produced no countable result")
    boxes = [(d.x_min, d.y_min, d.x_max, d.y_max) for d in result.detections[:16]]
    transport.publish_overlay(frame_id, boxes, line_x=tracker.line_x)
    return result


def _uint(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"{name} must be an unsigned 32-bit integer")
    return value
