"""One-shot camera → JPEG PL → FRG tracker → frame-matched HDMI overlay runner.

This path is the default for camera/hardware invocations through
``scripts/rle/run_counter.py``; ``--jpeg-pl`` remains a compatibility alias.
It requires an explicit --execute switch and routed, hash-matched BIT/HWH files.
It does not download dependencies or stop other apps.
"""
from __future__ import annotations

import argparse
import binascii
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.rle.jpeg_runtime import process_jpeg_frame
from scripts.rle.jpeg_transport import JPEG_MAX_BYTES, JpegRleDmaTransport, iter_raw_mjpeg
from scripts.rle.components import build_native
from scripts.rle.protocol import HEIGHT, RX_CAPACITY, WIDTH
from scripts.rle.transport import RLE_SIGNATURE
from scripts.wo0015.board_preflight import (audit_process_ownership,
    inspect_pynq_global_state, read_boot_id)
from scripts.rle.counter import RleCounter


EXPECTED_PART = "xc7z020clg400-1"
DMA_BASE = 0x40400000
VDMA_BASE = 0x43000000
RLE_BASE = 0x43C20000
OVERLAY_BASE = 0x43C50000
JPEG_BASE = 0x43C80000
OVERLAY_MAP_BYTES = 0x5000
HDMI_MONITOR_BASE = 0x43C40000
HDMI_MONITOR_SIGNATURE = 0x4D435631
HDMI_WIDTH, HDMI_HEIGHT, HDMI_MARKER_X = 1280, 720, 640
HDMI_MARKER_BYTES = 28
ROUTED_STATUS = "ROUTED_NOT_BOARD_VERIFIED"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_native_library(requested: Path | None, output_directory: Path) -> Path:
    """Select a configured native library or build a source-bound local copy."""
    configured = requested or os.environ.get("RLE_NATIVE_LIB")
    if configured is not None:
        library = Path(configured).expanduser().resolve()
        if not library.is_file():
            raise FileNotFoundError(f"native RLE library does not exist: {library}")
        return library

    source = Path(__file__).resolve().parents[1] / "rle" / "native" / "rle_components.cpp"
    source_digest = _sha256(source)
    suffix = ".dll" if os.name == "nt" else ".so"
    library = Path(output_directory).resolve() / f"rle_components_v2_{source_digest[:12]}{suffix}"
    if not library.is_file():
        build_native(library)
    if not library.is_file() or library.stat().st_size == 0:
        raise RuntimeError(f"native RLE build produced no library: {library}")
    return library


