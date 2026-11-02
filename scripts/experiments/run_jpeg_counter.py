"""One-shot camera → JPEG PL → FRG tracker → frame-matched HDMI overlay runner.

This experimental path is available as ``scripts/rle/run_counter.py --jpeg-pl``
and as a direct module entry. It requires an explicit --execute switch and routed,
hash-matched BIT/HWH files. It does not download dependencies or stop other apps.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import time
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.rle.jpeg_runtime import process_jpeg_frame
from scripts.rle.jpeg_transport import JPEG_MAX_BYTES, JpegRleDmaTransport, iter_raw_mjpeg
from scripts.rle.protocol import HEIGHT, RX_CAPACITY, WIDTH
from scripts.rle.transport import RLE_SIGNATURE
from scripts.standalone.count_controls import make_runtime_counter
from scripts.standalone.display_bringup import prepare_display
from scripts.wo0015.board_preflight import (audit_process_ownership,
    inspect_pynq_global_state, read_boot_id, read_fpga_manager, read_kernel_log)


EXPECTED_PART = "xc7z020clg400-1"
DMA_BASE = 0x40400000
RLE_BASE = 0x43C20000
OVERLAY_BASE = 0x43C50000
JPEG_BASE = 0x43C80000
OVERLAY_MAP_BYTES = 0x5000
ROUTED_STATUS = "ROUTED_NOT_BOARD_VERIFIED"
OLD_RLE_BIT_SHA256 = "ca55911e6f286a986fcdeb35f99a2245e74383f739e93dd38a6fa3c52f1868a6"
_CANDIDATE_CONTROL_SIGNATURE = RLE_SIGNATURE
_BOOT_WRITE = re.compile(r"writing (.+?) to Xilinx Zynq FPGA Manager")


class DmaOwnershipUncertain(RuntimeError):
    """DMA ownership is not proven idle/fault-free; retain resources."""


def known_overlays(candidate_bit_sha256: str) -> tuple[dict[str, Any], ...]:
    """Only overlays whose identity and address map are already confirmed."""
    return (
        {"name": "jpeg_candidate", "sha256": str(candidate_bit_sha256).lower(),
         "dma_base": DMA_BASE, "control_base": RLE_BASE,
         "control_signature": _CANDIDATE_CONTROL_SIGNATURE,
         "overlay_base": OVERLAY_BASE, "jpeg_base": JPEG_BASE},
        {"name": "rle_previous", "sha256": OLD_RLE_BIT_SHA256,
         "dma_base": DMA_BASE, "control_base": RLE_BASE,
         "control_signature": RLE_SIGNATURE,
         "overlay_base": None, "jpeg_base": None},
    )


def inspect_boot_state(*, fpga_manager_reader=read_fpga_manager,
                       kernel_log_reader=read_kernel_log,
                       boot_id_reader=read_boot_id) -> dict[str, Any]:
    """Read the boot-time PL evidence without touching any PL MMIO."""
    try:
        manager = fpga_manager_reader()
        manager_error = None
    except BaseException as exc:
        manager = {}
        manager_error = repr(exc)
    try:
        bitstream = None
        for line in kernel_log_reader():
            match = _BOOT_WRITE.search(line)
            if match:
                bitstream = match.group(1).strip()
        kernel_error = None
    except BaseException as exc:
        bitstream = None
        kernel_error = repr(exc)
    return {"boot_id": str(boot_id_reader()),
            "fpga_manager": manager, "fpga_manager_error": manager_error,
            "boot_bitstream": bitstream, "boot_bitstream_error": kernel_error}


def classify_preload_state(*, pynq_state: dict[str, Any], boot_state: dict[str, Any],
                           owners: dict[str, Any],
                           known: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    """Classify the pre-load PL state without touching candidate MMIO.

    COLD_BASE is accepted only when the PYNQ state is cold, the FPGA manager is
    operating/flags=0, the last boot bitstream is base.bin, and no FPGA owner is
    present. COLD_BOOT_NO_GLOBAL_STATE alone is not enough to treat candidate
    addresses as valid.
    """
    findings = owners.get("findings", []) if isinstance(owners, dict) else []
    denied = owners.get("denied", []) if isinstance(owners, dict) else []
    if findings or denied:
        return {"kind": "UNKNOWN", "reason": "FPGA owner present",
                "owners": {"findings": findings, "denied": denied}}
    status = pynq_state.get("pl_state_status")
    if status == "COLD_BOOT_NO_GLOBAL_STATE":
        manager = boot_state.get("fpga_manager") or {}
        if (manager.get("state") == "operating" and manager.get("flags") == "0"
                and boot_state.get("boot_bitstream") == "base.bin"
                and not pynq_state.get("shutdown_ips")):
            return {"kind": "COLD_BASE",
                    "reason": "cold PYNQ state, boot base.bin, no FPGA owner",
                    "candidate_mmio_before_load": False}
        return {"kind": "UNKNOWN",
                "reason": "cold state is not verified as boot base.bin",
                "boot_bitstream": boot_state.get("boot_bitstream"),
                "fpga_manager": manager}
    if status == "GLOBAL_STATE_PRESENT":
        loaded = str(pynq_state.get("loaded_bit_sha256") or "").lower()
        for overlay in known:
            if loaded and loaded == overlay["sha256"].lower():
                return {"kind": "KNOWN_OLD",
                        "reason": f"loaded BIT matches {overlay['name']}",
                        "candidate_mmio_before_load": True,
                        "overlay": overlay}
        return {"kind": "UNKNOWN",
                "reason": "loaded overlay identity is not in the known table",
                "loaded_bit_sha256": loaded or None}
    return {"kind": "UNKNOWN", "reason": f"unrecognized PYNQ state {status!r}"}


def probe_preload_mmio(classification: dict[str, Any], *, mmio_factory):
    """Read only the confirmed old-overlay addresses; never probe unknowns.

    COLD_BASE returns None without any MMIO. UNKNOWN raises before any MMIO.
    KNOWN_OLD reads its confirmed control identity first, then its DMA status.
    """
    kind = classification.get("kind")
    if kind == "COLD_BASE":
        return None
    if kind != "KNOWN_OLD":
        raise RuntimeError("preload state is unknown; refusing candidate/overlay MMIO")
    overlay = classification["overlay"]
    control = mmio_factory(int(overlay["control_base"]), 0x10000)
    signature = int(control.read(0x04)) & 0xFFFFFFFF
    if signature != int(overlay["control_signature"]):
        raise RuntimeError(
            f"known old overlay control signature mismatch: 0x{signature:08x}")
    dma = mmio_factory(int(overlay["dma_base"]), 0x10000)
    status = {"mm2s": int(dma.read(0x04)) & 0xFFFFFFFF,
              "s2mm": int(dma.read(0x34)) & 0xFFFFFFFF}
    if any(value & 0x770 or not value & 0x3 for value in status.values()):
        raise DmaOwnershipUncertain(
            f"known old overlay DMA is active/faulty: {status}")
    return {"overlay": overlay["name"], "control_signature": signature,
            "dma": status}


def check_candidate_dma_idle(*, mmio_factory):
    """Post-load candidate DMA check at the HWH-confirmed DMA base."""
    dma = mmio_factory(DMA_BASE, 0x10000)
    status = {"mm2s": int(dma.read(0x04)) & 0xFFFFFFFF,
              "s2mm": int(dma.read(0x34)) & 0xFFFFFFFF}
    if any(value & 0x770 or not value & 0x3 for value in status.values()):
        raise DmaOwnershipUncertain(
            f"candidate DMA is active or faulty after load: {status}")
    return status


def build_transport_callback(*, transport_factory, mmio_factory):
    """Production transport factory: explicit overlay + preview ownership."""
    def make_transport(tx, rx, overlay, preview):
        return transport_factory(
            overlay.axi_dma_0.sendchannel,
            overlay.axi_dma_0.recvchannel,
            mmio_factory(DMA_BASE, 0x10000),
            mmio_factory(RLE_BASE, 0x10000),
            mmio_factory(JPEG_BASE, 0x10000),
            preview,
            tx, rx)
    return make_transport


def assemble_candidate_callbacks(*, artifacts, output, report, pynq_module,
                                 mmio_factory, overlay_factory, allocate, dtype,
                                 display_prepare, transport_factory,
                                 post_load_identity=None, stage=None):
    """Build the exact callback set used by main; tests can inject fakes.

    Every hardware-dependent callback receives its owned object explicitly:
    post_load_identity(overlay), prepare_display_fn(overlay),
    make_transport(tx, rx, overlay, preview). No main-scope overlay/preview
    variable is closed over.
    """
    def identity(overlay):
        if post_load_identity is not None:
            return post_load_identity(overlay)
        return inspect_pynq_global_state(
            pynq_module.__file__, require_empty_shutdown=True,
            require_loaded_bit=True,
            expected_bit_path=artifacts["bit_path"],
            expected_bit_sha256=artifacts["bit_sha256"],
            allow_equivalent_bit_path=True)

    return {
        "overlay_factory": overlay_factory,
        "post_load_identity": identity,
        "candidate_dma_check": lambda: check_candidate_dma_idle(
            mmio_factory=mmio_factory),
        "preview_factory": lambda: mmio_factory(OVERLAY_BASE, OVERLAY_MAP_BYTES),
        "prepare_display_fn": display_prepare,
        "allocate_tx": lambda: allocate(shape=(JPEG_MAX_BYTES,), dtype=dtype),
        "allocate_rx": lambda: allocate(shape=(RX_CAPACITY,), dtype=dtype),
        "make_transport": build_transport_callback(
            transport_factory=transport_factory, mmio_factory=mmio_factory),
        "stage": stage or (lambda name, **fields: write_stage(
            output, report, name, **fields)),
    }


def run_candidate_sequence(*, classification, mmio_factory, overlay_factory,
                           post_load_identity, candidate_dma_check,
                           preview_factory, prepare_display_fn,
                           allocate_tx, allocate_rx, make_transport,
                           stage=lambda *args, **kwargs: None):
    """Load the candidate only after the three-state pre-load decision.

    COLD_BASE: no candidate MMIO before Overlay.download.
    KNOWN_OLD: only the confirmed old control/DMA addresses are read.
    UNKNOWN: stop before Overlay.download; never probe unknown MMIO.
    After load: identity/contract, candidate DMA, display, then allocation.
    """
    if classification.get("kind") not in ("COLD_BASE", "KNOWN_OLD"):
        raise RuntimeError(
            "preload state is unknown; refusing Overlay.download and MMIO")
    allocated: list[Any] = []
    probe = None
    if classification["kind"] == "KNOWN_OLD":
        stage("known_old_probe", classification=classification)
        probe = probe_preload_mmio(classification, mmio_factory=mmio_factory)
        stage("known_old_probe_done", probe=probe)
    stage("overlay_download", classification=classification)
    overlay = overlay_factory()
    stage("overlay_loaded")
    identity = post_load_identity(overlay)
    stage("post_load_identity", identity=identity)
    candidate_dma = candidate_dma_check()
    stage("candidate_dma", candidate_dma=candidate_dma)
    stage("display_prepare")
    display = prepare_display_fn(overlay)
    stage("display_prepared")
    try:
        stage("display_start")
        display.start()
        stage("display_started")
        stage("overlay_mmio")
        preview = preview_factory()
        stage("overlay_mmio_ready")
        tx = allocate_tx()
        allocated.append(tx)
        rx = allocate_rx()
        allocated.append(rx)
        transport = make_transport(tx, rx, overlay, preview)
        stage("transport_ready")
        return {"display": display, "tx": tx, "rx": rx,
                "transport": transport, "allocated": allocated,
                "preview": preview, "probe": probe, "identity": identity,
                "candidate_dma": candidate_dma}
    except BaseException as exc:
        setattr(exc, "display", display)
        setattr(exc, "allocated", allocated)
        raise


def write_stage(output: Path, report: dict[str, Any], stage: str, **fields) -> None:
    """Persist the boot record/stage before the next hardware access and flush."""
    payload = {
        "schema": "material-counter.jpeg-smoke-stage.v1",
        "stage": stage,
        "monotonic": time.monotonic(),
        "boot_id": report.get("boot_id_expected"),
        "report": {key: value for key, value in report.items()
                   if key not in ("frames_evidence", "timing_ms")},
        **fields,
    }
    path = Path(str(output) + ".stage.json")
    with path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(f"JPEG_STAGE {stage}", flush=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


_BIT_MAGIC = b"\x00\x09\x0f\xf0\x0f\xf0\x0f\xf0\x0f\xf0\x00\x00\x01"


def _validate_bit(bit: Path) -> None:
    """Reject non-bitstream byte files via the Xilinx .bit header magic."""
    with bit.open("rb") as stream:
        header = stream.read(len(_BIT_MAGIC))
    if header != _BIT_MAGIC:
        raise ValueError("BIT is not a Xilinx bitstream (magic header mismatch)")


def _parse_hwh(hwh: Path) -> dict[str, Any]:
    """Parse the handoff and return its silicon part and control base addresses."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(hwh).getroot()
        system = root.find("SYSTEMINFO")
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"HWH is not a valid Xenon handoff: {exc}") from exc
    if root.tag != "EDKSYSTEM" or system is None:
        raise ValueError("HWH must be an EDKSYSTEM handoff with SYSTEMINFO")
    device = system.get("DEVICE") or ""
    package = system.get("PACKAGE") or ""
    speed = system.get("SPEEDGRADE") or ""
    if not (device and package and speed):
        raise ValueError("HWH SYSTEMINFO is missing device/package/speed fields")
    bases = set()
    for parameter in root.iter("PARAMETER"):
        name = parameter.get("NAME") or ""
        if name == "C_BASEADDR" or name.endswith("_BASEADDR"):
            value = parameter.get("VALUE") or ""
            if not value.lower().startswith("0x"):
                if name == "C_BASEADDR":
                    raise ValueError("HWH C_BASEADDR is not hexadecimal")
                continue
            bases.add(int(value, 16))
    # HLS control interfaces name the address parameter C_S_AXI_CONTROL_BASEADDR;
    # the MEMRANGE BASEVALUE is the same address in the address-map table.
    for memrange in root.iter("MEMRANGE"):
        value = memrange.get("BASEVALUE") or ""
        if value.lower().startswith("0x"):
            bases.add(int(value, 16))
    return {"part": f"xc{device}{package}{speed}", "bases": bases}


