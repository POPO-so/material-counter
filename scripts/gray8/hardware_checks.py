"""Checks surrounding each Gray8 transfer; importing never opens MMIO."""
import time

import numpy as np
from packet_codec import HEADER_MAGIC, PIXELS, TX_BYTES, validate_packet, binary_golden

OFFSETS = (0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20, 0x24,
           0x34, 0x38, 0x3C, 0x40, 0x4C, 0x50, 0x54, 0x58)


def require_dma_quiescent(read, *, allow_unprogrammed=False):
    status = {name: int(read(offset)) for name, offset in (("mm2s", 0x04), ("s2mm", 0x34))}
    # PYNQ starts both channels before the first transfer. In this state Idle
    # remains clear; accept it only when no address or length was programmed.
    fresh = allow_unprogrammed and all(int(read(offset)) == 0 for offset in
                                      (0x18, 0x1C, 0x28, 0x48, 0x4C, 0x58))
    for name, value in status.items():
        if value & 0x770 or (not value & 3 and not fresh):
            raise RuntimeError("DMA %s is busy or has an error: 0x%08x" % (name, value))
    return status


class Gray8Checks:
    def __init__(self, mmio, verify_pixels=True, dma_read=None, validation=None):
        self.mmio = mmio
        if validation is None:
            validation = "full" if verify_pixels else "runtime"
        if validation not in ("full", "runtime"):
            raise ValueError("validation must be 'full' or 'runtime'")
        self.validation = validation
        self.verify_pixels = validation == "full"
        self.dma_read = dma_read
        self.previous = None
        self.verified_frames = 0
        self.output_checksum_frames = 0
        self.completions = []

    @staticmethod
    def _measure(call):
        wall_started = time.perf_counter()
        cpu_started = time.thread_time()
        try:
            value = call()
        finally:
            wall_ms = (time.perf_counter() - wall_started) * 1000.0
            cpu_ms = (time.thread_time() - cpu_started) * 1000.0
        return value, wall_ms, cpu_ms

    def signatures(self):
        if self.mmio.read(0x04) != HEADER_MAGIC or self.mmio.read(0x60) != 0x574F3135:
            raise RuntimeError("Gray8 overlay signature mismatch; no transfer permitted")

    def snapshot(self):
        return {offset: self.mmio.read(offset) for offset in OFFSETS}

    def before_transfer(self, frame_id, slot):
        self.signatures()
        snapshot = self.snapshot()
        if self.dma_read is not None:
            fresh = (frame_id == 0 and self.verified_frames == 0 and
                     all(snapshot[offset] == 0 for offset in (0x14, 0x1C, 0x20, 0x24)))
            require_dma_quiescent(self.dma_read, allow_unprogrammed=fresh)
        if snapshot[0x4C] & 5 or snapshot[0x58] or snapshot[0x18] or snapshot[0x0C]:
            raise RuntimeError("Gray8 pipeline is busy or has a prior fault")
        self.previous = snapshot
        self.mmio.write(0x5C, 1)

    def after_transfer(self, frame_id, slot):
        previous = self.previous
        if previous is None:
            raise RuntimeError("No matching Gray8 arm record")

        def check_registers():
            self.signatures()
            snapshot = self.snapshot()
            wanted = {0x08: 9, 0x0C: 0, 0x10: frame_id, 0x18: 0,
                      0x34: PIXELS, 0x38: 1, 0x3C: 240,
                      0x4C: 2, 0x50: PIXELS, 0x54: PIXELS // 4, 0x58: 0}
            wanted.update({offset: (previous[offset] + delta) & 0xFFFFFFFF for offset, delta in
                           ((0x14, 1), (0x1C, TX_BYTES), (0x20, TX_BYTES // 4), (0x24, 1))})
            for offset, value in wanted.items():
                if snapshot[offset] != value:
                    raise RuntimeError("Gray8 register 0x%02x=%d expected=%d" %
                                       (offset, snapshot[offset], value))
            return snapshot

        snapshot, mmio_wall_ms, mmio_cpu_ms = self._measure(check_registers)
        mask = np.asarray(slot.cached_mask)

        def check_output_checksum():
            if snapshot[0x40] != int(mask.sum(dtype=np.uint64)):
                raise RuntimeError("Gray8 output checksum differs from returned bytes")

        _, checksum_wall_ms, checksum_cpu_ms = self._measure(check_output_checksum)
        pixel_wall_ms = pixel_cpu_ms = 0.0
        if self.verify_pixels:
            def check_pixels():
                gray = validate_packet(slot.tx_buffer, frame_id=frame_id)
                golden = binary_golden(gray)
                mismatches = int(np.count_nonzero(mask != golden))
                if mismatches:
                    raise RuntimeError("Gray8 pixel comparison failed: %d mismatches" % mismatches)

            _, pixel_wall_ms, pixel_cpu_ms = self._measure(check_pixels)
            self.verified_frames += 1
        self.output_checksum_frames += 1
        timings_ms = {"mmio": mmio_wall_ms, "output_checksum": checksum_wall_ms,
                      "pixel_verify": pixel_wall_ms}
        cpu_ms = {"mmio": mmio_cpu_ms, "output_checksum": checksum_cpu_ms,
                  "pixel_verify": pixel_cpu_ms}
        record = dict(frame_id=frame_id, pixel_verified=self.verify_pixels,
                      output_checksum_verified=True, timings_ms=timings_ms, cpu_ms=cpu_ms,
                      checker={"%02x" % k: v for k, v in snapshot.items()})
        self.completions.append(record)
        self.previous = None
        return record

    def summarize_timings_ms(self):
        result = {}
        for field in ("wall_ms", "cpu_ms"):
            names = ("mmio", "output_checksum", "pixel_verify")
            result[field] = {
                name: (sum(sample["timings_ms" if field == "wall_ms" else "cpu_ms"][name]
                           for sample in self.completions) / len(self.completions)
                       if self.completions else 0.0)
                for name in names
            }
        result["frames"] = len(self.completions)
        return result
