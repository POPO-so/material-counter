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
from scripts.rle.transport import (DMA_MM2S_ADDRESS, DMA_S2MM_ADDRESS,
                                   RLE_SIGNATURE)
from scripts.standalone.count_controls import make_runtime_counter
from scripts.standalone.display_bringup import prepare_display
from scripts.wo0015.board_preflight import (audit_process_ownership,
    inspect_pynq_global_state, read_boot_id, read_cma_meminfo,
    read_fpga_manager, read_kernel_log)


EXPECTED_PART = "xc7z020clg400-1"
DMA_BASE = 0x40400000
RLE_BASE = 0x43C20000
OVERLAY_BASE = 0x43C50000
JPEG_BASE = 0x43C80000
OVERLAY_MAP_BYTES = 0x5000
ROUTED_STATUS = "ROUTED_NOT_BOARD_VERIFIED"
OLD_RLE_BIT_SHA256 = "ca55911e6f286a986fcdeb35f99a2245e74383f739e93dd38a6fa3c52f1868a6"
KNOWN_BASE_BIT_SHA256 = "3239e9cc37ee4993c25f02ae6c97da0b3372f0e722e267d10073e20adc5d6717"
KNOWN_BASE_BIT_SHA1 = "39b937b490015ec0e1d29f18a9143292821f74ad"
KNOWN_BASE_HWH_SHA256 = "222898ef2c0ccc4a0c47608e99d8dc7c9c6cdfed5130964884ff35005827ef3a"
BASE_VDMA_BASE = 0x43000000
BASE_VDMA_HIGH = 0x4300FFFF
CMA_RESERVE_KIB = 4096
# AXI VDMA v6.3 (PG020) MM2S/S2MM status-register fields. Bit 1 is Reserved
# (always 0), not Idle; the AXI DMA mask 0x770 must not be reused. Error and
# reserved masks are channel-specific; bit 15 is EOLLateErr only on S2MM.
VDMA_CHANNELS = {
    "mm2s": {"control_offset": 0x00, "status_offset": 0x04,
             "error_mask": 0x00F0,
             "error_fields": ((4, "vdma_int_err"), (5, "vdma_slv_err"),
                              (6, "vdma_dec_err"), (7, "sof_early_err")),
             "reserved_mask": 0x8F0E},
    "s2mm": {"control_offset": 0x30, "status_offset": 0x34,
             "error_mask": 0x89F0,
             "error_fields": ((4, "vdma_int_err"), (5, "vdma_slv_err"),
                              (6, "vdma_dec_err"), (7, "sof_early_err"),
                              (8, "eol_early_err"), (11, "sof_late_err"),
                              (15, "eol_late_err")),
             "reserved_mask": 0x060E},
}
# AXI DMA v7.1 (PG021) DMASR: bit 1 is a real Idle bit, but it reads 0 while
# Halted=1, so a halted channel is Halted=1 + RS=0, not Idle=1. The 0x770
# error mask is correct for AXI DMA (bits 4-6 and 8-10).
AXI_DMA_CHANNELS = {
    "mm2s": {"control_offset": 0x00, "status_offset": 0x04,
             "error_mask": 0x0770,
             "error_fields": ((4, "dma_int_err"), (5, "dma_slv_err"),
                              (6, "dma_dec_err"), (8, "sg_int_err"),
                              (9, "sg_slv_err"), (10, "sg_dec_err")),
             "reserved_mask": 0x8884},
    "s2mm": {"control_offset": 0x30, "status_offset": 0x34,
             "error_mask": 0x0770,
             "error_fields": ((4, "dma_int_err"), (5, "dma_slv_err"),
                              (6, "dma_dec_err"), (8, "sg_int_err"),
                              (9, "sg_slv_err"), (10, "sg_dec_err")),
             "reserved_mask": 0x8884},
}
BUS_MASTER_PROFILES = {"axi_vdma": VDMA_CHANNELS, "axi_dma": AXI_DMA_CHANNELS}
TRACE_DMA_TARGETS = {
    "trace_analyzer_pi_axi_dma_0": 0x80430000,
    "trace_analyzer_pmodb_axi_dma_0": 0x80420000,
}
_CANDIDATE_CONTROL_SIGNATURE = RLE_SIGNATURE
_BOOT_WRITE = re.compile(r"writing (.+?) to Xilinx Zynq FPGA Manager")


