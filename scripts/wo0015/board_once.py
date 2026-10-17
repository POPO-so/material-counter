"""One-shot WO-0015 PS executor with offline-first gates and register Mock.

Importing this module is harmless: PYNQ is imported only inside the explicit
hardware API methods, after a stage-specific authorization and fixed guard.
"""
from __future__ import annotations

import argparse
import json
import math
import mmap
import operator
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Mapping

try:
    import board_preflight as preflight
except ImportError:  # pragma: no cover - package-style import support
    from . import board_preflight as preflight


DMA_MM2S_DMACR = 0x00
DMA_MM2S_DMASR = 0x04
DMA_MM2S_SA = 0x18
DMA_MM2S_LENGTH = 0x28
DMA_S2MM_DMACR = 0x30
DMA_S2MM_DMASR = 0x34
DMA_S2MM_DA = 0x48
DMA_S2MM_LENGTH = 0x58

# Zynq-7000 AFI1 and SLCR read-only observation points. The physical reader
# below opens /dev/mem with O_RDONLY and maps each page with PROT_READ.
AFI1_RDCHAN_CTRL = 0xF8009000
AFI1_WRCHAN_CTRL = 0xF8009014
SLCR_FPGA0_CLK_CTRL = 0xF8000170
SLCR_FPGA_RST_CTRL = 0xF8000240
SNAPSHOT_REGISTERS = {
    "AFI1_RDCHAN_CTRL": AFI1_RDCHAN_CTRL,
    "AFI1_WRCHAN_CTRL": AFI1_WRCHAN_CTRL,
    "SLCR_FPGA0_CLK_CTRL": SLCR_FPGA0_CLK_CTRL,
    "SLCR_FPGA_RST_CTRL": SLCR_FPGA_RST_CTRL,
}

DMA_RUN = 1 << 0
CMA_WATERMARK_KIB = 16384
RX_POISON_BYTE = 0xA5
DMA_HALTED = 1 << 0
DMA_IDLE = 1 << 1
DMA_INTERNAL_ERROR = 1 << 4
DMA_SLAVE_ERROR = 1 << 5
DMA_DECODE_ERROR = 1 << 6
DMA_IOC_IRQ = 1 << 12
DMA_DLY_IRQ = 1 << 13
DMA_ERR_IRQ = 1 << 14
DMA_ERROR_MASK = (DMA_INTERNAL_ERROR | DMA_SLAVE_ERROR | DMA_DECODE_ERROR | DMA_ERR_IRQ)
DMA_IRQ_CLEAR_MASK = DMA_IOC_IRQ | DMA_DLY_IRQ

CHECKER_STATUS = 0x08
CHECKER_ERROR_CODE = 0x0C
CHECKER_FRAME_ID = 0x10
CHECKER_PACKET_DONE_COUNT = 0x14
CHECKER_PACKET_ERROR_COUNT = 0x18
CHECKER_INPUT_BYTES = 0x1C
CHECKER_INPUT_BEATS = 0x20
CHECKER_INPUT_TLAST = 0x24
CHECKER_INPUT_FIRST_WORD = 0x28
CHECKER_INPUT_LAST_WORD = 0x2C
CHECKER_INPUT_CHECKSUM = 0x30
CHECKER_PIXELS = 0x34
CHECKER_SOFS = 0x38
CHECKER_EOLS = 0x3C
CHECKER_OUTPUT_CHECKSUM = 0x40
CHECKER_OUTPUT_TOTAL = 0x44
CHECKER_OUTPUT_STALLS = 0x48

PACKER_STATUS = 0x4C
PACKER_ACCEPTED_PIXELS = 0x50
PACKER_SENT_BEATS = 0x54
PACKER_ERROR_CODE = 0x58
PACKER_ARM = 0x5C
PACKER_SIGNATURE = 0x60
PACKER_BUSY = 1 << 0
PACKER_DONE = 1 << 1
PACKER_FAULT = 1 << 2
CHECKER_PACKET_DONE = 1 << 0
CHECKER_PACKET_ERROR = 1 << 1
CHECKER_GEOMETRY_ERROR = 1 << 2
CHECKER_OUTPUT_COMPLETE = 1 << 3

POLL_INTERVAL_S = 0.001
DEFAULT_TIMEOUT_S = 5.0
DEFAULT_GUARD_DIR = Path("/var/lib/wo0015-s2mm")
OVERLAY_GUARD = DEFAULT_GUARD_DIR / "overlay-only-consumed.json"
DMA_SINGLE_GUARD = DEFAULT_GUARD_DIR / "dma-single-consumed.json"
DMA_THREE_GUARD = DEFAULT_GUARD_DIR / "dma-three-consumed.json"
TERMINAL_FAILURE = DEFAULT_GUARD_DIR / "terminal-failure.json"
OVERLAY_PASS_RECEIPT = DEFAULT_GUARD_DIR / "overlay-only-pass.json"
DMA_SINGLE_PASS_RECEIPT = DEFAULT_GUARD_DIR / "dma-single-pass.json"

# Preserve CMA-backed buffers for the lifetime of the process after an
# uncertain in-flight failure. The hardware CLI additionally enters fail-hold.
QUARANTINED_BUFFERS: list[tuple[Any, Any, Any, str]] = []
_REAL_BOARD_ACCESS_OCCURRED = False
_PYNQ_IMPORTED = False


class ExecutionFailure(RuntimeError):
    """A fail-closed execution outcome, including whether DMA may be active."""

    def __init__(self, message: str, *, transfer_started: bool,
                 evidence: Mapping[str, Any] | None = None,
                 dma_start_attempted: bool | None = None,
                 dma_may_be_active: bool | None = None):
        super().__init__(message)
        self.transfer_started = transfer_started
        self.dma_start_attempted = (transfer_started if dma_start_attempted is None
                                    else dma_start_attempted)
        self.dma_may_be_active = (transfer_started if dma_may_be_active is None
                                  else dma_may_be_active)
        self.evidence = dict(evidence or {})


def _mark_board_accessed() -> None:
    global _REAL_BOARD_ACCESS_OCCURRED
    _REAL_BOARD_ACCESS_OCCURRED = True


def _u32_delta(current: int, previous: int) -> int:
    return (current - previous) & 0xFFFFFFFF


def _validate_timeout(timeout_s: float) -> None:
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout_s must be finite and positive")


def read_phys32_readonly(address: int, *, mem_path: str = "/dev/mem",
                         page_size: int | None = None, open_fn: Any = None,
                         mmap_fn: Any = None, close_fn: Any = None) -> int:
    """Read one whitelisted physical register through a read-only mapping."""
    if isinstance(address, bool) or not isinstance(address, int) or address not in SNAPSHOT_REGISTERS.values():
        raise ValueError("physical register address is not in the read-only snapshot allowlist")
    if page_size is None:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
    if page_size <= 0 or page_size & (page_size - 1):
        raise ValueError("page_size must be a positive power of two")
    open_fn = os.open if open_fn is None else open_fn
    mmap_fn = mmap.mmap if mmap_fn is None else mmap_fn
    close_fn = os.close if close_fn is None else close_fn
    page_base = address & ~(page_size - 1)
    page_offset = address - page_base
    fd = open_fn(mem_path, os.O_RDONLY | getattr(os, "O_SYNC", 0))
    try:
        mapped = mmap_fn(fd, page_size, access=mmap.ACCESS_READ, offset=page_base)
        try:
            return int.from_bytes(mapped[page_offset:page_offset + 4], "little")
        finally:
            mapped.close()
    finally:
        close_fn(fd)


def read_afi1_snapshot(api: Any, stage: str) -> dict[str, Any]:
    """Read AFI1 width controls and FPGA clock/reset control state, without writes."""
    if not stage or not isinstance(stage, str):
        raise ValueError("snapshot stage must be a nonempty string")
    registers: dict[str, dict[str, Any]] = {}
    for name, address in SNAPSHOT_REGISTERS.items():
        value = api.read_phys32(address)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
            raise ValueError(f"{name} read did not return an unsigned 32-bit integer")
        register: dict[str, Any] = {
            "address": f"0x{address:08x}", "value": value,
            "raw_hex": f"0x{value:08x}",
        }
        if name in ("AFI1_RDCHAN_CTRL", "AFI1_WRCHAN_CTRL"):
            register["width_bit0"] = value & 1
            register["width_bits"] = 32 if value & 1 else 64
        registers[name] = register
    return {"stage": stage, "access": "read-only", "registers": registers}


def _artifact_fingerprints(plan: preflight.ValidatedRun) -> dict[str, Any]:
    bit_sha256 = preflight.sha256_file(plan.bit_path)
    hwh_sha256 = preflight.sha256_file(plan.hwh_path)
    return {
        "bit_path": str(plan.bit_path), "bit_sha256": bit_sha256,
        "bit_matches_manifest": bit_sha256 == plan.bit_sha256,
        "hwh_path": str(plan.hwh_path), "hwh_sha256": hwh_sha256,
        "hwh_matches_manifest": hwh_sha256 == plan.hwh_sha256,
    }


def _checker_snapshot(api: Any) -> dict[str, int]:
    offsets = {
        "status": CHECKER_STATUS,
        "error_code": CHECKER_ERROR_CODE,
        "frame_id": CHECKER_FRAME_ID,
        "packet_done_count": CHECKER_PACKET_DONE_COUNT,
        "packet_error_count": CHECKER_PACKET_ERROR_COUNT,
        "input_bytes": CHECKER_INPUT_BYTES,
        "input_beats": CHECKER_INPUT_BEATS,
        "input_tlast": CHECKER_INPUT_TLAST,
        "input_first_word": CHECKER_INPUT_FIRST_WORD,
        "input_last_word": CHECKER_INPUT_LAST_WORD,
        "input_checksum": CHECKER_INPUT_CHECKSUM,
        "pixels": CHECKER_PIXELS,
        "sofs": CHECKER_SOFS,
        "eols": CHECKER_EOLS,
        "output_checksum": CHECKER_OUTPUT_CHECKSUM,
        "output_total": CHECKER_OUTPUT_TOTAL,
        "output_stalls": CHECKER_OUTPUT_STALLS,
    }
    return {name: api.read32(preflight.CHECKER_BASE, offset) for name, offset in offsets.items()}