def verify_runtime_artifacts(bit_path: str | Path, bit_sha256: str,
                             hwh_path: str | Path, hwh_sha256: str,
                             *, status: str) -> dict[str, Any]:
    """Admit only adjacent, nonempty, hash-matched routed artifacts whose HWH
    declares the expected silicon part and kernel topology (not merely a hash
    match on arbitrary byte files)."""
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
    _validate_bit(bit)
    hwh_info = _parse_hwh(hwh)
    if hwh_info["part"] != EXPECTED_PART:
        raise ValueError(f"HWH part {hwh_info['part']!r} does not match {EXPECTED_PART!r}")
    for base, label in ((DMA_BASE, "DMA"), (RLE_BASE, "RLE"),
                        (OVERLAY_BASE, "overlay"), (JPEG_BASE, "JPEG")):
        if base not in hwh_info["bases"]:
            raise ValueError(f"HWH is missing the {label} control base 0x{base:08x}")
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
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-lib", type=Path)
    return parser


def bring_up_display_and_dma(*, prepare, allocate_tx, allocate_rx, make_transport):
    """Start the display before allocating or transferring any DMA buffer.

    If display bring-up fails, no JPEG/RLE buffer is allocated and no frame can
    be published. The display handle is attached to the exception so the caller
    can decide whether the VDMA still owns resources.
    """
    display = None
    allocated = []
    try:
        display = prepare()
        display.start()
        tx = allocate_tx()
        allocated.append(tx)
        rx = allocate_rx()
        allocated.append(rx)
        transport = make_transport(tx, rx)
        return display, tx, rx, transport, allocated
    except BaseException as exc:
        if display is not None:
            setattr(exc, "display", display)
        setattr(exc, "allocated", allocated)
        raise