class DmaOwnershipUncertain(RuntimeError):
    """DMA ownership is not proven idle/fault-free; retain resources."""


class PreloadUnsafe(RuntimeError):
    """A verified base's HWH map or bus-master state is not safe to overwrite."""


class DmaStopTimeout(RuntimeError):
    """The bounded normal-stop request did not reach RS=0/Halted=1."""


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


def known_bases() -> tuple[dict[str, Any], ...]:
    """The reviewed PYNQ base overlay, pinned by actual bitstream content.

    The SHA-1/SHA-256 pair was captured from the installed base.bit; the HWH
    hash and the VDMA address were captured by the 2026-10-29 read-only board
    probe (base_artifacts/base_ips in standalone-20261029-g0/board-probe).
    The path or the file name is never used as identity by itself.
    """
    return (
        {"name": "pynq_base",
         "bit_sha256": KNOWN_BASE_BIT_SHA256,
         "bit_sha1": KNOWN_BASE_BIT_SHA1,
         "hwh_sha256": KNOWN_BASE_HWH_SHA256,
         "bus_masters": (
             {"instance": "video_axi_vdma", "type": "axi_vdma",
              "vlnv": "xilinx.com:ip:axi_vdma:6.3",
              "base": BASE_VDMA_BASE, "high": BASE_VDMA_HIGH,
              "channels": ("mm2s", "s2mm")},
             {"instance": "trace_analyzer_pi_axi_dma_0", "type": "axi_dma",
              "vlnv": "xilinx.com:ip:axi_dma:7.1",
              "base": 0x80430000, "high": 0x8043FFFF,
              "channels": ("s2mm",)},
             {"instance": "trace_analyzer_pmodb_axi_dma_0", "type": "axi_dma",
              "vlnv": "xilinx.com:ip:axi_dma:7.1",
              "base": 0x80420000, "high": 0x8042FFFF,
              "channels": ("s2mm",)},)},
    )


def cma_budget() -> dict[str, int]:
    """Record the explicit TX/RX CMA budget plus a display/allocator reserve.

    This is an allocation headroom check, not a leak detector: one snapshot is
    recorded before and one after the run, and no reboot/retry is derived from
    either value.
    """
    explicit = JPEG_MAX_BYTES + RX_CAPACITY
    required_kib = (explicit + 1023) // 1024 + CMA_RESERVE_KIB
    return {"tx_bytes": JPEG_MAX_BYTES, "rx_bytes": RX_CAPACITY,
            "explicit_bytes": explicit, "reserve_kib": CMA_RESERVE_KIB,
            "required_kib": required_kib}


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


def _file_hash_pair(path: str | Path) -> dict[str, str]:
    """Hash the actual file content; the state-recorded digests are not trusted."""
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(str(file_path))
    sha1 = hashlib.sha1()
    sha256 = hashlib.sha256()
    with file_path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha1.update(block)
            sha256.update(block)
    return {"sha1": sha1.hexdigest(), "sha256": sha256.hexdigest()}