def _validate_jpeg_fixture_header(payload: bytes) -> None:
    """Admit only the baseline 640x400 4:2:2/restart-40 JPEG subset used by PL."""
    offset = 2
    saw_sof = False
    saw_dri = False
    dqt = {0: False, 1: False}
    dht = {(table_class, table_id): False
           for table_class in (0, 1) for table_id in (0, 1)}
    component_ids: list[int] = []

    while offset + 4 <= len(payload) - 2:
        if payload[offset] != 0xFF:
            raise ValueError("JPEG fixture has malformed marker framing")
        marker = payload[offset + 1]
        if marker in (0x00, 0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            raise ValueError("JPEG fixture contains an unsupported standalone marker")
        segment_length = int.from_bytes(payload[offset + 2:offset + 4], "big")
        end = offset + 2 + segment_length
        if segment_length < 2 or end > len(payload) - 2 or end > 4096:
            raise ValueError("JPEG fixture header has an invalid or oversized segment")
        body = payload[offset + 4:end]

        if 0xE0 <= marker <= 0xEF:
            pass
        elif marker == 0xDB:
            cursor = 0
            while cursor < len(body):
                info = body[cursor]
                cursor += 1
                precision, table_id = info >> 4, info & 0x0F
                if precision != 0 or table_id > 1 or cursor + 64 > len(body):
                    raise ValueError("JPEG fixture has an unsupported quantization table")
                if any(value == 0 for value in body[cursor:cursor + 64]):
                    raise ValueError("JPEG fixture has a zero quantization entry")
                cursor += 64
                dqt[table_id] = True
        elif marker == 0xC4:
            cursor = 0
            while cursor < len(body):
                info = body[cursor]
                cursor += 1
                table_class, table_id = info >> 4, info & 0x0F
                if table_class > 1 or table_id > 1 or cursor + 16 > len(body):
                    raise ValueError("JPEG fixture has an unsupported Huffman table")
                symbol_count = sum(body[cursor:cursor + 16])
                cursor += 16
                limit = 165 if table_class else 12
                if (symbol_count == 0 or symbol_count > limit or
                        cursor + symbol_count > len(body)):
                    raise ValueError("JPEG fixture has an invalid Huffman table")
                cursor += symbol_count
                dht[(table_class, table_id)] = True
        elif marker == 0xC0:
            if (saw_sof or segment_length != 17 or len(body) != 15 or body[0] != 8 or
                    int.from_bytes(body[1:3], "big") != HEIGHT or
                    int.from_bytes(body[3:5], "big") != WIDTH or body[5] != 3):
                raise ValueError("JPEG fixture must be 8-bit 640x400 with 3-component baseline SOF0")
            components = [tuple(body[6 + 3 * index:9 + 3 * index]) for index in range(3)]
            component_ids = [component[0] for component in components]
            if (len(set(component_ids)) != 3 or
                    [component[1] for component in components] != [0x21, 0x11, 0x11] or
                    [component[2] for component in components] != [0, 1, 1]):
                raise ValueError("JPEG fixture must use the PL decoder's 4:2:2 sampling and quantization tables")
            saw_sof = True
        elif marker == 0xDD:
            if saw_dri or len(body) != 2 or int.from_bytes(body, "big") != 40:
                raise ValueError("JPEG fixture must have one restart interval of 40")
            saw_dri = True
        elif marker == 0xDA:
            if (not saw_sof or segment_length != 12 or len(body) != 10 or body[0] != 3 or
                    not all(dqt.values()) or not all(dht.values()) or not saw_dri):
                raise ValueError("JPEG fixture is missing the PL decoder's SOF/DRI/DQT/DHT/SOS requirements")
            scan = [tuple(body[1 + 2 * index:3 + 2 * index]) for index in range(3)]
            if ([item[0] for item in scan] != component_ids or
                    [item[1] for item in scan] != [0, 0x11, 0x11] or
                    body[7:10] != b"\x00\x3f\x00"):
                raise ValueError("JPEG fixture scan selectors are outside the PL decoder's supported subset")
            return
        else:
            raise ValueError(f"JPEG fixture contains unsupported header marker 0x{marker:02x}")
        offset = end

    raise ValueError("JPEG fixture has no complete baseline SOS header")


def load_jpeg_fixture(path: str | Path, *, frames: int) -> bytes:
    """Read one bounded, complete JPEG for a deterministic PL hardware probe."""
    if frames != 1:
        raise ValueError("a JPEG fixture is a single frame; pass --frames 1")
    with Path(path).expanduser().open("rb") as stream:
        payload = stream.read(JPEG_MAX_BYTES + 1)
    if len(payload) > JPEG_MAX_BYTES:
        raise ValueError(f"JPEG fixture exceeds {JPEG_MAX_BYTES} bytes")
    if len(payload) < 4 or payload[:2] != b"\xff\xd8" or payload[-2:] != b"\xff\xd9":
        raise ValueError("JPEG fixture must have SOI and EOI markers")
    _validate_jpeg_fixture_header(payload)
    return payload


def load_jpeg_fixture_sequence(paths: list[str | Path], *, frames: int) -> list[bytes]:
    """Preflight a bounded, ordered JPEG sequence before touching the board."""
    if frames != len(paths) or frames < 1:
        raise ValueError("a JPEG sequence must contain one JPEG per frame")
    total_bytes = 0
    payloads = []
    for path in paths:
        payload = load_jpeg_fixture(path, frames=1)
        total_bytes += len(payload)
        if total_bytes > 32 * 1024 * 1024:
            raise ValueError("JPEG fixture sequence exceeds 32 MiB")
        payloads.append(payload)
    return payloads


def verify_runtime_artifacts(bit_path: str | Path, bit_sha256: str,
                             hwh_path: str | Path, hwh_sha256: str,
                             *, status: str) -> dict[str, Any]:
    """Admit only adjacent, nonempty, hash-matched routed artifacts."""
    bit = Path(bit_path).expanduser().resolve(strict=True)
    hwh = Path(hwh_path).expanduser().resolve(strict=True)
    if not bit.is_file() or bit.suffix.lower() != ".bit" or bit.stat().st_size == 0:
        raise ValueError("BIT must be a nonempty .bit file")
    if not hwh.is_file() or hwh.suffix.lower() != ".hwh" or hwh.stat().st_size == 0:
        raise ValueError("HWH must be a nonempty .hwh file")
    if bit.parent != hwh.parent or bit.stem != hwh.stem:
        raise ValueError("BIT and HWH must be adjacent files with the same stem")
    if status != ROUTED_STATUS:
        raise ValueError("runtime entry requires routed evidence; synthesis-only artifacts are not admissible")
    actual_bit = _sha256(bit)
    actual_hwh = _sha256(hwh)
    if actual_bit != str(bit_sha256).lower():
        raise ValueError("BIT bitstream SHA-256 mismatch")
    if actual_hwh != str(hwh_sha256).lower():
        raise ValueError("HWH handoff SHA-256 mismatch")
    return {"status": status, "part": EXPECTED_PART,
            "bit_path": str(bit), "bit_sha256": actual_bit,
            "hwh_path": str(hwh), "hwh_sha256": actual_hwh,
            "dma_base": DMA_BASE, "rle_base": RLE_BASE,
            "overlay_base": OVERLAY_BASE, "jpeg_base": JPEG_BASE}


def camera_owners(proc_root: str | Path = "/proc", *, current_pid: int | None = None) -> list[dict[str, Any]]:
    """Return other processes holding V4L2 camera nodes; unreadable owners fail closed."""
    proc = Path(proc_root)
    if not proc.is_dir():
        raise RuntimeError(f"process table is unavailable: {proc}")
    self_pid = os.getpid() if current_pid is None else current_pid
    owners = []
    denied = []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == self_pid:
            continue
        fd_dir = entry / "fd"
        if not fd_dir.is_dir():
            continue
        found = []
        try:
            for fd in fd_dir.iterdir():
                try:
                    target = os.readlink(fd)
                except FileNotFoundError:
                    continue
                if target.startswith("/dev/video"):
                    found.append(target)
            if found:
                command = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="replace")
                owners.append({"pid": int(entry.name), "devices": sorted(set(found)), "command": command})
        except PermissionError:
            denied.append(int(entry.name))
    if denied:
        raise RuntimeError(f"cannot establish camera ownership for PIDs {denied}")
    return owners