def _require_clean_start(api: Any, plan: preflight.ValidatedRun,
                         expected_prior_frames: int) -> dict[str, int]:
    checker_signature = api.read32(preflight.CHECKER_BASE, 0x04)
    packer_signature = api.read32(preflight.CHECKER_BASE, PACKER_SIGNATURE)
    if checker_signature != preflight.PACKET_MAGIC:
        raise ExecutionFailure(
            f"Checker signature 0x{checker_signature:08x} does not match WO-0014",
            transfer_started=False,
        )
    if packer_signature != preflight.PACKER_SIGNATURE:
        raise ExecutionFailure(
            f"Packer signature 0x{packer_signature:08x} does not match WO-0015",
            transfer_started=False,
        )
    snapshot = _checker_snapshot(api)
    if snapshot["packet_error_count"] or snapshot["error_code"]:
        raise ExecutionFailure("Checker reports a pre-existing packet/error condition",
                               transfer_started=False, evidence={"checker": snapshot})
    if expected_prior_frames not in (0, 1):
        raise ValueError("expected_prior_frames must be 0 or 1")
    expected = {"packet_done_count": 0, "packet_error_count": 0, "input_bytes": 0,
                "input_beats": 0, "input_tlast": 0, "input_checksum": 0,
                "pixels": 0, "sofs": 0, "eols": 0, "output_checksum": 0,
                "output_total": 0, "frame_id": 0}
    if expected_prior_frames:
        prior = plan.frames[0]
        expected.update({
            "packet_done_count": 1,
            "input_bytes": preflight.TX_BYTES,
            "input_beats": preflight.TX_BEATS,
            "input_tlast": 1,
            "input_checksum": prior.packet_checksum,
            "pixels": preflight.PIXEL_COUNT,
            "sofs": 1,
            "eols": preflight.HEIGHT,
            "output_checksum": prior.binary_checksum,
            "output_total": preflight.PIXEL_COUNT,
            "frame_id": prior.frame_id,
        })
        expected_status = CHECKER_PACKET_DONE | CHECKER_OUTPUT_COMPLETE
    else:
        expected_status = 0
    for field, value in expected.items():
        if snapshot[field] != value:
            raise ExecutionFailure(
                f"Checker {field} before this stage is {snapshot[field]}, expected {value}",
                transfer_started=False, evidence={"checker": snapshot},
            )
    if snapshot["status"] != expected_status:
        raise ExecutionFailure(
            f"Checker status before this stage is 0x{snapshot['status']:08x}, expected 0x{expected_status:08x}",
            transfer_started=False, evidence={"checker": snapshot},
        )
    if expected_prior_frames:
        s2mm_status = api.read32(preflight.DMA_BASE, DMA_S2MM_DMASR)
        mm2s_status = api.read32(preflight.DMA_BASE, DMA_MM2S_DMASR)
        _require_dma_clean(s2mm_status, "S2MM")
        _require_dma_clean(mm2s_status, "MM2S")
        if (not (s2mm_status & DMA_IDLE) or not (s2mm_status & DMA_IOC_IRQ) or
                not (mm2s_status & DMA_IDLE) or not (mm2s_status & DMA_IOC_IRQ)):
            raise ExecutionFailure("DMA channels do not show the completed single-frame PASS baseline",
                                   transfer_started=False,
                                   evidence={"s2mm_dmasr": s2mm_status,
                                             "mm2s_dmasr": mm2s_status})
    packer_status = api.read32(preflight.CHECKER_BASE, PACKER_STATUS)
    expected_packer_status = PACKER_DONE if expected_prior_frames else 0
    expected_packer_pixels = preflight.PIXEL_COUNT if expected_prior_frames else 0
    expected_packer_beats = preflight.RX_BEATS if expected_prior_frames else 0
    if (packer_status != expected_packer_status or
            api.read32(preflight.CHECKER_BASE, PACKER_ACCEPTED_PIXELS) != expected_packer_pixels or
            api.read32(preflight.CHECKER_BASE, PACKER_SENT_BEATS) != expected_packer_beats):
        raise ExecutionFailure("Packer status/counters do not match the expected stage baseline",
                               transfer_started=False,
                               evidence={"packer_status": packer_status,
                                         "accepted_pixels": api.read32(preflight.CHECKER_BASE, PACKER_ACCEPTED_PIXELS),
                                         "sent_beats": api.read32(preflight.CHECKER_BASE, PACKER_SENT_BEATS)})
    if api.read32(preflight.CHECKER_BASE, PACKER_ERROR_CODE) != 0:
        raise ExecutionFailure("Packer has a pre-existing error code",
                               transfer_started=False)
    return snapshot


def _require_dma_clean(status: int, channel: str) -> None:
    if status & DMA_ERROR_MASK:
        raise ExecutionFailure(f"{channel} DMASR error bits set: 0x{status:08x}",
                               transfer_started=False,
                               evidence={f"{channel.lower()}_dmasr": status})


def _wait_not_halted(api: Any, offset: int, channel: str, deadline: float,
                     transfer_started: bool) -> int:
    while True:
        status = api.read32(preflight.DMA_BASE, offset)
        _require_dma_clean(status, channel)
        if not status & DMA_HALTED:
            return status
        if api.monotonic() >= deadline:
            raise ExecutionFailure(f"{channel} remained Halted after Run=1",
                                   transfer_started=transfer_started,
                                   evidence={f"{channel.lower()}_dmasr": status})
        api.sleep(POLL_INTERVAL_S)


def _wait_frame_complete(api: Any, deadline: float, *, frame_id: int,
                         transfer_started: bool) -> tuple[int, int, int]:
    while True:
        s2mm_status = api.read32(preflight.DMA_BASE, DMA_S2MM_DMASR)
        mm2s_status = api.read32(preflight.DMA_BASE, DMA_MM2S_DMASR)
        _require_dma_clean(s2mm_status, "S2MM")
        _require_dma_clean(mm2s_status, "MM2S")
        packer_status = api.read32(preflight.CHECKER_BASE, PACKER_STATUS)
        packer_error = api.read32(preflight.CHECKER_BASE, PACKER_ERROR_CODE)
        checker = _checker_snapshot(api)
        if packer_status & PACKER_FAULT or packer_error:
            raise ExecutionFailure(
                f"Packer fault during frame {frame_id}: status=0x{packer_status:08x} error={packer_error}",
                transfer_started=transfer_started,
                evidence={"packer_status": packer_status, "packer_error": packer_error,
                          "checker": checker, "s2mm_dmasr": s2mm_status,
                          "mm2s_dmasr": mm2s_status},
            )
        completed = (
            (s2mm_status & DMA_IDLE) and (s2mm_status & DMA_IOC_IRQ) and
            (mm2s_status & DMA_IDLE) and (mm2s_status & DMA_IOC_IRQ) and
            (packer_status & PACKER_DONE) and not (packer_status & PACKER_BUSY) and
            (checker["status"] & CHECKER_OUTPUT_COMPLETE)
        )
        if completed:
            return s2mm_status, mm2s_status, packer_status
        if api.monotonic() >= deadline:
            raise ExecutionFailure(
                f"frame {frame_id} timed out waiting for both DMA channels, Packer EOF, and Checker",
                transfer_started=transfer_started,
                evidence={"packer_status": packer_status, "checker": checker,
                          "s2mm_dmasr": s2mm_status, "mm2s_dmasr": mm2s_status},
            )
        api.sleep(POLL_INTERVAL_S)


def _check_checker_frame(before: Mapping[str, int], after: Mapping[str, int],
                         frame: preflight.FrameArtifact) -> None:
    status = after["status"]
    if status & (CHECKER_PACKET_ERROR | CHECKER_GEOMETRY_ERROR):
        raise ExecutionFailure(f"Checker status has error bits for frame {frame.frame_id}: 0x{status:08x}",
                               transfer_started=False, evidence={"checker": dict(after)})
    if not status & CHECKER_PACKET_DONE:
        raise ExecutionFailure(f"Checker did not observe packet_done for frame {frame.frame_id}",
                               transfer_started=False, evidence={"checker": dict(after)})
    if after["error_code"] != 0 or after["frame_id"] != frame.frame_id:
        raise ExecutionFailure(f"Checker error/frame ID mismatch for frame {frame.frame_id}",
                               transfer_started=False, evidence={"checker": dict(after)})
    expected_current = {
        "pixels": preflight.RX_BYTES,
        "sofs": 1,
        "eols": preflight.HEIGHT,
        "output_checksum": frame.binary_checksum,
    }
    for field, expected in expected_current.items():
        if after[field] != expected:
            raise ExecutionFailure(
                f"Checker {field} for frame {frame.frame_id} is {after[field]}, expected {expected}",
                transfer_started=False, evidence={"checker": dict(after)},
            )
    expected_deltas = {
        "packet_done_count": 1,
        "packet_error_count": 0,
        "input_bytes": preflight.TX_BYTES,
        "input_beats": preflight.TX_BEATS,
        "input_tlast": 1,
        "input_checksum": frame.packet_checksum,
        "output_total": preflight.RX_BYTES,
    }
    for field, expected in expected_deltas.items():
        actual = _u32_delta(after[field], before[field])
        if actual != expected:
            raise ExecutionFailure(
                f"Checker cumulative {field} delta for frame {frame.frame_id} is {actual}, expected {expected}",
                transfer_started=False, evidence={"checker_before": dict(before),
                                                   "checker_after": dict(after)},
            )


