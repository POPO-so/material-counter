"""Fail-closed reader for the candidate FRG1/FRG2 PBO region-feature window."""
from __future__ import annotations

from numbers import Integral

from .components import Component


FRG1_SIGNATURE = 0x46524731
FRG2_SIGNATURE = 0x46524732
SIGNATURE = FRG2_SIGNATURE
MAX_AREA_OFFSET = 0x220
MAX_W_OFFSET = 0x224
MAX_H_OFFSET = 0x228
ASPECT_X16_OFFSET = 0x22C
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
    """Read stable FRG1/FRG2 snapshots through a synchronous MMIO owner."""

    def __init__(self, mmio):
        self.mmio = mmio
        self.reason_counts = {reason: 0 for reason in (
            "invalid_frame", "mmio_error", "not_done", "overflow", "busy",
            "frame_mismatch", "bad_count", "foreground_mismatch", "snapshot_changed", "bad_record")}
        self.last_dropped = None
        self.signature = int(mmio.read(SIGNATURE_OFFSET))
        if self.signature not in (FRG1_SIGNATURE, FRG2_SIGNATURE):
            raise ValueError("FRG1/FRG2 signature mismatch")
        if int(mmio.read(CAPACITY_OFFSET)) != CAPACITY:
            raise ValueError("FRG capacity mismatch")
        self.candidate_count = 0

    @property
    def supports_candidates(self) -> bool:
        return self.signature == FRG2_SIGNATURE

    def _reject(self, reason):
        self.reason_counts[reason] += 1
        return None

    def configure_thresholds(self, *, max_area: int = 0, max_w: int = 0,
                             max_h: int = 0, aspect_x16: int = 0) -> bool:
        """Configure FRG2 while idle; return False only for an unchanged FRG1 core.

        Busy hardware raises RuntimeError; signature changes, unknown capabilities,
        and rejected or mismatched register writes raise ValueError/RuntimeError.
        """
        values = (("max_area", max_area, 0xFFFFFF), ("max_w", max_w, 0xFFFF),
                  ("max_h", max_h, 0xFFFF), ("aspect_x16", aspect_x16, 0xFF))
        for name, value, maximum in values:
            if isinstance(value, bool) or not isinstance(value, Integral) or not 0 <= int(value) <= maximum:
                raise ValueError(f"{name} must be an integer in [0, {maximum}]")
        try:
            signature = int(self.mmio.read(SIGNATURE_OFFSET))
        except Exception as exc:
            raise ValueError("cannot verify FRG2 capability") from exc
        if signature not in (FRG1_SIGNATURE, FRG2_SIGNATURE):
            raise ValueError("unknown FRG capability signature")
        if signature != self.signature:
            raise ValueError("FRG capability signature changed")
        if signature == FRG1_SIGNATURE:
            return False
        try:
            status = int(self.mmio.read(STATUS_OFFSET))
        except Exception as exc:
            raise RuntimeError("cannot verify FRG busy status") from exc
        if status & 0x4:
            raise RuntimeError("cannot configure FRG2 thresholds while feature core is busy")
        values = ((MAX_AREA_OFFSET, int(max_area)), (MAX_W_OFFSET, int(max_w)),
                  (MAX_H_OFFSET, int(max_h)), (ASPECT_X16_OFFSET, int(aspect_x16)))
        for offset, value in values:
            self.mmio.write(offset, value)
        try:
            readback = tuple(int(self.mmio.read(offset)) for offset, _ in values)
        except Exception as exc:
            raise RuntimeError("FRG2 threshold readback failed") from exc
        expected = tuple(value for _, value in values)
        if readback != expected:
            raise RuntimeError(f"FRG2 threshold readback mismatch: got {readback}, expected {expected}")
        return True

    @staticmethod
    def _u32(value):
        return (not isinstance(value, bool) and isinstance(value, Integral)
                and 0 <= int(value) <= 0xFFFFFFFF)

    def read(self, frame) -> tuple[Component, ...] | None:
        """Return features only for a stable, complete snapshot matching ``frame``."""
        self.candidate_count = None
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
            area, sum_x, sum_y, packed_x, packed_y, record_frame, word6, word7 = map(int, record)
            candidate = None
            if self.signature == FRG1_SIGNATURE:
                if word6 != 0 or word7 != 0:
                    return self._reject("bad_record")
            else:
                candidate_flag = word6 & 1
                candidate_reason = (word6 >> 1) & 0x7
                if word6 & ~0xF or word7 != 0 or candidate_flag != int(candidate_reason != 0):
                    return self._reject("bad_record")
                candidate = candidate_reason
            x0, x1 = packed_x & 0xFFFF, packed_x >> 16
            y0, y1 = packed_y & 0xFFFF, packed_y >> 16
            if (record_frame != frame_id
                    or x0 > x1 or y0 > y1 or x1 >= frame.width or y1 >= frame.height
                    or area <= 0 or area > (x1-x0+1)*(y1-y0+1)
                    or not area*x0 <= sum_x <= area*x1
                    or not area*y0 <= sum_y <= area*y1):
                return self._reject("bad_record")
            result.append(Component(x0, y0, x1, y1, area, sum_x / area, sum_y / area, candidate))
            area_sum += area
        if area_sum != total_fg:
            return self._reject("foreground_mismatch")
        self.candidate_count = sum(component.candidate is not None and component.candidate != 0
                                   for component in result)
        return tuple(result)
