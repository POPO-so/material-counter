from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hardware_checks import Gray8Checks, require_dma_quiescent
from packet_codec import TX_BYTES, gray320x240_to_gray_packet, binary_golden


class Registers:
    def __init__(self, signature=0x47525938):
        self.values = {0x04: signature, 0x60: 0x574F3135}
        self.writes = []

    def read(self, offset):
        return self.values.get(offset, 0)

    def write(self, offset, value):
        self.writes.append((offset, value))


def test_wrong_overlay_is_rejected_before_arm_or_dma():
    regs = Registers(signature=0x574F3134)
    check = Gray8Checks(regs)
    with pytest.raises(RuntimeError, match="signature"):
        check.before_transfer(0, SimpleNamespace())
    assert regs.writes == []


def test_dma_busy_or_error_is_rejected_before_driver_or_transfer_start():
    assert require_dma_quiescent(lambda offset: 1) == {"mm2s": 1, "s2mm": 1}
    assert require_dma_quiescent(lambda offset: 0x1002) == {"mm2s": 0x1002, "s2mm": 0x1002}
    for status in (0, 0x12, 0x42):
        with pytest.raises(RuntimeError, match="DMA"):
            require_dma_quiescent(lambda offset: status)


def test_completion_checks_actual_gray_payload_and_every_output_pixel():
    regs = Registers()
    gray = np.resize(np.array([0, 127, 128, 255], dtype=np.uint8), (240, 320))
    packet = gray320x240_to_gray_packet(gray, frame_id=0)
    slot = SimpleNamespace(tx_buffer=np.frombuffer(packet, dtype=np.uint8), cached_mask=binary_golden(gray))
    check = Gray8Checks(regs)
    check.before_transfer(0, slot)
    assert regs.writes == [(0x5C, 1)]
    regs.values.update({0x08: 9, 0x10: 0, 0x14: 1, 0x1C: TX_BYTES,
                        0x20: TX_BYTES // 4, 0x24: 1, 0x34: 76800,
                        0x38: 1, 0x3C: 240, 0x40: int(slot.cached_mask.sum()),
                        0x4C: 2, 0x50: 76800, 0x54: 19200})
    check.after_transfer(0, slot)
    assert check.verified_frames == 1
    slot.cached_mask[0, 0] = 255
    with pytest.raises(RuntimeError):
        check.after_transfer(0, slot)


def test_fresh_started_dma_is_allowed_only_before_any_programmed_transfer():
    regs = Registers()
    dma = {0x04: 0, 0x34: 0}
    check = Gray8Checks(regs, dma_read=lambda offset: dma.get(offset, 0))
    check.before_transfer(0, SimpleNamespace())
    assert regs.writes == [(0x5C, 1)]
    for offset in (0x18, 0x1C, 0x28, 0x48, 0x4C, 0x58):
        dma[offset] = 4
        with pytest.raises(RuntimeError, match="DMA"):
            check.before_transfer(0, SimpleNamespace())
        dma[offset] = 0
    regs.values[0x1C] = 76832
    with pytest.raises(RuntimeError, match="DMA"):
        check.before_transfer(0, SimpleNamespace())
    regs.values[0x1C] = 0
    check.verified_frames = 1
    with pytest.raises(RuntimeError, match="DMA"):
        check.before_transfer(1, SimpleNamespace())
