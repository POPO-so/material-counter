"""Isolated WO-0013 Phase A PYNQ MM2S-to-monitor replay.

Run from a PYNQ Jupyter kernel after loading the matching .bit/.hwh files.
This file has no S2MM channel and does not read stream data back from PL.
"""

from __future__ import annotations

import argparse
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional


WIDTH = 320
HEIGHT = 240
PIXEL_BYTES = WIDTH * HEIGHT
TRAILER_BYTES = 16
FRAME_BYTES = PIXEL_BYTES + TRAILER_BYTES
FRAME_BEATS = FRAME_BYTES // 4
DEFAULT_DMA_BASE = 0x40400000
DEFAULT_MONITOR_BASE = 0x43C10000
PYNQ_Z2_DDR_LIMIT = 0x20000000  # 512 MiB DDR window visible to the PS HP port.
DMA_SOURCE_ALIGNMENT = 8  # Conservative alignment for the 64-bit HP0 memory path.

DMA_MM2S_DMACR = 0x00
DMA_MM2S_DMASR = 0x04
DMA_MM2S_SA = 0x18
DMA_MM2S_LENGTH = 0x28
DMA_CR_RUNSTOP = 1 << 0
DMA_CR_RESET = 1 << 2
DMA_SR_HALTED = 1 << 0
DMA_SR_IDLE = 1 << 1
DMA_SR_ERROR_MASK = (1 << 4) | (1 << 5) | (1 << 6) | (1 << 14)
DMA_SR_IRQ_CLEAR_MASK = 0x7000  # IOC, delay, and error IRQ status are W1C.

MON_CONTROL = 0x00
MON_STATUS = 0x04
MON_BYTES = 0x08
MON_BEATS = 0x0C
MON_FIRST_WORD = 0x10
MON_LAST_WORD = 0x14
MON_TLAST_COUNT = 0x18
MON_CHECKSUM = 0x1C
MON_ERROR_CODE = 0x20
MON_EXPECTED_BYTES = 0x24
MON_STATUS_ARMED = 1 << 0
MON_STATUS_DONE = 1 << 1
MON_STATUS_ERROR = 1 << 2

_QUARANTINED_BUFFERS = []


class ReplayError(RuntimeError):
    """A bounded transfer, hardware, or verification failure."""


class RecoveryError(ReplayError):
    """DMA reset was not confirmed; keep the source buffer alive in-kernel."""


@dataclass(frozen=True)
class ExpectedFrame:
    byte_count: int
    beat_count: int
    first_word: int
    last_word: int
    tlast_count: int
    checksum: int
    foreground_pixels: int