def analyze_rx_mismatch(rx: bytes, golden: bytes, *, poison_byte: int = RX_POISON_BYTE) -> dict[str, Any]:
    """Analyze completed RX bytes; parity means alternating 32-bit words."""
    if not rx or len(rx) != len(golden) or len(rx) % 4:
        raise ValueError("RX/Golden must have equal nonempty whole-word byte lengths")
    if type(poison_byte) is not int or not 0 <= poison_byte <= 255:
        raise ValueError("Poison must be one byte")
    parity = {name: {"word_count": 0, "mismatch_bytes": 0, "zero_words": 0,
                     "poison_words": 0, "zero_word_bytes": 0,
                     "poison_word_bytes": 0, "golden_match_bytes": 0}
              for name in ("even", "odd")}
    prefix = []
    first = []
    mismatch_bytes = 0
    for offset in range(0, len(rx), 4):
        actual, expected = rx[offset:offset + 4], golden[offset:offset + 4]
        word_index = offset // 4
        name = "odd" if word_index % 2 else "even"
        row = parity[name]
        row["word_count"] += 1
        zero_word = actual == b"\x00" * 4
        poison_word = actual == bytes([poison_byte]) * 4
        row["zero_words"] += int(zero_word)
        row["poison_words"] += int(poison_word)
        row["zero_word_bytes"] += 4 * int(zero_word)
        row["poison_word_bytes"] += 4 * int(poison_word)
        count = sum(a != b for a, b in zip(actual, expected))
        row["mismatch_bytes"] += count
        row["golden_match_bytes"] += 4 - count
        mismatch_bytes += count
        for byte_index, (a, b) in enumerate(zip(actual, expected)):
            if a != b and len(first) < 32:
                first.append({"offset": offset + byte_index, "expected": b, "actual": a})
        if offset < 64:
            prefix.append({"word_index": word_index, "offset": offset, "parity": name,
                           "rx_hex": actual.hex(), "golden_hex": expected.hex(),
                           "rx_u32_le": int.from_bytes(actual, "little"),
                           "golden_u32_le": int.from_bytes(expected, "little"),
                           "mismatch_bytes": count})
    return {"schema": 1, "byte_count": len(rx), "word_bytes": 4,
            "poison_byte": poison_byte, "poison_u32": poison_byte * 0x01010101,
            "rx_sha256": preflight.sha256_bytes(rx),
            "golden_sha256": preflight.sha256_bytes(golden),
            "mismatch_bytes": mismatch_bytes, "first_mismatch": first[0]["offset"] if first else None,
            "first_mismatches": first, "first_64_bytes_words": prefix, "parity": parity}