def _classify_known_base(base: dict[str, Any], *, pynq_state: dict[str, Any],
                         boot_ok: bool,
                         hash_reader=_file_hash_pair) -> dict[str, Any]:
    if pynq_state.get("shutdown_ips"):
        return {"kind": "UNKNOWN", "reason": "known base has a non-empty shutdown_ips"}
    if not boot_ok:
        return {"kind": "UNKNOWN",
                "reason": "known base state is not a verified boot base.bin"}
    bit_path = pynq_state.get("loaded_bit_path")
    state_sha1 = str(pynq_state.get("loaded_bit_sha1") or "").lower()
    state_sha256 = str(pynq_state.get("loaded_bit_sha256") or "").lower()
    if not bit_path:
        return {"kind": "UNKNOWN", "reason": "known base state has no loaded bit path"}
    try:
        actual = hash_reader(bit_path)
    except OSError as exc:
        return {"kind": "UNKNOWN",
                "reason": f"known base bit file cannot be hashed: {exc}"}
    actual_sha1 = str(actual.get("sha1") or "").lower()
    actual_sha256 = str(actual.get("sha256") or "").lower()
    pinned_sha1 = str(base["bit_sha1"]).lower()
    pinned_sha256 = str(base["bit_sha256"]).lower()
    if not (actual_sha256 == state_sha256 == pinned_sha256):
        return {"kind": "UNKNOWN",
                "reason": "base.bit content SHA-256 does not match the pinned base",
                "loaded_bit_sha256": actual_sha256 or None,
                "state_bit_sha256": state_sha256 or None}
    if not (actual_sha1 == state_sha1 == pinned_sha1):
        return {"kind": "UNKNOWN",
                "reason": "base.bit content SHA-1 does not match the pinned base",
                "loaded_bit_sha1": actual_sha1 or None,
                "state_bit_sha1": state_sha1 or None}
    resolved = Path(bit_path)
    return {"kind": "KNOWN_BASE",
            "reason": "loaded BIT content matches the pinned PYNQ base overlay",
            "candidate_mmio_before_load": False,
            "base": {**base, "bit_path": str(resolved),
                     "hwh_path": str(resolved.with_suffix(".hwh"))}}


def classify_preload_state(*, pynq_state: dict[str, Any], boot_state: dict[str, Any],
                           owners: dict[str, Any],
                           known: tuple[dict[str, Any], ...],
                           base_table: tuple[dict[str, Any], ...] | None = None,
                           hash_reader=_file_hash_pair) -> dict[str, Any]:
    """Classify the pre-load PL state without touching candidate MMIO.

    COLD_BASE is accepted only when the PYNQ state is cold, the FPGA manager is
    operating/flags=0, the last boot bitstream is base.bin, and no FPGA owner is
    present. COLD_BOOT_NO_GLOBAL_STATE alone is not enough to treat candidate
    addresses as valid. KNOWN_BASE additionally requires the actual loaded
    base.bit content to match the pinned SHA-1/SHA-256 pair; a matching path,
    file name, or active name is not identity.
    """
    findings = owners.get("findings", []) if isinstance(owners, dict) else []
    denied = owners.get("denied", []) if isinstance(owners, dict) else []
    if findings or denied:
        return {"kind": "UNKNOWN", "reason": "FPGA owner present",
                "owners": {"findings": findings, "denied": denied}}
    manager = boot_state.get("fpga_manager") or {}
    boot_ok = (manager.get("state") == "operating" and manager.get("flags") == "0"
               and boot_state.get("boot_bitstream") == "base.bin")
    status = pynq_state.get("pl_state_status")
    if status == "COLD_BOOT_NO_GLOBAL_STATE":
        if boot_ok and not pynq_state.get("shutdown_ips"):
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
        for base in (base_table if base_table is not None else known_bases()):
            if loaded and loaded == str(base["bit_sha256"]).lower():
                return _classify_known_base(base, pynq_state=pynq_state,
                                            boot_ok=boot_ok,
                                            hash_reader=hash_reader)
        return {"kind": "UNKNOWN",
                "reason": "loaded overlay identity is not in the known table",
                "loaded_bit_sha256": loaded or None}
    return {"kind": "UNKNOWN", "reason": f"unrecognized PYNQ state {status!r}"}


