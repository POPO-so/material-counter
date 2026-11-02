"""Cold-start HDMI/VTC/VDMA bring-up for the JPEG candidate smoke.

The clock, VideoOut/monitor and VDMA-halt sequence is extracted from
``scripts/standalone/g1_run.py`` (``setup_hardware`` and ``snapshot``). The
G1/WO-0015 bitstream-transition logic (old RLE identity, previous-display SHA,
old-DMA halt) is deliberately not copied: this package starts from a cold
Overlay load of the reviewed JPEG candidate.

The module imports PYNQ only inside the functions so offline tests can use
fakes without a board runtime.
"""

from __future__ import annotations

import time

MONITOR_BASE = 0x43C40000
MONITOR_SIGNATURE = 0x4D435631
VIDEO_MODE = (1280, 720, 8, 60)
EXPECTED_CLOCKS = (50.0, 100.0, 142.857)
VDMA_MM2S_STATUS = 0x04
VDMA_HALTED_BIT = 0x1
VDMA_ERROR_MASK = 0xF0  # MM2S VDMA status bits 4..7


class DisplayBringupError(RuntimeError):
    """Display bring-up failed; ``display`` is set once the handle exists."""

    def __init__(self, message: str, display: "DisplayHandle | None" = None):
        super().__init__(message)
        self.display = display


def _snapshot(monitor) -> dict:
    """Read the frame monitor's atomic snapshot mailbox.

    Copied from ``g1_run.snapshot``; the write is a mailbox request, not a
    reset. ``scans`` advances once per VSYNC.
    """
    capture_started = time.monotonic()
    previous = int(monitor.read(0x0C))
    monitor.write(0x04, 1)
    deadline = time.monotonic() + 0.2
    while True:
        status, sequence = int(monitor.read(0x08)), int(monitor.read(0x0C))
        if status & 3 == 2 and sequence != previous:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("output monitor snapshot did not complete")
        time.sleep(0.0002)
    values = {
        name: int(monitor.read(offset)) | (int(monitor.read(offset + 4)) << 32)
        for name, offset in (
            ("unique", 0x10), ("scans", 0x18), ("repeat", 0x20),
            ("invalid", 0x28), ("partial", 0x30), ("frame", 0x48),
        )
    }
    values["sequence"] = sequence
    values["capture_started"] = capture_started
    values["sampled_monotonic"] = time.monotonic()
    return values


class DisplayHandle:
    """Handle for a prepared/started HDMI VideoOut and its MM2S VDMA."""

    def __init__(self, hdmi, vdma, monitor, clocks):
        self.hdmi = hdmi
        self.vdma = vdma
        self.monitor = monitor
        self.clocks = list(clocks)
        self.started = False
        self.closed = False
        self.vsync_snapshot = None

    def start(self, *, timeout_s: float = 2.0, video_module=None) -> "DisplayHandle":
        """Configure and start HDMI/VTC/VDMA, then prove VSYNC advances."""
        if self.started:
            raise DisplayBringupError("display was already started", self)
        if video_module is None:
            from pynq.lib.video import PIXEL_GRAY, VideoMode
            class _VideoModule:
                pass
            video_module = _VideoModule()
            video_module.PIXEL_GRAY = PIXEL_GRAY
            video_module.VideoMode = VideoMode
        try:
            self.hdmi.configure(
                video_module.VideoMode(*VIDEO_MODE), video_module.PIXEL_GRAY)
            self.hdmi.start()
        except BaseException as exc:
            raise DisplayBringupError(f"HDMI/VTC/VDMA start failed: {exc!r}", self) from exc
        self.started = True
        try:
            first = _snapshot(self.monitor)
            deadline = time.monotonic() + float(timeout_s)
            while time.monotonic() < deadline:
                current = _snapshot(self.monitor)
                if current["scans"] != first["scans"]:
                    self.vsync_snapshot = current
                    return self
                time.sleep(0.001)
        except BaseException as exc:
            raise DisplayBringupError(f"display VSYNC readback failed: {exc!r}", self) from exc
        raise DisplayBringupError(
            "display VSYNC did not advance after hdmi.start(); publish_overlay "
            "would block on cfg_ready", self)

    def dma_uncertain(self) -> bool:
        """True when the display VDMA is not proven halted/error-free."""
        try:
            status = int(self.vdma.read(VDMA_MM2S_STATUS)) & 0xFFFFFFFF
        except BaseException:
            return True
        return (status & VDMA_HALTED_BIT) == 0 or (status & VDMA_ERROR_MASK) != 0

    def stop(self, *, timeout_s: float = 1.0) -> None:
        """Controlled stop used only on normal completion or proven-idle failure."""
        if self.closed:
            return
        self.hdmi.close()
        deadline = time.monotonic() + float(timeout_s)
        while time.monotonic() < deadline:
            try:
                if int(self.vdma.read(VDMA_MM2S_STATUS)) & VDMA_HALTED_BIT:
                    self.closed = True
                    return
            except BaseException:
                pass
            time.sleep(0.001)
        raise DisplayBringupError("display VDMA halt was not proven after close", self)

    def state(self) -> dict:
        return {
            "started": self.started,
            "closed": self.closed,
            "clocks": list(self.clocks),
            "vsync": self.vsync_snapshot,
            "vdma_uncertain": self.dma_uncertain() if self.started else None,
        }


def prepare_display(overlay, *, expected_clocks=EXPECTED_CLOCKS, pynq_module=None) -> DisplayHandle:
    """Set FCLKs, resolve VideoOut/VDMA/monitor; do not start the VDMA yet."""
    if pynq_module is None:
        from pynq import Clocks, MMIO
        class _PynqModule:
            pass
        pynq_module = _PynqModule()
        pynq_module.Clocks = Clocks
        pynq_module.MMIO = MMIO
    clocks_api = pynq_module.Clocks
    clocks_api.fclk0_mhz = expected_clocks[0]
    clocks_api.fclk1_mhz = expected_clocks[1]
    clocks_api.fclk2_mhz = expected_clocks[2]
    clocks = [clocks_api.fclk0_mhz, clocks_api.fclk1_mhz, clocks_api.fclk2_mhz]
    if any(abs(a - b) > 0.15 for a, b in zip(clocks, expected_clocks)):
        raise DisplayBringupError(f"unexpected display clocks: {clocks}")
    hdmi = overlay.hdmi_out
    hdmi._vdma = overlay.axi_vdma  # Official VideoOut accepts an external VDMA.
    monitor = pynq_module.MMIO(MONITOR_BASE, 0x1000)
    if int(monitor.read(0)) != MONITOR_SIGNATURE:
        raise DisplayBringupError("output monitor signature mismatch")
    return DisplayHandle(hdmi, overlay.axi_vdma.mmio, monitor, clocks)