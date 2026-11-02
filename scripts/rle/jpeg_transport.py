"""Experimental raw-MJPEG to PL JPEG/RLE/FRG transport.

This module is isolated from the existing board service and RLE runner. It
fails closed: ambiguous DMA ownership holds both CMA buffers until externally
established safe; it never resets a channel or retries implicitly.
"""
from __future__ import annotations

import hashlib
import math
import numbers
import operator
import struct
import time
from typing import Any, Iterator

from .protocol import HEIGHT, MAX_RUNS, RX_CAPACITY, WIDTH, decode_packet
from .transport import (
    DDR_BASE, DDR_LIMIT, DMA_HALTED, DMA_IDLE, DMA_STATUS_ERROR_MASK,
    DMA_MM2S_ADDRESS, DMA_MM2S_LENGTH, DMA_MM2S_STATUS,
    DMA_S2MM_ADDRESS, DMA_S2MM_LENGTH, DMA_S2MM_STATUS, RLE_SIGNATURE,
)

JPEG_MAX_BYTES = 512000
MIN_RLE_PACKET_BYTES = (16 + (WIDTH + HEIGHT + 1) // 2 + 16) * 4
DMA_CONTROL_RS = 0x1
DMA_CONTROL_RESET = 0x4
DMA_STATUS_RESERVED_MASK = 0x8884
RLE_RAW_CAPABILITY = 0x70
RLE_RAW_CAPABILITY_VALUE = 0x52415731
RLE_INPUT_MODE = 0x74
JPEG_SIZE = 0x10
JPEG_FRAME_ID = 0x18
JPEG_STATUS = 0x20
JPEG_STATUS_VALID = 0x24
JPEG_AP_CTRL = 0x00
PREVIEW_STATUS = 0x0008
PREVIEW_CAPTURED_ID = 0x0018
PREVIEW_DISPLAYED_ID = 0x001C
PREVIEW_ACCEPT_ID = 0x0020
PREVIEW_MASK_ACCEPT = 0x0024
FRG_SIGNATURE = 0x46524731


def _uint(value: Any, name: str, maximum: int = 0xFFFFFFFF) -> int:
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise TypeError(f"{name} must be an integer")
    value = int(value)
    if not 0 <= value <= maximum:
        raise ValueError(f"{name} must be in 0..{maximum}")
    return value


def _buffer_span(buffer: Any, size: int, alignment: int, name: str) -> tuple[int, int]:
    try:
        physical = operator.index(buffer.physical_address)
        device = operator.index(buffer.device_address)
        nbytes = operator.index(buffer.nbytes)
    except (AttributeError, TypeError) as exc:
        raise ValueError(f"{name} buffer must expose addresses and nbytes") from exc
    if physical != device:
        raise ValueError(f"{name} physical and device addresses must match")
    if nbytes != size:
        raise ValueError(f"{name} buffer must be exactly {size} bytes")
    if physical < DDR_BASE or physical + size > DDR_LIMIT:
        raise ValueError(f"{name} buffer is outside the admitted DDR range")
    if physical % alignment:
        raise ValueError(f"{name} buffer must be {alignment}-byte aligned")
    return physical, physical + size


def _bytes_view(value: Any) -> memoryview:
    try:
        view = memoryview(value)
        if not view.contiguous:
            raise TypeError
        return view.cast("B")
    except (TypeError, ValueError) as exc:
        raise TypeError("JPEG frame must be contiguous bytes-like data") from exc


def preflight_jpeg_header(payload: Any) -> dict[str, Any]:
    """Parse a baseline JPEG header and reject shapes the PL decoder rejects.

    The fixed decoder accepts only a 640x400, 8-bit baseline, 4:2:2, single-scan,
    DRI=40 stream with a header of at most 4096 bytes. This PS preflight parses
    those same fields for an early, board-free rejection; the PL wrapper remains
    the authoritative validator of entropy and anything this pass cannot see.
    """
    view = memoryview(payload).cast("B")
    if view.nbytes < 4 or view[:2].tobytes() != b"\xff\xd8":
        raise ValueError("JPEG must start with SOI")
    pos = 2
    end = view.nbytes
    width = height = precision = dri = None
    sampling: list[tuple[int, int]] = []
    sof_seen = False
    scans = 0
    while pos + 4 <= end:
        if view[pos] != 0xFF:
            raise ValueError("JPEG marker alignment lost before SOS")
        marker = view[pos + 1]
        if marker == 0xFF:                          # fill byte
            pos += 1
            continue
        if marker in (0xD8, 0xD9):                  # SOI (repeat) / EOI
            pos += 2
            if marker == 0xD9:
                break
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:  # TEM / RSTn carry no length
            pos += 2
            continue
        if pos + 4 > end:
            raise ValueError("JPEG segment header is truncated")
        seg_len = (view[pos + 2] << 8) | view[pos + 3]
        if seg_len < 2 or pos + 2 + seg_len > end:
            raise ValueError("JPEG segment length is invalid")
        body = view[pos + 4: pos + 2 + seg_len]
        if marker == 0xC0:                          # SOF0 baseline
            if len(body) < 6:
                raise ValueError("SOF0 is truncated")
            precision = body[0]
            height = (body[1] << 8) | body[2]
            width = (body[3] << 8) | body[4]
            nf = body[5]
            if len(body) < 6 + 3 * nf:
                raise ValueError("SOF0 component table is truncated")
            sampling = [((body[6 + 3 * i + 1] >> 4) & 0xF, body[6 + 3 * i + 1] & 0xF)
                        for i in range(nf)]
            sof_seen = True
        elif marker in (0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            raise ValueError("JPEG must be baseline SOF0 (non-baseline SOF found)")
        elif marker == 0xDD:                        # DRI restart interval
            if len(body) < 2:
                raise ValueError("DRI is truncated")
            dri = (body[0] << 8) | body[1]
        elif marker == 0xDA:                        # SOS: entropy data follows
            scans += 1
            if scans > 1:
                raise ValueError("JPEG must be single-scan")
            break
        pos += 2 + seg_len
    if not sof_seen:
        raise ValueError("JPEG header has no baseline SOF0")
    if precision != 8 or width != WIDTH or height != HEIGHT:
        raise ValueError(f"JPEG must be 640x400 8-bit, got {width}x{height} precision {precision}")
    if len(sampling) != 3 or sampling[0] != (2, 1) or any(s != (1, 1) for s in sampling[1:]):
        raise ValueError(f"JPEG must be 4:2:2 YCbCr, got sampling {sampling}")
    if dri != 40:
        raise ValueError(f"JPEG DRI must be 40, got {dri}")
    if pos > 4096:
        raise ValueError(f"JPEG header exceeds 4096 bytes ({pos})")
    return {"width": width, "height": height, "precision": precision,
            "sampling": sampling, "dri": dri, "scans": scans, "header_bytes": pos}


def iter_raw_mjpeg(device: Any = 0, *, cv2_module: Any = None) -> Iterator[bytes]:
    """Yield unchanged V4L2 MJPG payloads requested at 640x400."""
    if cv2_module is None:
        import cv2 as cv2_module
    capture = cv2_module.VideoCapture(device, cv2_module.CAP_V4L2)
    if not capture.isOpened():
        capture.release()
        raise RuntimeError("cannot open V4L2 camera")
    try:
        settings = (
            (cv2_module.CAP_PROP_FOURCC, cv2_module.VideoWriter_fourcc(*"MJPG")),
            (cv2_module.CAP_PROP_FRAME_WIDTH, WIDTH),
            (cv2_module.CAP_PROP_FRAME_HEIGHT, HEIGHT),
            (cv2_module.CAP_PROP_CONVERT_RGB, 0),
        )
        for prop, value in settings:
            if not capture.set(prop, value):
                raise RuntimeError(f"V4L2 camera rejected property {prop}")
        actual_fourcc = int(round(capture.get(cv2_module.CAP_PROP_FOURCC)))
        actual_format = "".join(chr((actual_fourcc >> (8 * byte)) & 0xFF)
                                for byte in range(4))
        actual_width = int(round(capture.get(cv2_module.CAP_PROP_FRAME_WIDTH)))
        actual_height = int(round(capture.get(cv2_module.CAP_PROP_FRAME_HEIGHT)))
        if (actual_format != "MJPG" or actual_width != WIDTH or actual_height != HEIGHT):
            raise RuntimeError(
                f"V4L2 negotiated {actual_format!r} {actual_width}x{actual_height}; "
                f"expected MJPG {WIDTH}x{HEIGHT}"
            )
        while True:
            ok, encoded = capture.read()
            if not ok:
                return
            if encoded is None:
                raise RuntimeError("V4L2 returned an empty MJPG payload")
            payload = _bytes_view(encoded)
            if (payload.nbytes < 4 or payload[:2].tobytes() != b"\xff\xd8"
                    or payload[-2:].tobytes() != b"\xff\xd9"):
                raise RuntimeError("V4L2 did not return a complete JPEG packet")
            if payload.nbytes > JPEG_MAX_BYTES:
                raise RuntimeError(f"V4L2 JPEG packet exceeds {JPEG_MAX_BYTES} bytes")
            preflight_jpeg_header(payload)
            yield payload.tobytes()
    finally:
        capture.release()


class JpegRleDmaTransport:
    """Transfer compressed JPEG and accept a preview only after all gates pass."""

    def __init__(self, send_channel: Any, recv_channel: Any, dma_regs: Any,
                 rle_control: Any, jpeg_control: Any, preview_control: Any,
                 tx_buffer: Any, rx_buffer: Any, timeout_s: float = 5.0,
                 *, first_transfer_guard=None, rx_capture=None):
        if (isinstance(timeout_s, bool) or not isinstance(timeout_s, numbers.Real)
                or not math.isfinite(timeout_s) or timeout_s <= 0):
            raise ValueError("timeout_s must be a positive finite number")
        self.send_channel, self.recv_channel, self.dma_regs = send_channel, recv_channel, dma_regs
        self.rle_control, self.jpeg_control = rle_control, jpeg_control
        self.preview_control = preview_control
        self.tx_buffer, self.rx_buffer = tx_buffer, rx_buffer
        self.timeout_s = float(timeout_s)
        self.first_transfer_guard = first_transfer_guard
        self.rx_capture = rx_capture
        self.rx_capture_error = None
        self.rx_capture_decision = None
        self._rx_capture_done = False
        self._rx_diagnostic_done = False
        self.dma_may_be_active = False
        self._closed = False
        self._completed_frames = 0
        self._submitted_frames = 0
        self._raw_mode_configured = False
        self._last_frame_id: int | None = None
        self._pending_frame_id: int | None = None

    def _preflight(self) -> None:
        tx0, tx1 = _buffer_span(self.tx_buffer, JPEG_MAX_BYTES, 8, "TX")
        rx0, rx1 = _buffer_span(self.rx_buffer, RX_CAPACITY, 4, "RX")
        if tx0 < rx1 and rx0 < tx1:
            raise ValueError("TX and RX buffers overlap")
        if RX_CAPACITY > (1 << 19) - 1:
            raise ValueError("RX capacity exceeds the configured 19-bit DMA length")
        if int(self.rle_control.read(0x04)) != RLE_SIGNATURE:
            raise RuntimeError("RLE hardware signature mismatch")
        if int(self.rle_control.read(0x2C)) != (HEIGHT << 16) | WIDTH or int(self.rle_control.read(0x30)) != MAX_RUNS:
            raise RuntimeError("RLE geometry or capacity mismatch")
        rle_state = int(self.rle_control.read(0x08))
        if rle_state & 0x5:
            raise RuntimeError("RLE pipeline is busy or has a latched input error")
        if rle_state & 0x2 and self._completed_frames == 0:
            raise RuntimeError("RLE pipeline reports an unconsumed prior frame")
        jpeg_ctrl = int(self.jpeg_control.read(JPEG_AP_CTRL))
        if not jpeg_ctrl & 0x4:
            raise RuntimeError("JPEG core is not idle")
        actual_frg = int(self.preview_control.read(0x0200))
        if actual_frg != FRG_SIGNATURE:
            raise RuntimeError(
                f"overlay region-feature signature mismatch: read 0x{actual_frg:08x}, "
                f"expected FRG1 0x{FRG_SIGNATURE:08x}; a FRG2 bitstream uses a "
                f"different record layout that this transport does not implement")
        for offset, name, address_offset, length_offset in (
                (DMA_MM2S_STATUS, "MM2S", DMA_MM2S_ADDRESS, DMA_MM2S_LENGTH),
                (DMA_S2MM_STATUS, "S2MM", DMA_S2MM_ADDRESS, DMA_S2MM_LENGTH)):
            status = int(self.dma_regs.read(offset)) & 0xFFFFFFFF
            if status & DMA_STATUS_ERROR_MASK:
                raise RuntimeError(f"{name} DMA has error bits set")
            if status & (DMA_IDLE | DMA_HALTED):
                continue
            self._require_unsubmitted_first_transfer(
                name, status, address_offset, length_offset)

    def _require_unsubmitted_first_transfer(self, name: str,
                                            observed_status: int,
                                            address_offset: int,
                                            length_offset: int) -> None:
        """Allow Halted=0/Idle=0 only with ownership and unarmed proof.

        PYNQ starts both Direct-mode channels when the DMA driver is created,
        so before the first submission they read Halted=0, Idle=0. A real
        in-progress transfer has the same status, so the exception requires:
        this transport has never submitted, the production guard proves the
        exact channel/MMIO ownership, and the hardware address and length
        registers are still zero. Both the outer status and the guard status
        must be strictly zero; a transition to Idle/Halted during the proof is
        rejected rather than accepted as a normal idle state.
        """
        if self._submitted_frames:
            raise RuntimeError(f"{name} DMA is not idle")
        if observed_status != 0:
            raise RuntimeError(
                f"{name} DMA is not idle: first-transfer status is not zero")
        guard = self.first_transfer_guard
        if guard is None:
            raise RuntimeError(
                f"{name} DMA is not idle: no first-transfer ownership evidence")
        channel = self.send_channel if name == "MM2S" else self.recv_channel
        try:
            proof = guard(name, channel, self.dma_regs)
        except BaseException as exc:
            raise RuntimeError(
                f"{name} DMA is not idle: first-transfer proof failed: {exc}") from exc
        if not isinstance(proof, dict) or proof.get("owned") is not True:
            raise RuntimeError(
                f"{name} DMA is not idle: first-transfer ownership missing")
        control = int(proof.get("control", -1)) & 0xFFFFFFFF
        status = int(proof.get("status", -1)) & 0xFFFFFFFF
        address = int(proof.get("address", -1)) & 0xFFFFFFFF
        length = int(proof.get("length", -1)) & 0xFFFFFFFF
        if status & DMA_STATUS_ERROR_MASK:
            raise RuntimeError(f"{name} DMA has error bits set")
        if status & DMA_STATUS_RESERVED_MASK:
            raise RuntimeError(f"{name} DMA has reserved status bits set")
        if status != observed_status:
            raise RuntimeError(
                f"{name} DMA is not idle: state changed during first-transfer proof")
        if status != 0:
            raise RuntimeError(
                f"{name} DMA is not idle: first-transfer status is not zero")
        if not control & DMA_CONTROL_RS:
            raise RuntimeError(f"{name} DMA is not idle: driver is not running")
        if control & DMA_CONTROL_RESET:
            raise RuntimeError(f"{name} DMA is not idle: reset is asserted")
        if address != 0 or length != 0:
            raise RuntimeError(
                f"{name} DMA is not idle: committed first transfer")

    def _ensure_raw_input_mode(self) -> None:
        """Select and verify the pinned wrapper's raw-gray input mode.

        The candidate wrapper exposes RAW1 at 0x70 and selects the raw adapter
        with 0x74=1. The JPEG path is a raw gray stream, so the transport must
        not leave the default packetized adapter selected. This runs after the
        existing preflight and before TX writes, RLE/JPEG start, or DMA submit.
        """
        capability = int(self.rle_control.read(RLE_RAW_CAPABILITY)) & 0xFFFFFFFF
        if capability != RLE_RAW_CAPABILITY_VALUE:
            raise RuntimeError(
                f"RLE raw input capability missing: 0x{capability:08x}")
        if not self._raw_mode_configured:
            self.rle_control.write(RLE_INPUT_MODE, 1)
        mode = int(self.rle_control.read(RLE_INPUT_MODE)) & 0xFFFFFFFF
        if mode != 1:
            raise RuntimeError(
                f"RLE raw input mode readback mismatch: 0x{mode:08x}")
        self._raw_mode_configured = True

    def _capture_metadata(self, frame_id: int, packet: bytes,
                          dma_bytes: int, rle_bytes: int) -> dict:
        header = (list(struct.unpack_from("<16I", packet))
                  if len(packet) >= 64 else None)
        status = int(self.dma_regs.read(DMA_S2MM_STATUS)) & 0xFFFFFFFF
        da = int(self.dma_regs.read(DMA_S2MM_ADDRESS)) & 0xFFFFFFFF
        physical = int(self.rx_buffer.physical_address)
        return {
            "frame_id": frame_id,
            "length": len(packet),
            "sha256": hashlib.sha256(packet).hexdigest(),
            "head_hex": packet[:64].hex(),
            "tail_hex": packet[-64:].hex() if len(packet) >= 64 else packet.hex(),
            "header_words": header,
            "dma_bytes": dma_bytes,
            "rle_bytes": rle_bytes,
            "rx_physical_address": physical,
            "rx_device_address": int(self.rx_buffer.device_address),
            "rx_nbytes": int(self.rx_buffer.nbytes),
            "hardware_s2mm_da": da,
            "hardware_s2mm_da_msb":
                int(self.dma_regs.read(DMA_S2MM_ADDRESS + 4)) & 0xFFFFFFFF,
            "hardware_s2mm_length":
                int(self.dma_regs.read(DMA_S2MM_LENGTH)) & 0xFFFFFFFF,
            "hardware_s2mm_status": status,
            "physical_matches_da": physical == da,
            "s2mm_idle": bool(status & DMA_IDLE),
            "completion_state": {
                "dma_length_matches_rle": dma_bytes == rle_bytes,
                "rle_state": int(self.rle_control.read(0x08)) & 0xFFFFFFFF,
                "rle_actual_length": int(self.rle_control.read(0x0C)) & 0xFFFFFFFF,
            },
            "raw_capability": int(self.rle_control.read(RLE_RAW_CAPABILITY)) & 0xFFFFFFFF,
            "input_mode": int(self.rle_control.read(RLE_INPUT_MODE)) & 0xFFFFFFFF,
        }

    def _capture_rx(self, frame_id: int, packet: bytes,
                    dma_bytes: int, rle_bytes: int) -> None:
        """Capture the exact bytes handed to decode_packet; never mask errors."""
        if self.rx_capture is None:
            self.rx_capture_decision = {
                "attempted": False, "reason": "no capture callback"}
            return
        if self._rx_capture_done:
            return
        # One attempt per transport: mark it before any metadata read or write
        # so a failed first attempt is never retried on a later frame.
        self._rx_capture_done = True
        try:
            metadata = self._capture_metadata(frame_id, packet, dma_bytes, rle_bytes)
            self.rx_capture(frame_id, packet, metadata)
            self.rx_capture_decision = {
                "attempted": True, "captured": True, "diagnostic": False}
        except Exception as exc:
            # Capture must never replace the decode/transport exception or
            # alter the HOLD decision. KeyboardInterrupt/SystemExit are not
            # Exception subclasses and propagate to the existing main flow.
            self.rx_capture_error = repr(exc)
            self.rx_capture_decision = {
                "attempted": True, "captured": False, "diagnostic": False,
                "reason": repr(exc)}

    def _diagnostic_capture_facts(self) -> tuple[dict, str | None]:
        """Read endpoint state and decide whether an RX read is safe.

        Reading is allowed only after the write side has stopped: both DMA
        channels Idle with no error bits, RLE not busy, packet_done observed,
        and the S2MM/RLE lengths equal, aligned and inside the RX buffer.
        """
        mm2s = int(self.dma_regs.read(DMA_MM2S_STATUS)) & 0xFFFFFFFF
        s2mm = int(self.dma_regs.read(DMA_S2MM_STATUS)) & 0xFFFFFFFF
        rle_state = int(self.rle_control.read(0x08)) & 0xFFFFFFFF
        dma_bytes = int(self.dma_regs.read(DMA_S2MM_LENGTH)) & 0xFFFFFFFF
        rle_bytes = int(self.rle_control.read(0x0C)) & 0xFFFFFFFF
        facts = {"mm2s_status": mm2s, "s2mm_status": s2mm,
                 "rle_state": rle_state, "dma_bytes": dma_bytes,
                 "rle_bytes": rle_bytes}
        if mm2s & DMA_STATUS_ERROR_MASK:
            return facts, "MM2S DMA error bits set"
        if s2mm & DMA_STATUS_ERROR_MASK:
            return facts, "S2MM DMA error bits set"
        if not mm2s & DMA_IDLE:
            return facts, "MM2S DMA is not idle"
        if not s2mm & DMA_IDLE:
            return facts, "S2MM DMA is not idle"
        if rle_state & 0x1:
            return facts, "RLE pipeline is busy"
        if not rle_state & 0x2:
            return facts, "RLE packet_done was not observed"
        if dma_bytes != rle_bytes:
            return facts, "DMA and RLE lengths disagree"
        if (dma_bytes % 4 or
                not MIN_RLE_PACKET_BYTES <= dma_bytes <= RX_CAPACITY):
            return facts, "DMA length is outside the packet bounds"
        if int(self.rx_buffer.nbytes) < dma_bytes:
            return facts, "DMA length exceeds the RX buffer"
        return facts, None

    def _capture_rx_diagnostic(self, frame_id: int, exc: Exception) -> None:
        """Capture an error frame only after the endpoint is proven quiescent.

        The original completion exception is always re-raised by the caller;
        this path never accepts a frame, publishes, retries, or releases.
        """
        if self.rx_capture is None:
            self.rx_capture_decision = {
                "attempted": False, "reason": "no capture callback"}
            return
        if self._rx_diagnostic_done:
            return
        self._rx_diagnostic_done = True
        decision = {"attempted": True, "captured": False, "diagnostic": True,
                    "original_error": repr(exc),
                    "original_error_type": type(exc).__name__}
        try:
            facts, reason = self._diagnostic_capture_facts()
            decision.update(facts)
            if reason is not None:
                decision["reason"] = reason
                self.rx_capture_decision = decision
                return
            sync_rx = getattr(self.rx_buffer, "sync_from_device", None)
            if not callable(sync_rx):
                decision["reason"] = "RX buffer has no sync_from_device method"
                self.rx_capture_decision = decision
                return
            sync_rx()
            dma_bytes = facts["dma_bytes"]
            packet = bytes(memoryview(self.rx_buffer).cast("B")[:dma_bytes])
            metadata = self._capture_metadata(
                frame_id, packet, dma_bytes, facts["rle_bytes"])
            metadata.update({
                "diagnostic": True,
                "frame_accepted": False,
                "original_error": repr(exc),
                "original_error_type": type(exc).__name__,
                "safe_capture": dict(facts),
            })
            self.rx_capture(frame_id, packet, metadata)
            decision["captured"] = True
            decision["length"] = dma_bytes
            self.rx_capture_decision = decision
        except Exception as caught:
            self.rx_capture_error = repr(caught)
            decision["reason"] = repr(caught)
            self.rx_capture_decision = decision

    def _wait_for_completion(self) -> tuple[int, int]:
        deadline = time.monotonic() + self.timeout_s
        jpeg_valid = False
        jpeg_done = False
        while True:
            mm2s = int(self.dma_regs.read(DMA_MM2S_STATUS))
            s2mm = int(self.dma_regs.read(DMA_S2MM_STATUS))
            if mm2s & DMA_STATUS_ERROR_MASK:
                raise RuntimeError(f"MM2S DMA error: 0x{mm2s:08x}")
            if s2mm & DMA_STATUS_ERROR_MASK:
                raise RuntimeError(f"S2MM DMA error: 0x{s2mm:08x}")
            rle_state = int(self.rle_control.read(0x08))
            jpeg_ctrl = int(self.jpeg_control.read(JPEG_AP_CTRL))
            # AP_CTRL ap_done is clear-on-read; retain the observation while
            # the downstream RLE and DMA stages finish.
            jpeg_done = jpeg_done or bool(jpeg_ctrl & 0x2)
            # status_ap_vld is read-to-clear; do not read the status data until
            # this fresh post-start valid indication has been observed.
            valid = bool(int(self.jpeg_control.read(JPEG_STATUS_VALID)) & 1)
            jpeg_valid = jpeg_valid or valid
            if ((mm2s & DMA_IDLE) and (s2mm & DMA_IDLE) and
                    (rle_state & 0x2) and not (rle_state & 0x5) and
                    jpeg_done and jpeg_valid):
                return int(self.dma_regs.read(DMA_S2MM_LENGTH)), int(self.rle_control.read(0x0C))
            if time.monotonic() >= deadline:
                raise TimeoutError("JPEG/RLE/DMA completion timed out")
            time.sleep(min(0.001, max(0.0001, self.timeout_s / 100)))

    def _wait_preview(self, frame_id: int, dropped_before: int) -> None:
        deadline = time.monotonic() + self.timeout_s
        while True:
            status = int(self.preview_control.read(PREVIEW_STATUS))
            frg_state = int(self.preview_control.read(0x0204))
            frg_id = int(self.preview_control.read(0x0208))
            dropped = int(self.preview_control.read(0x0214))
            captured_id = int(self.preview_control.read(PREVIEW_CAPTURED_ID))
            if dropped != dropped_before:
                raise RuntimeError("FRG dropped counter changed during frame")
            # FRG status bits are frame-scoped. Until its ID advances, even
            # overflow/busy/done describe the previous frame and are stale.
            if frg_id == frame_id:
                if frg_state & 0x2:
                    raise RuntimeError("FRG overflow")
                if not (frg_state & 0x4) and frg_state & 1:
                    if status & (1 << 3) and captured_id == frame_id:
                        return
            if time.monotonic() >= deadline:
                raise TimeoutError("FRG or preview capture completion timed out")
            time.sleep(min(0.001, max(0.0001, self.timeout_s / 100)))

    def transfer(self, jpeg: Any, frame_id: int, *, params_version: int = 0,
                 roi: tuple[int, int, int, int] = (0, 0, WIDTH, HEIGHT),
                 threshold: int = 128, intensity_offset: int = 0,
                 morph_mode: int = 0, auto_accept: bool = True):
        if self._closed:
            raise RuntimeError("JPEG transport is closed")
        if not isinstance(auto_accept, bool):
            raise TypeError("auto_accept must be bool")
        if self._pending_frame_id is not None:
            raise RuntimeError("previous completed frame has not been published")
        if self.dma_may_be_active:
            raise RuntimeError("DMA ownership uncertain; automatic retry is forbidden")
        frame_id = _uint(frame_id, "frame_id")
        if self._last_frame_id is not None and frame_id <= self._last_frame_id:
            raise ValueError("frame IDs must strictly increase")
        params_version = _uint(params_version, "params_version")
        view = _bytes_view(jpeg)
        length = view.nbytes
        if not length or length > JPEG_MAX_BYTES:
            raise ValueError("JPEG size must be in 1..512000 bytes")
        if length < 4 or bytes(view[:2]) != b"\xff\xd8" or bytes(view[-2:]) != b"\xff\xd9":
            raise ValueError("JPEG must have SOI and EOI markers")
        if (len(roi) != 4 or any(isinstance(v, bool) or not isinstance(v, numbers.Integral) for v in roi)
                or not (0 <= roi[0] < roi[2] <= WIDTH and 0 <= roi[1] < roi[3] <= HEIGHT)):
            raise ValueError("roi must be a valid nonempty rectangle in the 640x400 frame")
        threshold = _uint(threshold, "threshold", 255)
        if isinstance(intensity_offset, bool) or not isinstance(intensity_offset, numbers.Integral):
            raise TypeError("intensity_offset must be an integer")
        intensity_offset = int(intensity_offset)
        if not -255 <= intensity_offset <= 255:
            raise ValueError("intensity_offset must be in -255..255")
        morph_mode = _uint(morph_mode, "morph_mode", 2)
        self._preflight()
        self._ensure_raw_input_mode()
        self.tx_buffer[:length] = view
        sync_tx = getattr(self.tx_buffer, "sync_to_device", None)
        if not callable(sync_tx):
            raise RuntimeError("TX buffer has no sync_to_device method")
        sync_tx()
        # Snapshot drop count and clear any stale HLS valid indication before start.
        dropped_before = int(self.preview_control.read(0x0214))
        self.jpeg_control.read(JPEG_STATUS_VALID)
        self.jpeg_control.write(JPEG_SIZE, length)
        self.jpeg_control.write(JPEG_FRAME_ID, frame_id)
        if int(self.jpeg_control.read(JPEG_SIZE)) != length or int(self.jpeg_control.read(JPEG_FRAME_ID)) != frame_id:
            raise RuntimeError("JPEG size/frame ID readback mismatch")
        rle_values = {
            0x10: frame_id, 0x14: params_version,
            0x18: (roi[1] << 16) | roi[0], 0x1C: (roi[3] << 16) | roi[2],
            0x20: threshold, 0x24: intensity_offset & 0xFFFFFFFF, 0x28: morph_mode,
        }
        for offset, value in rle_values.items(): self.rle_control.write(offset, value)
        for offset, value in rle_values.items():
            if (int(self.rle_control.read(offset)) & 0xFFFFFFFF) != value:
                raise RuntimeError(f"RLE configuration readback mismatch at 0x{offset:02x}")
        # Mark ownership uncertain before the first operation that can arm/start DMA.
        # Once this point is reached, a start may occur; never reuse this ID even
        # if arming or any subsequent hardware transaction fails.
        self._last_frame_id = frame_id
        self.dma_may_be_active = True
        self._submitted_frames += 1
        self.recv_channel.transfer(self.rx_buffer, nbytes=RX_CAPACITY)
        self.rle_control.write(0x00, 1)
        self.jpeg_control.write(JPEG_AP_CTRL, 1)
        self.send_channel.transfer(self.tx_buffer, nbytes=length)
        try:
            dma_bytes, rle_bytes = self._wait_for_completion()
        except Exception as exc:
            self._capture_rx_diagnostic(frame_id, exc)
            raise
        if dma_bytes != rle_bytes:
            raise RuntimeError("RLE and S2MM actual lengths disagree")
        if not 32 * 4 <= dma_bytes <= RX_CAPACITY or dma_bytes % 4:
            raise RuntimeError("RLE packet length is invalid")
        self.send_channel.wait()
        self.recv_channel.wait()
        self.dma_may_be_active = False
        self._completed_frames += 1
        sync_rx = getattr(self.rx_buffer, "sync_from_device", None)
        if not callable(sync_rx):
            raise RuntimeError("RX buffer has no sync_from_device method")
        sync_rx()
        jpeg_status = int(self.jpeg_control.read(JPEG_STATUS)) & 0xFFFFFFFF
        if jpeg_status:
            raise RuntimeError(f"JPEG status error: 0x{jpeg_status:08x}")
        packet = bytes(memoryview(self.rx_buffer).cast("B")[:dma_bytes])
        self._capture_rx(frame_id, packet, dma_bytes, rle_bytes)
        frame = decode_packet(packet, expected_frame_id=frame_id,
                              expected_params_version=params_version)
        if not frame.valid or frame.status != 0:
            raise RuntimeError("RLE packet status is nonzero or invalid")
        if (frame.roi != tuple(roi) or frame.threshold != threshold or
                frame.intensity_offset != intensity_offset or frame.morph_mode != morph_mode):
            raise ValueError("RLE packet parameters do not match request")
        self._wait_preview(frame_id, dropped_before)
        self._pending_frame_id = frame_id
        if auto_accept:
            self.accept_mask(frame_id)
        return frame

    def accept_mask(self, frame_id: int) -> None:
        """Publish the already validated mask after its matching metadata is ready."""
        frame_id = _uint(frame_id, "frame_id")
        if self._closed or self.dma_may_be_active:
            raise RuntimeError("cannot publish while transport is closed or DMA ownership is active")
        if self._pending_frame_id != frame_id:
            raise ValueError("mask acceptance does not match the pending completed frame")
        self.preview_control.write(PREVIEW_ACCEPT_ID, frame_id)
        self.preview_control.write(PREVIEW_MASK_ACCEPT, 1)
        deadline = time.monotonic() + self.timeout_s
        while True:
            status = int(self.preview_control.read(PREVIEW_STATUS))
            displayed_id = int(self.preview_control.read(PREVIEW_DISPLAYED_ID))
            if status & (1 << 4) and displayed_id == frame_id:
                self._pending_frame_id = None
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("preview mask display timed out")
            time.sleep(min(0.001, max(0.0001, self.timeout_s / 100)))

    def publish_overlay(self, frame_id: int, boxes: Any, *,
                        line_x: int, enabled: bool = True) -> None:
        """Commit line and boxes for the pending frame before mask accept.

        The running total count is intentionally not written here: neither the
        fixed c5a8680 overlay nor the latest ``video_binary_overlay_bd.v`` map has
        a total_count digit register, so a 0x44 write returns SLVERR and fails the
        readback. Displaying the count digit requires a new RTL register plus a
        glyph renderer; that contract is not implemented yet (pending RTL change).
        """
        frame_id = _uint(frame_id, "frame_id")
        line_x = _uint(line_x, "line_x")
        if line_x >= WIDTH:
            raise ValueError("line_x is outside the source image")
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be bool")
        if self._closed or self.dma_may_be_active:
            raise RuntimeError("cannot configure overlay while transport is closed or DMA ownership is active")
        if self._pending_frame_id != frame_id:
            raise ValueError("overlay configuration does not match the pending completed frame")
        boxes = list(boxes)
        if len(boxes) > 16:
            raise ValueError("at most 16 overlay boxes are supported")
        checked = []
        for box in boxes:
            if not isinstance(box, (tuple, list)) or len(box) != 4:
                raise ValueError("each box must be (x0, y0, x1, y1)")
            x0, y0, x1, y1 = (_uint(v, "box coordinate") for v in box)
            if not (x0 <= x1 < WIDTH and y0 <= y1 < HEIGHT):
                raise ValueError("overlay box is outside the source image")
            checked.append((x0, y0, x1, y1))

        deadline = time.monotonic() + self.timeout_s
        while not (int(self.preview_control.read(PREVIEW_STATUS)) & 1):
            if time.monotonic() >= deadline:
                raise TimeoutError("overlay configuration mailbox did not become ready")
            time.sleep(min(0.001, max(0.0001, self.timeout_s / 100)))

        values = ((0x30, int(enabled)), (0x34, frame_id), (0x38, len(checked)),
                  (0x3C, line_x))
        for offset, value in values:
            self.preview_control.write(offset, value)
        for offset, value in values:
            if (int(self.preview_control.read(offset)) & 0xFFFFFFFF) != value:
                raise RuntimeError(f"overlay configuration readback mismatch at 0x{offset:02x}")
        for index, (x0, y0, x1, y1) in enumerate(checked):
            x_word, y_word = x0 | (x1 << 16), y0 | (y1 << 16)
            self.preview_control.write(0x100 + 8 * index, x_word)
            self.preview_control.write(0x104 + 8 * index, y_word)
            if (int(self.preview_control.read(0x100 + 8 * index)) != x_word or
                    int(self.preview_control.read(0x104 + 8 * index)) != y_word):
                raise RuntimeError(f"overlay box {index} readback mismatch")
        self.preview_control.write(0x40, 1)

        # cfg_ready only rises again after the complete mailbox payload has
        # crossed into pixel_clk and has been latched at a VSYNC boundary.
        while not (int(self.preview_control.read(PREVIEW_STATUS)) & 1):
            if time.monotonic() >= deadline:
                raise TimeoutError("overlay configuration commit timed out")
            time.sleep(min(0.001, max(0.0001, self.timeout_s / 100)))
        self.accept_mask(frame_id)

    def close(self) -> None:
        if self._closed: return
        if self.dma_may_be_active:
            raise RuntimeError("DMA-owned buffers cannot be freed while ownership is uncertain")
        for buffer in (self.tx_buffer, self.rx_buffer):
            free = getattr(buffer, "freebuffer", None)
            if not callable(free): raise RuntimeError("DMA buffer has no freebuffer method")
            free()
        self._closed = True
