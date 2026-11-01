"""Guarded AXI-Lite control for the B1a axis_gray_overlay (default off).

The overlay lives in the FCLK2 / video_owner domain, so every register access
is submitted through the existing ``DriverOwner`` that owns the display.  This
module never creates a second Overlay, a second MMIO object or a second owner.

Safety rules enforced here:
- only documented 4-byte offsets in 0x00..0x5C are addressable; the AXI-Lite
  slave truncates to 8 bits, so stray offsets would alias onto real registers;
- the only write permitted in this phase is CTRL=0x00000000;
- no automatic retry on any error, and a SIGBUS is deliberately not treated as
  a catchable Python exception (the process dies; the fail-hold contract keeps
  the hardware evidence).
"""

from __future__ import annotations

import time

ID_MCO1 = 0x4D434F31
CTRL_BYPASS = 0x1
STATUS_SOF_SEEN = 0x2
STATUS_COORD_ERROR = 0x4
REG_CTRL = 0x00
REG_ID = 0x04
REG_FRAME_COUNT = 0x08
REG_STATUS = 0x0C
REG_LINE_THICK = 0x1C

ALLOWED_OFFSETS = frozenset(range(0x00, 0x60, 4))
ALLOWED_WRITES = frozenset({(REG_CTRL, 0x00000000)})
_UINT32_MAX = 0xFFFFFFFF


class OverlayControlError(RuntimeError):
    """Raised before or during an overlay register operation."""


def _uint32(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("{} must be an int".format(name))
    if not 0 <= value <= _UINT32_MAX:
        raise ValueError("{} must be in 0..0xFFFFFFFF".format(name))
    return value


class OverlayControl:
    def __init__(self, overlay, owner, *, sof_timeout_s=2.0, frame_timeout_s=2.0,
                 poll_s=0.02):
        self._owner = owner
        self._mmio = self._resolve_mmio(overlay)
        self.sof_timeout_s = float(sof_timeout_s)
        self.frame_timeout_s = float(frame_timeout_s)
        self.poll_s = float(poll_s)
        self.last_reads = {}
        self.writes = 0

    @staticmethod
    def _resolve_mmio(overlay):
        candidates = []
        hdmi_out = getattr(overlay, "hdmi_out", None)
        if hdmi_out is not None:
            candidates.append(getattr(hdmi_out, "axis_gray_overlay", None))
        candidates.append(getattr(overlay, "hdmi_out_axis_gray_overlay", None))
        for candidate in candidates:
            mmio = getattr(candidate, "mmio", None)
            if mmio is not None:
                return mmio
        raise OverlayControlError(
            "overlay lacks hdmi_out.axis_gray_overlay.mmio; refusing all access")

    def _call(self, function, *args):
        try:
            return self._owner.call(function, *args, timeout=1.0)
        except Exception as error:
            # Do not retry. A SIGBUS is not an Exception and terminates the
            # process before this handler can run; that case is handled by the
            # existing fail-hold/evidence path, not here.
            raise OverlayControlError(
                "MMIO operation failed: {!r}".format(error)) from error

    @staticmethod
    def _validate_offset(offset):
        offset = _uint32(offset, "offset")
        if offset not in ALLOWED_OFFSETS:
            raise OverlayControlError(
                "offset 0x{:02x} is outside the documented 0x00..0x5C map".format(offset))
        return offset

    def read(self, offset):
        offset = self._validate_offset(offset)
        value = int(self._call(self._mmio.read, offset)) & _UINT32_MAX
        self.last_reads[offset] = value
        return value

    def write(self, offset, value):
        offset = self._validate_offset(offset)
        value = _uint32(value, "value")
        if (offset, value) not in ALLOWED_WRITES:
            raise OverlayControlError(
                "write 0x{:02x}=0x{:08x} is not permitted in this phase".format(offset, value))
        self._call(self._mmio.write, offset, value)
        self.writes += 1

    def _wait_sof(self):
        deadline = time.monotonic() + self.sof_timeout_s
        while True:
            status = self.read(REG_STATUS)
            if status & STATUS_COORD_ERROR:
                raise OverlayControlError(
                    "overlay reports coord_error before the first write")
            if status & STATUS_SOF_SEEN:
                return status
            if time.monotonic() >= deadline:
                raise OverlayControlError("overlay never reported a frame start (SOF)")
            time.sleep(self.poll_s)

    def _wait_frame_advance(self, previous):
        deadline = time.monotonic() + self.frame_timeout_s
        while True:
            current = self.read(REG_FRAME_COUNT)
            if current != previous:
                if current < previous:
                    raise OverlayControlError("frame counter moved backwards")
                return current
            if time.monotonic() >= deadline:
                raise OverlayControlError("frame counter did not advance")
            time.sleep(self.poll_s)

    def preflight(self):
        """Read-only checks required before the first write."""
        identity = self.read(REG_ID)
        if identity != ID_MCO1:
            raise OverlayControlError(
                "overlay ID mismatch: 0x{:08x}".format(identity))
        ctrl = self.read(REG_CTRL)
        if ctrl != CTRL_BYPASS:
            raise OverlayControlError(
                "overlay is not in the reset bypass state: CTRL=0x{:08x}".format(ctrl))
        status = self._wait_sof()
        thick = self.read(REG_LINE_THICK)
        if thick != 0:
            raise OverlayControlError(
                "LINE_THICK is not zero (0x{:x}); a line could become visible".format(thick))
        first = self.read(REG_FRAME_COUNT)
        second = self._wait_frame_advance(first)
        return dict(identity=identity, ctrl=ctrl, status=status,
                    line_thick=thick, frame_count_before=first,
                    frame_count_after=second)

    def first_write(self):
        """The only permitted write: CTRL=0x00000000, followed by readbacks."""
        if self.writes != 0:
            raise OverlayControlError("first_write may be used only once")
        before = self.preflight()
        self.write(REG_CTRL, 0x00000000)
        ctrl = self.read(REG_CTRL)
        if ctrl != 0:
            raise OverlayControlError(
                "CTRL readback after write is 0x{:08x}, expected 0".format(ctrl))
        frame_before = self.read(REG_FRAME_COUNT)
        frame_after = self._wait_frame_advance(frame_before)
        status = self.read(REG_STATUS)
        if status & STATUS_COORD_ERROR:
            raise OverlayControlError("overlay reported coord_error after the write")
        return dict(before=before, ctrl=ctrl, frame_count=frame_after,
                    status=status, writes=self.writes)