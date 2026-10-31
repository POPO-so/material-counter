"""Fail-closed reader for the candidate FRG1 PBO region-feature window."""
from __future__ import annotations

from numbers import Integral

from .components import Component


SIGNATURE = 0x46524731
SIGNATURE_OFFSET = 0x200
STATUS_OFFSET = 0x204
FRAME_ID_OFFSET = 0x208
COUNT_OFFSET = 0x20C
TOTAL_FG_OFFSET = 0x210
DROPPED_OFFSET = 0x214
CAPACITY_OFFSET = 0x218
RECORDS_OFFSET = 0x1000
CAPACITY = 512


class PlRegionFeatures:
    """Read a stable, validated FRG1 snapshot through a synchronous MMIO owner."""

    def __init__(self, mmio):
        self.mmio = mmio
        self.reason_counts = {reason: 0 for reason in (
            "invalid_frame", "mmio_error", "not_done", "overflow", "busy",
            "frame_mismatch", "bad_count", "foreground_mismatch", "snapshot_changed", "bad_record")}
        self.last_dropped = None
        if int(mmio.read(SIGNATURE_OFFSET)) != SIGNATURE:
            raise ValueError("FRG1 signature mismatch")
        if int(mmio.read(CAPACITY_OFFSET)) != CAPACITY:
            raise ValueError("FRG1 capacity mismatch")

    def _reject(self, reason):
        self.reason_counts[reason] += 1
        return None

    @staticmethod
    def _u32(value):
        return (not isinstance(value, bool) and isinstance(value, Integral)
                and 0 <= int(value) <= 0xFFFFFFFF)

    def read(self, frame) -> tuple[Component, ...] | None:
        """Return features only for a stable, complete snapshot matching ``frame``."""
        if not getattr(frame, "valid", False):
            return self._reject("invalid_frame")
        try:
            status = int(self.mmio.read(STATUS_OFFSET))
            frame_id = int(self.mmio.read(FRAME_ID_OFFSET))
            count = int(self.mmio.read(COUNT_OFFSET))
            total_fg = int(self.mmio.read(TOTAL_FG_OFFSET))
            dropped = int(self.mmio.read(DROPPED_OFFSET))
        except Exception:
            return self._reject("mmio_error")
        self.last_dropped = dropped
        if status & 0x1 == 0:
            return self._reject("not_done")
        if status & 0x2:
            return self._reject("overflow")
        if status & 0x4:
            return self._reject("busy")
        if frame_id != frame.frame_id:
            return self._reject("frame_mismatch")
        if not (0 <= count <= CAPACITY):
            return self._reject("bad_count")
        if total_fg != frame.total_fg:
            return self._reject("foreground_mismatch")

        words = []
        try:
            for i in range(count * 8):
                words.append(self.mmio.read(RECORDS_OFFSET + i * 4))
            final_status = int(self.mmio.read(STATUS_OFFSET))
            final_frame_id = int(self.mmio.read(FRAME_ID_OFFSET))
        except Exception:
            return self._reject("mmio_error")
        if final_status != status or final_frame_id != frame_id:
            return self._reject("snapshot_changed")

        result = []
        area_sum = 0
        for i in range(count):
            record = words[i * 8:(i + 1) * 8]
            if any(not self._u32(word) for word in record):
                return self._reject("bad_record")
            area, sum_x, sum_y, packed_x, packed_y, record_frame, reserved0, reserved1 = map(int, record)
            x0, x1 = packed_x & 0xFFFF, packed_x >> 16
            y0, y1 = packed_y & 0xFFFF, packed_y >> 16
            if (record_frame != frame_id or reserved0 != 0 or reserved1 != 0
                    or x0 > x1 or y0 > y1 or x1 >= frame.width or y1 >= frame.height
                    or area <= 0 or area > (x1-x0+1)*(y1-y0+1)
                    or not area*x0 <= sum_x <= area*x1
                    or not area*y0 <= sum_y <= area*y1):
                return self._reject("bad_record")
            result.append(Component(x0, y0, x1, y1, area, sum_x / area, sum_y / area))
            area_sum += area
        if area_sum != total_fg:
            return self._reject("foreground_mismatch")
        return tuple(result)