def start_hdmi(hdmi: Any, vdma: Any) -> list[float]:
    """Start the routed Gray8 HDMI pipeline using the board's reviewed clocks."""
    from pynq import Clocks
    from pynq.lib.video import PIXEL_GRAY, VideoMode

    Clocks.fclk0_mhz = 50
    Clocks.fclk1_mhz = 100
    Clocks.fclk2_mhz = 142.857
    clocks = [float(Clocks.fclk0_mhz), float(Clocks.fclk1_mhz),
              float(Clocks.fclk2_mhz)]
    if any(abs(actual - expected) > 0.15
           for actual, expected in zip(clocks, (50, 100, 142.857))):
        raise RuntimeError(f"unexpected display clocks: {clocks}")

    hdmi._vdma = vdma
    hdmi.configure(VideoMode(HDMI_WIDTH, HDMI_HEIGHT, 8, 60), PIXEL_GRAY)
    hdmi.start()
    return clocks


def _encode_output_marker(session_id: uuid.UUID, frame_id: int) -> bytes:
    """Encode the HDMI monitor's 224-bit MC frame identity and CRC."""
    if not isinstance(session_id, uuid.UUID):
        raise TypeError("session_id must be uuid.UUID")
    if isinstance(frame_id, bool) or not isinstance(frame_id, int) or not 0 <= frame_id < (1 << 64):
        raise ValueError("frame_id must be an unsigned 64-bit integer")
    payload = b"MC" + session_id.bytes + frame_id.to_bytes(8, "big")
    return payload + binascii.crc_hqx(payload, 0xFFFF).to_bytes(2, "big")


