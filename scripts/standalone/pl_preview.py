"""Candidate PBO1 control: accept only a validated, completed binary frame.

All register access must run in the RLE DriverOwner domain. No pixel arrays are
read or copied. A busy display drops previews while counting continues.
"""
from dataclasses import dataclass

PBO1_SIGNATURE = 0x50424F31
BTN1_SIGNATURE = 0x42544E31
BUTTON_SIGNATURE_OFFSET = 0x80
BUTTON_STATE_OFFSET = 0x84
BUTTON_EVENTS_OFFSET = 0x88
BUTTON_FAULT_OFFSET = 0x8C


@dataclass(frozen=True)
class ButtonEventSnapshot:
    stable: int
    events: int
    fault: int


class ButtonEventStatus:
    """BTN1 status reader with optional W1C of bits observed by each snapshot."""

    def __init__(self, mmio):
        self.mmio = mmio
        self._require_signature()

    def _require_signature(self):
        if int(self.mmio.read(BUTTON_SIGNATURE_OFFSET)) != BTN1_SIGNATURE:
            raise ValueError("button status capability signature mismatch")

    def snapshot(self, *, clear=False) -> ButtonEventSnapshot:
        if type(clear) is not bool:
            raise ValueError("clear must be bool")
        self._require_signature()
        stable = int(self.mmio.read(BUTTON_STATE_OFFSET))
        events = int(self.mmio.read(BUTTON_EVENTS_OFFSET))
        fault = int(self.mmio.read(BUTTON_FAULT_OFFSET))
        if any(value < 0 or value & ~0xF for value in (stable, events, fault)):
            raise ValueError("button status contains reserved bits")
        result = ButtonEventSnapshot(stable, events, fault)
        if clear:
            if events:
                self.mmio.write(BUTTON_EVENTS_OFFSET, events)
            if fault:
                self.mmio.write(BUTTON_FAULT_OFFSET, fault)
        return result


class PlPreview:
    def __init__(self, mmio):
        self.mmio = mmio
        self._stats = dict(submitted=0, dropped=0, annotations_truncated=0)
        if mmio.read(0) != PBO1_SIGNATURE or mmio.read(4) != (400 << 16) | 640:
            raise ValueError("PL preview capability or geometry mismatch")

    def submit(self, packet, result, *, line_x=320, enabled=True):
        if not packet.valid:
            self._stats['dropped'] += 1
            return False
        if type(line_x) is not int or not 0 <= line_x < 640:
            raise ValueError("preview line outside frame")
        fid = packet.frame_id
        if type(fid) is not int or not 0 <= fid <= 0xffffffff:
            raise ValueError("invalid preview frame ID")
        detections = tuple(result.detections) if result is not None else ()
        boxes = []
        for d in detections[:16]:
            coords = tuple(getattr(d, n) for n in ('x_min', 'x_max', 'y_min', 'y_max'))
            if any(type(v) is not int for v in coords):
                raise ValueError("preview box coordinates must be integers")
            x0, x1, y0, y1 = coords
            if not (0 <= x0 <= x1 < 640 and 0 <= y0 <= y1 < 400):
                raise ValueError("preview box outside frame")
            boxes.append((x0 | (x1 << 16), y0 | (y1 << 16)))
        status = self.mmio.read(8)
        if (status & 0xf) != 9 or self.mmio.read(0x18) != fid:
            self._stats['dropped'] += 1
            return False
        # Core copies all metadata into a stable CDC mailbox on commit. Frame
        # acceptance comes last, after the caller's full packet validation.
        self.mmio.write(0x30, int(bool(enabled)))
        self.mmio.write(0x34, fid)
        self.mmio.write(0x38, len(boxes))
        self.mmio.write(0x3c, line_x)
        for i, (x, y) in enumerate(boxes):
            self.mmio.write(0x100 + 8*i, x)
            self.mmio.write(0x104 + 8*i, y)
        self.mmio.write(0x40, 1)
        self.mmio.write(0x20, fid)
        self.mmio.write(0x24, 1)
        self._stats['submitted'] += 1
        self._stats['annotations_truncated'] += int(len(detections) > 16)
        return True

    def stats(self):
        return dict(self._stats)
