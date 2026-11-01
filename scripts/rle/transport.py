"""Fail-closed, single-frame simple-mode DMA transport for RLE_PACKET_V1."""
from __future__ import annotations

import numbers
import operator
import math
import time
from typing import Any

import numpy as np

from .protocol import (
    HEIGHT, MAX_RUNS, RX_CAPACITY, TX_BYTES, WIDTH, decode_packet,
    encode_gray_into,
)


RLE_SIGNATURE = 0x31454C52
GEOMETRY = (HEIGHT << 16) | WIDTH
DMA_MM2S_STATUS = 0x04
DMA_MM2S_ADDRESS = 0x18
DMA_MM2S_LENGTH = 0x28
DMA_S2MM_STATUS = 0x34
DMA_S2MM_ADDRESS = 0x48
DMA_S2MM_LENGTH = 0x58
DMA_STATUS_ERROR_MASK = 0x770
DMA_IDLE = 0x2
DMA_HALTED = 0x1
DDR_BASE = 0x00100000
DDR_LIMIT = 0x20000000

_CONFIG_REGISTERS = (
    (0x10, "frame_id"),
    (0x14, "params_version"),
    (0x18, "roi_start"),
    (0x1C, "roi_end"),
    (0x20, "threshold"),
    (0x24, "intensity_offset"),
    (0x28, "morph_mode"),
)


def _uint(value: Any, name: str, maximum: int = 0xFFFFFFFF) -> int:
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise TypeError(f"{name} must be an integer")
    value = int(value)
    if value < 0 or value > maximum:
        raise ValueError(f"{name} must be in 0..{maximum}")
    return value


def _roi_word(value: Any) -> tuple[tuple[int, int, int, int], int, int]:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise ValueError("roi must be (x0, y0, x1, y1)")
    x0, y0, x1, y1 = (_uint(v, "roi coordinate", 0xFFFF) for v in value)
    if x0 >= x1 or y0 >= y1 or x1 > WIDTH or y1 > HEIGHT:
        raise ValueError("roi must be a non-empty rectangle inside the frame")
    return (x0, y0, x1, y1), (y0 << 16) | x0, (y1 << 16) | x1


def _buffer_address(buffer: Any, size: int, alignment: int, name: str) -> tuple[int, int]:
    try:
        address = operator.index(buffer.physical_address)
        device_address = operator.index(buffer.device_address)
        actual_size = operator.index(buffer.nbytes)
    except (AttributeError, TypeError) as exc:
        raise ValueError(f"{name} must expose physical/device addresses and nbytes") from exc
    if isinstance(buffer.physical_address, bool) or isinstance(buffer.device_address, bool):
        raise ValueError(f"{name} DMA addresses must be integers")
    if address != device_address:
        raise ValueError(f"{name} device_address differs from physical_address")
    if actual_size != size:
        raise ValueError(f"{name} buffer must be exactly {size} bytes")
    if address < DDR_BASE or address + size > DDR_LIMIT:
        raise ValueError(f"{name} DMA span is outside the admitted HP DDR range")
    if address % alignment:
        raise ValueError(f"{name} DMA address must be {alignment}-byte aligned")
    return address, address + size