def make_tb_payload() -> bytes:
    """Recreate the exact pixel/trailer bytes emitted by the WO-0013 RTL TB."""
    pixels = bytearray(PIXEL_BYTES)
    foreground = 0
    for index in range(PIXEL_BYTES):
        is_foreground = ((index * 13 + index // 7) % 19) < 8
        pixels[index] = 0xFF if is_foreground else 0x00
        foreground += int(is_foreground)

    # AXI TDATA words from the TB, stored in little-endian DDR byte order.
    trailer = struct.pack("<IIII", 0x10203040, 0x50607080,
                          foreground, 0x12340000)
    payload = bytes(pixels) + trailer
    if len(payload) != FRAME_BYTES:
        raise AssertionError("internal fixture size mismatch")
    return payload


def load_tb_payload(path: Optional[str] = None) -> bytes:
    """Load only the exact TB fixture, or generate it when path is omitted."""
    expected = make_tb_payload()
    if path is None:
        return expected
    payload = Path(path).read_bytes()
    if len(payload) != FRAME_BYTES:
        raise ReplayError(f"payload must be exactly {FRAME_BYTES} bytes; got {len(payload)}")
    if payload != expected:
        raise ReplayError("payload does not match the exact WO-0013 TB fixture")
    return payload


def summarize_payload(payload: bytes) -> ExpectedFrame:
    if len(payload) != FRAME_BYTES or len(payload) % 4:
        raise ReplayError(f"frame must contain {FRAME_BYTES} bytes aligned to 32-bit words")
    words = struct.iter_unpack("<I", payload)
    total = 0
    first = last = 0
    for index, (word,) in enumerate(words):
        if index == 0:
            first = word
        last = word
        total = (total + word) & 0xFFFFFFFF
    foreground = sum(value == 0xFF for value in payload[:PIXEL_BYTES])
    return ExpectedFrame(FRAME_BYTES, FRAME_BEATS, first, last, 1, total, foreground)


def verify_monitor_snapshot(registers: Mapping[str, int], expected: ExpectedFrame) -> None:
    checks = {
        "status.done": (registers["status"] & MON_STATUS_DONE) != 0,
        "status.error": (registers["status"] & MON_STATUS_ERROR) == 0,
        "status.armed": (registers["status"] & MON_STATUS_ARMED) == 0,
        "expected_bytes": registers["expected_bytes"] == expected.byte_count,
        "byte_count": registers["byte_count"] == expected.byte_count,
        "beat_count": registers["beat_count"] == expected.beat_count,
        "first_word": registers["first_word"] == expected.first_word,
        "last_word": registers["last_word"] == expected.last_word,
        "tlast_count": registers["tlast_count"] == expected.tlast_count,
        "checksum": registers["checksum"] == expected.checksum,
        "error_code": registers["error_code"] == 0,
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        detail = ", ".join(
            f"{name}=0x{registers.get(name.split('.')[0], 0):08x}"
            for name in failures
        )
        raise ReplayError("monitor verification failed: " + ", ".join(failures) +
                          f"; status=0x{registers['status']:08x}, "
                          f"error_code={registers['error_code']}, details={detail}")


def verify_monitor_expected_bytes(register_value: int, expected: ExpectedFrame) -> None:
    if register_value != expected.byte_count:
        raise ReplayError(
            f"monitor EXPECTED_BYTES is {register_value}; fixture requires {expected.byte_count}"
        )


def validate_dma_range(source_address: int, length: int,
                       limit_exclusive: int = PYNQ_Z2_DDR_LIMIT) -> None:
    if source_address < 0 or source_address + length > limit_exclusive:
        raise ReplayError(
            f"DMA buffer range [0x{source_address:x}, 0x{source_address + length:x}) "
            f"exceeds PYNQ-Z2 DDR/HP window ending at 0x{limit_exclusive:x}"
        )


def validate_dma_source_alignment(source_address: int) -> None:
    if source_address % DMA_SOURCE_ALIGNMENT:
        raise ReplayError(
            f"DMA source address 0x{source_address:08x} is not "
            f"{DMA_SOURCE_ALIGNMENT}-byte aligned"
        )


def _channel_started_result(status: int):
    """Return a truthy poll result when DMASR.Halted has cleared, even for zero."""
    return True if not (status & DMA_SR_HALTED) else None


def bounded_poll(predicate: Callable[[], object], timeout_s: float,
                 description: str, poll_interval_s: float = 0.001):
    """Return the first truthy predicate result, using one absolute deadline."""
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    deadline = time.monotonic() + timeout_s
    while True:
        result = predicate()
        if result:
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for {description} after {timeout_s:.3f}s")
        time.sleep(min(poll_interval_s, remaining))


def _entry_base(metadata: Mapping[str, object]) -> int:
    for key in ("phys_addr", "base_addr", "base_address"):
        value = metadata.get(key)
        if value is not None:
            return int(value, 0) if isinstance(value, str) else int(value)
    raise ReplayError(f"HWH IP entry has no physical base address: {metadata!r}")


def resolve_hwh_base(ip_dict: Mapping[str, Mapping[str, object]],
                     requested_name: str, expected_base: int) -> int:
    """Resolve a unique IP from Overlay.ip_dict and enforce the expected map."""
    matches = []
    for name, metadata in ip_dict.items():
        normalized = str(name).strip("/")
        # PYNQ's HWH parser can append a module_ref AXI interface, e.g.
        # `rx_monitor/s_axi`; match cell path components and require uniqueness.
        if requested_name in normalized.split("/"):
            matches.append((name, _entry_base(metadata)))
    if len(matches) != 1:
        raise ReplayError(f"expected one HWH entry named {requested_name!r}; found {matches}")
    actual = matches[0][1]
    if actual != expected_base:
        raise ReplayError(
            f"HWH base for {requested_name} is 0x{actual:08x}; "
            f"expected 0x{expected_base:08x}. Pass the reviewed address explicitly if revised."
        )
    return actual


def _read_monitor(mmio) -> dict[str, int]:
    return {
        "status": mmio.read(MON_STATUS),
        "byte_count": mmio.read(MON_BYTES),
        "beat_count": mmio.read(MON_BEATS),
        "first_word": mmio.read(MON_FIRST_WORD),
        "last_word": mmio.read(MON_LAST_WORD),
        "tlast_count": mmio.read(MON_TLAST_COUNT),
        "checksum": mmio.read(MON_CHECKSUM),
        "error_code": mmio.read(MON_ERROR_CODE),
        "expected_bytes": mmio.read(MON_EXPECTED_BYTES),
    }


def _reset_dma(dma_mmio, timeout_s: float) -> int:
    dma_mmio.write(DMA_MM2S_DMACR, DMA_CR_RESET)

    def reset_complete():
        control = dma_mmio.read(DMA_MM2S_DMACR)
        status = dma_mmio.read(DMA_MM2S_DMASR)
        if ((control & (DMA_CR_RESET | DMA_CR_RUNSTOP)) == 0 and
                (status & DMA_SR_HALTED)):
            return status
        return None

    return int(bounded_poll(reset_complete, timeout_s, "MM2S reset/halt confirmation"))


def _sync_buffer_to_device(buffer) -> str:
    sync = getattr(buffer, "sync_to_device", None)
    method_name = "sync_to_device"
    if not callable(sync):
        sync = getattr(buffer, "flush", None)
        method_name = "flush"
    if not callable(sync):
        raise ReplayError("PYNQ Buffer has neither sync_to_device() nor flush()")
    sync()
    return method_name


def _buffer_device_address(buffer) -> int:
    value = getattr(buffer, "device_address", None)
    if value is None:
        value = getattr(buffer, "physical_address", None)
    if value is None:
        raise ReplayError("PYNQ Buffer exposes neither device_address nor physical_address")
    return int(value)


def run_replay(bit_path: str, payload_path: Optional[str] = None,
               dma_name: str = "axi_dma_0",
               monitor_name: str = "rx_monitor",
               dma_base: int = DEFAULT_DMA_BASE,
               monitor_base: int = DEFAULT_MONITOR_BASE,
               timeout_s: float = 5.0,
               reset_timeout_s: float = 2.0) -> dict[str, int]:
    """Download the supplied overlay and replay one fixture frame to the sink."""
    if timeout_s <= 0 or reset_timeout_s <= 0:
        raise ValueError("transfer and reset timeouts must be positive")
    payload = load_tb_payload(payload_path)
    expected = summarize_payload(payload)

    # Import on the target so pure packet/check logic remains testable on a PC.
    import numpy as np
    from pynq import MMIO, Overlay, allocate

    overlay = Overlay(bit_path, download=True)
    dma_base = resolve_hwh_base(overlay.ip_dict, dma_name, dma_base)
    monitor_base = resolve_hwh_base(overlay.ip_dict, monitor_name, monitor_base)
    dma = MMIO(dma_base, length=0x1000)
    monitor = MMIO(monitor_base, length=0x1000)
    verify_monitor_expected_bytes(monitor.read(MON_EXPECTED_BYTES), expected)

    buffer = allocate(shape=(FRAME_BYTES,), dtype=np.uint8)
    dma_owns_buffer = False
    safe_to_release = False
    transfer_started = False
    try:
        buffer[:] = np.frombuffer(payload, dtype=np.uint8)
        source_address = _buffer_device_address(buffer)
        validate_dma_source_alignment(source_address)
        if source_address > 0xFFFFFFFF:
            raise ReplayError(f"32-bit AXI DMA cannot address 0x{source_address:x}")
        validate_dma_range(source_address, FRAME_BYTES)
        if len(payload) > 0x1FFFF:
            raise ReplayError("payload exceeds the configured 17-bit MM2S length field")

        # Establish a known MM2S state on every run, including the first transfer.
        _reset_dma(dma, reset_timeout_s)
        # DMASR error causes (bits 4..6) are read-only; only IRQ status bits are W1C.
        dma.write(DMA_MM2S_DMASR, DMA_SR_IRQ_CLEAR_MASK)
        monitor.write(MON_CONTROL, 0x1)
        armed = _read_monitor(monitor)
        if not (armed["status"] & MON_STATUS_ARMED) or armed["status"] & (
                MON_STATUS_DONE | MON_STATUS_ERROR):
            raise ReplayError(f"monitor failed to arm cleanly: status=0x{armed['status']:08x}")

        sync_method = _sync_buffer_to_device(buffer)
        if dma.read(DMA_MM2S_DMASR) & DMA_SR_ERROR_MASK:
            raise ReplayError(f"MM2S reports stale/error status 0x{dma.read(DMA_MM2S_DMASR):08x}")
        dma.write(DMA_MM2S_DMACR, DMA_CR_RUNSTOP)

        def channel_running():
            status = dma.read(DMA_MM2S_DMASR)
            if status & DMA_SR_ERROR_MASK:
                raise ReplayError(f"MM2S failed to start; status=0x{status:08x}")
            return _channel_started_result(status)

        bounded_poll(
            channel_running, min(timeout_s, 1.0), "MM2S channel start")
        dma.write(DMA_MM2S_SA, source_address & 0xFFFFFFFF)
        # Writing LENGTH is the final command and starts the direct-register transfer.
        dma_owns_buffer = True
        transfer_started = True
        dma.write(DMA_MM2S_LENGTH, FRAME_BYTES)

        def completed():
            status = dma.read(DMA_MM2S_DMASR)
            mon = _read_monitor(monitor)
            if status & DMA_SR_ERROR_MASK:
                raise ReplayError(f"MM2S error status 0x{status:08x}")
            if mon["status"] & MON_STATUS_ERROR:
                raise ReplayError(
                    f"monitor error status=0x{mon['status']:08x} "
                    f"code={mon['error_code']} bytes={mon['byte_count']}"
                )
            if status & DMA_SR_IDLE and mon["status"] & MON_STATUS_DONE:
                return status, mon
            return None

        dma_status, snapshot = bounded_poll(completed, timeout_s,
                                            "MM2S idle and receiver done")
        safe_to_release = True
        verify_monitor_snapshot(snapshot, expected)

        coherent = bool(getattr(buffer, "coherent", False))
        print(f"WO0013_MM2S_PASS bytes={snapshot['byte_count']} "
              f"beats={snapshot['beat_count']} first=0x{snapshot['first_word']:08x} "
              f"last=0x{snapshot['last_word']:08x} tlast={snapshot['tlast_count']} "
              f"checksum=0x{snapshot['checksum']:08x} dma_status=0x{dma_status:08x} "
              f"buffer_coherent={coherent} tx_sync={sync_method}")
        return snapshot
    except BaseException as original:
        if dma_owns_buffer and not safe_to_release:
            try:
                reset_status = _reset_dma(dma, reset_timeout_s)
                safe_to_release = True
                # The receiver is independently re-armed through its AXI-Lite control CSR.
            except BaseException as recovery_error:
                _QUARANTINED_BUFFERS.append(buffer)
                buffer = None
                raise RecoveryError(
                    "MM2S reset was not confirmed; source buffer is retained in "
                    "_QUARANTINED_BUFFERS. Keep this Jupyter kernel alive and reset or "
                    "power-cycle the board before releasing it. Original failure: "
                    f"{original}; recovery failure: {recovery_error}"
                ) from recovery_error
            try:
                monitor.write(MON_CONTROL, 0x1)
                armed = _read_monitor(monitor)
                if not (armed["status"] & MON_STATUS_ARMED):
                    raise ReplayError(f"monitor did not re-arm: status=0x{armed['status']:08x}")
                print(f"WO0013_RECOVERY_PASS dma_status=0x{reset_status:08x} monitor_rearmed=1")
            except BaseException as recovery_error:
                raise ReplayError(
                    "MM2S reset was confirmed and source buffer is safe to release, "
                    f"but monitor re-arm failed: {recovery_error}; original failure: {original}"
                ) from recovery_error
        raise
    finally:
        if buffer is not None:
            if safe_to_release or not transfer_started:
                free = getattr(buffer, "freebuffer", None)
                if callable(free):
                    free()
            elif dma_owns_buffer:
                # Defensive: never explicitly release memory while DMA ownership is unknown.
                _QUARANTINED_BUFFERS.append(buffer)


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bit", help="path to the .bit file; matching .hwh must sit beside it")
    parser.add_argument("--payload", help="optional exact 76816-byte WO-0013 TB fixture")
    parser.add_argument("--dma-name", default="axi_dma_0")
    parser.add_argument("--monitor-name", default="rx_monitor")
    parser.add_argument("--dma-base", type=lambda value: int(value, 0), default=DEFAULT_DMA_BASE)
    parser.add_argument("--monitor-base", type=lambda value: int(value, 0),
                        default=DEFAULT_MONITOR_BASE)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--reset-timeout", type=float, default=2.0)
    args = parser.parse_args()
    try:
        run_replay(args.bit, args.payload, args.dma_name, args.monitor_name,
                   args.dma_base, args.monitor_base, args.timeout, args.reset_timeout)
        return 0
    except Exception as error:
        print(f"WO0013_MM2S_FAIL: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