def install_hold_signal_handlers(hold_mode, *, signal_module=signal):
    """Consume SIGINT/SIGTERM while a FAIL_HOLD process is retaining DMA.

    Outside HOLD the handler raises KeyboardInterrupt so the normal fail-hold
    path can inspect DMA ownership. Inside HOLD it records the signal and
    returns; it must not let a second Ctrl+C/SIGTERM unwind the process and run
    buffer destructors. SIGKILL cannot be intercepted.
    """
    def handler(signum, frame):
        if hold_mode.get("active"):
            hold_mode.setdefault("signals", []).append(int(signum))
            print(f"JPEG_PL_HOLD_SIGNAL_IGNORED:{int(signum)}", flush=True)
            return
        raise KeyboardInterrupt(f"signal {int(signum)}")

    signal_module.signal(signal_module.SIGINT, handler)
    signal_module.signal(signal_module.SIGTERM, handler)
    return handler


def _write_hold_state(output: Path, report: dict[str, Any], hold_mode: dict[str, Any]) -> None:
    state = {
        "status": "FAIL_HOLD",
        "output": str(output),
        "buffers_held": bool(report.get("buffers_held", True)),
        "hold_signals": list(hold_mode.get("signals", [])),
        "display": report.get("display"),
        "error": report.get("error"),
        "error_type": report.get("error_type"),
        "hint": ("keep this process alive; collect the report via an independent "
                 "connection; reboot with operator authorization"),
    }
    try:
        Path(str(output) + ".hold.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except BaseException as exc:
        print(f"JPEG_PL_HOLD_STATE_WRITE_FAILED:{exc!r}", flush=True)


def finalize_run(*, held, display, transport, allocated, capture_source, report, output,
                 hold_mode, hold_loop=True, sleep=time.sleep):
    """Normal cleanup or FAIL_HOLD retention.

    When held is true, neither DMA owner is closed/freed/reset/reloaded. The
    camera is not DMA-owned and may be closed. During cleanup and HOLD, signals
    are consumed so an abnormal exit cannot trigger buffer destructors.
    """
    hold_mode["active"] = True
    if capture_source is not None:
        close = getattr(capture_source, "close", None)
        if callable(close):
            try:
                close()
            except BaseException as exc:
                report["camera_close_error"] = repr(exc)
    if not held and display is not None and not getattr(display, "closed", False):
        try:
            display.stop()
        except BaseException as exc:
            held = True
            report["display_stop_error"] = repr(exc)
    if not held:
        try:
            if transport is not None:
                transport.close()
            else:
                for buffer in allocated:
                    buffer.freebuffer()
        except BaseException as exc:
            held = True
            report["close_error"] = repr(exc)
    if held and report.get("status") == "JPEG_PL_RUN_COMPLETED_NOT_BOARD_VALIDATED":
        report["status"] = "FAIL_HOLD"
        report.setdefault("error", "display stop or transport close failed during normal completion")
        report.setdefault("error_type", "CloseError")
    report["display"] = display.state() if display is not None else None
    report["buffers_held"] = held
    report["hold_signals"] = list(hold_mode.get("signals", []))
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ("frames_evidence", "timing_ms")},
                     ensure_ascii=False), flush=True)
    if held:
        print("JPEG_PL_FAIL_HOLD: retaining DMA-owned buffers/display; "
              "keep this process alive and recover by authorized reboot", flush=True)
        _write_hold_state(output, report, hold_mode)
        if hold_loop:
            while True:
                sleep(1.0)
                _write_hold_state(output, report, hold_mode)
    return held


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error("hardware access requires --execute")
    if not 1 <= args.frames <= 100000:
        parser.error("--frames must be in 1..100000")
    if args.output.exists():
        raise FileExistsError(args.output)
    artifacts = verify_runtime_artifacts(args.bit, args.bit_sha256, args.hwh,
                                         args.hwh_sha256, status=args.build_status)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "status": "STARTING", "mode": "raw_mjpeg_to_jpeg_pl",
        "resolution": [WIDTH, HEIGHT], "expected_frames": args.frames,
        "frames": 0, "total_count": 0, "count_accuracy": "NOT_MEASURED",
        "artifacts": artifacts, "boot_id_expected": args.boot_id,
        "timing_ms": [], "frames_evidence": [],
    }
    transport = None
    display = None
    capture_source = None
    allocated = []
    held = False
    hold_mode = {"active": False, "signals": []}
    install_hold_signal_handlers(hold_mode)
    try:
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

        pynq_state_before = inspect_pynq_global_state(
            pynq.__file__, require_empty_shutdown=True, require_loaded_bit=False)
        report["pynq_state_before"] = pynq_state_before
        boot_state = inspect_boot_state()
        report["boot_state"] = boot_state
        known = known_overlays(artifacts["bit_sha256"])
        classification = classify_preload_state(
            pynq_state=pynq_state_before, boot_state=boot_state,
            owners=report["process_ownership"], known=known)
        report["preload_classification"] = classification
        write_stage(args.output, report, "preflight")

        callbacks = assemble_candidate_callbacks(
            artifacts=artifacts, output=args.output, report=report,
            pynq_module=pynq, mmio_factory=MMIO,
            overlay_factory=lambda: Overlay(str(artifacts["bit_path"]), download=True),
            allocate=allocate, dtype=np.uint8, display_prepare=prepare_display,
            transport_factory=JpegRleDmaTransport)
        sequence = run_candidate_sequence(
            classification=classification, mmio_factory=MMIO, **callbacks)
        display = sequence["display"]
        tx = sequence["tx"]
        rx = sequence["rx"]
        transport = sequence["transport"]
        allocated = sequence["allocated"]
        preview = sequence["preview"]
        report["preload_probe"] = sequence["probe"]
        report["pynq_overlay"] = sequence["identity"]
        report["candidate_dma_after_load"] = sequence["candidate_dma"]
        write_stage(args.output, report, "frames_start")
        # Field-v1 counter keeps the production count line, association and
        # 2-4 way touching split; the JPEG path counts through update_rle.
        tracker = make_runtime_counter(args.native_lib, profile="field-v1")
        capture_source = iter_raw_mjpeg(args.device)
        start = time.perf_counter()
        previous = start
        for frame_id in range(args.frames):
            jpeg = next(capture_source)
            stamp = time.monotonic_ns()
            frame_start = time.perf_counter()
            result = process_jpeg_frame(transport, tracker, jpeg, frame_id,
                                        capture_monotonic_ns=stamp)
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
            })
            previous = finished
        elapsed = time.perf_counter() - start
        display.stop()
        report.update(status="JPEG_PL_RUN_COMPLETED_NOT_BOARD_VALIDATED",
                      elapsed_s=elapsed, throughput_fps=report["frames"] / elapsed,
                      total_count=tracker.total_count,
                      hdmi_evidence="PL displayed_frame_id register only; no physical monitor observation")
        write_stage(args.output, report, "frames_done")
    except BaseException as exc:
        display = getattr(exc, "display", display)
        allocated = getattr(exc, "allocated", allocated)
        held = (isinstance(exc, DmaOwnershipUncertain) or
                bool((transport and transport.dma_may_be_active) or
                     (display is not None and display.dma_uncertain())))
        if isinstance(exc, DmaOwnershipUncertain):
            report["dma_ownership_error"] = str(exc)
        report.update(status="FAIL_HOLD" if held else "FAILED",
                      error=str(exc), error_type=type(exc).__name__)
    finally:
        finalize_run(held=held, display=display, transport=transport,
                     allocated=allocated, capture_source=capture_source,
                     report=report, output=args.output, hold_mode=hold_mode,
                     hold_loop=True)
    return 0 if report["status"] == "JPEG_PL_RUN_COMPLETED_NOT_BOARD_VALIDATED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