def make_hdmi_frame(hdmi: Any, numpy: Any, session_id: uuid.UUID,
                    frame_id: int) -> Any:
    """Create a black Gray8 background with matching top/bottom HDMI markers."""
    frame = hdmi.newframe()
    if frame.shape != (HDMI_HEIGHT, HDMI_WIDTH) or frame.dtype != numpy.uint8:
        free = getattr(frame, "freebuffer", None)
        if callable(free):
            free()
        raise RuntimeError("HDMI did not provide a 1280x720 Gray8 frame")
    frame.fill(0)
    marker = _encode_output_marker(session_id, frame_id)
    bits = numpy.unpackbits(numpy.frombuffer(marker, dtype=numpy.uint8), bitorder="big")
    pixels = bits * numpy.uint8(255)
    marker_end = HDMI_MARKER_X + HDMI_MARKER_BYTES * 8
    frame[0, HDMI_MARKER_X:marker_end] = pixels
    frame[-1, HDMI_MARKER_X:marker_end] = pixels
    return frame


def output_monitor_snapshot(monitor: Any, *, timeout_s: float = 0.3) -> dict[str, Any]:
    """Read one atomic frame-monitor snapshot from the routed HDMI pixel stream."""
    previous = int(monitor.read(0x0C))
    monitor.write(0x04, 1)
    deadline = time.monotonic() + timeout_s
    while True:
        status, sequence = int(monitor.read(0x08)), int(monitor.read(0x0C))
        if status & 3 == 2 and sequence != previous:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("HDMI output monitor snapshot did not complete")
        time.sleep(0.0002)

    values = {name: int(monitor.read(offset)) | (int(monitor.read(offset + 4)) << 32)
              for name, offset in (("unique", 0x10), ("scans", 0x18),
                                   ("invalid", 0x28), ("partial", 0x30),
                                   ("frame", 0x48))}
    values["session"] = sum(int(monitor.read(0x38 + index * 4)) << (index * 32)
                            for index in range(4))
    flags = int(monitor.read(0x50))
    values["diagnostics"] = {
        name: bool(flags & (1 << bit))
        for bit, name in enumerate(("gray_error", "marker_pixel_error", "pair_mismatch",
                                    "top_invalid", "bottom_invalid"))
    }
    if sequence != int(monitor.read(0x0C)):
        raise RuntimeError("HDMI output monitor snapshot changed during read")
    return values


