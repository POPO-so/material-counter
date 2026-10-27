"""Checks surrounding each Gray8 transfer; importing never opens MMIO."""
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
    def __init__(self, mmio, verify_pixels=True, dma_read=None):
        self.mmio = mmio
        self.verify_pixels = verify_pixels
        self.dma_read = dma_read
        self.previous = None
        self.verified_frames = 0
        self.completions = []

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
        self.signatures()
        snapshot = self.snapshot()
        previous = self.previous
        if previous is None:
            raise RuntimeError("No matching Gray8 arm record")
        wanted = {0x08: 9, 0x0C: 0, 0x10: frame_id, 0x18: 0,
                  0x34: PIXELS, 0x38: 1, 0x3C: 240,
                  0x4C: 2, 0x50: PIXELS, 0x54: PIXELS // 4, 0x58: 0}
        wanted.update({offset: (previous[offset] + delta) & 0xFFFFFFFF for offset, delta in
                       ((0x14, 1), (0x1C, TX_BYTES), (0x20, TX_BYTES // 4), (0x24, 1))})
        for offset, value in wanted.items():
            if snapshot[offset] != value:
                raise RuntimeError("Gray8 register 0x%02x=%d expected=%d" %
                                   (offset, snapshot[offset], value))
        mask = np.asarray(slot.cached_mask)
        if snapshot[0x40] != int(mask.sum(dtype=np.uint64)):
            raise RuntimeError("Gray8 output checksum differs from returned bytes")
        if self.verify_pixels:
            gray = validate_packet(slot.tx_buffer, frame_id=frame_id)
            golden = binary_golden(gray)
            mismatches = int(np.count_nonzero(mask != golden))
            if mismatches:
                raise RuntimeError("Gray8 pixel comparison failed: %d mismatches" % mismatches)
            self.verified_frames += 1
        self.completions.append(dict(frame_id=frame_id, pixel_verified=self.verify_pixels,
                                     checker={"%02x" % k: v for k, v in snapshot.items()}))
        self.previous = None