def _hwh_dma_masters(hwh: Path) -> list[dict[str, Any]]:
    """Return every DMA-capable module's confirmed control base from a HWH."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(hwh).getroot()
    except (OSError, ET.ParseError) as exc:
        raise PreloadUnsafe(f"base HWH is not readable or valid: {exc}") from exc
    masters = []
    for module in root.iter():
        if module.tag.split("}")[-1] not in ("MODULE", "IP"):
            continue
        vlnv = module.get("VLNV") or module.get("VLNV_NAME") or ""
        lowered = vlnv.lower()
        if not any(token in lowered
                   for token in ("axi_vdma", "axi_dma", "axi_cdma")):
            continue
        if "axi_vdma" in lowered:
            master_type = "axi_vdma"
        elif "axi_dma" in lowered:
            master_type = "axi_dma"
        else:
            raise PreloadUnsafe(f"unsupported DMA bus master {vlnv!r}")
        parameters = {}
        for item in module.iter():
            if item.tag.split("}")[-1] in ("PARAMETER", "PARAM"):
                name = item.get("NAME") or item.get("name")
                value = item.get("VALUE") or item.get("value")
                if name:
                    parameters[name] = value
        base_value = (parameters.get("C_BASEADDR")
                      or parameters.get("C_S_AXI_LITE_BASEADDR"))
        if not base_value or not str(base_value).lower().startswith("0x"):
            raise PreloadUnsafe(
                f"base HWH DMA module {vlnv!r} has no confirmed control base")
        include_mm2s = parameters.get("C_INCLUDE_MM2S",
                                      parameters.get("c_include_mm2s"))
        include_s2mm = parameters.get("C_INCLUDE_S2MM",
                                      parameters.get("c_include_s2mm"))
        if include_mm2s is None or include_s2mm is None:
            raise PreloadUnsafe(
                f"base HWH DMA module {vlnv!r} lacks C_INCLUDE_MM2S/S2MM")
        try:
            channels = tuple(
                name for name, flag in (("mm2s", include_mm2s),
                                        ("s2mm", include_s2mm))
                if int(str(flag), 0) == 1)
        except ValueError as exc:
            raise PreloadUnsafe(
                f"base HWH DMA module {vlnv!r} has invalid channel flags") from exc
        if not channels:
            raise PreloadUnsafe(f"base HWH DMA module {vlnv!r} has no DMA channel")
        masters.append({
            "instance": (module.get("INSTANCENAME") or module.get("INSTANCE")
                         or module.get("NAME") or "unknown"),
            "type": master_type,
            "vlnv": vlnv, "base": int(str(base_value), 16),
            "channels": channels})
    return masters


def read_base_hwh_evidence(base: dict[str, Any]) -> dict[str, Any]:
    """Verify the pinned base HWH and its complete DMA bus-master map."""
    hwh = Path(base["hwh_path"])
    try:
        digest = _sha256(hwh)
    except OSError as exc:
        raise PreloadUnsafe(f"base HWH cannot be read: {exc}") from exc
    if digest != str(base["hwh_sha256"]).lower():
        raise PreloadUnsafe(
            "base HWH SHA-256 differs from the captured base address-map evidence")
    masters = _hwh_dma_masters(hwh)
    expected = {(str(item["vlnv"]).lower(), str(item["type"]),
                 int(item["base"]), tuple(item.get("channels", ())))
                for item in base["bus_masters"]}
    actual = {(str(item["vlnv"]).lower(), str(item["type"]),
               int(item["base"]), tuple(item.get("channels", ())))
              for item in masters}
    if actual != expected:
        raise PreloadUnsafe(
            f"base HWH bus-master map differs from the captured evidence: "
            f"{sorted(actual)} != {sorted(expected)}")
    return {"hwh_path": str(hwh), "hwh_sha256": digest, "bus_masters": masters}


def probe_preload_mmio(classification: dict[str, Any], *, mmio_factory,
                       base_evidence_reader=None):
    """Read only the confirmed old-overlay addresses; never probe unknowns.

    COLD_BASE returns None without any MMIO. UNKNOWN raises before any MMIO.
    KNOWN_OLD reads its confirmed control identity first, then its DMA status.
    KNOWN_BASE verifies the pinned base HWH/address map first, then reads only
    the confirmed base VDMA status; it never touches candidate addresses.
    """
    kind = classification.get("kind")
    if kind == "COLD_BASE":
        return None
    if kind == "KNOWN_BASE":
        base = classification["base"]
        evidence = (base_evidence_reader or read_base_hwh_evidence)(base)
        statuses = {}
        for master in base["bus_masters"]:
            dma = mmio_factory(int(master["base"]), 0x1000)
            decoded = {}
            for channel in master["channels"]:
                spec = BUS_MASTER_PROFILES[master["type"]][channel]
                control = int(dma.read(spec["control_offset"])) & 0xFFFFFFFF
                status = int(dma.read(spec["status_offset"])) & 0xFFFFFFFF
                errors = [name for bit, name in spec["error_fields"]
                          if status & (1 << bit)]
                reserved = status & spec["reserved_mask"]
                decoded[channel] = {
                    "type": master["type"],
                    "control": control, "status": status,
                    "rs": bool(control & 0x1),
                    "halted": bool(status & 0x1),
                    # Bit 1 is Reserved in AXI VDMA v6.3; recorded, not gated.
                    "reserved_bit1": (status >> 1) & 0x1,
                    "error_mask": spec["error_mask"],
                    "error_bits": errors,
                    "reserved_bits": reserved,
                }
                if (control & 0x1 or not (status & 0x1) or errors or reserved):
                    raise PreloadUnsafe(
                        f"known base {master['instance']} {channel} is not safely "
                        f"halted: control=0x{control:08x} status=0x{status:08x} "
                        f"errors={errors or 'none'} reserved=0x{reserved:x}")
            statuses[master["instance"]] = decoded
        return {"base": base["name"], "hwh_sha256": evidence["hwh_sha256"],
                "bus_masters": statuses}
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


def _axi_dma_s2mm_state(mmio) -> tuple[int, int]:
    return (int(mmio.read(0x30)) & 0xFFFFFFFF,
            int(mmio.read(0x34)) & 0xFFFFFFFF)


def _axi_dma_s2mm_decode(control: int, status: int) -> dict[str, Any]:
    spec = AXI_DMA_CHANNELS["s2mm"]
    errors = [name for bit, name in spec["error_fields"]
              if status & (1 << bit)]
    return {"control": control, "status": status,
            "rs": bool(control & 0x1), "halted": bool(status & 0x1),
            "idle_bit1": (status >> 1) & 0x1,
            "error_bits": errors,
            "reserved_bits": status & spec["reserved_mask"]}


def stop_base_trace_dmas(classification: dict[str, Any], *, mmio_factory,
                         timeout_s: float = 1.0, poll_s: float = 0.02,
                         stable_s: float = 0.2, sleep=time.sleep,
                         clock=time.monotonic) -> dict[str, Any]:
    """Clear RS on exactly the two confirmed base trace S2MM channels.

    The only write is S2MM_DMACR (offset 0x30) with bit 0 cleared; every other
    control bit is preserved. S2MM_DMASR is never written, so errors are not
    cleared and no reset is requested. The channel must reach RS=0 and
    Halted=1 within the timeout and stay stopped through the stability window.
    """
    if classification.get("kind") != "KNOWN_BASE":
        raise PreloadUnsafe("trace DMA stop is only defined for KNOWN_BASE")
    base = classification["base"]
    if base.get("name") != "pynq_base":
        raise PreloadUnsafe("trace DMA stop requires the pinned pynq_base")
    axi_masters = [m for m in base["bus_masters"] if m.get("type") == "axi_dma"]
    actual = {m.get("instance"): int(m.get("base", -1)) for m in axi_masters}
    if actual != TRACE_DMA_TARGETS:
        raise PreloadUnsafe(
            f"unexpected base axi_dma set; refusing to write: {actual}")
    for master in axi_masters:
        if master.get("channels") != ("s2mm",):
            raise PreloadUnsafe(
                f"{master.get('instance')} is not the expected S2MM-only channel")
    result = {}
    for instance, address in TRACE_DMA_TARGETS.items():
        mmio = mmio_factory(address, 0x1000)
        before_control, before_status = _axi_dma_s2mm_state(mmio)
        wrote = False
        if before_control & 0x1:
            mmio.write(0x30, before_control & ~0x1 & 0xFFFFFFFF)
            wrote = True
        deadline = clock() + max(0.0, float(timeout_s))
        while True:
            control, status = _axi_dma_s2mm_state(mmio)
            if not (control & 0x1) and (status & 0x1):
                break
            if clock() >= deadline:
                raise DmaStopTimeout(
                    f"{instance} did not reach RS=0/Halted=1 within "
                    f"{timeout_s}s: control=0x{control:08x} status=0x{status:08x}")
            sleep(poll_s)
        if stable_s:
            sleep(stable_s)
        after_control, after_status = _axi_dma_s2mm_state(mmio)
        if (after_control & 0x1) or not (after_status & 0x1):
            raise PreloadUnsafe(
                f"{instance} was re-armed after stop: "
                f"control=0x{after_control:08x} status=0x{after_status:08x}")
        decoded = _axi_dma_s2mm_decode(after_control, after_status)
        if decoded["error_bits"] or decoded["reserved_bits"]:
            raise PreloadUnsafe(
                f"{instance} stopped with error/reserved bits: {decoded}")
        result[instance] = {
            "base": f"0x{address:08x}",
            "before": _axi_dma_s2mm_decode(before_control, before_status),
            "after": decoded,
            "wrote_rs_clear": wrote,
        }
    return result


def check_candidate_dma_idle(*, mmio_factory):
    """Post-load candidate DMA check at the HWH-confirmed DMA base."""
    dma = mmio_factory(DMA_BASE, 0x10000)
    status = {"mm2s": int(dma.read(0x04)) & 0xFFFFFFFF,
              "s2mm": int(dma.read(0x34)) & 0xFFFFFFFF}
    if any(value & 0x770 or not value & 0x3 for value in status.values()):
        raise DmaOwnershipUncertain(
            f"candidate DMA is active or faulty after load: {status}")
    return status


def make_rx_capture(output: Path):
    """Write the exact bytes handed to decode_packet, bounded to one frame."""
    def capture(frame_id, packet, metadata):
        base = str(output) + f".rx-frame-{frame_id}"
        bin_path = Path(base + ".bin")
        json_path = Path(base + ".json")
        with bin_path.open("wb") as stream:
            stream.write(packet)
            stream.flush()
            os.fsync(stream.fileno())
        payload = {"schema": "material-counter.jpeg-smoke-rx.v1", **metadata}
        with json_path.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    return capture


def build_transport_callback(*, transport_factory, mmio_factory,
                             rx_capture=None):
    """Production transport factory: explicit overlay + preview ownership."""
    def make_transport(tx, rx, overlay, preview):
        send = overlay.axi_dma_0.sendchannel
        recv = overlay.axi_dma_0.recvchannel
        dma_regs = mmio_factory(DMA_BASE, 0x10000)

        def first_transfer_guard(name, channel, regs):
            """Prove the exact channel/MMIO ownership for a status-0 first frame.

            The transport uses the returned control/status/address/length
            values as the unarmed proof; a different channel or MMIO object is
            not owned evidence.
            """
            if regs is not dma_regs:
                return None
            if name == "MM2S":
                if channel is not send:
                    return None
                control_offset, address_offset = 0x00, DMA_MM2S_ADDRESS
            elif name == "S2MM":
                if channel is not recv:
                    return None
                control_offset, address_offset = 0x30, DMA_S2MM_ADDRESS
            else:
                return None
            return {"owned": True,
                    "control": int(regs.read(control_offset)) & 0xFFFFFFFF,
                    "status": int(regs.read(control_offset + 4)) & 0xFFFFFFFF,
                    "address": int(regs.read(address_offset)) & 0xFFFFFFFF,
                    "length": int(regs.read(control_offset + 0x28)) & 0xFFFFFFFF}

        return transport_factory(
            send,
            recv,
            dma_regs,
            mmio_factory(RLE_BASE, 0x10000),
            mmio_factory(JPEG_BASE, 0x10000),
            preview,
            tx, rx,
            first_transfer_guard=first_transfer_guard,
            rx_capture=rx_capture)
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
            transport_factory=transport_factory, mmio_factory=mmio_factory,
            rx_capture=make_rx_capture(output)),
        "stage": stage or (lambda name, **fields: write_stage(
            output, report, name, **fields)),
    }


def run_candidate_sequence(*, classification, mmio_factory, overlay_factory,
                           post_load_identity, candidate_dma_check,
                           preview_factory, prepare_display_fn,
                           allocate_tx, allocate_rx, make_transport,
                           base_evidence_reader=None, stop_base_dmas=False,
                           stop_kwargs=None,
                           stage=lambda *args, **kwargs: None):
    """Load the candidate only after the pre-load decision.

    COLD_BASE: no candidate MMIO before Overlay.download.
    KNOWN_OLD: only the confirmed old control/DMA addresses are read.
    KNOWN_BASE: only the pinned base HWH map and its confirmed DMA channels are
    read; no candidate address is touched before Overlay.download. When
    ``stop_base_dmas`` is true, the two confirmed trace S2MM channels are
    normally stopped (RS bit cleared only) before the four-channel recheck.
    UNKNOWN: stop before Overlay.download; never probe unknown MMIO.
    After load: identity/contract, candidate DMA, display, then allocation.
    """
    kind = classification.get("kind")
    if kind not in ("COLD_BASE", "KNOWN_OLD", "KNOWN_BASE"):
        raise RuntimeError(
            "preload state is unknown; refusing Overlay.download and MMIO")
    allocated: list[Any] = []
    probe = None
    stop_result = None
    if kind == "KNOWN_OLD":
        stage("known_old_probe", classification=classification)
        probe = probe_preload_mmio(classification, mmio_factory=mmio_factory)
        stage("known_old_probe_done", probe=probe)
    elif kind == "KNOWN_BASE":
        stage("known_base_probe", classification=classification)
        evidence = (base_evidence_reader or read_base_hwh_evidence)(
            classification["base"])
        if stop_base_dmas:
            stage("known_base_dma_stop", classification=classification)
            stop_result = stop_base_trace_dmas(
                classification, mmio_factory=mmio_factory,
                **(stop_kwargs or {}))
            stage("known_base_dma_stop_done", stop_result=stop_result)
        probe = probe_preload_mmio(
            classification, mmio_factory=mmio_factory,
            base_evidence_reader=lambda _base: evidence)
        stage("known_base_probe_done", probe=probe)
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
                "candidate_dma": candidate_dma,
                "base_dma_stop": stop_result}
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
    parser.add_argument("--stop-base-trace-dmas", action="store_true",
                        help=("clear RS only on the two confirmed base trace "
                              "S2MM channels before Overlay.download"))
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
        cma_before = read_cma_meminfo()
        budget = cma_budget()
        report["cma_before"] = cma_before
        report["cma_budget"] = budget
        if cma_before["CmaFree"] < budget["required_kib"]:
            raise RuntimeError(
                f"insufficient free CMA for this run: {cma_before['CmaFree']} KiB "
                f"< required {budget['required_kib']} KiB")
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
            classification=classification, mmio_factory=MMIO,
            stop_base_dmas=args.stop_base_trace_dmas, **callbacks)
        display = sequence["display"]
        tx = sequence["tx"]
        rx = sequence["rx"]
        transport = sequence["transport"]
        allocated = sequence["allocated"]
        preview = sequence["preview"]
        report["preload_probe"] = sequence["probe"]
        report["base_dma_stop_result"] = sequence["base_dma_stop"]
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
        try:
            report["cma_after"] = read_cma_meminfo()
        except BaseException as exc:
            report["cma_after_error"] = repr(exc)
        if transport is not None:
            report["rx_capture_error"] = getattr(
                transport, "rx_capture_error", None)
        finalize_run(held=held, display=display, transport=transport,
                     allocated=allocated, capture_source=capture_source,
                     report=report, output=args.output, hold_mode=hold_mode,
                     hold_loop=True)
    return 0 if report["status"] == "JPEG_PL_RUN_COMPLETED_NOT_BOARD_VALIDATED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