class RleDmaTransport:
    """Transfer one 640x400 frame and parse only S2MM's measured byte count.

    The caller retains ownership of both CMA buffers until ``close``. If a DMA
    start, timeout, or ambiguous status leaves ownership uncertain, close refuses
    to release either buffer. There is no implicit retry or channel reset.
    """

    def __init__(self, send_channel: Any, recv_channel: Any,
                 dma_regs: Any, control: Any, tx_buffer: Any,
                 rx_buffer: Any, timeout_s: float = 5.0):
        if (isinstance(timeout_s, bool) or not isinstance(timeout_s, numbers.Real)
                or not math.isfinite(timeout_s) or timeout_s <= 0):
            raise ValueError("timeout_s must be a positive number")
        self.send_channel = send_channel
        self.recv_channel = recv_channel
        self.dma_regs = dma_regs
        self.control = control
        self.tx_buffer = tx_buffer
        self.rx_buffer = rx_buffer
        self.timeout_s = float(timeout_s)
        self.dma_may_be_active = False
        self._closed = False
        self._completed_frames = 0
        self._last_snapshot: dict[str, int] | None = None
        self.last_rx_bytes = 0

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("RLE DMA transport is closed")
        if self.dma_may_be_active:
            raise RuntimeError("DMA ownership is uncertain; automatic retry is forbidden")

    def _preflight_buffers(self) -> None:
        tx_start, tx_end = _buffer_address(self.tx_buffer, TX_BYTES, 8, "TX")
        rx_start, rx_end = _buffer_address(self.rx_buffer, RX_CAPACITY, 4, "RX")
        if tx_start < rx_end and rx_start < tx_end:
            raise ValueError("TX and RX DMA spans overlap")
        if MAX_RUNS > 0xFFFFFFFF or RX_CAPACITY > (1 << 18) - 1:
            raise ValueError("RLE output capacity exceeds the configured DMA length width")

    def _check_channel(self, status: int, name: str, address_register: int,
                       length_register: int) -> None:
        if status & DMA_STATUS_ERROR_MASK:
            raise RuntimeError(f"{name} DMA has error bits set: 0x{status:08x}")
        if status & (DMA_IDLE | DMA_HALTED):
            return
        fresh = all(self.dma_regs.read(register) == 0 for register in
                    (DMA_MM2S_ADDRESS, DMA_MM2S_LENGTH,
                     DMA_S2MM_ADDRESS, DMA_S2MM_LENGTH))
        if fresh and status == 0:
            return
        raise RuntimeError(f"{name} DMA is active before transfer: 0x{status:08x}")

    def _preflight_hardware(self) -> None:
        signature = int(self.control.read(0x04))
        geometry = int(self.control.read(0x2C))
        max_runs = int(self.control.read(0x30))
        if signature != RLE_SIGNATURE:
            raise RuntimeError("RLE hardware signature mismatch")
        if geometry != GEOMETRY or max_runs != MAX_RUNS:
            raise RuntimeError("RLE hardware geometry or run capacity mismatch")
        state = int(self.control.read(0x08))
        if state & 0x5:
            raise RuntimeError("RLE pipeline is busy or has a latched input error")
        if state & 0x2 and self._completed_frames == 0:
            raise RuntimeError("RLE pipeline reports an unconsumed prior frame")
        self._check_channel(int(self.dma_regs.read(DMA_MM2S_STATUS)), "MM2S",
                            DMA_MM2S_ADDRESS, DMA_MM2S_LENGTH)
        self._check_channel(int(self.dma_regs.read(DMA_S2MM_STATUS)), "S2MM",
                            DMA_S2MM_ADDRESS, DMA_S2MM_LENGTH)

    def _configure(self, frame_id: int, params_version: int,
                   roi: tuple[int, int, int, int], start_word: int,
                   end_word: int, threshold: int, offset: int,
                   morph_mode: int) -> None:
        values = {
            0x10: frame_id, 0x14: params_version,
            0x18: start_word, 0x1C: end_word,
            0x20: threshold, 0x24: offset & 0xFFFFFFFF,
            0x28: morph_mode,
        }
        for offset_reg, value in values.items():
            self.control.write(offset_reg, value)
        for offset_reg, value in values.items():
            actual = int(self.control.read(offset_reg)) & 0xFFFFFFFF
            if actual != value:
                raise RuntimeError(f"RLE configuration readback mismatch at 0x{offset_reg:02x}")
        # Cross-check packed geometry to catch a malformed ROI readback.
        if int(self.control.read(0x18)) != start_word or int(self.control.read(0x1C)) != end_word:
            raise RuntimeError("RLE ROI readback mismatch")

    @staticmethod
    def _mark(timing, name):
        if timing is not None:
            timing[name] = time.monotonic()

    def _wait_complete(self, timing=None) -> tuple[int, int, int]:
        deadline = time.monotonic() + self.timeout_s
        polls = 0
        last_poll = time.monotonic()
        max_gap = 0.0
        while True:
            now = time.monotonic()
            if polls:
                gap = now - last_poll
                if gap > max_gap:
                    max_gap = gap
            last_poll = now
            polls += 1
            mm2s = int(self.dma_regs.read(DMA_MM2S_STATUS))
            s2mm = int(self.dma_regs.read(DMA_S2MM_STATUS))
            state = int(self.control.read(0x08))
            if mm2s & DMA_STATUS_ERROR_MASK:
                raise RuntimeError(f"MM2S DMA failed: 0x{mm2s:08x}")
            if s2mm & DMA_STATUS_ERROR_MASK:
                raise RuntimeError(f"S2MM DMA failed: 0x{s2mm:08x}")
            if (mm2s & DMA_IDLE) and (s2mm & DMA_IDLE) and (state & 0x2) and not (state & 0x1):
                actual_dma = int(self.dma_regs.read(DMA_S2MM_LENGTH))
                actual_hw = int(self.control.read(0x0C))
                if timing is not None:
                    timing["poll_count"] = polls
                    timing["poll_max_gap_ms"] = max_gap * 1000.0
                    timing["poll_total_ms"] = (time.monotonic() - (deadline - self.timeout_s)) * 1000.0
                return actual_dma, actual_hw, state
            if time.monotonic() >= deadline:
                if timing is not None:
                    timing["poll_count"] = polls
                    timing["poll_max_gap_ms"] = max_gap * 1000.0
                raise TimeoutError("RLE DMA transfer did not complete before timeout")
            time.sleep(min(0.001, max(0.0001, self.timeout_s / 100)))

    def transfer(self, gray: Any, frame_id: int, *, params_version: int = 0,
                 roi: tuple[int, int, int, int] = (0, 0, WIDTH, HEIGHT),
                 threshold: int = 128, intensity_offset: int = 0,
                 morph_mode: int = 0, timing=None):
        self._mark(timing, "enter")
        self._ensure_open()
        self.last_rx_bytes = 0
        frame_id = _uint(frame_id, "frame_id")
        params_version = _uint(params_version, "params_version")
        roi, roi_start, roi_end = _roi_word(roi)
        threshold = _uint(threshold, "threshold", 255)
        if isinstance(intensity_offset, bool) or not isinstance(intensity_offset, numbers.Integral):
            raise TypeError("intensity_offset must be an integer")
        intensity_offset = int(intensity_offset)
        if not -255 <= intensity_offset <= 255:
            raise ValueError("intensity_offset must be in -255..255")
        morph_mode = _uint(morph_mode, "morph_mode", 2)

        # Validate addresses and the exact hardware identity before touching DMA.
        self._preflight_buffers()
        self._preflight_hardware()
        self._mark(timing, "preflighted")
        encode_gray_into(gray, self.tx_buffer, frame_id=frame_id)
        self._mark(timing, "packed")
        sync_to = getattr(self.tx_buffer, "sync_to_device", None)
        if callable(sync_to):
            sync_to()
        else:
            flush = getattr(self.tx_buffer, "flush", None)
            if not callable(flush):
                raise RuntimeError("TX buffer has no sync_to_device or flush method")
            flush()
        self._mark(timing, "encoded")

        self._configure(frame_id, params_version, roi, roi_start, roi_end,
                        threshold, intensity_offset, morph_mode)
        self._mark(timing, "configured")
        # Set before the first channel start. Any exception afterwards keeps both
        # buffers held until an operator can establish that DMA ownership ended.
        self.dma_may_be_active = True
        self.recv_channel.transfer(self.rx_buffer, nbytes=RX_CAPACITY)
        self.control.write(0x00, 1)
        self.send_channel.transfer(self.tx_buffer, nbytes=TX_BYTES)
        self._mark(timing, "started")

        dma_actual, hw_actual, state = self._wait_complete(timing)
        self._mark(timing, "waited")
        if self.send_channel is not None:
            self.send_channel.wait()
        self.recv_channel.wait()
        self._mark(timing, "dma_done")
        # Both channels and PL have completed. Later format/identity errors do
        # not imply the hardware still owns CMA storage.
        self.dma_may_be_active = False
        self._completed_frames += 1
        sync_from = getattr(self.rx_buffer, "sync_from_device", None)
        if callable(sync_from):
            sync_from()
        else:
            invalidate = getattr(self.rx_buffer, "invalidate", None)
            if not callable(invalidate):
                raise RuntimeError("RX buffer has no sync_from_device or invalidate method")
            invalidate()
        self._mark(timing, "synced")

        if dma_actual != hw_actual:
            raise RuntimeError(f"RLE actual length differs between DMA ({dma_actual}) and hardware ({hw_actual})")
        if dma_actual % 4 or not 32 * 4 <= dma_actual <= RX_CAPACITY:
            raise RuntimeError(f"RLE DMA returned an invalid actual length: {dma_actual}")
        self.last_rx_bytes = dma_actual
        self._last_snapshot = dict(mm2s_status=int(self.dma_regs.read(DMA_MM2S_STATUS)),
                                   s2mm_status=int(self.dma_regs.read(DMA_S2MM_STATUS)),
                                   actual_dma_bytes=dma_actual, actual_hw_bytes=hw_actual,
                                   pipeline_state=state)
        packet = memoryview(self.rx_buffer).cast("B")[:dma_actual]
        frame = decode_packet(packet, expected_frame_id=frame_id,
                              expected_params_version=params_version)
        if (frame.roi != roi or frame.threshold != threshold
                or frame.intensity_offset != intensity_offset
                or frame.morph_mode != morph_mode):
            raise ValueError("RLE packet configuration metadata does not match the request")

        self._mark(timing, "decoded")
        return frame

    def transfer_timed(self, gray: Any, frame_id: int, timing):
        """transfer() with a required timing sink (positional for DriverOwner.call)."""
        return self.transfer(gray, frame_id, timing=timing)

    def snapshot(self) -> dict[str, int] | None:
        if self._last_snapshot is None:
            return None
        return dict(self._last_snapshot)

    def close(self) -> None:
        if self._closed:
            return
        if self.dma_may_be_active:
            raise RuntimeError("DMA-owned buffers cannot be freed while DMA ownership is uncertain")
        for buffer in (self.tx_buffer, self.rx_buffer):
            free = getattr(buffer, "freebuffer", None)
            if not callable(free):
                raise RuntimeError("DMA buffer has no freebuffer method")
            free()
        self._closed = True