def persist_rx_diagnostics(directory: str | Path, stem: str, rx: bytes, golden: bytes,
                           *, context: Mapping[str, Any], tx: bytes | None = None,
                           artifacts: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Durably save full TX/RX/Golden evidence and diagnostics; never overwrite."""
    if not stem or stem in (".", "..") or "/" in stem or "\\" in stem:
        raise ValueError("diagnostic stem must be one filename component")
    if tx is not None and len(tx) != preflight.TX_BYTES:
        raise ValueError(f"TX capture must be exactly {preflight.TX_BYTES} bytes")
    target = Path(directory).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    result = analyze_rx_mismatch(rx, golden)
    result.update(capture_status="SAVED", captured_before_release=True, context=dict(context),
                  artifacts=dict(artifacts or {}))
    paths = {"rx_path": target / f"{stem}_rx_raw.bin",
             "expected_golden_path": target / f"{stem}_expected_golden.bin",
             "diagnostics_path": target / f"{stem}_diagnostics.json"}
    paths["golden_path"] = paths["expected_golden_path"]  # Backward-compatible result key.
    if tx is not None:
        paths["tx_path"] = target / f"{stem}_tx_raw.bin"
    result.update({k: str(v) for k, v in paths.items()})
    result["expected_golden_sha256"] = result["golden_sha256"]
    if tx is not None:
        result["tx_sha256"] = preflight.sha256_bytes(tx)

    def save(path: Path, raw: bytes) -> None:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if preflight.sha256_bytes(path.read_bytes()) != preflight.sha256_bytes(raw):
            raise OSError("diagnostic file readback hash differs")

    if tx is not None:
        save(paths["tx_path"], tx)
    save(paths["rx_path"], rx)
    save(paths["expected_golden_path"], golden)
    save(paths["diagnostics_path"], (json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    if os.name != "nt":
        fd = os.open(str(target), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return result


def run_frames(plan: preflight.ValidatedRun, api: Any, *,
               timeout_s: float = DEFAULT_TIMEOUT_S,
               frame_limit: int | None = None,
               expected_prior_frames: int = 0,
               diagnostic_dir: str | Path | None = None) -> dict[str, Any]:
    """Run one or three frames through an injected low-level Board API.

    The API contract is intentionally small: allocate, free, buffer sync, MMIO,
    monotonic/sleep, and a CmaFree reader. Hardware and Mock use the same path.
    """
    _validate_timeout(timeout_s)
    if frame_limit is None:
        frame_limit = len(plan.frames)
    if frame_limit not in (1, 3) or frame_limit > len(plan.frames):
        raise ValueError("frame_limit must select one or three manifest frames")
    if expected_prior_frames and (expected_prior_frames != 1 or len(plan.frames) != 3):
        raise ValueError("one prior frame is supported only for a three-frame manifest")
    frames_to_run = plan.frames[:frame_limit]
    begin_run = getattr(api, "begin_run", None)
    if callable(begin_run):
        begin_run(frame_limit, expected_prior_frames)
    initial_cma_free = api.cma_free_kib()
    mm2s_buffer = None
    s2mm_buffer = None
    may_be_in_flight = False
    dma_start_attempted = False
    transfer_started = False
    released = False
    retain_for_evidence = False
    evidence: dict[str, Any] = {
        "task": "WO-0015",
        "status": "RUNNING",
        "board_accessed": bool(getattr(api, "board_accessed", False)),
        "dma_start_attempted": False,
        "transfer_started": False,
        "dma_may_be_active": False,
        "boot_id": plan.boot_id,
        "manifest_sha256": plan.manifest_sha256,
        "build_id": plan.build_id,
        "bit_sha256": plan.bit_sha256,
        "hwh_sha256": plan.hwh_sha256,
        "artifact_fingerprints": _artifact_fingerprints(plan),
        "afi1_snapshots": [],
        "clock_mhz": 50,
        "frame_count": frame_limit,
        "tx_bytes_each": preflight.TX_BYTES,
        "rx_bytes_each": preflight.RX_BYTES,
        "cma_free_before_kib": initial_cma_free,
        "cma_watermark_kib": CMA_WATERMARK_KIB,
        "cma_allocation_samples": [{"stage": "before", "cma_free_kib": initial_cma_free}],
        "frames": [],
        "hardware_stage": getattr(api, "stage", "mock"),
    }
    def record_release_cma() -> int:
        value = api.cma_free_kib()
        evidence["cma_free_after_kib"] = value
        evidence["cma_free_delta_kib"] = value - initial_cma_free
        evidence["cma_allocation_samples"].append({"stage": "after_release", "cma_free_kib": value})
        return value

    def require_watermark(value: int, stage: str) -> None:
        if type(value) is not int or value < CMA_WATERMARK_KIB:
            raise ExecutionFailure(
                f"CmaFree watermark failed at {stage}: {value} KiB < {CMA_WATERMARK_KIB} KiB",
                transfer_started=False, evidence=evidence,
            )

    try:
        require_watermark(initial_cma_free, "before allocation")
        baseline_checker = _require_clean_start(api, plan, expected_prior_frames)
        mm2s_buffer = api.allocate(preflight.TX_BYTES)
        after_mm2s_cma_free = api.cma_free_kib()
        evidence["cma_allocation_samples"].append(
            {"stage": "after_mm2s", "cma_free_kib": after_mm2s_cma_free})
        require_watermark(after_mm2s_cma_free, "after MM2S allocation")
        s2mm_buffer = api.allocate(preflight.RX_BYTES)
        during_cma_free = api.cma_free_kib()
        evidence["cma_free_during_kib"] = during_cma_free
        evidence["cma_allocation_samples"].append(
            {"stage": "after_s2mm", "cma_free_kib": during_cma_free})
        expected_kib = sum((n + preflight.PAGE_BYTES - 1) // preflight.PAGE_BYTES
                           for n in (preflight.TX_BYTES, preflight.RX_BYTES)) * preflight.PAGE_BYTES // 1024
        evidence["allocation_accounting"] = {
            "page_rounded_bytes_kib": expected_kib,
            "observed_cma_decrease_kib": initial_cma_free - during_cma_free,
            "hard_gate": False,
            "note": "Global CMA free-page differences are telemetry, not per-buffer allocation proof."}
        require_watermark(during_cma_free, "after S2MM allocation")
        mm2s_address = api.device_address(mm2s_buffer)
        s2mm_address = api.device_address(s2mm_buffer)
        evidence["post_alloc_physical_gate"] = preflight.validate_dma_buffer_pair(
            tx_address=mm2s_address, tx_size=memoryview(mm2s_buffer).nbytes,
            rx_address=s2mm_address, rx_size=memoryview(s2mm_buffer).nbytes,
            cma_ranges=getattr(api, "cma_ranges", None),
            hp0_range=plan.hp0_range, hp1_range=plan.hp1_range)
        previous_checker = baseline_checker

        for index, frame in enumerate(frames_to_run):
            # Before the first transfer, some AXI DMA builds report Idle=0
            # until the first length write. After every completed frame, both
            # channels must report Idle before the next arm is allowed.
            s2mm_before = api.read32(preflight.DMA_BASE, DMA_S2MM_DMASR)
            mm2s_before = api.read32(preflight.DMA_BASE, DMA_MM2S_DMASR)
            prepare_frame = getattr(api, "before_frame", None)
            if callable(prepare_frame):
                prepare_frame(frame, index)
                s2mm_before = api.read32(preflight.DMA_BASE, DMA_S2MM_DMASR)
                mm2s_before = api.read32(preflight.DMA_BASE, DMA_MM2S_DMASR)
            _require_dma_clean(s2mm_before, "S2MM")
            _require_dma_clean(mm2s_before, "MM2S")
            if index and (not (s2mm_before & DMA_IDLE) or not (mm2s_before & DMA_IDLE)):
                raise ExecutionFailure(
                    f"frame {frame.frame_id} pre-length DMA state is not Idle",
                    transfer_started=False,
                    evidence={"s2mm_dmasr": s2mm_before, "mm2s_dmasr": mm2s_before},
                )

            api.write_buffer(mm2s_buffer, frame.packet)
            api.sync_to_device(mm2s_buffer)
            api.write_buffer(s2mm_buffer, bytes([RX_POISON_BYTE]) * preflight.RX_BYTES)
            api.sync_to_device(s2mm_buffer)
            afi_before = read_afi1_snapshot(api, f"before_dma_frame_{index}")
            evidence["afi1_snapshots"].append(afi_before)
            packer_status = api.read32(preflight.CHECKER_BASE, PACKER_STATUS)
            if packer_status & (PACKER_BUSY | PACKER_FAULT):
                raise ExecutionFailure(f"Packer is busy/faulted before frame {frame.frame_id}",
                                       transfer_started=False,
                                       evidence={"packer_status": packer_status})
            if api.read32(preflight.CHECKER_BASE, PACKER_ERROR_CODE):
                raise ExecutionFailure(f"Packer has an error before frame {frame.frame_id}",
                                       transfer_started=False)
            # Exactly one arm write per accepted frame; no reset or relaunch.
            api.write32(preflight.CHECKER_BASE, PACKER_ARM, 1)

            # Clear only stale interrupt indications. Existing error bits are
            # evidence and are never cleared or retried by this executor.
            if s2mm_before & (DMA_IOC_IRQ | DMA_DLY_IRQ):
                api.write32(preflight.DMA_BASE, DMA_S2MM_DMASR, s2mm_before & (DMA_IOC_IRQ | DMA_DLY_IRQ))
            if mm2s_before & (DMA_IOC_IRQ | DMA_DLY_IRQ):
                api.write32(preflight.DMA_BASE, DMA_MM2S_DMASR, mm2s_before & (DMA_IOC_IRQ | DMA_DLY_IRQ))

            deadline = api.monotonic() + timeout_s
            api.write32(preflight.DMA_BASE, DMA_S2MM_DMACR, DMA_RUN)
            _wait_not_halted(api, DMA_S2MM_DMASR, "S2MM", deadline, False)
            api.write32(preflight.DMA_BASE, DMA_S2MM_DA, s2mm_address)
            # From the first attempted S2MM length write onward the buffers
            # are quarantined on any uncertain outcome.
            may_be_in_flight = True
            dma_start_attempted = True
            transfer_started = True
            evidence.update({"dma_start_attempted": True, "transfer_started": True,
                             "dma_may_be_active": True})
            api.write32(preflight.DMA_BASE, DMA_S2MM_LENGTH, preflight.RX_BYTES)

            api.write32(preflight.DMA_BASE, DMA_MM2S_DMACR, DMA_RUN)
            _wait_not_halted(api, DMA_MM2S_DMASR, "MM2S", deadline, True)
            api.write32(preflight.DMA_BASE, DMA_MM2S_SA, mm2s_address)
            api.write32(preflight.DMA_BASE, DMA_MM2S_LENGTH, preflight.TX_BYTES)

            s2mm_status, mm2s_status, packer_status = _wait_frame_complete(
                api, deadline, frame_id=frame.frame_id, transfer_started=True,
            )
            # Both Idle+IOC plus Packer DONE means neither DMA can still own
            # either buffer; later data/stat mismatches are safe to release.
            may_be_in_flight = False
            evidence["dma_may_be_active"] = False
            afi_after = read_afi1_snapshot(api, f"after_dma_frame_{index}")
            evidence["afi1_snapshots"].append(afi_after)
            actual_s2mm_length = api.read32(preflight.DMA_BASE, DMA_S2MM_LENGTH)
            if actual_s2mm_length != preflight.RX_BYTES:
                raise ExecutionFailure(
                    f"S2MM actual length for frame {frame.frame_id} is {actual_s2mm_length}, expected {preflight.RX_BYTES}",
                    transfer_started=False,
                    evidence={"s2mm_actual_length": actual_s2mm_length,
                              "s2mm_dmasr": s2mm_status, "mm2s_dmasr": mm2s_status},
                )
            pixels = api.read32(preflight.CHECKER_BASE, PACKER_ACCEPTED_PIXELS)
            beats = api.read32(preflight.CHECKER_BASE, PACKER_SENT_BEATS)
            packer_error = api.read32(preflight.CHECKER_BASE, PACKER_ERROR_CODE)
            if (packer_status != PACKER_DONE or pixels != preflight.PIXEL_COUNT or
                    beats != preflight.RX_BEATS or packer_error != 0):
                raise ExecutionFailure(
                    f"Packer completion counters invalid for frame {frame.frame_id}: "
                    f"status=0x{packer_status:08x} pixels={pixels} beats={beats} error={packer_error}",
                    transfer_started=False,
                    evidence={"packer_status": packer_status, "accepted_pixels": pixels,
                              "sent_beats": beats, "packer_error": packer_error},
                )
            after_checker = _checker_snapshot(api)
            _check_checker_frame(previous_checker, after_checker, frame)
            api.sync_from_device(s2mm_buffer)
            returned = bytes(memoryview(s2mm_buffer).cast("B")[:preflight.RX_BYTES])
            completion = {"frame_id": frame.frame_id, "frame_index": index,
                          "s2mm_dmasr": s2mm_status, "mm2s_dmasr": mm2s_status,
                          "s2mm_actual_length": actual_s2mm_length,
                          "packer_status": packer_status, "accepted_pixels": pixels,
                          "sent_beats": beats, "packer_error": packer_error,
                          "checker_before": dict(previous_checker), "checker_after": dict(after_checker)}
            capture_record = None
            if frame_limit == 1 or returned != frame.binary:
                directory = (Path(diagnostic_dir) if diagnostic_dir is not None else
                             plan.manifest_path.parent / "mismatch-diagnostics")
                capture_stage = "step2" if frame_limit == 1 else "step3"
                stem = f"{capture_stage}_frame{index:02d}_id{frame.frame_id:010d}"
                tx_raw = bytes(memoryview(mm2s_buffer).cast("B")[:preflight.TX_BYTES])
                try:
                    capture_record = persist_rx_diagnostics(
                        directory, stem, returned, frame.binary, tx=tx_raw,
                        artifacts=evidence["artifact_fingerprints"],
                        context={**completion,
                                 "physical_gate": evidence["post_alloc_physical_gate"],
                                 "boot_id": plan.boot_id,
                                 "manifest_sha256": plan.manifest_sha256,
                                 "afi1_before_dma": afi_before,
                                 "afi1_after_dma": afi_after})
                    evidence["rx_diagnostics"] = capture_record
                except BaseException as save_exc:
                    retain_for_evidence = True
                    evidence["buffers_retained_for_evidence"] = True
                    evidence["rx_diagnostics"] = {"capture_status": "FAILED",
                        "save_error": f"{type(save_exc).__name__}: {save_exc}",
                        "directory": str(directory), "stem": stem,
                        "partial_files_may_exist": True,
                        "planned_paths": {
                            "rx_path": str(directory / f"{stem}_rx_raw.bin"),
                            "tx_path": str(directory / f"{stem}_tx_raw.bin"),
                            "expected_golden_path": str(directory / f"{stem}_expected_golden.bin"),
                            "diagnostics_path": str(directory / f"{stem}_diagnostics.json")}}
                    raise ExecutionFailure(
                        f"could not persist completed-frame TX/RX/Golden evidence: {save_exc}",
                        transfer_started=False,
                        evidence={"rx_diagnostics": evidence["rx_diagnostics"]}) from save_exc
            if returned != frame.binary:
                mismatches = sum(left != right for left, right in zip(returned, frame.binary))
                first = next((offset for offset, pair in enumerate(zip(returned, frame.binary))
                              if pair[0] != pair[1]), None)
                evidence["failed_frame_completion"] = completion
                evidence.update(byte_mismatches=mismatches, first_mismatch=first,
                                s2mm_actual_length=actual_s2mm_length)
                raise ExecutionFailure(
                    f"S2MM Binary8 differs from golden for frame {frame.frame_id}: "
                    f"{mismatches} mismatches, first offset={first}",
                    transfer_started=False,
                    evidence={"byte_mismatches": mismatches, "first_mismatch": first,
                              "s2mm_actual_length": actual_s2mm_length},
                )

            evidence["frames"].append({
                "frame_id": frame.frame_id,
                "s2mm_dmasr": s2mm_status,
                "mm2s_dmasr": mm2s_status,
                "packer_status": packer_status,
                "accepted_pixels": pixels,
                "sent_beats": beats,
                "s2mm_actual_length": actual_s2mm_length,
                "raw_capture": capture_record,
                "checker": {
                    "pixels": after_checker["pixels"],
                    "sofs": after_checker["sofs"],
                    "eols": after_checker["eols"],
                    "checksum": after_checker["output_checksum"],
                    "foreground_pixels": after_checker["output_checksum"] // 255,
                    "input_bytes_delta": _u32_delta(after_checker["input_bytes"], previous_checker["input_bytes"]),
                    "input_beats_delta": _u32_delta(after_checker["input_beats"], previous_checker["input_beats"]),
                    "input_tlast_delta": _u32_delta(after_checker["input_tlast"], previous_checker["input_tlast"]),
                    "packet_done_delta": _u32_delta(after_checker["packet_done_count"], previous_checker["packet_done_count"]),
                    "output_total_delta": _u32_delta(after_checker["output_total"], previous_checker["output_total"]),
                },
                "byte_mismatches": 0,
            })
            previous_checker = after_checker

        api.free(s2mm_buffer)
        api.free(mm2s_buffer)
        released = True
        final_cma_free = record_release_cma()
        require_watermark(final_cma_free, "after release")
        evidence["status"] = "DMA_ONCE_PASS"
        evidence.update({"dma_start_attempted": dma_start_attempted,
                         "transfer_started": transfer_started,
                         "dma_may_be_active": False})
        return evidence
    except ExecutionFailure as exc:
        if (may_be_in_flight or retain_for_evidence) and not released and mm2s_buffer is not None and s2mm_buffer is not None:
            QUARANTINED_BUFFERS.append((api, mm2s_buffer, s2mm_buffer, str(exc)))
            exc.dma_may_be_active = may_be_in_flight
        elif not released:
            if s2mm_buffer is not None:
                api.free(s2mm_buffer)
            if mm2s_buffer is not None:
                api.free(mm2s_buffer)
            released = True
            record_release_cma()
        exc.dma_start_attempted = exc.dma_start_attempted or dma_start_attempted
        exc.transfer_started = exc.transfer_started or transfer_started
        exc.dma_may_be_active = may_be_in_flight
        evidence.update({"dma_start_attempted": exc.dma_start_attempted,
                         "transfer_started": exc.transfer_started,
                         "dma_may_be_active": exc.dma_may_be_active})
        evidence["board_accessed"] = bool(getattr(api, "board_accessed", False))
        exc.evidence = {**evidence, **exc.evidence}
        raise
    except BaseException as exc:
        if may_be_in_flight and not released and mm2s_buffer is not None and s2mm_buffer is not None:
            QUARANTINED_BUFFERS.append((api, mm2s_buffer, s2mm_buffer, str(exc)))
            raise ExecutionFailure(f"unexpected error with DMA potentially in flight: {exc}",
                                   transfer_started=transfer_started,
                                   dma_start_attempted=dma_start_attempted,
                                   dma_may_be_active=True, evidence=evidence) from exc
        if not released:
            if s2mm_buffer is not None:
                api.free(s2mm_buffer)
            if mm2s_buffer is not None:
                api.free(mm2s_buffer)
            record_release_cma()
        evidence.update({"dma_start_attempted": dma_start_attempted,
                         "transfer_started": transfer_started,
                         "dma_may_be_active": False})
        raise ExecutionFailure(f"offline executor error: {exc}",
                               transfer_started=transfer_started,
                               dma_start_attempted=dma_start_attempted,
                               dma_may_be_active=False, evidence=evidence) from exc


class _MockBuffer(bytearray):
    def __init__(self, size: int, device_address: int):
        super().__init__(size)
        self.device_address = device_address


class MockBoardAPI:
    """Deterministic register-level simulation; this is never board evidence."""

    board_accessed = False
    stage = "mock"

    def __init__(self, plan: preflight.ValidatedRun, *, initial_idle: bool = True,
                 s2mm_error_frame: int | None = None,
                 force_nonidle_frame: int | None = None,
                 s2mm_address_override: int | None = None,
                 corrupt_s2mm_frame: int | None = None):
        self.plan = plan
        self.allocations: list[int] = []
        self.free_count = 0
        self.write_trace: list[tuple[int, int, int]] = []
        self.last_s2mm_address = 0
        self.last_mm2s_address = 0
        self.s2mm_error_frame = s2mm_error_frame
        self.force_nonidle_frame = force_nonidle_frame
        self.s2mm_address_override = s2mm_address_override
        self.corrupt_s2mm_frame = corrupt_s2mm_frame
        self.cma_ranges: tuple[tuple[int, int], ...] = ((0, 0x20000000),)
        self.sync_to_device_trace: list[tuple[Any, bytes]] = []
        self._start_cma_free_kib = max(32768, preflight.MIN_CMA_KIB + 128)
        self._cma_free_kib = self._start_cma_free_kib
        self._next_address = 0x10000000
        self._current_frame_index = -1
        self._frame_limit = len(plan.frames)
        self._run_allocation_index = 0
        self.overlay_load_count = 0
        self._overlay_loaded = False
        self._mm2s_buffer: _MockBuffer | None = None
        self._s2mm_buffer: _MockBuffer | None = None
        self._packer_status = 0
        self._packer_pixels = 0
        self._packer_beats = 0
        self._packer_error = 0
        self._actual_s2mm_length = 0
        self.physical_read_trace: list[int] = []
        self._checker: dict[int, int] = {
            CHECKER_STATUS: 0,
            CHECKER_ERROR_CODE: 0,
            CHECKER_FRAME_ID: 0,
            CHECKER_PACKET_DONE_COUNT: 0,
            CHECKER_PACKET_ERROR_COUNT: 0,
            CHECKER_INPUT_BYTES: 0,
            CHECKER_INPUT_BEATS: 0,
            CHECKER_INPUT_TLAST: 0,
            CHECKER_INPUT_FIRST_WORD: 0,
            CHECKER_INPUT_LAST_WORD: 0,
            CHECKER_INPUT_CHECKSUM: 0,
            CHECKER_PIXELS: 0,
            CHECKER_SOFS: 0,
            CHECKER_EOLS: 0,
            CHECKER_OUTPUT_CHECKSUM: 0,
            CHECKER_OUTPUT_TOTAL: 0,
            CHECKER_OUTPUT_STALLS: 0,
        }
        initial = DMA_IDLE if initial_idle else 0
        self._dma: dict[int, int] = {
            DMA_MM2S_DMASR: initial,
            DMA_S2MM_DMASR: initial,
            DMA_MM2S_LENGTH: 0,
            DMA_S2MM_LENGTH: 0,
        }
        self._monotonic = 0.0

    def cma_free_kib(self) -> int:
        return self._cma_free_kib

    def load_overlay(self) -> None:
        self.overlay_load_count += 1
        self._overlay_loaded = True

    def open_mmio(self) -> None:
        return None

    def audit_environment(self, *, require_empty_shutdown: bool,
                          require_loaded_bit: bool) -> dict[str, Any]:
        if require_loaded_bit and not self._overlay_loaded:
            raise preflight.PreflightError("mock PYNQ state does not identify the loaded BIT")
        return {
            "boot_id": self.plan.boot_id,
            "process_ownership": {"findings": [], "denied": []},
            "fpga_manager": {"state": "operating", "flags": "0"},
            "pynq_state": {
                "shutdown_ips_empty": True,
                "loaded_bit_sha256": self.plan.bit_sha256 if self._overlay_loaded else None,
                "load_status": "LOADED" if self._overlay_loaded else "NO_GLOBAL_STATE",
                "require_empty_shutdown": require_empty_shutdown,
            },
            "kernel_log": [f"mock kernel baseline {index}" for index in range(12)],
        }

    def current_boot_id(self) -> str:
        return self.plan.boot_id

    def begin_run(self, frame_limit: int, _expected_prior_frames: int) -> None:
        self._frame_limit = frame_limit
        self._current_frame_index = -1
        self._run_allocation_index = 0

    def before_frame(self, frame: preflight.FrameArtifact, _index: int) -> None:
        if self.force_nonidle_frame == frame.frame_id:
            self._dma[DMA_MM2S_DMASR] = 0
            self._dma[DMA_S2MM_DMASR] = 0

    def allocate(self, size: int) -> _MockBuffer:
        self.allocations.append(size)
        address = (self._next_address + preflight.PAGE_BYTES - 1) & ~(preflight.PAGE_BYTES - 1)
        if self._run_allocation_index == 1 and self.s2mm_address_override is not None:
            address = self.s2mm_address_override
        pages = (size + preflight.PAGE_BYTES - 1) // preflight.PAGE_BYTES
        self._next_address = address + pages * preflight.PAGE_BYTES
        self._cma_free_kib -= pages * preflight.PAGE_BYTES // 1024
        buffer = _MockBuffer(size, address)
        if self._run_allocation_index == 0:
            self._mm2s_buffer = buffer
        elif self._run_allocation_index == 1:
            self._s2mm_buffer = buffer
        else:
            raise AssertionError("runner allocated a third buffer in one stage")
        self._run_allocation_index += 1
        return buffer

    def device_address(self, buffer: _MockBuffer) -> int:
        return buffer.device_address

    def write_buffer(self, buffer: _MockBuffer, data: bytes) -> None:
        buffer[:len(data)] = data

    def sync_to_device(self, _buffer: Any) -> None:
        self.sync_to_device_trace.append((_buffer, bytes(memoryview(_buffer).cast("B"))))

    def sync_from_device(self, _buffer: Any) -> None:
        return None

    def free(self, buffer: _MockBuffer) -> None:
        if buffer is self._mm2s_buffer:
            size = preflight.TX_BYTES
        elif buffer is self._s2mm_buffer:
            size = preflight.RX_BYTES
        else:
            raise AssertionError("free of an unknown buffer")
        pages = (size + preflight.PAGE_BYTES - 1) // preflight.PAGE_BYTES
        self._cma_free_kib += pages * preflight.PAGE_BYTES // 1024
        self.free_count += 1

    def monotonic(self) -> float:
        return self._monotonic

    def sleep(self, seconds: float) -> None:
        self._monotonic += seconds

    def read32(self, base: int, offset: int) -> int:
        if base == preflight.CHECKER_BASE:
            if offset == 0x04:
                return preflight.PACKET_MAGIC
            if offset == PACKER_STATUS:
                return self._packer_status
            if offset == PACKER_ACCEPTED_PIXELS:
                return self._packer_pixels
            if offset == PACKER_SENT_BEATS:
                return self._packer_beats
            if offset == PACKER_ERROR_CODE:
                return self._packer_error
            if offset == PACKER_SIGNATURE:
                return preflight.PACKER_SIGNATURE
            return self._checker.get(offset, 0)
        if base == preflight.DMA_BASE:
            return self._dma.get(offset, 0)
        raise AssertionError(f"unexpected MMIO base 0x{base:x}")

    def read_phys32(self, address: int) -> int:
        self.physical_read_trace.append(address)
        values = {
            AFI1_RDCHAN_CTRL: 0x00000000,
            AFI1_WRCHAN_CTRL: 0x00000000,
            SLCR_FPGA0_CLK_CTRL: 0x0001A00A,
            SLCR_FPGA_RST_CTRL: 0x0000000F,
        }
        try:
            return values[address]
        except KeyError as exc:
            raise AssertionError(f"unexpected physical register 0x{address:x}") from exc

    def write32(self, base: int, offset: int, value: int) -> None:
        self.write_trace.append((base, offset, value))
        if base == preflight.CHECKER_BASE and offset == PACKER_ARM:
            if value != 1 or self._packer_status & (PACKER_BUSY | PACKER_FAULT):
                raise AssertionError("invalid/repeated Packer arm")
            self._packer_status = PACKER_BUSY
            self._packer_pixels = 0
            self._packer_beats = 0
            self._packer_error = 0
            self._current_frame_index += 1
            if self._current_frame_index >= self._frame_limit:
                raise AssertionError("Packer armed more times than the manifest frame count")
            return
        if base != preflight.DMA_BASE:
            raise AssertionError(f"unexpected MMIO write base 0x{base:x}")
        if offset in (DMA_MM2S_DMASR, DMA_S2MM_DMASR):
            self._dma[offset] = self._dma.get(offset, 0) & ~value
            return
        if offset == DMA_S2MM_DMACR:
            if value != DMA_RUN:
                raise AssertionError("S2MM control write must set RS only, without reset")
            return
        if offset == DMA_MM2S_DMACR:
            if value != DMA_RUN:
                raise AssertionError("MM2S control write must set RS only, without reset")
            return
        if offset == DMA_S2MM_DA:
            self.last_s2mm_address = value
            return
        if offset == DMA_MM2S_SA:
            self.last_mm2s_address = value
            return
        if offset == DMA_S2MM_LENGTH:
            if value != preflight.RX_BYTES:
                raise AssertionError("S2MM length is not 76800 bytes")
            self._dma[offset] = value
            return
        if offset == DMA_MM2S_LENGTH:
            if value != preflight.TX_BYTES:
                raise AssertionError("MM2S length is not 230432 bytes")
            self._dma[offset] = value
            self._complete_mock_frame()
            return
        raise AssertionError(f"unexpected DMA register write offset 0x{offset:x}")

    def _complete_mock_frame(self) -> None:
        frame = self.plan.frames[self._current_frame_index]
        if self.s2mm_error_frame == frame.frame_id:
            self._dma[DMA_S2MM_DMASR] = DMA_IDLE | DMA_ERR_IRQ | DMA_SLAVE_ERROR
            self._dma[DMA_MM2S_DMASR] = DMA_IDLE | DMA_IOC_IRQ
            return
        assert self._s2mm_buffer is not None
        self._s2mm_buffer[:preflight.RX_BYTES] = frame.binary
        if self.corrupt_s2mm_frame == frame.frame_id:
            self._s2mm_buffer[17] ^= 0xFF
        self._actual_s2mm_length = preflight.RX_BYTES
        self._dma[DMA_S2MM_DMASR] = DMA_IDLE | DMA_IOC_IRQ
        self._dma[DMA_MM2S_DMASR] = DMA_IDLE | DMA_IOC_IRQ
        self._packer_status = PACKER_DONE
        self._packer_pixels = preflight.PIXEL_COUNT
        self._packer_beats = preflight.RX_BEATS
        self._checker[CHECKER_STATUS] = CHECKER_PACKET_DONE | CHECKER_OUTPUT_COMPLETE
        self._checker[CHECKER_FRAME_ID] = frame.frame_id
        self._checker[CHECKER_PACKET_DONE_COUNT] += 1
        self._checker[CHECKER_INPUT_BYTES] += preflight.TX_BYTES
        self._checker[CHECKER_INPUT_BEATS] += preflight.TX_BEATS
        self._checker[CHECKER_INPUT_TLAST] += 1
        self._checker[CHECKER_INPUT_FIRST_WORD] = int.from_bytes(frame.packet[:4], "little")
        self._checker[CHECKER_INPUT_LAST_WORD] = int.from_bytes(frame.packet[-4:], "little")
        self._checker[CHECKER_INPUT_CHECKSUM] = (
            self._checker[CHECKER_INPUT_CHECKSUM] + frame.packet_checksum
        ) & 0xFFFFFFFF
        self._checker[CHECKER_PIXELS] = preflight.PIXEL_COUNT
        self._checker[CHECKER_SOFS] = 1
        self._checker[CHECKER_EOLS] = preflight.HEIGHT
        self._checker[CHECKER_OUTPUT_CHECKSUM] = frame.binary_checksum
        self._checker[CHECKER_OUTPUT_TOTAL] += preflight.PIXEL_COUNT


class PynqBoardAPI:
    """Lazy PYNQ adapter. Construct only after all static gates and guard."""

    def __init__(self, plan: preflight.ValidatedRun, *, stage: str,
                 cma_ranges: tuple[tuple[int, int], ...] | None = None,
                 phys_reader: Any = None):
        self.plan = plan
        self.stage = stage
        self.cma_ranges = cma_ranges
        self._overlay = None
        self._mmio: dict[int, Any] = {}
        self._allocate = None
        self._pynq = None
        self._phys_reader = read_phys32_readonly if phys_reader is None else phys_reader
        self.board_accessed = False

    def _load_pynq(self) -> Any:
        global _PYNQ_IMPORTED
        if self._pynq is None:
            import pynq

            self._pynq = pynq
            _PYNQ_IMPORTED = True
        return self._pynq

    def audit_environment(self, *, require_empty_shutdown: bool,
                          require_loaded_bit: bool) -> dict[str, Any]:
        pynq = self._load_pynq()
        module_file = getattr(pynq, "__file__", None)
        if not module_file:
            raise preflight.PreflightError("PYNQ package path is unavailable for global-state audit")
        process_ownership = preflight.audit_process_ownership()
        fpga_manager = preflight.read_fpga_manager()
        pynq_state = preflight.inspect_pynq_global_state(
            module_file,
            require_empty_shutdown=require_empty_shutdown,
            require_loaded_bit=require_loaded_bit,
            expected_bit_path=self.plan.bit_path if require_loaded_bit else None,
            expected_bit_sha256=self.plan.bit_sha256 if require_loaded_bit else None,
        )
        return {
            "boot_id": preflight.read_boot_id(),
            "process_ownership": process_ownership,
            "fpga_manager": fpga_manager,
            "pynq_state": pynq_state,
            "kernel_log": preflight.read_kernel_log(),
        }

    def current_boot_id(self) -> str:
        return preflight.read_boot_id()

    def load_overlay(self) -> None:
        pynq = self._load_pynq()
        self._overlay = pynq.Overlay(str(self.plan.bit_path), download=False)
        _mark_board_accessed()
        self.board_accessed = True
        self._overlay.download()

    def open_mmio(self) -> None:
        pynq = self._load_pynq()

        self._allocate = pynq.allocate
        _mark_board_accessed()
        self.board_accessed = True
        regions = {preflight.CHECKER_BASE: pynq.MMIO(preflight.CHECKER_BASE, 0x10000)}
        if self.stage != "overlay-only":
            regions[preflight.DMA_BASE] = pynq.MMIO(preflight.DMA_BASE, 0x10000)
        self._mmio = regions

    def allocate(self, size: int) -> Any:
        if self._allocate is None:
            raise RuntimeError("PYNQ MMIO/allocator has not been initialized")
        return self._allocate(shape=(size,), dtype="u1")

    def device_address(self, buffer: Any) -> int:
        # Simple AXI HP DMA consumes CPU physical DDR addresses, not an IOVA.
        try:
            if isinstance(buffer.physical_address, bool) or isinstance(buffer.device_address, bool):
                raise TypeError("boolean physical address")
            physical = operator.index(buffer.physical_address)
            device = operator.index(buffer.device_address)
        except (AttributeError, TypeError, ValueError) as exc:
            raise preflight.PreflightError("PYNQ buffer physical_address is unavailable") from exc
        if physical != device:
            raise preflight.PreflightError("PYNQ device_address differs from physical_address")
        if physical <= 0:
            raise preflight.PreflightError("PYNQ physical_address must be a nonzero integer")
        return physical

    def write_buffer(self, buffer: Any, data: bytes) -> None:
        memoryview(buffer).cast("B")[:len(data)] = data

    def sync_to_device(self, buffer: Any) -> None:
        method = getattr(buffer, "sync_to_device", None)
        if callable(method):
            method()
        else:
            flush = getattr(buffer, "flush", None)
            if callable(flush):
                flush()
            else:
                raise RuntimeError("buffer has neither sync_to_device nor flush cache operation")

    def sync_from_device(self, buffer: Any) -> None:
        method = getattr(buffer, "sync_from_device", None)
        if callable(method):
            method()
        else:
            invalidate = getattr(buffer, "invalidate", None)
            if callable(invalidate):
                invalidate()
            else:
                raise RuntimeError("buffer has neither sync_from_device nor invalidate cache operation")

    def free(self, buffer: Any) -> None:
        method = getattr(buffer, "freebuffer", None)
        if not callable(method):
            raise RuntimeError("PYNQ buffer has no explicit freebuffer method")
        method()

    def read32(self, base: int, offset: int) -> int:
        return int(self._mmio[base].read(offset))

    def read_phys32(self, address: int) -> int:
        _mark_board_accessed()
        self.board_accessed = True
        return self._phys_reader(address)

    def write32(self, base: int, offset: int, value: int) -> None:
        self._mmio[base].write(offset, value)

    def cma_free_kib(self) -> int:
        return preflight.read_cma_meminfo()["CmaFree"]

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def fail_hold(self, evidence_path: str | None, evidence: Mapping[str, Any]) -> None:
        _install_hold_signals()
        stage_status = str(evidence.get("status", ""))
        payload = {**evidence, "status": stage_status if stage_status.endswith("_FAIL_HOLD") else "FAIL_HOLD",
                   "hardware_stage": self.stage, "buffers_released": False,
                   "hold_reason": evidence.get("hold_reason", "DMA may own CMA buffers; no reset/retry/free is permitted")}
        try:
            _emit(payload, evidence_path)
        except BaseException:
            # Evidence persistence is best effort; it must never release a
            # potentially DMA-owned CMA buffer by escaping before HOLD.
            pass
        _hold_forever()


def _install_hold_signals() -> None:
    def ignore_signal(_signum: int, _frame: Any) -> None:
        return None

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signum, ignore_signal)
        except (ValueError, OSError):
            pass


def _hold_forever() -> None:
    _install_hold_signals()
    # Keep the Python references, PYNQ buffers, and device state alive.
    # The OS/hardware remains the only way out of a wedged MMIO read.
    while True:
        time.sleep(60)


def _receipt_identity(plan: preflight.ValidatedRun) -> dict[str, Any]:
    return {
        "schema": 1,
        "work_order": "WO-0015",
        "stage": "overlay-only",
        "status": "OVERLAY_LOAD_PASS",
        "load_performed": True,
        "boot_id": plan.boot_id,
        "manifest_sha256": plan.manifest_sha256,
        "build_id": plan.build_id,
        "bit_sha256": plan.bit_sha256,
        "hwh_sha256": plan.hwh_sha256,
        "clock_mhz": 50,
        "checker_signature": preflight.PACKET_MAGIC,
        "packer_signature": preflight.PACKER_SIGNATURE,
    }


def _audit_digest(runtime_audit: Mapping[str, Any] | None) -> str:
    canonical = json.dumps(runtime_audit or {}, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False).encode("utf-8")
    return preflight.sha256_bytes(canonical)


def write_overlay_pass_receipt(path: str | Path,
                               plan: preflight.ValidatedRun,
                               runtime_audit: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Create a durable O_EXCL receipt proving the authorized BIT load stage."""
    target = Path(path)
    payload = {**_receipt_identity(plan),
               "runtime_audit_sha256": _audit_digest(runtime_audit)}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise preflight.PreflightError("overlay-only PASS receipt already exists") from exc
    except OSError as exc:
        raise preflight.PreflightError(f"cannot create overlay-only PASS receipt: {exc}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            dir_fd = os.open(str(target.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except OSError as exc:
        raise preflight.PreflightError(f"overlay-only receipt was claimed but write failed: {exc}") from exc
    return payload


def require_overlay_pass_receipt(path: str | Path,
                                 plan: preflight.ValidatedRun) -> dict[str, Any]:
    try:
        receipt = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise preflight.PreflightError(f"cannot read overlay-only PASS receipt: {exc}") from exc
    expected = _receipt_identity(plan)
    if not isinstance(receipt, dict):
        raise preflight.PreflightError("overlay-only PASS receipt must be a JSON object")
    for field, value in expected.items():
        if receipt.get(field) != value:
            raise preflight.PreflightError(f"overlay-only PASS receipt {field} does not match current run")
    if (set(receipt) != set(expected) | {"runtime_audit_sha256"} or
            not isinstance(receipt.get("runtime_audit_sha256"), str) or
            len(receipt["runtime_audit_sha256"]) != 64):
        raise preflight.PreflightError("overlay-only PASS receipt fields differ from the reviewed schema")
    return receipt


def _dma_pass_identity(plan: preflight.ValidatedRun) -> dict[str, Any]:
    return {
        "schema": 1,
        "work_order": "WO-0015",
        "stage": "dma-single",
        "status": "DMA_SINGLE_PASS",
        "boot_id": plan.boot_id,
        "manifest_sha256": plan.manifest_sha256,
        "build_id": plan.build_id,
        "bit_sha256": plan.bit_sha256,
        "hwh_sha256": plan.hwh_sha256,
        "clock_mhz": 50,
        "frame_count": 1,
        "frame_ids": [plan.frames[0].frame_id],
        "s2mm_bytes": preflight.RX_BYTES,
        "s2mm_beats": preflight.RX_BEATS,
    }


def write_dma_single_pass_receipt(path: str | Path,
                                  plan: preflight.ValidatedRun,
                                  runtime_audit: Mapping[str, Any] | None = None) -> dict[str, Any]:
    target = Path(path)
    payload = {**_dma_pass_identity(plan),
               "runtime_audit_sha256": _audit_digest(runtime_audit)}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise preflight.PreflightError("dma-single PASS receipt already exists") from exc
    except OSError as exc:
        raise preflight.PreflightError(f"cannot create dma-single PASS receipt: {exc}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            dir_fd = os.open(str(target.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except OSError as exc:
        raise preflight.PreflightError(f"dma-single receipt was claimed but write failed: {exc}") from exc
    return payload


def require_dma_single_pass_receipt(path: str | Path,
                                    plan: preflight.ValidatedRun) -> dict[str, Any]:
    try:
        receipt = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise preflight.PreflightError(f"cannot read dma-single PASS receipt: {exc}") from exc
    expected = _dma_pass_identity(plan)
    if not isinstance(receipt, dict):
        raise preflight.PreflightError("dma-single PASS receipt must be a JSON object")
    for field, value in expected.items():
        if receipt.get(field) != value:
            raise preflight.PreflightError(f"dma-single PASS receipt {field} does not match current run")
    if (set(receipt) != set(expected) | {"runtime_audit_sha256"} or
            not isinstance(receipt.get("runtime_audit_sha256"), str) or
            len(receipt["runtime_audit_sha256"]) != 64):
        raise preflight.PreflightError("dma-single PASS receipt fields differ from the reviewed schema")
    return receipt


def _emit(result: Mapping[str, Any], output: str | None) -> None:
    encoded = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if output:
        target = Path(output).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


def _assert_no_terminal_failure() -> None:
    if TERMINAL_FAILURE.exists():
        raise preflight.PreflightError(
            f"WO-0015 terminal-failure marker exists: {TERMINAL_FAILURE}; later stages are blocked"
        )


def _record_terminal_failure(stage: str, plan: preflight.ValidatedRun,
                             reason: str) -> None:
    payload = {
        "schema": 1,
        "work_order": "WO-0015",
        "status": "TERMINAL_FAILURE",
        "stage": stage,
        "boot_id": plan.boot_id,
        "manifest_sha256": plan.manifest_sha256,
        "build_id": plan.build_id,
        "bit_sha256": plan.bit_sha256,
        "reason": reason,
    }
    try:
        TERMINAL_FAILURE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(TERMINAL_FAILURE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        return
    except OSError as exc:
        raise preflight.PreflightError(
            f"stage failed and terminal marker could not be persisted: {exc}"
        ) from exc


def _post_stage_audit(api: Any, plan: preflight.ValidatedRun,
                      before: Mapping[str, Any]) -> dict[str, Any]:
    after = api.audit_environment(require_empty_shutdown=False, require_loaded_bit=True)
    before_boot = str(before.get("boot_id", ""))
    after_boot = str(after.get("boot_id", ""))
    if before_boot != plan.boot_id or after_boot != plan.boot_id:
        raise preflight.PreflightError(
            f"boot_id changed across hardware stage ({before_boot!r} -> {after_boot!r})"
        )
    dmesg_delta = preflight.kernel_log_delta(
        list(before.get("kernel_log", [])), list(after.get("kernel_log", [])))
    return {
        "boot_id_before": before_boot,
        "boot_id_after": after_boot,
        "process_ownership": after.get("process_ownership"),
        "fpga_manager": after.get("fpga_manager"),
        "pynq_state": after.get("pynq_state"),
        "dmesg_health": "PASS",
        "dmesg_new_lines": dmesg_delta,
    }


def _runtime_preflight(plan: preflight.ValidatedRun, *, boot_id_path: str,
                       meminfo_path: str, reserved_memory_path: str, stage: str
                       ) -> tuple[str, dict[str, int], tuple[tuple[int, int], ...], dict[str, Any]]:
    live_boot_id = preflight.read_boot_id(boot_id_path)
    if live_boot_id != plan.boot_id or live_boot_id != preflight.AUTHORIZED_BOOT_ID:
        raise preflight.PreflightError("live boot_id differs from the manifest and approved WO-0015 boot")
    meminfo = preflight.read_cma_meminfo(meminfo_path)
    cma_admission = preflight.read_cma_admission(reserved_memory_path, meminfo=meminfo)
    cma_ranges = cma_admission.ranges
    preflight.validate_runtime_cma(meminfo, cma_ranges, (plan.hp0_range, plan.hp1_range))
    if stage not in preflight.BOARD_STAGES:
        raise preflight.PreflightError(f"unsupported runtime audit stage {stage!r}")
    api = PynqBoardAPI(plan, stage=stage, cma_ranges=cma_ranges)
    runtime_audit = dict(api.audit_environment(
        require_empty_shutdown=stage == "overlay-only",
        require_loaded_bit=stage != "overlay-only"))
    runtime_audit["cma_admission"] = cma_admission.as_evidence()
    if runtime_audit.get("boot_id") != live_boot_id:
        raise preflight.PreflightError("boot_id changed while runtime preflight was being collected")
    return live_boot_id, meminfo, cma_ranges, runtime_audit


def _guard_path(stage: str) -> Path:
    if stage == "overlay-only":
        return OVERLAY_GUARD
    if stage == "dma-single":
        return DMA_SINGLE_GUARD
    if stage == "dma-three":
        return DMA_THREE_GUARD
    raise preflight.PreflightError(f"unknown board stage {stage!r}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WO-0015 offline-first one-shot PS executor")
    parser.add_argument("--mode", choices=("offline", "mock", "overlay-only", "dma-single", "dma-three"),
                        default="offline")
    parser.add_argument("--manifest", required=True, help="reviewed WO-0015 run manifest")
    parser.add_argument("--manifest-sha256", help="separately reviewed manifest SHA-256")
    parser.add_argument("--execute", action="store_true", help="required for real board access")
    parser.add_argument("--authorize-stage", choices=preflight.BOARD_STAGES,
                        help="name the exact authorized stage: overlay-only, dma-single, or dma-three")
    parser.add_argument("--approve-once", action="store_true",
                        help="consume the fixed one-shot guard for the selected stage")
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--evidence-json", help="write a machine-readable stage receipt/result")
    return parser


def _overlay_stage(plan: preflight.ValidatedRun, live_boot_id: str,
                   evidence_path: str | None,
                   runtime_snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
    _assert_no_terminal_failure()
    preflight.validate_manifest(plan.manifest_path, plan.manifest_sha256)
    api = PynqBoardAPI(plan, stage="overlay-only")
    before = dict(runtime_snapshot or api.audit_environment(
        require_empty_shutdown=True, require_loaded_bit=False))
    if before.get("boot_id") != live_boot_id or live_boot_id != plan.boot_id:
        raise preflight.PreflightError("overlay stage boot_id differs from its audited run identity")
    preflight.claim_once_guard(_guard_path("overlay-only"), boot_id=live_boot_id,
                               manifest_sha256=plan.manifest_sha256, stage="overlay-only")
    try:
        afi_before = read_afi1_snapshot(api, "before_overlay_load")
        api.load_overlay()
        afi_after = read_afi1_snapshot(api, "after_overlay_load")
        api.open_mmio()
        # Overlay-only means download plus exactly these two read-only signature reads.
        checker_signature = api.read32(preflight.CHECKER_BASE, 0x04)
        packer_signature = api.read32(preflight.CHECKER_BASE, PACKER_SIGNATURE)
        if checker_signature != preflight.PACKET_MAGIC or packer_signature != preflight.PACKER_SIGNATURE:
            raise preflight.PreflightError(
                f"loaded BIT signature mismatch: checker=0x{checker_signature:08x} "
                f"packer=0x{packer_signature:08x}"
            )
        post_audit = _post_stage_audit(api, plan, before)
        receipt = write_overlay_pass_receipt(OVERLAY_PASS_RECEIPT, plan, post_audit)
        if api.current_boot_id() != plan.boot_id:
            raise preflight.PreflightError("boot_id changed after overlay PASS receipt was written")
        result = {
            "task": "WO-0015", "status": "OVERLAY_LOAD_PASS",
            "operation": "AFI1/SLCR read-only snapshots around Overlay download plus two signature reads",
            "afi1_snapshots": [afi_before, afi_after],
            "board_accessed": bool(getattr(api, "board_accessed", False)),
            "pynq_imported": _PYNQ_IMPORTED,
            "runtime_preflight": before, "post_stage_audit": post_audit, **receipt,
        }
        _emit(result, evidence_path)
        return result
    except BaseException as exc:
        _record_terminal_failure("overlay-only", plan, f"{type(exc).__name__}: {exc}")
        try:
            exc.board_accessed = bool(getattr(api, "board_accessed", False))
        except Exception:
            pass
        raise


def _dma_stage(plan: preflight.ValidatedRun, live_boot_id: str, stage: str,
               evidence_path: str | None, timeout_s: float,
               cma_ranges: tuple[tuple[int, int], ...] | None = None,
               runtime_snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
    _validate_timeout(timeout_s)
    _assert_no_terminal_failure()
    if stage not in ("dma-single", "dma-three"):
        raise preflight.PreflightError(f"invalid DMA stage {stage!r}")
    if len(plan.frames) != 3:
        raise preflight.PreflightError("DMA stages require the shared three-frame manifest")
    preflight.validate_manifest(plan.manifest_path, plan.manifest_sha256)
    require_overlay_pass_receipt(OVERLAY_PASS_RECEIPT, plan)
    if stage == "dma-three":
        require_dma_single_pass_receipt(DMA_SINGLE_PASS_RECEIPT, plan)
    frame_limit = 1 if stage == "dma-single" else 3
    expected_prior_frames = 0 if stage == "dma-single" else 1
    api = PynqBoardAPI(plan, stage=stage, cma_ranges=cma_ranges)
    before = dict(runtime_snapshot or api.audit_environment(
        require_empty_shutdown=False, require_loaded_bit=True))
    if before.get("boot_id") != live_boot_id or live_boot_id != plan.boot_id:
        raise preflight.PreflightError(f"{stage} boot_id differs from its audited run identity")
    preflight.claim_once_guard(_guard_path(stage), boot_id=live_boot_id,
                               manifest_sha256=plan.manifest_sha256, stage=stage)
    try:
        api.open_mmio()  # Attach to the already loaded BIT; never construct/download Overlay here.
        result = run_frames(plan, api, timeout_s=timeout_s,
                            frame_limit=frame_limit,
                            expected_prior_frames=expected_prior_frames,
                            diagnostic_dir=(Path(evidence_path).parent / f"{stage}-diagnostics"
                                            if evidence_path else plan.manifest_path.parent / f"{stage}-diagnostics"))
        post_audit = _post_stage_audit(api, plan, before)
        result["runtime_preflight"] = before
        result["post_stage_audit"] = post_audit
        result["board_accessed"] = bool(getattr(api, "board_accessed", False))
        result["pynq_imported"] = _PYNQ_IMPORTED
    except ExecutionFailure as exc:
        if exc.dma_may_be_active or exc.evidence.get("buffers_retained_for_evidence"):
            # Once a DMA length write may have started a transfer, no
            # diagnostic operation may let this process unwind and release
            # the retained PYNQ buffers. All persistence is best effort.
            try:
                _install_hold_signals()
                payload = {
                    "task": "WO-0015",
                    **exc.evidence,
                    "status": f"{stage.upper().replace('-', '_')}_FAIL_HOLD",
                    "board_accessed": bool(getattr(api, "board_accessed", False)),
                    "dma_start_attempted": exc.dma_start_attempted,
                    "transfer_started": exc.transfer_started,
                    "dma_may_be_active": exc.dma_may_be_active,
                    "hold_reason": ("RX evidence persistence failed; retain completed buffers without retry/free"
                                    if exc.evidence.get("buffers_retained_for_evidence") else
                                    "DMA may own CMA buffers; no reset/retry/free is permitted"),
                    "error": f"{type(exc).__name__}: {exc}",
                }
                try:
                    _record_terminal_failure(stage, plan, payload["error"])
                except BaseException as marker_exc:
                    payload["terminal_marker_write_error"] = (
                        f"{type(marker_exc).__name__}: {marker_exc}")
                exc.board_accessed = payload["board_accessed"]
                exc.evidence = payload
                api.fail_hold(evidence_path, payload)
            finally:
                _hold_forever()
        payload = {
            "task": "WO-0015",
            **exc.evidence,
            "status": f"{stage.upper().replace('-', '_')}_FAIL",
            "board_accessed": bool(getattr(api, "board_accessed", False)),
            "dma_start_attempted": exc.dma_start_attempted,
            "transfer_started": exc.transfer_started,
            "dma_may_be_active": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        _record_terminal_failure(stage, plan, payload["error"])
        exc.board_accessed = payload["board_accessed"]
        exc.evidence = payload
        raise
    except BaseException as exc:
        _record_terminal_failure(stage, plan, f"{type(exc).__name__}: {exc}")
        try:
            exc.board_accessed = bool(getattr(api, "board_accessed", False))
        except Exception:
            pass
        raise
    result["status"] = "DMA_SINGLE_PASS" if stage == "dma-single" else "DMA_THREE_PASS"
    if stage == "dma-single":
        receipt = write_dma_single_pass_receipt(DMA_SINGLE_PASS_RECEIPT, plan, post_audit)
        result["single_pass_receipt"] = receipt
    if api.current_boot_id() != plan.boot_id:
        _record_terminal_failure(stage, plan, "boot_id changed after stage PASS receipt")
        raise preflight.PreflightError("boot_id changed after stage PASS receipt was written")
    _emit(result, evidence_path)
    return result


def main(argv: list[str] | None = None) -> int:
    global _REAL_BOARD_ACCESS_OCCURRED, _PYNQ_IMPORTED
    _REAL_BOARD_ACCESS_OCCURRED = False
    _PYNQ_IMPORTED = False
    args = _parser().parse_args(argv)
    try:
        _validate_timeout(args.timeout_s)
        plan = preflight.validate_manifest(args.manifest, args.manifest_sha256)
        if args.mode == "offline":
            _emit({
                "task": "WO-0015", "status": "OFFLINE_PREFLIGHT_PASS",
                "board_accessed": False, "pynq_imported": False,
                "manifest_sha256": plan.manifest_sha256, "build_id": plan.build_id,
                "boot_id_bound": plan.boot_id, "clock_mhz": 50,
                "frame_count": len(plan.frames), "tx_bytes_each": preflight.TX_BYTES,
                "rx_bytes_each": preflight.RX_BYTES, "bit_sha256": plan.bit_sha256,
                "hwh_sha256": plan.hwh_sha256, "ready_for_board": plan.ready_for_board,
            }, args.evidence_json)
            return 0
        if args.mode == "mock":
            mock_api = MockBoardAPI(plan)
            result = run_frames(plan, mock_api, timeout_s=args.timeout_s)
            result["status"] = "MOCK_DMA_ONCE_PASS"
            result["board_accessed"] = False
            result["pynq_imported"] = False
            result["register_protocol_exercised"] = True
            _emit(result, args.evidence_json)
            return 0

        if len(plan.frames) != 3:
            raise preflight.PreflightError(
                "authorized board stages require one fixed three-frame manifest so dma-single and dma-three share its hash"
            )
        preflight.require_board_authorization(
            execute=args.execute,
            stage=args.mode,
            authorize_stage=args.authorize_stage,
            approve_once=args.approve_once,
            manifest_sha256=plan.manifest_sha256,
            expected_manifest_sha256=args.manifest_sha256,
            boot_id=plan.boot_id,
            ready_for_board=plan.ready_for_board,
        )
        _assert_no_terminal_failure()
        live_boot_id, meminfo, cma_ranges, runtime_snapshot = _runtime_preflight(
            plan,
            boot_id_path="/proc/sys/kernel/random/boot_id",
            meminfo_path="/proc/meminfo",
            reserved_memory_path="/proc/device-tree/reserved-memory",
            stage=args.mode,
        )
        if meminfo["CmaFree"] < preflight.MIN_CMA_KIB:
            raise preflight.PreflightError(
                "CmaFree is below the static two-buffer CMA minimum before this stage"
            )
        if args.mode == "overlay-only":
            _overlay_stage(plan, live_boot_id, args.evidence_json, runtime_snapshot)
            return 0
        if args.mode in ("dma-single", "dma-three"):
            result = _dma_stage(plan, live_boot_id, args.mode,
                                args.evidence_json, args.timeout_s,
                                cma_ranges=cma_ranges,
                                runtime_snapshot=runtime_snapshot)
            expected_status = "DMA_SINGLE_PASS" if args.mode == "dma-single" else "DMA_THREE_PASS"
            return 0 if result["status"] == expected_status else 2
        raise preflight.PreflightError(f"unsupported mode {args.mode!r}")
    except ExecutionFailure as exc:
        stage = args.mode.upper().replace("-", "_")
        status = f"{stage}_FAIL_HOLD" if exc.dma_may_be_active else f"{stage}_FAIL"
        _emit({"task": "WO-0015", "status": status,
               "board_accessed": bool(getattr(exc, "board_accessed", _REAL_BOARD_ACCESS_OCCURRED)),
               "pynq_imported": _PYNQ_IMPORTED,
               "dma_start_attempted": exc.dma_start_attempted,
               "transfer_started": exc.transfer_started,
               "dma_may_be_active": exc.dma_may_be_active,
               "error": f"{type(exc).__name__}: {exc}",
               **exc.evidence}, args.evidence_json)
        return 3
    except (OSError, ValueError, preflight.PreflightError) as exc:
        status = {
            "offline": "OFFLINE_PREFLIGHT_FAIL",
            "mock": "MOCK_DMA_ONCE_FAIL",
            "overlay-only": "OVERLAY_ONLY_REJECTED",
            "dma-single": "DMA_SINGLE_REJECTED",
            "dma-three": "DMA_THREE_REJECTED",
        }[args.mode]
        if args.mode not in ("offline", "mock") and _REAL_BOARD_ACCESS_OCCURRED:
            status = f"{args.mode.upper().replace('-', '_')}_FAIL"
        _emit({"task": "WO-0015", "status": status,
               "board_accessed": _REAL_BOARD_ACCESS_OCCURRED,
               "pynq_imported": _PYNQ_IMPORTED,
               "error": f"{type(exc).__name__}: {exc}"}, args.evidence_json)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
