"""Offline preflight and one explicitly approved WO-0014 PYNQ transfer.

Dry-run is the default and never imports PYNQ. A physical transfer requires
both ``--execute`` and ``--approve-once``. The latter is an operator assertion
that a human has approved exactly this one board test; it cannot grant approval.

Run on the PYNQ host in a persistent Python/Jupyter session. If a transfer
times out while MM2S remains active, the source buffer is quarantined in this
module and must stay referenced until a human has independently made the DMA
safe. This script never resets, retries, reloads, or sends a second packet.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Optional


WIDTH = 320
HEIGHT = 240
PIXEL_COUNT = WIDTH * HEIGHT
RGB_PAYLOAD_BYTES = PIXEL_COUNT * 3
PACKET_BYTES = 230432
PACKET_BEATS = PACKET_BYTES // 4
PACKET_MAGIC = 0x574F3134
TRAILER_MAGIC = 0x454E4434
TRAILER_END = 0xC0DEC014
THRESHOLD = 128
EXPECTED_PART = "xc7z020clg400-1"
EXPECTED_DMA_BASE = 0x40400000
EXPECTED_CHECKER_BASE = 0x43C20000
HP0_BASE = 0x00000000
HP0_END_EXCLUSIVE = 0x20000000
DMA_ALIGNMENT = 8
DMA_MAX_ADDRESS = 1 << 32

DMA_MM2S_DMACR = 0x00
DMA_MM2S_DMASR = 0x04
DMA_MM2S_SA = 0x18
DMA_MM2S_LENGTH = 0x28
DMA_CR_RUNSTOP = 1 << 0
DMA_CR_RESET = 1 << 2
DMA_SR_HALTED = 1 << 0
DMA_SR_IDLE = 1 << 1
DMA_SR_ERROR_MASK = (1 << 4) | (1 << 5) | (1 << 6) | (1 << 14)

CHECKER_REGS = {
    "drain_enable": 0x00,
    "magic": 0x04,
    "status": 0x08,
    "error_code": 0x0C,
    "frame_id": 0x10,
    "packet_done_count": 0x14,
    "packet_error_count": 0x18,
    "input_bytes": 0x1C,
    "beats": 0x20,
    "tlast_count": 0x24,
    "first_word": 0x28,
    "last_word": 0x2C,
    "input_byte_checksum": 0x30,
    "observed_pixels": 0x34,
    "sofs": 0x38,
    "eols": 0x3C,
    "binary_checksum": 0x40,
    "accepted_output_total": 0x44,
    "stalled_cycles": 0x48,
}
STATUS_PACKET_DONE = 1 << 0
STATUS_PACKET_ERROR = 1 << 1
STATUS_CHECKER_ERROR = 1 << 2
STATUS_OUTPUT_COMPLETE = 1 << 3
_ZERO_BEFORE_SEND = (
    "packet_done_count", "packet_error_count", "input_bytes", "beats",
    "tlast_count", "input_byte_checksum", "observed_pixels", "sofs",
    "eols", "binary_checksum", "accepted_output_total", "stalled_cycles",
)
_QUARANTINED_BUFFERS: list[Any] = []
_QUARANTINED_CHANNELS: list[tuple[Any, Any]] = []


class PreflightError(RuntimeError):
    """The offline artifacts or their recorded hardware contract disagree."""


@dataclass(frozen=True)
class ValidatedInputs:
    bit_path: Path
    hwh_path: Path
    packet_path: Path
    manifest_path: Path
    packet: bytes
    manifest: Mapping[str, Any]
    bit_sha256: str
    hwh_sha256: str
    packet_sha256: str
    manifest_sha256: str
    input_byte_checksum: int
    expected_payload_checksum: int
    expected_binary_checksum: int
    frame_id: int

    @property
    def clock_mhz(self) -> int:
        return int(self.manifest["clock_mhz"])

    @property
    def build_id(self) -> str:
        return str(self.manifest["build_id"])

    @property
    def dma_base(self) -> int:
        return _parse_int(self.manifest["dma_base"], "dma_base")

    @property
    def checker_base(self) -> int:
        return _parse_int(self.manifest["checker_base"], "checker_base")

    @property
    def packet_bytes(self) -> int:
        return len(self.packet)

    @property
    def packet_beats(self) -> int:
        return len(self.packet) // 4


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_int(value: Any, field: str) -> int:
    try:
        return int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise PreflightError(f"manifest {field} is not an integer: {value!r}") from exc


def _manifest_path_basename(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PreflightError(f"manifest {field} must be a non-empty path string")
    text = value.strip()
    if not (PureWindowsPath(text).is_absolute() or PurePosixPath(text).is_absolute()):
        raise PreflightError(f"manifest {field} must be an absolute build path")
    return PureWindowsPath(text).name if "\\" in text else PurePosixPath(text).name


def _local_tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _module_by_instance(root: ET.Element, instance: str) -> ET.Element:
    matches = [node for node in root.iter()
               if _local_tag(node) == "MODULE" and node.attrib.get("INSTANCE") == instance]
    if len(matches) != 1:
        raise PreflightError(f"HWH must contain exactly one MODULE INSTANCE={instance!r}; "
                             f"found {len(matches)}")
    return matches[0]


def _module_parameters(module: ET.Element) -> dict[str, str]:
    return {node.attrib["NAME"]: node.attrib["VALUE"]
            for node in module.iter()
            if _local_tag(node) == "PARAMETER" and "NAME" in node.attrib
            and "VALUE" in node.attrib}


def _require_parameter(parameters: Mapping[str, str], name: str, expected: str,
                       instance: str) -> None:
    actual = parameters.get(name)
    if actual is None or actual.strip().lower() != expected.lower():
        raise PreflightError(f"HWH {instance}.{name} is {actual!r}; expected {expected!r}")


def _require_manifest_contract(manifest: Mapping[str, Any]) -> None:
    required = {
        "schema", "build_id", "clock_mhz", "part", "bit_path", "hwh_path",
        "bit_sha256", "hwh_sha256", "dma_base", "checker_base",
        "dma_length_width", "dma_mode", "dma_mm2s", "dma_s2mm",
        "dma_stream_width_bits", "dma_addr_width_bits",
        "dma_mm2s_memory_width_bits", "dma_mm2s_dre", "dma_include_sg",
        "dma_micro_mode", "hp0_range_start", "hp0_range_end_exclusive",
    }
    missing = sorted(required - manifest.keys())
    if missing:
        raise PreflightError("pair manifest is missing: " + ", ".join(missing))
    if type(manifest["schema"]) is not int or manifest["schema"] != 1:
        raise PreflightError(f"unsupported pair manifest schema: {manifest['schema']!r}")
    if not isinstance(manifest["build_id"], str) or not manifest["build_id"].strip():
        raise PreflightError("pair manifest build_id must be non-empty")
    if _parse_int(manifest["clock_mhz"], "clock_mhz") not in (50, 100):
        raise PreflightError("pair manifest clock_mhz must be 50 or 100")
    if manifest["part"] != EXPECTED_PART:
        raise PreflightError(f"pair manifest part is {manifest['part']!r}; "
                             f"expected {EXPECTED_PART!r}")
    exact = {
        "dma_base": EXPECTED_DMA_BASE,
        "checker_base": EXPECTED_CHECKER_BASE,
        "dma_length_width": 18,
        "dma_stream_width_bits": 32,
        "dma_addr_width_bits": 32,
        "dma_mm2s_memory_width_bits": 64,
        "hp0_range_start": HP0_BASE,
        "hp0_range_end_exclusive": HP0_END_EXCLUSIVE,
    }
    for field, expected in exact.items():
        actual = _parse_int(manifest[field], field)
        if actual != expected:
            raise PreflightError(f"pair manifest {field} is 0x{actual:x}; "
                                 f"expected 0x{expected:x}")
    bools = {
        "dma_mm2s": True,
        "dma_s2mm": False,
        "dma_mm2s_dre": False,
        "dma_include_sg": False,
        "dma_micro_mode": False,
    }
    for field, expected in bools.items():
        if type(manifest[field]) is not bool or manifest[field] is not expected:
            raise PreflightError(f"pair manifest {field} must be {expected}")
    if str(manifest["dma_mode"]).lower() != "simple":
        raise PreflightError("pair manifest DMA mode must be simple")
    for field in ("bit_sha256", "hwh_sha256"):
        value = manifest[field]
        if (not isinstance(value, str) or len(value) != 64 or
                any(char not in "0123456789abcdef" for char in value.lower())):
            raise PreflightError(f"pair manifest {field} must be a SHA-256 hex digest")


def _validate_hwh(path: Path, manifest: Mapping[str, Any]) -> None:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise PreflightError(f"cannot parse HWH XML {path}: {exc}") from exc

    dma = _module_parameters(_module_by_instance(root, "axi_dma_0"))
    dma_expected = {
        "C_INCLUDE_MM2S": "1",
        "C_INCLUDE_S2MM": "0",
        "C_INCLUDE_SG": "0",
        "C_MICRO_DMA": "0",
        "C_SG_LENGTH_WIDTH": "18",
        "C_M_AXI_MM2S_ADDR_WIDTH": "32",
        "C_M_AXI_MM2S_DATA_WIDTH": "64",
        "C_M_AXIS_MM2S_TDATA_WIDTH": "32",
        "C_INCLUDE_MM2S_DRE": "0",
    }
    for name, value in dma_expected.items():
        _require_parameter(dma, name, value, "axi_dma_0")
    capacity = (1 << int(dma["C_SG_LENGTH_WIDTH"])) - 1
    if capacity < PACKET_BYTES:
        raise PreflightError(f"18-bit MM2S length limit {capacity} is below {PACKET_BYTES}")

    ps = _module_parameters(_module_by_instance(root, "ps7"))
    _require_parameter(ps, "PCW_FPGA0_PERIPHERAL_FREQMHZ",
                       str(_parse_int(manifest["clock_mhz"], "clock_mhz")), "ps7")
    _require_parameter(ps, "PCW_USE_S_AXI_HP0", "1", "ps7")
    _require_parameter(ps, "PCW_S_AXI_HP0_DATA_WIDTH", "64", "ps7")

    pipeline = _module_parameters(_module_by_instance(root, "image_pipeline"))
    for name, expected in (("WIDTH", "320"), ("HEIGHT", "240"), ("THRESHOLD", "128")):
        _require_parameter(pipeline, name, expected, "image_pipeline")

    memranges = [node.attrib for node in root.iter() if _local_tag(node) == "MEMRANGE"]
    expected_ranges = {
        "axi_dma_0": EXPECTED_DMA_BASE,
        "image_pipeline": EXPECTED_CHECKER_BASE,
    }
    for instance, expected_base in expected_ranges.items():
        matches = [entry for entry in memranges if entry.get("INSTANCE") == instance]
        if len(matches) != 1:
            raise PreflightError(f"HWH must contain exactly one MEMRANGE for {instance}; "
                                 f"found {len(matches)}")
        entry = matches[0]
        actual_base = _parse_int(entry.get("BASEVALUE"), f"HWH {instance} BASEVALUE")
        if actual_base != expected_base:
            raise PreflightError(f"HWH base for {instance} is 0x{actual_base:x}; "
                                 f"expected 0x{expected_base:x}")
        if entry.get("MEMTYPE", "").upper() != "REGISTER":
            raise PreflightError(f"HWH {instance} address range is not a REGISTER map")

    hp0 = [entry for entry in memranges
           if entry.get("INSTANCE") == "ps7"
           and entry.get("ADDRESSBLOCK") == "HP0_DDR_LOWOCM"
           and entry.get("SLAVEBUSINTERFACE") == "S_AXI_HP0"]
    if len(hp0) != 1:
        raise PreflightError(f"HWH must contain one PS HP0 DDR range; found {len(hp0)}")
    hp0_base = _parse_int(hp0[0].get("BASEVALUE"), "HWH HP0 BASEVALUE")
    hp0_high_inclusive = _parse_int(hp0[0].get("HIGHVALUE"), "HWH HP0 HIGHVALUE")
    if hp0_base != HP0_BASE or hp0_high_inclusive + 1 != HP0_END_EXCLUSIVE:
        raise PreflightError("HWH HP0 range must be [0x00000000,0x20000000)")


def _binary_checksum(payload: bytes) -> int:
    total = 0
    for offset in range(0, len(payload), 3):
        red, green, blue = payload[offset:offset + 3]
        gray = (77 * red + 150 * green + 29 * blue) // 256
        if gray >= THRESHOLD:
            total = (total + 0xFF) & 0xFFFFFFFF
    return total


def _validate_packet(packet: bytes) -> tuple[int, int, int, int]:
    if len(packet) != PACKET_BYTES:
        raise PreflightError(f"packet must be exactly {PACKET_BYTES} bytes; got {len(packet)}")
    if len(packet) % 4:
        raise PreflightError("packet size must be aligned to 32-bit AXI stream words")
    magic, geometry, payload_length, frame_id = struct.unpack_from("<IIII", packet, 0)
    if magic != PACKET_MAGIC:
        raise PreflightError(f"packet header magic is 0x{magic:08x}; expected 0x{PACKET_MAGIC:08x}")
    expected_geometry = (HEIGHT << 16) | WIDTH
    if geometry != expected_geometry:
        raise PreflightError(f"packet geometry word is 0x{geometry:08x}; "
                             f"expected 0x{expected_geometry:08x}")
    if payload_length != RGB_PAYLOAD_BYTES:
        raise PreflightError(f"packet payload length is {payload_length}; "
                             f"expected {RGB_PAYLOAD_BYTES}")
    payload = packet[16:16 + RGB_PAYLOAD_BYTES]
    trailer = struct.unpack_from("<IIII", packet, 16 + RGB_PAYLOAD_BYTES)
    trailer_magic, trailer_frame, payload_checksum, trailer_end = trailer
    if trailer_magic != TRAILER_MAGIC:
        raise PreflightError(f"trailer magic is 0x{trailer_magic:08x}; "
                             f"expected 0x{TRAILER_MAGIC:08x}")
    if trailer_frame != frame_id:
        raise PreflightError("header and trailer frame IDs differ")
    expected_payload_checksum = sum(payload) & 0xFFFFFFFF
    if payload_checksum != expected_payload_checksum:
        raise PreflightError(f"payload checksum is 0x{payload_checksum:08x}; "
                             f"expected 0x{expected_payload_checksum:08x}")
    if trailer_end != TRAILER_END:
        raise PreflightError(f"trailer end marker is 0x{trailer_end:08x}; "
                             f"expected 0x{TRAILER_END:08x}")
    return frame_id, expected_payload_checksum, _binary_checksum(payload), sum(packet) & 0xFFFFFFFF


def validate_inputs(bit_path: str | Path, hwh_path: str | Path,
                    packet_path: str | Path, manifest_path: str | Path) -> ValidatedInputs:
    """Verify artifact identity, HWH configuration, and one formal RGB packet."""
    bit = Path(bit_path).expanduser().resolve()
    hwh = Path(hwh_path).expanduser().resolve()
    packet_file = Path(packet_path).expanduser().resolve()
    manifest_file = Path(manifest_path).expanduser().resolve()
    for label, path in (("BIT", bit), ("HWH", hwh), ("packet", packet_file),
                        ("pair manifest", manifest_file)):
        if not path.is_file():
            raise PreflightError(f"{label} file does not exist: {path}")
    if bit.suffix.lower() != ".bit" or hwh.suffix.lower() != ".hwh":
        raise PreflightError("BIT and HWH inputs must have .bit and .hwh suffixes")
    if bit.stem != hwh.stem:
        raise PreflightError("BIT and HWH must share a basename for PYNQ Overlay discovery")
    if bit.parent != hwh.parent:
        raise PreflightError("BIT and HWH must be colocated so PYNQ loads the verified HWH")
    try:
        manifest_bytes = manifest_file.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreflightError(f"cannot read pair manifest {manifest_file}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise PreflightError("pair manifest root must be a JSON object")
    _require_manifest_contract(manifest)
    if (_manifest_path_basename(manifest["bit_path"], "bit_path") != bit.name or
            _manifest_path_basename(manifest["hwh_path"], "hwh_path") != hwh.name):
        raise PreflightError("CLI BIT/HWH basenames do not match the pair manifest")
    bit_hash = _sha256_file(bit)
    hwh_hash = _sha256_file(hwh)
    if bit_hash != manifest["bit_sha256"].lower():
        raise PreflightError("BIT SHA-256 does not match pair manifest")
    if hwh_hash != manifest["hwh_sha256"].lower():
        raise PreflightError("HWH SHA-256 does not match pair manifest")
    _validate_hwh(hwh, manifest)
    packet = packet_file.read_bytes()
    frame_id, payload_checksum, binary_checksum, input_checksum = _validate_packet(packet)
    return ValidatedInputs(
        bit, hwh, packet_file, manifest_file, packet, manifest, bit_hash, hwh_hash,
        _sha256_bytes(packet), _sha256_bytes(manifest_bytes), input_checksum,
        payload_checksum, binary_checksum, frame_id,
    )


def _validate_dma_address(address: int, size: int, allocated_bytes: int) -> None:
    if allocated_bytes < size:
        raise PreflightError(f"PYNQ buffer has {allocated_bytes} bytes; need {size}")
    if address < 0 or address % DMA_ALIGNMENT:
        raise PreflightError(f"DMA address 0x{address:x} is not {DMA_ALIGNMENT}-byte aligned")
    end_exclusive = address + size
    if address >= DMA_MAX_ADDRESS or end_exclusive > DMA_MAX_ADDRESS:
        raise PreflightError(f"DMA range [0x{address:x},0x{end_exclusive:x}) exceeds 32-bit space")
    if address < HP0_BASE or end_exclusive > HP0_END_EXCLUSIVE:
        raise PreflightError(f"DMA range [0x{address:x},0x{end_exclusive:x}) is outside HP0 DDR")


def _buffer_address(buffer: Any) -> int:
    address = getattr(buffer, "device_address", None)
    if address is None:
        address = getattr(buffer, "physical_address", None)
    if address is None:
        raise PreflightError("PYNQ buffer exposes neither device_address nor physical_address")
    return int(address)


def _sync_to_device(buffer: Any) -> str:
    method = getattr(buffer, "sync_to_device", None)
    label = "sync_to_device"
    if not callable(method):
        method = getattr(buffer, "flush", None)
        label = "flush"
    if not callable(method):
        raise PreflightError("PYNQ buffer has neither sync_to_device() nor flush()")
    method()
    return label


def _release_buffer(buffer: Any) -> None:
    method = getattr(buffer, "freebuffer", None)
    if not callable(method):
        method = getattr(buffer, "close", None)
    if callable(method):
        method()


def _read_checker(mmio: Any) -> dict[str, int]:
    return {name: int(mmio.read(offset)) & 0xFFFFFFFF
            for name, offset in CHECKER_REGS.items()}


def _read_raw_registers(dma_mmio: Any, checker_mmio: Any) -> dict[str, Optional[int]]:
    result: dict[str, Optional[int]] = {}
    for name, offset in (("mm2s_dmacr", DMA_MM2S_DMACR),
                         ("mm2s_dmasr", DMA_MM2S_DMASR),
                         ("mm2s_source_address", DMA_MM2S_SA),
                         ("mm2s_length", DMA_MM2S_LENGTH)):
        try:
            result[name] = int(dma_mmio.read(offset)) & 0xFFFFFFFF
        except Exception:
            result[name] = None
    for name, offset in CHECKER_REGS.items():
        try:
            result[f"checker_{name}"] = int(checker_mmio.read(offset)) & 0xFFFFFFFF
        except Exception:
            result[f"checker_{name}"] = None
    return result


def _check_checker_ready(checker: Mapping[str, int]) -> None:
    if checker["drain_enable"] != 1:
        raise PreflightError(f"checker drain_enable is {checker['drain_enable']}; expected 1")
    if checker["magic"] != PACKET_MAGIC:
        raise PreflightError(f"checker magic is 0x{checker['magic']:08x}; "
                             f"expected 0x{PACKET_MAGIC:08x}")
    if checker["status"] & 0xF:
        raise PreflightError(f"checker status is not clear: 0x{checker['status']:08x}")
    if checker["error_code"] != 0:
        raise PreflightError(f"checker error_code is not clear: 0x{checker['error_code']:08x}")
    nonzero = {name: checker[name] for name in _ZERO_BEFORE_SEND if checker[name] != 0}
    if nonzero:
        raise PreflightError(f"checker counters are not zero before send: {nonzero}")


def _check_dma_quiescent_status(status: int, where: str) -> int:
    """Accept only a clean, explicitly halted or idle MM2S channel."""
    if status & DMA_SR_ERROR_MASK:
        raise PreflightError(f"MM2S has error status {where}: 0x{status:08x}")
    state = status & (DMA_SR_HALTED | DMA_SR_IDLE)
    if state not in (DMA_SR_HALTED, DMA_SR_IDLE):
        raise PreflightError(f"MM2S is neither halted nor idle {where}: 0x{status:08x}")
    return state


def _check_dma_idle_before_length(dma_mmio: Any, where: str) -> int:
    status = int(dma_mmio.read(DMA_MM2S_DMASR)) & 0xFFFFFFFF
    state = _check_dma_quiescent_status(status, where)
    if state != DMA_SR_IDLE:
        raise PreflightError(f"MM2S is halted, not idle {where}: 0x{status:08x}")
    control = int(dma_mmio.read(DMA_MM2S_DMACR)) & 0xFFFFFFFF
    if control & DMA_CR_RESET:
        raise PreflightError(f"MM2S reset bit is set {where}: 0x{control:08x}")
    if not control & DMA_CR_RUNSTOP:
        raise PreflightError(f"MM2S RUNSTOP is clear {where}: 0x{control:08x}")
    return status


def _check_checker_result(plan: ValidatedInputs,
                          checker: Mapping[str, int]) -> None:
    status = checker["status"]
    required_status = STATUS_PACKET_DONE | STATUS_OUTPUT_COMPLETE
    if status & (STATUS_PACKET_ERROR | STATUS_CHECKER_ERROR):
        raise PreflightError(f"checker reported error status 0x{status:08x}")
    if status & required_status != required_status:
        raise PreflightError(f"packet/output completion status is incomplete: 0x{status:08x}")
    expected = {
        "error_code": 0,
        "frame_id": plan.frame_id,
        "packet_done_count": 1,
        "packet_error_count": 0,
        "input_bytes": PACKET_BYTES,
        "beats": PACKET_BEATS,
        "tlast_count": 1,
        "first_word": PACKET_MAGIC,
        "last_word": TRAILER_END,
        "input_byte_checksum": plan.input_byte_checksum,
        "observed_pixels": PIXEL_COUNT,
        "sofs": 1,
        "eols": HEIGHT,
        "binary_checksum": plan.expected_binary_checksum,
        "accepted_output_total": PIXEL_COUNT,
    }
    failures = {name: {"actual": checker[name], "expected": value}
                for name, value in expected.items() if checker[name] != value}
    if failures:
        raise PreflightError(f"checker statistics do not match the legal frame: {failures}")


def _load_dependencies() -> Any:
    # Keep all target-only imports out of module import and the default dry-run.
    import numpy as np
    from pynq import MMIO, Overlay, allocate

    class Dependencies:
        pass

    dependencies = Dependencies()
    dependencies.np = np
    dependencies.MMIO = MMIO
    dependencies.Overlay = Overlay
    dependencies.allocate = allocate
    return dependencies


def run_hardware_once(plan: ValidatedInputs, *, api: Any = None,
                      timeout_s: float = 5.0, poll_interval_s: float = 0.005) -> dict[str, Any]:
    """Attempt no more than one MM2S_LENGTH write and return JSON-ready evidence."""
    evidence: dict[str, Any] = {
        "task": "WO-0014",
        "status": "FAIL",
        "dry_run": False,
        "hardware_attempted": True,
        "board_tested": False,
        "send_attempt_count": 0,
        "build": {
            "build_id": plan.build_id,
            "clock_mhz": plan.clock_mhz,
            "part": plan.manifest["part"],
            "bit_path": str(plan.bit_path),
            "hwh_path": str(plan.hwh_path),
            "bit_sha256": plan.bit_sha256,
            "hwh_sha256": plan.hwh_sha256,
            "manifest_path": str(plan.manifest_path),
            "manifest_sha256": plan.manifest_sha256,
            "dma_base": f"0x{plan.dma_base:08x}",
            "checker_base": f"0x{plan.checker_base:08x}",
            "dma_length_width": 18,
            "dma_mode": "simple",
            "dma_mm2s": True,
            "dma_s2mm": False,
            "dma_stream_width_bits": 32,
            "dma_addr_width_bits": 32,
            "dma_mm2s_memory_width_bits": 64,
            "dma_mm2s_dre": False,
            "hp0_range": "[0x00000000,0x20000000)",
        },
        "packet": {
            "path": str(plan.packet_path),
            "bytes": plan.packet_bytes,
            "beats": plan.packet_beats,
            "sha256": plan.packet_sha256,
            "frame_id": plan.frame_id,
            "payload_checksum": plan.expected_payload_checksum,
            "input_byte_checksum": plan.input_byte_checksum,
            "expected_binary_checksum": plan.expected_binary_checksum,
            "gray_formula": "(77*R+150*G+29*B)//256",
            "binary_threshold": THRESHOLD,
            "expected_pixels": PIXEL_COUNT,
            "expected_sofs": 1,
            "expected_eols": HEIGHT,
            "expected_tlast_count": 1,
        },
        "checker_limits": {
            "aggregate_only": True,
            "per_pixel_output_readback": False,
            "underlength_early_sof_and_error_recovery_exercised": False,
            "note": ("One legal frame and aggregate counters do not prove every output pixel "
                     "against a golden image, nor do they exercise underlength, early SOF, "
                     "or error-recovery behavior."),
        },
        "raw_registers": {},
    }
    if timeout_s <= 0 or timeout_s > 120:
        evidence["error"] = "timeout_s must be in (0,120]"
        return evidence
    if poll_interval_s <= 0 or poll_interval_s > 0.1:
        evidence["error"] = "poll_interval_s must be in (0,0.1]"
        return evidence

    buffer = None
    dma_mmio = None
    checker_mmio = None
    safe_to_release = False
    try:
        if api is None:
            api = _load_dependencies()
        if _sha256_file(plan.bit_path) != plan.bit_sha256:
            raise PreflightError("BIT changed after offline preflight; refusing Overlay load")
        if _sha256_file(plan.hwh_path) != plan.hwh_sha256:
            raise PreflightError("HWH changed after offline preflight; refusing Overlay load")
        overlay = api.Overlay(str(plan.bit_path), download=True)
        evidence["overlay_loaded_once"] = True
        evidence["overlay_ip_bases"] = _resolve_overlay_bases(
            overlay.ip_dict, plan.dma_base, plan.checker_base)
        dma_mmio = api.MMIO(plan.dma_base, length=0x1000)
        checker_mmio = api.MMIO(plan.checker_base, length=0x1000)

        dma_status = int(dma_mmio.read(DMA_MM2S_DMASR)) & 0xFFFFFFFF
        initial_state = _check_dma_quiescent_status(dma_status, "before send")
        control = int(dma_mmio.read(DMA_MM2S_DMACR)) & 0xFFFFFFFF
        if control & DMA_CR_RESET:
            raise PreflightError(f"MM2S reset bit is set before send: 0x{control:08x}")
        if initial_state == DMA_SR_HALTED and control & DMA_CR_RUNSTOP:
            raise PreflightError(f"MM2S is halted with RUNSTOP set before send: 0x{control:08x}")
        checker = _read_checker(checker_mmio)
        _check_checker_ready(checker)
        evidence["pre_send_checker"] = checker
        evidence["pre_send_dma_status"] = dma_status
        evidence["pre_send_dma_control"] = control

        buffer = api.allocate(shape=(PACKET_BYTES,), dtype=api.np.uint8)
        if len(buffer) < PACKET_BYTES:
            raise PreflightError(f"allocated buffer has {len(buffer)} bytes; need {PACKET_BYTES}")
        flags = getattr(buffer, "flags", None)
        if flags is None or not bool(getattr(flags, "c_contiguous", False)):
            raise PreflightError("PYNQ DMA buffer is not a contiguous C-order allocation")
        buffer[:PACKET_BYTES] = api.np.frombuffer(plan.packet, dtype=api.np.uint8)
        address = _buffer_address(buffer)
        _validate_dma_address(address, PACKET_BYTES, len(buffer))
        cpu_hash = _sha256_bytes(bytes(buffer[:PACKET_BYTES]))
        if cpu_hash != plan.packet_sha256:
            raise PreflightError("CPU readback hash of allocated DMA buffer differs from packet")
        sync_method = _sync_to_device(buffer)
        evidence["buffer"] = {
            "device_address": f"0x{address:08x}",
            "size_bytes": len(buffer),
            "alignment_bytes": DMA_ALIGNMENT,
            "range_end_exclusive": f"0x{address + PACKET_BYTES:08x}",
            "cpu_readback_sha256": cpu_hash,
            "sync_method": sync_method,
        }

        # Recheck state immediately before programming the one-shot channel.
        dma_status = int(dma_mmio.read(DMA_MM2S_DMASR)) & 0xFFFFFFFF
        initial_state = _check_dma_quiescent_status(dma_status, "immediately before RUNSTOP")
        checker = _read_checker(checker_mmio)
        _check_checker_ready(checker)
        control = int(dma_mmio.read(DMA_MM2S_DMACR)) & 0xFFFFFFFF
        if control & DMA_CR_RESET:
            raise PreflightError(f"MM2S reset bit is set before send: 0x{control:08x}")
        if initial_state == DMA_SR_HALTED and control & DMA_CR_RUNSTOP:
            raise PreflightError(f"MM2S is halted with RUNSTOP set before send: 0x{control:08x}")
        dma_mmio.write(DMA_MM2S_DMACR, control | DMA_CR_RUNSTOP)

        start_deadline = time.monotonic() + min(timeout_s, 1.0)
        while True:
            status = int(dma_mmio.read(DMA_MM2S_DMASR)) & 0xFFFFFFFF
            if status & DMA_SR_ERROR_MASK:
                raise PreflightError(f"MM2S failed to start: status=0x{status:08x}")
            state = status & (DMA_SR_HALTED | DMA_SR_IDLE)
            if state == DMA_SR_IDLE:
                evidence["post_runstop_dma_status"] = status
                break
            if state == (DMA_SR_HALTED | DMA_SR_IDLE):
                raise PreflightError(f"MM2S has invalid HALTED+IDLE state after RUNSTOP: "
                                     f"0x{status:08x}")
            # A transition with neither status bit set is never considered
            # ready; wait only within the bound and require IDLE before LENGTH.
            remaining = start_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for MM2S IDLE after RUNSTOP")
            time.sleep(min(poll_interval_s, remaining))

        # SA does not start direct mode; LENGTH is the start command. Recheck
        # idle, RUNSTOP, and reset immediately before the sole LENGTH write.
        dma_mmio.write(DMA_MM2S_SA, address & 0xFFFFFFFF)
        evidence["pre_length_dma_status"] = _check_dma_idle_before_length(
            dma_mmio, "before LENGTH")
        # One MM2S_LENGTH write starts the transfer. Increment before the write
        # so even an MMIO exception cannot lead to an uncounted second attempt.
        evidence["send_attempt_count"] = 1
        evidence["board_tested"] = True
        dma_mmio.write(DMA_MM2S_LENGTH, PACKET_BYTES)

        deadline = time.monotonic() + timeout_s
        final_checker: dict[str, int] = {}
        final_dma_status = 0
        while True:
            final_dma_status = int(dma_mmio.read(DMA_MM2S_DMASR)) & 0xFFFFFFFF
            final_checker = _read_checker(checker_mmio)
            if final_dma_status & DMA_SR_ERROR_MASK:
                raise PreflightError(f"MM2S error after send: status=0x{final_dma_status:08x}")
            if final_checker["status"] & (STATUS_PACKET_ERROR | STATUS_CHECKER_ERROR):
                raise PreflightError(f"pipeline reported error status=0x{final_checker['status']:08x}, "
                                     f"code=0x{final_checker['error_code']:08x}")
            complete = bool(final_dma_status & DMA_SR_IDLE)
            complete = complete and (final_checker["status"] &
                                     (STATUS_PACKET_DONE | STATUS_OUTPUT_COMPLETE) ==
                                     (STATUS_PACKET_DONE | STATUS_OUTPUT_COMPLETE))
            if complete:
                safe_to_release = True
                _check_checker_result(plan, final_checker)
                evidence["status"] = "PASS"
                evidence["post_dma_status"] = final_dma_status
                evidence["checker"] = final_checker
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for MM2S idle and complete pipeline statistics")
            time.sleep(min(poll_interval_s, remaining))
    except BaseException as exc:
        evidence["status"] = "FAIL"
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        if dma_mmio is not None and checker_mmio is not None:
            evidence["raw_registers"] = _read_raw_registers(dma_mmio, checker_mmio)
        if dma_mmio is not None:
            try:
                status = int(dma_mmio.read(DMA_MM2S_DMASR)) & 0xFFFFFFFF
                evidence["failure_dma_status"] = status
                state = status & (DMA_SR_HALTED | DMA_SR_IDLE)
                safe_to_release = state in (DMA_SR_HALTED, DMA_SR_IDLE)
            except Exception as read_error:
                evidence["dma_status_read_error"] = f"{type(read_error).__name__}: {read_error}"
                safe_to_release = False
    finally:
        if buffer is not None:
            if safe_to_release:
                try:
                    _release_buffer(buffer)
                    evidence["buffer_released"] = True
                except Exception as exc:
                    evidence["buffer_released"] = False
                    evidence["buffer_release_error"] = f"{type(exc).__name__}: {exc}"
                    evidence["status"] = "FAIL"
            else:
                _QUARANTINED_BUFFERS.append(buffer)
                _QUARANTINED_CHANNELS.append((buffer, dma_mmio))
                evidence["buffer_released"] = False
                evidence["buffer_quarantined_in_process"] = True
                evidence["operator_action"] = (
                    "Keep this Python/Jupyter process alive and retain its buffer until a human "
                    "has independently established that MM2S is idle or the board is reset. "
                    "This script does not reset or retry."
                )
        if dma_mmio is not None and checker_mmio is not None:
            evidence["raw_registers"] = _read_raw_registers(dma_mmio, checker_mmio)
    return evidence


def _resolve_overlay_bases(ip_dict: Mapping[str, Mapping[str, Any]],
                            dma_base: int, checker_base: int) -> dict[str, str]:
    def resolve(component: str, expected: int) -> int:
        matches = []
        for name, metadata in ip_dict.items():
            if component not in str(name).strip("/").split("/"):
                continue
            actual = None
            for field in ("phys_addr", "base_addr", "base_address"):
                if metadata.get(field) is not None:
                    actual = _parse_int(metadata[field], f"Overlay {name}.{field}")
                    break
            if actual is None:
                raise PreflightError(f"Overlay.ip_dict {name!r} has no physical base")
            matches.append(actual)
        if len(matches) != 1 or matches[0] != expected:
            raise PreflightError(f"Overlay HWH base for {component} is {matches}; "
                                 f"expected one entry at 0x{expected:08x}")
        return matches[0]

    return {
        "axi_dma_0": f"0x{resolve('axi_dma_0', dma_base):08x}",
        "image_pipeline": f"0x{resolve('image_pipeline', checker_base):08x}",
    }


def _dry_run_evidence(plan: ValidatedInputs) -> dict[str, Any]:
    return {
        "task": "WO-0014",
        "status": "DRY_RUN_READY",
        "dry_run": True,
        "hardware_attempted": False,
        "board_tested": False,
        "send_attempt_count": 0,
        "build": {
            "build_id": plan.build_id,
            "clock_mhz": plan.clock_mhz,
            "part": plan.manifest["part"],
            "bit_path": str(plan.bit_path),
            "hwh_path": str(plan.hwh_path),
            "bit_sha256": plan.bit_sha256,
            "hwh_sha256": plan.hwh_sha256,
            "manifest_path": str(plan.manifest_path),
            "manifest_sha256": plan.manifest_sha256,
            "dma_base": f"0x{plan.dma_base:08x}",
            "checker_base": f"0x{plan.checker_base:08x}",
            "dma_length_width": 18,
            "maximum_length_bytes": (1 << 18) - 1,
            "dma_mode": "simple",
            "dma_mm2s": True,
            "dma_s2mm": False,
            "dma_stream_width_bits": 32,
            "dma_addr_width_bits": 32,
            "dma_mm2s_memory_width_bits": 64,
            "dma_mm2s_dre": False,
            "hp0_range": "[0x00000000,0x20000000)",
        },
        "packet": {
            "path": str(plan.packet_path),
            "bytes": plan.packet_bytes,
            "beats": plan.packet_beats,
            "sha256": plan.packet_sha256,
            "frame_id": plan.frame_id,
            "payload_checksum": plan.expected_payload_checksum,
            "input_byte_checksum": plan.input_byte_checksum,
            "expected_binary_checksum": plan.expected_binary_checksum,
            "gray_formula": "(77*R+150*G+29*B)//256",
            "binary_threshold": THRESHOLD,
            "expected_pixels": PIXEL_COUNT,
            "expected_sofs": 1,
            "expected_eols": HEIGHT,
            "expected_tlast_count": 1,
            "tlast_contract": "only the final trailer word; generated by one DMA transfer",
        },
        "checker_limits": {
            "aggregate_only": True,
            "per_pixel_output_readback": False,
            "underlength_early_sof_and_error_recovery_exercised": False,
            "note": ("Offline preflight verifies packet bytes and derives the Binary checksum. "
                     "Aggregate hardware statistics do not prove every output pixel against a "
                     "golden image, nor do they exercise underlength, early SOF, or recovery."),
        },
    }


def _emit_json(result: Mapping[str, Any], output_path: Optional[str]) -> None:
    encoded = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if output_path:
        path = Path(output_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="WO-0014 one-shot RGB DMA replay. Default mode is offline dry-run.")
    parser.add_argument("--bit", required=True, help="matching experimental .bit file")
    parser.add_argument("--hwh", required=True, help="matching experimental .hwh file")
    parser.add_argument("--packet", required=True, help="formal 230432-byte RGB packet")
    parser.add_argument("--manifest", required=True,
                        help="pair-manifest.json with BIT/HWH SHA-256 and build metadata")
    parser.add_argument("--execute", action="store_true",
                        help="load this overlay once and attempt one MM2S transfer")
    parser.add_argument("--approve-once", action="store_true",
                        help="operator assertion: a human approved exactly one board test")
    parser.add_argument("--timeout-s", type=float, default=5.0,
                        help="bounded MM2S plus pipeline drain deadline (default 5 seconds)")
    parser.add_argument("--poll-interval-s", type=float, default=0.005,
                        help="bounded status polling interval (default 5 ms)")
    parser.add_argument("--evidence-json", help="also write full JSON evidence to this path")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.execute != args.approve_once:
        result = {
            "task": "WO-0014", "status": "FAIL", "dry_run": True,
            "hardware_attempted": False, "send_attempt_count": 0,
            "error": "--execute and --approve-once must be supplied together; "
                     "the flag records an existing human approval and does not grant one.",
        }
        _emit_json(result, args.evidence_json)
        return 2
    try:
        plan = validate_inputs(args.bit, args.hwh, args.packet, args.manifest)
        if args.execute:
            result = run_hardware_once(plan, timeout_s=args.timeout_s,
                                       poll_interval_s=args.poll_interval_s)
            _emit_json(result, args.evidence_json)
            return 0 if result["status"] == "PASS" else 2
        result = _dry_run_evidence(plan)
        _emit_json(result, args.evidence_json)
        return 0
    except BaseException as exc:
        _emit_json({
            "task": "WO-0014", "status": "FAIL", "dry_run": not args.execute,
            "hardware_attempted": False, "send_attempt_count": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }, args.evidence_json)
        return 2


if __name__ == "__main__":
    _exit_status = main()
    if _QUARANTINED_CHANNELS:
        print("WO-0014 failure left MM2S active; buffer retained. Do not close this "
              "process until an external idle/reset check succeeds.", file=sys.stderr)
        while _QUARANTINED_CHANNELS:
            try:
                input("After you have independently stopped/reset the board, press Enter "
                      "to recheck MM2S idle. The script will not reset or retry: ")
            except (EOFError, KeyboardInterrupt):
                # Keep the backing allocation alive if the shell has no stdin or the
                # operator interrupts. The only release condition remains MM2S idle.
                time.sleep(1.0)
                continue
            for quarantined_buffer, quarantined_dma in list(_QUARANTINED_CHANNELS):
                try:
                    status = int(quarantined_dma.read(DMA_MM2S_DMASR)) & 0xFFFFFFFF
                except Exception:
                    status = 0
                if status & DMA_SR_IDLE:
                    try:
                        _release_buffer(quarantined_buffer)
                    except Exception as release_error:
                        print(f"MM2S is idle, but buffer release failed; buffer remains "
                              f"retained: {release_error}", file=sys.stderr)
                        continue
                    _QUARANTINED_CHANNELS.remove((quarantined_buffer, quarantined_dma))
                    _QUARANTINED_BUFFERS.remove(quarantined_buffer)
                    print(f"MM2S is idle (DMASR=0x{status:08x}); retained buffer released.",
                          file=sys.stderr)
                else:
                    print(f"MM2S remains active (DMASR=0x{status:08x}); buffer retained.",
                          file=sys.stderr)
    sys.exit(_exit_status)