def wait_for_output_identity(monitor: Any, session_id: uuid.UUID, frame_id: int,
                             *, baseline: dict[str, Any] | None,
                             timeout_s: float = 1.0) -> dict[str, Any]:
    """Wait until the routed pixel monitor sees this exact frame identity."""
    deadline = time.monotonic() + timeout_s
    latest = None
    while True:
        latest = output_monitor_snapshot(monitor)
        if latest["session"] == session_id.int and latest["frame"] == frame_id:
            if ((baseline is not None and
                 (latest["invalid"] != baseline["invalid"] or
                  latest["partial"] != baseline["partial"])) or
                    any(latest["diagnostics"].values())):
                raise RuntimeError(f"HDMI output monitor rejected frame {frame_id}: {latest}")
            return latest
        if latest["session"] == session_id.int and latest["frame"] > frame_id:
            raise RuntimeError(f"HDMI output skipped frame {frame_id}: observed {latest['frame']}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"HDMI output did not show frame {frame_id}: {latest}")
        time.sleep(0.001)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="required explicit hardware execution gate")
    parser.add_argument("--bit", type=Path, required=True)
    parser.add_argument("--bit-sha256", required=True)
    parser.add_argument("--hwh", type=Path, required=True)
    parser.add_argument("--hwh-sha256", required=True)
    parser.add_argument("--build-status", required=True)
    parser.add_argument("--boot-id", required=True)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--input-jpeg", type=Path, action="append",
                        help="send one JPEG file instead of the camera; repeat once per sequence frame")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-lib", type=Path)
    parser.add_argument("--line-x", type=int, default=320)
    parser.add_argument("--line-y", type=int, default=200)
    parser.add_argument("--min-area", type=int, default=48)
    parser.add_argument("--max-area", type=int, default=3600)
    parser.add_argument("--hysteresis", type=int, default=4)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error("hardware access requires --execute")
    if not 1 <= args.frames <= 100000:
        parser.error("--frames must be in 1..100000")
    fixture_jpegs = None
    if args.input_jpeg is not None:
        try:
            fixture_jpegs = load_jpeg_fixture_sequence(args.input_jpeg, frames=args.frames)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    if not 0 <= args.line_x < WIDTH or not 0 <= args.line_y < HEIGHT:
        parser.error("counting line is outside the 640x400 source frame")
    if not 1 <= args.min_area <= args.max_area <= WIDTH * HEIGHT:
        parser.error("invalid component area bounds")
    if args.output.exists():
        raise FileExistsError(args.output)
    artifacts = verify_runtime_artifacts(args.bit, args.bit_sha256, args.hwh,
                                         args.hwh_sha256, status=args.build_status)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "status": "STARTING",
        "mode": ("raw_mjpeg_to_jpeg_pl" if fixture_jpegs is None else
                 "jpeg_file_to_jpeg_pl" if len(fixture_jpegs) == 1 else
                 "jpeg_sequence_to_jpeg_pl"),
        "resolution": [WIDTH, HEIGHT], "expected_frames": args.frames,
        "frames": 0, "total_count": 0, "count_accuracy": "NOT_MEASURED",
        "artifacts": artifacts, "boot_id_expected": args.boot_id,
        "timing_ms": [], "frames_evidence": [],
    }
    if fixture_jpegs is not None:
        fixture_records = [
            {"frame_index": index + 1,
             "path": str(path.expanduser().resolve()),
             "jpeg_bytes": len(payload),
             "sha256": hashlib.sha256(payload).hexdigest()}
            for index, (path, payload) in enumerate(zip(args.input_jpeg, fixture_jpegs))
        ]
        report["input_source"] = (
            {"kind": "single_jpeg_fixture", **fixture_records[0]}
            if len(fixture_records) == 1 else
            {"kind": "jpeg_fixture_sequence", "frame_count": len(fixture_records),
             "total_jpeg_bytes": sum(item["jpeg_bytes"] for item in fixture_records),
             "frames": fixture_records}
        )
    transport = None
    capture_source = None
    capture_stats: dict[str, Any] = {}
    allocated = []
    held = False
    hdmi = None
    hdmi_started = False
    session_id = uuid.uuid4()
    cleanup_errors: list[str] = []
    native_library: Path | None = None
    try:
        native_library = resolve_native_library(args.native_lib, args.output.parent)
        report["native_library"] = {
            "path": str(native_library),
            "sha256": _sha256(native_library),
        }
        if not hasattr(os, "geteuid") or os.geteuid() != 0 or os.environ.get("XILINX_XRT") != "/usr":
            raise RuntimeError("hardware mode requires root and XILINX_XRT=/usr")
        if read_boot_id() != args.boot_id:
            raise RuntimeError("board boot ID differs from the explicitly supplied value")
        report["process_ownership"] = audit_process_ownership()
        owners = camera_owners()
        report["camera_owners_before"] = owners
        if owners:
            raise RuntimeError(f"camera is already owned; refusing PL reload: {owners}")

        import numpy as np
        import pynq
        from pynq import MMIO, Overlay, allocate

        report["pynq_state_before"] = inspect_pynq_global_state(
            pynq.__file__, require_empty_shutdown=True, require_loaded_bit=False)
        dma_probe = MMIO(DMA_BASE, 0x10000)
        prior_dma = {"mm2s": int(dma_probe.read(0x04)), "s2mm": int(dma_probe.read(0x34))}
        report["dma_before_load"] = prior_dma
        if any(value & 0x770 or not value & 0x3 for value in prior_dma.values()):
            raise RuntimeError("DMA is active or faulty before the experimental PL load")
        vdma_probe = MMIO(VDMA_BASE, 0x1000)
        prior_vdma = {"control": int(vdma_probe.read(0x00)),
                      "status": int(vdma_probe.read(0x04))}
        report["vdma_before_load"] = prior_vdma
        if (prior_vdma["control"] & 1 or not prior_vdma["status"] & 1 or
                prior_vdma["status"] & 0x770):
            raise RuntimeError("HDMI VDMA is active or faulty before the experimental PL load")

        overlay = Overlay(str(artifacts["bit_path"]), download=True)
        report["pynq_overlay"] = inspect_pynq_global_state(
            pynq.__file__, require_empty_shutdown=True, require_loaded_bit=True,
            expected_bit_path=artifacts["bit_path"], expected_bit_sha256=artifacts["bit_sha256"],
            allow_equivalent_bit_path=True)
        from scripts.standalone.hp2 import sync_hp1_readwrite32
        report["hp1_readwrite32"] = sync_hp1_readwrite32(artifacts["hwh_path"], MMIO)

        hdmi = overlay.hdmi_out
        report["hdmi_monitor_base"] = f"0x{HDMI_MONITOR_BASE:08x}"
        hdmi_monitor = MMIO(HDMI_MONITOR_BASE, 0x1000)
        monitor_signature = int(hdmi_monitor.read(0))
        if monitor_signature != HDMI_MONITOR_SIGNATURE:
            raise RuntimeError(f"HDMI output monitor signature mismatch: 0x{monitor_signature:08x}")
        report["hdmi_monitor_signature"] = f"0x{monitor_signature:08x}"
        hdmi._vdma = overlay.axi_vdma
        hdmi_started = True
        report["hdmi_clocks_mhz"] = start_hdmi(hdmi, overlay.axi_vdma)
        report["hdmi_mode"] = {"width": HDMI_WIDTH, "height": HDMI_HEIGHT,
                               "bits_per_pixel": 8, "fps": 60, "format": "Gray8"}
        startup_frame = make_hdmi_frame(hdmi, np, session_id, 0)
        hdmi.writeframe(startup_frame)
        monitor_baseline = wait_for_output_identity(
            hdmi_monitor, session_id, 0, baseline=None)
        report["hdmi_monitor_baseline"] = monitor_baseline

        tx = allocate(shape=(JPEG_MAX_BYTES,), dtype=np.uint8); allocated.append(tx)
        rx = allocate(shape=(RX_CAPACITY,), dtype=np.uint8); allocated.append(rx)
        preview = MMIO(OVERLAY_BASE, OVERLAY_MAP_BYTES)
        transport = JpegRleDmaTransport(
            overlay.axi_dma_0.sendchannel, overlay.axi_dma_0.recvchannel,
            MMIO(DMA_BASE, 0x10000), MMIO(RLE_BASE, 0x10000),
            MMIO(JPEG_BASE, 0x10000), preview, tx, rx)
        tracker = RleCounter(library=native_library,
            width=WIDTH, height=HEIGHT, count_axis="x", line_x=args.line_x,
            line_y=args.line_y, min_area=args.min_area, max_area=args.max_area,
            hysteresis=args.hysteresis)
        if fixture_jpegs is None:
            capture_source = iter_raw_mjpeg(args.device, stats=capture_stats)
        report["session_id"] = str(session_id)
        start = time.perf_counter()
        previous = start
        for frame_id in range(1, args.frames + 1):
            if fixture_jpegs is None:
                if capture_source is None:
                    raise RuntimeError("camera source was not initialized")
                jpeg = next(capture_source)
            else:
                jpeg = fixture_jpegs[frame_id - 1]
            stamp = time.monotonic_ns()
            frame_start = time.perf_counter()
            result = process_jpeg_frame(transport, tracker, jpeg, frame_id,
                                        capture_monotonic_ns=stamp)
            hdmi_frame = make_hdmi_frame(hdmi, np, session_id, frame_id)
            hdmi.writeframe(hdmi_frame)
            output_observation = wait_for_output_identity(
                hdmi_monitor, session_id, frame_id, baseline=monitor_baseline)
            finished = time.perf_counter()
            report["frames"] += 1
            report["total_count"] = result.total_count
            report["timing_ms"].append((finished - frame_start) * 1000)
            report["frames_evidence"].append({
                "frame_id": frame_id, "jpeg_bytes": len(jpeg),
                "detection_count": len(result.detections),
                "total_count": result.total_count,
                "detections": [asdict(detection) for detection in result.detections],
                "frame_interval_ms": (finished - previous) * 1000,
                "displayed_frame_id": int(preview.read(0x001C)),
                "hdmi_output": output_observation,
            })
            previous = finished
        elapsed = time.perf_counter() - start
        report.update(status="JPEG_PL_RUN_COMPLETED_OUTPUT_MONITORED",
                      elapsed_s=elapsed, throughput_fps=report["frames"] / elapsed,
                      total_count=tracker.total_count,
                      hdmi_evidence=("on-board HDMI pixel monitor matched each submitted session/frame marker "
                                     "with valid 1280x720 Gray8 scan; external display was not visually observed"))
        report["external_display_visual_observation"] = "NOT_OBSERVED"
    except BaseException as exc:
        held = bool(transport and transport.dma_may_be_active)
        report.update(status="FAIL_HOLD" if held else "FAILED",
                      error=str(exc), error_type=type(exc).__name__)
    finally:
        if capture_stats:
            report["camera_capture"] = dict(capture_stats)
        if capture_source is not None:
            close = getattr(capture_source, "close", None)
            if callable(close):
                try:
                    close()
                except BaseException as exc:
                    cleanup_errors.append(f"camera close: {type(exc).__name__}: {exc}")
        if not held:
            if transport is not None:
                try:
                    transport.close()
                except BaseException as exc:
                    cleanup_errors.append(f"JPEG/RLE transport close: {type(exc).__name__}: {exc}")
                    held = True
            else:
                for buffer in allocated:
                    try:
                        buffer.freebuffer()
                    except BaseException as exc:
                        cleanup_errors.append(f"DMA buffer free: {type(exc).__name__}: {exc}")
        if not held and hdmi_started:
            try:
                hdmi.close()
                report["hdmi_stopped"] = True
            except BaseException as exc:
                cleanup_errors.append(f"HDMI close: {type(exc).__name__}: {exc}")
                held = True
                report["hdmi_stopped"] = False
        if cleanup_errors:
            report["cleanup_errors"] = cleanup_errors
            if not held and report.get("status") == "JPEG_PL_RUN_COMPLETED_OUTPUT_MONITORED":
                report["status"] = "FAILED_CLEANUP"
        if held:
            report["status"] = "FAIL_HOLD"
        report["buffers_held"] = held
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(json.dumps({key: value for key, value in report.items()
                          if key not in ("frames_evidence", "timing_ms")},
                         ensure_ascii=False), flush=True)
        if held:
            print("JPEG_PL_FAIL_HOLD: retaining DMA-owned CMA buffers; operator recovery required", flush=True)
            while True:
                time.sleep(1)
    return 0 if report["status"] == "JPEG_PL_RUN_COMPLETED_OUTPUT_MONITORED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
