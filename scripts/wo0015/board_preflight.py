"""Strict, offline-first artifact and admission checks for WO-0015.

This module uses only the Python standard library. It never imports PYNQ and
does not open MMIO. The board runner calls the same validators before its
explicitly authorized, one-shot hardware path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


WIDTH = 320
HEIGHT = 240
PIXEL_COUNT = WIDTH * HEIGHT
RGB_PAYLOAD_BYTES = PIXEL_COUNT * 3
TX_BYTES = 230432
RX_BYTES = PIXEL_COUNT
TX_BEATS = TX_BYTES // 4
RX_BEATS = RX_BYTES // 4
PACKET_MAGIC = 0x574F3134
TRAILER_MAGIC = 0x454E4434
TRAILER_END = 0xC0DEC014
PACKER_SIGNATURE = 0x574F3135
EXPECTED_PART = "xc7z020clg400-1"
AUTHORIZED_BOOT_ID = "cb087bbb-932d-4a6e-9600-98dd1c7e5f8d"
BOARD_STAGES = ("overlay-only", "dma-single", "dma-three")
RUN_AUTHORIZATION = "dma-three"
DMA_BASE = 0x40400000
CHECKER_BASE = 0x43C20000
EXPECTED_DDR_RANGE = (0, 0x20000000)
PAGE_BYTES = 4096
MIN_CMA_KIB = ((TX_BYTES + PAGE_BYTES - 1) // PAGE_BYTES +
               (RX_BYTES + PAGE_BYTES - 1) // PAGE_BYTES) * PAGE_BYTES // 1024
DEFAULT_GUARD_DIR = Path("/var/lib/wo0015-s2mm")

_MANIFEST_KEYS = {
    "schema", "work_order", "stage", "build_id", "boot_id", "part",
    "clock_mhz", "bit_path", "bit_sha256", "hwh_path", "hwh_sha256",
    "dma_base", "checker_base", "contract", "executor_sha256",
    "ready_for_board", "frames",
}
_CONTRACT_VALUES = {
    "dma_mode": "simple",
    "include_mm2s": True,
    "include_s2mm": True,
    "include_sg": False,
    "micro_dma": False,
    "dma_length_width_bits": 18,
    "dma_addr_width_bits": 32,
    "mm2s_memory_width_bits": 64,
    "mm2s_stream_width_bits": 32,
    "s2mm_memory_width_bits": 32,
    "s2mm_stream_width_bits": 32,
    "mm2s_dre": False,
    "s2mm_dre": False,
    "hp0_enabled": True,
    "hp1_enabled": True,
    "hp0_data_width_bits": 64,
    "hp1_data_width_bits": 32,
    "hp0_range": [0, 0x20000000],
    "hp1_range": [0, 0x20000000],
    "checker_signature": PACKET_MAGIC,
    "packer_signature": PACKER_SIGNATURE,
}


class PreflightError(RuntimeError):
    """A reviewed WO-0015 artifact or execution gate does not match."""


@dataclass(frozen=True)
class FrameArtifact:
    frame_id: int
    packet_path: Path
    packet: bytes
    packet_sha256: str
    binary_path: Path
    binary: bytes
    binary_sha256: str
    packet_checksum: int
    binary_checksum: int


@dataclass(frozen=True)
class ValidatedRun:
    manifest_path: Path
    manifest_sha256: str
    manifest: Mapping[str, Any]
    bit_path: Path
    bit_sha256: str
    hwh_path: Path
    hwh_sha256: str
    boot_id: str
    build_id: str
    frames: tuple[FrameArtifact, ...]
    hp0_range: tuple[int, int]
    hp1_range: tuple[int, int]
    ready_for_board: bool

    @property
    def dma_base(self) -> int:
        return DMA_BASE

    @property
    def checker_base(self) -> int:
        return CHECKER_BASE


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise PreflightError(f"{field} must be an integer")
    try:
        return int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise PreflightError(f"{field} is not an integer: {value!r}") from exc


def _require_digest(value: Any, field: str) -> str:
    if (not isinstance(value, str) or len(value) != 64 or
            any(ch not in "0123456789abcdef" for ch in value.lower())):
        raise PreflightError(f"{field} must be a 64-character SHA-256 hex digest")
    return value.lower()


def _resolve_file(base_dir: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise PreflightError(f"{field} must be a non-empty path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve()
    if not path.is_file():
        raise PreflightError(f"{field} file does not exist: {path}")
    return path


def _local_tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _find_module(root: ET.Element, instance: str) -> ET.Element:
    found = [node for node in root.iter()
             if _local_tag(node) == "MODULE" and node.attrib.get("INSTANCE") == instance]
    if len(found) != 1:
        raise PreflightError(f"HWH must contain one MODULE INSTANCE={instance!r}; found {len(found)}")
    return found[0]


def _module_parameters(module: ET.Element, instance: str) -> dict[str, str]:
    parameters: dict[str, str] = {}
    # HWH repeats generic names such as ADDR_WIDTH and DATA_WIDTH under each
    # BUSINTERFACE. Those describe individual ports, not IP CONFIG values.
    # Read only this module's PARAMETERS section, which is also where Vivado
    # emits the axi_dma_0 C_* configuration used by this gate.
    containers = [node for node in module if _local_tag(node) == "PARAMETERS"]
    if len(containers) != 1:
        raise PreflightError(f"HWH {instance} must contain exactly one PARAMETERS section")
    for node in containers[0].iter():
        if _local_tag(node) != "PARAMETER" or "NAME" not in node.attrib or "VALUE" not in node.attrib:
            continue
        key = node.attrib["NAME"].upper()
        value = node.attrib["VALUE"].strip()
        if key in parameters and parameters[key].lower() != value.lower():
            raise PreflightError(f"HWH {instance}.{key} has conflicting values")
        parameters[key] = value
    return parameters


def _require_parameter(parameters: Mapping[str, str], instance: str,
                       name: str, expected: str) -> None:
    actual = parameters.get(name.upper())
    if actual is None or actual.lower() != expected.lower():
        raise PreflightError(f"HWH {instance}.{name} is {actual!r}; expected {expected!r}")


def _bus_name(module: ET.Element, instance: str, interface: str) -> str:
    found = [node for node in module.iter()
             if _local_tag(node) == "BUSINTERFACE" and node.attrib.get("NAME", "").upper() == interface.upper()]
    if len(found) != 1 or not found[0].attrib.get("BUSNAME"):
        raise PreflightError(f"HWH must expose one {instance}.{interface} bus interface")
    return found[0].attrib["BUSNAME"]


def _bus_name_any(module: ET.Element, instance: str, interfaces: tuple[str, ...]) -> str:
    names = {name.upper() for name in interfaces}
    found = [node for node in module.iter()
             if _local_tag(node) == "BUSINTERFACE" and node.attrib.get("NAME", "").upper() in names]
    if len(found) != 1 or not found[0].attrib.get("BUSNAME"):
        raise PreflightError(
            f"HWH must expose exactly one of {instance} interfaces {interfaces}; found {len(found)}"
        )
    return found[0].attrib["BUSNAME"]


def _require_bus_width(module: ET.Element, instance: str, interface: str, expected: str) -> None:
    found = [node for node in module.iter()
             if _local_tag(node) == "BUSINTERFACE" and
             node.attrib.get("NAME", "").upper() == interface.upper()]
    if len(found) != 1 or found[0].attrib.get("DATAWIDTH", "").strip() != expected:
        actual = found[0].attrib.get("DATAWIDTH") if len(found) == 1 else None
        raise PreflightError(
            f"HWH {instance}.{interface} DATAWIDTH is {actual!r}; expected {expected}"
        )


def _require_memrange(root: ET.Element, instance: str, base: int,
                      high_exclusive: int | None = None, *,
                      slave_interface: str | None = None) -> None:
    found = [node for node in root.iter()
             if _local_tag(node) == "MEMRANGE"
             and node.attrib.get("INSTANCE") == instance
             and (slave_interface is None or
                  node.attrib.get("SLAVEBUSINTERFACE", "").upper() == slave_interface.upper())]
    if len(found) != 1:
        suffix = f" for {slave_interface}" if slave_interface else ""
        raise PreflightError(f"HWH must contain one MEMRANGE for {instance}{suffix}; found {len(found)}")
    entry = found[0]
    actual_base = _parse_int(entry.attrib.get("BASEVALUE"), f"HWH {instance} BASEVALUE")
    if actual_base != base:
        raise PreflightError(f"HWH base for {instance} is 0x{actual_base:x}; expected 0x{base:x}")
    if high_exclusive is not None:
        actual_high = _parse_int(entry.attrib.get("HIGHVALUE"), f"HWH {instance} HIGHVALUE") + 1
        if actual_high != high_exclusive:
            raise PreflightError(f"HWH range for {instance} ends at 0x{actual_high:x}; "
                                 f"expected 0x{high_exclusive:x}")


def validate_hwh(path: str | Path) -> None:
    """Check the HWH DMA widths, fixed register maps, clocks and HP routes."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise PreflightError(f"cannot parse HWH XML {path}: {exc}") from exc

    dma_module = _find_module(root, "axi_dma_0")
    dma = _module_parameters(dma_module, "axi_dma_0")
    for name, value in {
        "C_INCLUDE_MM2S": "1",
        "C_INCLUDE_S2MM": "1",
        "C_INCLUDE_SG": "0",
        "C_MICRO_DMA": "0",
        "C_SG_LENGTH_WIDTH": "18",
        "C_M_AXI_MM2S_ADDR_WIDTH": "32",
        "C_M_AXI_S2MM_ADDR_WIDTH": "32",
        "C_M_AXI_MM2S_DATA_WIDTH": "64",
        "C_M_AXI_S2MM_DATA_WIDTH": "32",
        "C_M_AXIS_MM2S_TDATA_WIDTH": "32",
        "C_S_AXIS_S2MM_TDATA_WIDTH": "32",
        "C_INCLUDE_MM2S_DRE": "0",
        "C_INCLUDE_S2MM_DRE": "0",
    }.items():
        _require_parameter(dma, "axi_dma_0", name, value)

    ps_module = _find_module(root, "ps7")
    ps = _module_parameters(ps_module, "ps7")
    for name, value in {
        "PCW_FPGA0_PERIPHERAL_FREQMHZ": "50",
        "PCW_USE_S_AXI_HP0": "1",
        "PCW_S_AXI_HP0_DATA_WIDTH": "64",
        "PCW_USE_S_AXI_HP1": "1",
        "PCW_S_AXI_HP1_DATA_WIDTH": "32",
    }.items():
        _require_parameter(ps, "ps7", name, value)

    hp0 = _find_module(root, "hp0_protocol")
    hp1 = _find_module(root, "hp1_fabric")
    hp1_params = _module_parameters(hp1, "hp1_fabric")
    _require_parameter(hp1_params, "hp1_fabric", "NUM_SI", "1")
    _require_parameter(hp1_params, "hp1_fabric", "NUM_MI", "1")
    pipeline = _find_module(root, "image_pipeline")
    for module, instance, interface in (
            (hp1, "hp1_fabric", "S00_AXI"),
            (hp1, "hp1_fabric", "M00_AXI"),
            (ps_module, "ps7", "S_AXI_HP1")):
        _require_bus_width(module, instance, interface, "32")
    routes = (
        (_bus_name(dma_module, "axi_dma_0", "M_AXI_MM2S"),
         _bus_name(hp0, "hp0_protocol", "S_AXI"), "DMA MM2S to HP0 converter"),
        (_bus_name(hp0, "hp0_protocol", "M_AXI"),
         _bus_name(ps_module, "ps7", "S_AXI_HP0"), "HP0 converter to PS"),
        (_bus_name(dma_module, "axi_dma_0", "M_AXI_S2MM"),
         _bus_name(hp1, "hp1_fabric", "S00_AXI"), "DMA S2MM to HP1 interconnect"),
        (_bus_name(hp1, "hp1_fabric", "M00_AXI"),
         _bus_name(ps_module, "ps7", "S_AXI_HP1"), "HP1 interconnect to PS"),
        (_bus_name_any(pipeline, "image_pipeline", ("m_axis", "M_AXIS_S2MM")),
         _bus_name(dma_module, "axi_dma_0", "S_AXIS_S2MM"), "Packer to DMA S2MM stream"),
    )
    for left, right, label in routes:
        if left != right:
            raise PreflightError(f"HWH {label} buses differ: {left!r} vs {right!r}")

    _require_memrange(root, "axi_dma_0", DMA_BASE)
    _require_memrange(root, "image_pipeline", CHECKER_BASE)
    _require_memrange(root, "ps7", *EXPECTED_DDR_RANGE,
                      slave_interface="S_AXI_HP0")
    _require_memrange(root, "ps7", *EXPECTED_DDR_RANGE,
                      slave_interface="S_AXI_HP1")


def _expected_binary(packet: bytes) -> bytes:
    payload = packet[16:16 + RGB_PAYLOAD_BYTES]
    binary = bytearray(PIXEL_COUNT)
    for pixel in range(PIXEL_COUNT):
        offset = pixel * 3
        red, green, blue = payload[offset:offset + 3]
        gray = (77 * red + 150 * green + 29 * blue) // 256
        binary[pixel] = 0xFF if gray >= 128 else 0x00
    return bytes(binary)


def _validate_packet(packet: bytes, frame_id: int) -> int:
    if len(packet) != TX_BYTES or len(packet) % 4:
        raise PreflightError(f"frame {frame_id} packet must be exactly {TX_BYTES} bytes")
    magic, geometry, payload_length, packet_frame_id = struct.unpack_from("<IIII", packet, 0)
    if magic != PACKET_MAGIC:
        raise PreflightError(f"frame {frame_id} packet header magic is invalid")
    if geometry != (HEIGHT << 16) | WIDTH:
        raise PreflightError(f"frame {frame_id} packet geometry is not 320x240")
    if payload_length != RGB_PAYLOAD_BYTES:
        raise PreflightError(f"frame {frame_id} packet RGB payload length is invalid")
    if packet_frame_id != frame_id:
        raise PreflightError(f"frame {frame_id} packet header ID does not match manifest")
    trailer_magic, trailer_id, payload_sum, end_marker = struct.unpack_from(
        "<IIII", packet, 16 + RGB_PAYLOAD_BYTES)
    payload = packet[16:16 + RGB_PAYLOAD_BYTES]
    if trailer_magic != TRAILER_MAGIC or trailer_id != frame_id or end_marker != TRAILER_END:
        raise PreflightError(f"frame {frame_id} packet trailer is invalid")
    if payload_sum != sum(payload) & 0xFFFFFFFF:
        raise PreflightError(f"frame {frame_id} packet RGB checksum is invalid")
    return sum(packet) & 0xFFFFFFFF


def _validate_contract(manifest: Mapping[str, Any]) -> tuple[tuple[int, int], tuple[int, int]]:
    contract = manifest.get("contract")
    if not isinstance(contract, dict):
        raise PreflightError("manifest contract must be an object")
    if set(contract) != set(_CONTRACT_VALUES):
        missing = sorted(set(_CONTRACT_VALUES) - set(contract))
        extra = sorted(set(contract) - set(_CONTRACT_VALUES))
        raise PreflightError(f"manifest contract fields differ (missing={missing}, extra={extra})")
    for name, expected in _CONTRACT_VALUES.items():
        actual = contract[name]
        if type(expected) is bool:
            matches = type(actual) is bool and actual is expected
        elif isinstance(expected, list):
            matches = actual == expected
        elif type(expected) is int:
            matches = type(actual) is int and actual == expected
        else:
            matches = actual == expected
        if not matches:
            raise PreflightError(f"manifest contract {name} is {actual!r}; expected {expected!r}")
    return tuple(contract["hp0_range"]), tuple(contract["hp1_range"])


def validate_manifest(path: str | Path,
                      expected_manifest_sha256: str | None = None) -> ValidatedRun:
    """Validate one or three fixed RGB→Binary8 frames and all bound artifacts."""
    manifest_path = Path(path).expanduser().resolve()
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PreflightError(f"cannot read WO-0015 manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise PreflightError("WO-0015 manifest must be a JSON object")
    manifest_sha = sha256_bytes(raw)
    if expected_manifest_sha256 is not None:
        wanted = _require_digest(expected_manifest_sha256, "expected manifest SHA-256")
        if manifest_sha != wanted:
            raise PreflightError("manifest SHA-256 does not match the reviewed digest")
    if set(manifest) != _MANIFEST_KEYS:
        missing = sorted(_MANIFEST_KEYS - set(manifest))
        extra = sorted(set(manifest) - _MANIFEST_KEYS)
        raise PreflightError(f"manifest fields differ (missing={missing}, extra={extra})")
    if type(manifest["schema"]) is not int or manifest["schema"] != 1:
        raise PreflightError("manifest schema must be integer 1")
    if manifest["work_order"] != "WO-0015" or manifest["stage"] != "S2MM_BINARY8_FRAME_RETURN":
        raise PreflightError("manifest is not the WO-0015 S2MM Binary8 frame-return stage")
    if not isinstance(manifest["build_id"], str) or not manifest["build_id"].strip():
        raise PreflightError("manifest build_id must be non-empty")
    if manifest["boot_id"] != AUTHORIZED_BOOT_ID:
        raise PreflightError("manifest boot_id differs from the approved boot")
    if manifest["part"] != EXPECTED_PART or type(manifest["clock_mhz"]) is not int \
            or manifest["clock_mhz"] != 50:
        raise PreflightError("manifest must identify the PYNQ-Z2 part at 50 MHz")
    if _parse_int(manifest["dma_base"], "dma_base") != DMA_BASE:
        raise PreflightError("manifest DMA base differs from 0x40400000")
    if _parse_int(manifest["checker_base"], "checker_base") != CHECKER_BASE:
        raise PreflightError("manifest checker base differs from 0x43c20000")
    hp0_range, hp1_range = _validate_contract(manifest)
    for label, value in (("HP0", hp0_range), ("HP1", hp1_range)):
        if len(value) != 2 or tuple(value) != EXPECTED_DDR_RANGE:
            raise PreflightError(f"manifest {label} DDR range must be [0, 0x20000000)")

    base_dir = manifest_path.parent
    bit_path = _resolve_file(base_dir, manifest["bit_path"], "bit_path")
    hwh_path = _resolve_file(base_dir, manifest["hwh_path"], "hwh_path")
    if bit_path.suffix.lower() != ".bit" or hwh_path.suffix.lower() != ".hwh":
        raise PreflightError("manifest bit_path/hwh_path must point to .bit and .hwh files")
    if bit_path.stem != hwh_path.stem:
        raise PreflightError("BIT and HWH must have the same basename")
    bit_sha = _require_digest(manifest["bit_sha256"], "bit_sha256")
    hwh_sha = _require_digest(manifest["hwh_sha256"], "hwh_sha256")
    if sha256_file(bit_path) != bit_sha:
        raise PreflightError("BIT file SHA-256 differs from the manifest")
    if sha256_file(hwh_path) != hwh_sha:
        raise PreflightError("HWH file SHA-256 differs from the manifest")
    validate_hwh(hwh_path)

    sources = manifest["executor_sha256"]
    if not isinstance(sources, dict) or set(sources) != {"board_once.py", "board_preflight.py"}:
        raise PreflightError("executor_sha256 must bind board_once.py and board_preflight.py")
    code_dir = Path(__file__).resolve().parent
    for name, digest in sources.items():
        expected = _require_digest(digest, f"executor_sha256.{name}")
        source_path = code_dir / name
        if not source_path.is_file() or sha256_file(source_path) != expected:
            raise PreflightError(f"executor source SHA-256 differs for {name}")

    if type(manifest["ready_for_board"]) is not bool:
        raise PreflightError("ready_for_board must be a boolean")
    raw_frames = manifest["frames"]
    if not isinstance(raw_frames, list) or len(raw_frames) not in (1, 3):
        raise PreflightError("manifest must declare exactly one or three frames")
    frames: list[FrameArtifact] = []
    previous_id = -1
    seen_paths: set[Path] = set()
    for index, item in enumerate(raw_frames):
        if not isinstance(item, dict) or set(item) != {
            "frame_id", "packet_path", "packet_sha256", "binary_path", "binary_sha256"
        }:
            raise PreflightError(f"frame entry {index} fields differ from the reviewed schema")
        frame_id = item["frame_id"]
        if type(frame_id) is not int or frame_id < 0 or frame_id > 0xFFFFFFFF:
            raise PreflightError(f"frame entry {index} frame_id must be uint32")
        if frame_id <= previous_id:
            raise PreflightError("frame IDs must be strictly increasing")
        previous_id = frame_id
        packet_path = _resolve_file(base_dir, item["packet_path"], f"frames[{index}].packet_path")
        binary_path = _resolve_file(base_dir, item["binary_path"], f"frames[{index}].binary_path")
        if packet_path in seen_paths or binary_path in seen_paths or packet_path == binary_path:
            raise PreflightError("frame artifact paths must be unique")
        seen_paths.update((packet_path, binary_path))
        packet_sha = _require_digest(item["packet_sha256"], f"frames[{index}].packet_sha256")
        binary_sha = _require_digest(item["binary_sha256"], f"frames[{index}].binary_sha256")
        packet = packet_path.read_bytes()
        binary = binary_path.read_bytes()
        if sha256_bytes(packet) != packet_sha:
            raise PreflightError(f"frame {frame_id} packet SHA-256 differs from the manifest")
        if sha256_bytes(binary) != binary_sha:
            raise PreflightError(f"frame {frame_id} Binary8 SHA-256 differs from the manifest")
        packet_checksum = _validate_packet(packet, frame_id)
        if len(binary) != RX_BYTES:
            raise PreflightError(f"frame {frame_id} Binary8 golden must be exactly {RX_BYTES} bytes")
        expected_binary = _expected_binary(packet)
        if binary != expected_binary:
            raise PreflightError(f"frame {frame_id} independent Binary8 golden differs byte-for-byte")
        if any(value not in (0, 0xFF) for value in binary):
            raise PreflightError(f"frame {frame_id} Binary8 golden contains values other than 0x00/0xFF")
        frames.append(FrameArtifact(
            frame_id=frame_id,
            packet_path=packet_path,
            packet=packet,
            packet_sha256=packet_sha,
            binary_path=binary_path,
            binary=binary,
            binary_sha256=binary_sha,
            packet_checksum=packet_checksum,
            binary_checksum=sum(binary) & 0xFFFFFFFF,
        ))

    return ValidatedRun(
        manifest_path=manifest_path,
        manifest_sha256=manifest_sha,
        manifest=manifest,
        bit_path=bit_path,
        bit_sha256=bit_sha,
        hwh_path=hwh_path,
        hwh_sha256=hwh_sha,
        boot_id=manifest["boot_id"],
        build_id=manifest["build_id"],
        frames=tuple(frames),
        hp0_range=hp0_range,
        hp1_range=hp1_range,
        ready_for_board=manifest["ready_for_board"],
    )


def require_board_authorization(*, execute: bool, stage: str,
                                authorize_stage: str | None, approve_once: bool,
                                manifest_sha256: str | None,
                                expected_manifest_sha256: str | None,
                                boot_id: str, ready_for_board: bool = True) -> None:
    """Fail closed unless the exact hardware stage is explicitly authorized once."""
    if execute is not True:
        raise PreflightError("board access requires --execute")
    if stage not in BOARD_STAGES:
        raise PreflightError(f"unknown WO-0015 board stage {stage!r}")
    if authorize_stage != stage:
        raise PreflightError(f"board access requires matching --authorize-stage {stage!r}")
    if approve_once is not True:
        raise PreflightError("board access requires --approve-once")
    wanted = _require_digest(expected_manifest_sha256, "reviewed manifest SHA-256")
    actual = _require_digest(manifest_sha256, "manifest SHA-256")
    if actual != wanted:
        raise PreflightError("manifest SHA-256 does not match the separately reviewed digest")
    if boot_id != AUTHORIZED_BOOT_ID:
        raise PreflightError("live boot_id differs from the one approved for WO-0015")
    if ready_for_board is not True:
        raise PreflightError("manifest is not marked ready_for_board")


def read_boot_id(path: str | Path = "/proc/sys/kernel/random/boot_id") -> str:
    try:
        value = Path(path).read_text(encoding="ascii").strip()
    except OSError as exc:
        raise PreflightError(f"cannot read live boot_id: {exc}") from exc
    if not value:
        raise PreflightError("live boot_id is empty")
    return value


def read_cma_meminfo(path: str | Path = "/proc/meminfo") -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        lines = Path(path).read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise PreflightError(f"cannot read /proc/meminfo: {exc}") from exc
    for line in lines:
        if line.startswith(("CmaTotal:", "CmaFree:")):
            key, value, unit, *_ = line.split()
            if unit != "kB":
                raise PreflightError(f"unexpected {key} unit in /proc/meminfo")
            values[key[:-1]] = int(value)
    if set(values) != {"CmaTotal", "CmaFree"}:
        raise PreflightError("/proc/meminfo lacks CmaTotal or CmaFree")
    if values["CmaFree"] > values["CmaTotal"]:
        raise PreflightError("CmaFree exceeds CmaTotal")
    return values


def audit_process_ownership(proc_root: str | Path = "/proc", *, current_pid: int | None = None,
                            require_root: bool = True) -> dict[str, Any]:
    """Reject another process with FPGA device mappings/FDs or known owners."""
    if require_root and (not hasattr(os, "geteuid") or os.geteuid() != 0):
        raise PreflightError("root is required for a complete /proc device ownership scan")
    proc = Path(proc_root)
    if not proc.is_dir():
        raise PreflightError(f"process table is unavailable: {proc}")
    self_pid = os.getpid() if current_pid is None else current_pid
    device_names = ("/dev/mem", "/dev/xdevcfg", "/dev/fpga0", "/dev/fpga", "/dev/uio")
    command_names = ("ipykernel_launcher", "fpgautil", "openocd", "xsdb", "xvcserver",
                     "remote-send-once", "dma_replay_verify", "diagnostic50")
    findings: list[dict[str, Any]] = []
    denied: list[int] = []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == self_pid:
            continue
        pid = int(entry.name)
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="replace")
            maps = (entry / "maps").read_text(encoding="utf-8", errors="replace")
            reasons: list[str] = []
            if any(name in command for name in command_names):
                reasons.append("known_fpga_owner_command")
            if any(device in maps for device in device_names):
                reasons.append("fpga_device_mapping")
            fd_dir = entry / "fd"
            for fd in fd_dir.iterdir():
                try:
                    target = os.readlink(fd)
                except FileNotFoundError:
                    continue
                if (any(target == device or target.startswith(device + " ")
                        for device in device_names) or
                        target == "/dev/uio" or target.startswith("/dev/uio")):
                    reasons.append("fpga_device_fd")
                    break
            if reasons:
                findings.append({"pid": pid, "reasons": reasons})
        except FileNotFoundError:
            # Processes naturally disappear while /proc is being enumerated.
            continue
        except PermissionError:
            denied.append(pid)
    if findings or denied:
        raise PreflightError("FPGA device ownership scan failed: " + json.dumps(
            {"findings": findings, "denied": denied}, sort_keys=True))
    return {"findings": findings, "denied": denied, "scanned_proc": str(proc)}


def read_fpga_manager(state_path: str | Path = "/sys/class/fpga_manager/fpga0/state",
                      flags_path: str | Path = "/sys/class/fpga_manager/fpga0/flags") -> dict[str, str]:
    try:
        state = Path(state_path).read_text(encoding="ascii").strip()
        flags = Path(flags_path).read_text(encoding="ascii").strip()
    except OSError as exc:
        raise PreflightError(f"cannot read FPGA manager state/flags: {exc}") from exc
    if state != "operating" or flags != "0":
        raise PreflightError(f"FPGA manager must be operating with flags=0 (state={state!r}, flags={flags!r})")
    return {"state": state, "flags": flags}


def inspect_pynq_global_state(pynq_module_file: str | Path, *,
                              require_empty_shutdown: bool,
                              require_loaded_bit: bool = False,
                              expected_bit_path: str | Path | None = None,
                              expected_bit_sha256: str | None = None) -> dict[str, Any]:
    """Audit the PYNQ shutdown list before load and current BIT identity after it."""
    state_path = Path(pynq_module_file).resolve().parent / "pl_server" / "global_pl_state_.json"
    if state_path.is_symlink():
        raise PreflightError("PYNQ global state must not be a symlink")
    try:
        raw = state_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if require_loaded_bit:
            raise PreflightError("PYNQ global state is absent after BIT load")
        # A cold boot with no global state has no stale shutdown list to invoke.
        return {"pl_state_status": "COLD_BOOT_NO_GLOBAL_STATE",
                "global_state_path": str(state_path), "shutdown_ips": {},
                "loaded_bit_path": None, "loaded_bit_sha1": None,
                "loaded_bit_sha256": None}
    except (OSError, UnicodeError) as exc:
        raise PreflightError(f"cannot read PYNQ global state: {exc}") from exc
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PreflightError("PYNQ global state is invalid JSON") from exc
    if not isinstance(state, dict) or not isinstance(state.get("shutdown_ips"), dict):
        raise PreflightError("PYNQ shutdown_ips must be an explicit dictionary")
    shutdown_ips = state["shutdown_ips"]
    if require_empty_shutdown and shutdown_ips:
        raise PreflightError("Existing shutdown_ips could cause implicit MMIO during Overlay download")
    ip_dict = state.get("ip_dict", {})
    if not isinstance(ip_dict, dict):
        raise PreflightError("PYNQ global ip_dict must be a dictionary")
    if require_empty_shutdown and any(
            "shutdown" in (str(name) + str(info)).lower() for name, info in ip_dict.items()):
        raise PreflightError("PYNQ IP metadata contains a shutdown manager")
    bit_path_value = state.get("bitfile_name")
    bit_sha1 = state.get("bitfile_hash")
    resolved_bit = None
    bit_sha256 = None
    if bit_path_value:
        resolved_bit = Path(str(bit_path_value)).resolve()
        if resolved_bit.is_file():
            bit_sha256 = sha256_file(resolved_bit)
    if require_loaded_bit:
        if not bit_path_value or not bit_sha1 or expected_bit_path is None or expected_bit_sha256 is None:
            raise PreflightError("PYNQ global state does not identify the loaded WO-0015 BIT")
        expected_path = Path(expected_bit_path).resolve()
        if resolved_bit != expected_path:
            raise PreflightError("PYNQ global state loaded BIT path differs from the reviewed artifact")
        if bit_sha256 != expected_bit_sha256:
            raise PreflightError("PYNQ global state loaded BIT SHA-256 differs from the reviewed artifact")
        import hashlib
        sha1 = hashlib.sha1(expected_path.read_bytes()).hexdigest()
        if str(bit_sha1).lower() != sha1:
            raise PreflightError("PYNQ global state loaded BIT SHA-1 does not match the selected file")
    return {"pl_state_status": "GLOBAL_STATE_PRESENT",
            "global_state_path": str(state_path),
            "shutdown_ips": shutdown_ips,
            "shutdown_ips_empty": not shutdown_ips,
            "loaded_bit_path": str(resolved_bit) if resolved_bit else None,
            "loaded_bit_sha1": bit_sha1,
            "loaded_bit_sha256": bit_sha256}


def read_kernel_log() -> list[str]:
    try:
        result = subprocess.run(["dmesg", "--color=never"], capture_output=True,
                                text=True, timeout=3, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreflightError(f"cannot capture dmesg health snapshot: {exc}") from exc
    return result.stdout.splitlines()


def kernel_log_delta(before: list[str], after: list[str], *, anchor_lines: int = 8) -> list[str]:
    """Return appended dmesg lines only when the previous tail is unique/present."""
    if not before or not after:
        raise PreflightError("dmesg continuity lost: empty kernel snapshot")
    anchor = before[-anchor_lines:]
    matches = [index for index in range(len(after) - len(anchor) + 1)
               if after[index:index + len(anchor)] == anchor]
    if len(matches) != 1:
        raise PreflightError("dmesg continuity lost: prior tail missing or ambiguous")
    new_lines = after[matches[0] + len(anchor):]
    bad_words = ("warn", "error", "oops", "panic", "fault", "hung", "timeout")
    bad = [line for line in new_lines if any(word in line.lower() for word in bad_words)]
    if bad:
        raise PreflightError("new kernel health warnings after hardware stage: " + json.dumps(bad))
    return new_lines


def _read_cells(path: Path, default: int) -> int:
    try:
        raw = path.read_bytes()
    except OSError:
        return default
    if len(raw) != 4:
        raise PreflightError(f"device-tree cell property has invalid length: {path}")
    value = int.from_bytes(raw, "big")
    if value not in (1, 2):
        raise PreflightError(f"unsupported device-tree cell count {value}: {path}")
    return value


def read_cma_ranges(root_path: str | Path = "/proc/device-tree/reserved-memory") -> tuple[tuple[int, int], ...]:
    """Read reusable shared-DMA-pool ranges from the live device tree."""
    root = Path(root_path)
    if not root.is_dir():
        raise PreflightError(f"CMA reserved-memory tree is unavailable: {root}")
    address_cells = _read_cells(root / "#address-cells", 1)
    size_cells = _read_cells(root / "#size-cells", 1)
    ranges: list[tuple[int, int]] = []
    for child in root.iterdir():
        reg_path = child / "reg"
        if not child.is_dir() or not reg_path.is_file():
            continue
        compatible_path = child / "compatible"
        compatible = compatible_path.read_bytes().replace(b"\x00", b",").decode("ascii", "ignore") \
            if compatible_path.exists() else ""
        cma_named = "cma" in child.name.lower()
        shared_pool = "shared-dma-pool" in compatible and (child / "reusable").exists()
        if not cma_named and not shared_pool:
            continue
        cells = address_cells + size_cells
        entry_size = cells * 4
        raw = reg_path.read_bytes()
        if not raw or len(raw) % entry_size:
            raise PreflightError(f"CMA device-tree reg has invalid length: {reg_path}")
        for offset in range(0, len(raw), entry_size):
            address = int.from_bytes(raw[offset:offset + address_cells * 4], "big")
            size_offset = offset + address_cells * 4
            size = int.from_bytes(raw[size_offset:size_offset + size_cells * 4], "big")
            if size <= 0:
                raise PreflightError(f"CMA range has zero size: {reg_path}")
            ranges.append((address, address + size))
    if not ranges:
        raise PreflightError("device tree contains no verifiable CMA range")
    return tuple(sorted(ranges))


def validate_runtime_cma(meminfo: Mapping[str, int],
                         cma_ranges: tuple[tuple[int, int], ...],
                         hp_ranges: tuple[tuple[int, int], tuple[int, int]]) -> None:
    if meminfo.get("CmaTotal", 0) < MIN_CMA_KIB:
        raise PreflightError(f"CmaTotal is below the {MIN_CMA_KIB} KiB two-buffer minimum")
    if meminfo.get("CmaFree", 0) < MIN_CMA_KIB:
        raise PreflightError(f"CmaFree is below the {MIN_CMA_KIB} KiB two-buffer minimum")
    if not cma_ranges:
        raise PreflightError("no runtime CMA ranges were supplied")
    for cma_start, cma_end in cma_ranges:
        if cma_start < 0 or cma_end <= cma_start:
            raise PreflightError("runtime CMA range is invalid")
        if not any(start <= cma_start and cma_end <= end for start, end in hp_ranges):
            raise PreflightError("runtime CMA range is outside the reviewed DMA-visible DDR windows")


@dataclass(frozen=True)
class CmaAdmission:
    ranges: tuple[tuple[int, int], ...]
    source: str
    exact: bool
    evidence: Mapping[str, Any]

    def as_evidence(self) -> dict[str, Any]:
        return {"source": self.source, "physical_cma_range_known": self.exact,
                "permitted_buffer_ranges": [list(r) for r in self.ranges],
                "limitation": ("exact CMA metadata; post-allocation checks still required"
                               if self.exact else
                               "kernel CMA accounting and verified DDR safety envelope only; "
                               "not a measured CMA interval or static CMA subregion; authoritative PYNQ physical-address, "
                               "size, overlap and allocation accounting checks remain mandatory"),
                **dict(self.evidence)}


def _checked_cma_intervals(ranges: Any) -> tuple[tuple[int, int], ...]:
    result = tuple(sorted(set(tuple(pair) for pair in ranges)))
    if not result:
        raise PreflightError("no runtime CMA ranges were supplied")
    previous_end = -1
    for start, end in result:
        if (start < 0 or end <= start or start % PAGE_BYTES or end % PAGE_BYTES or
                end > EXPECTED_DDR_RANGE[1] or start < previous_end):
            raise PreflightError("CMA intervals are invalid, overlapping, unaligned or outside DDR")
        previous_end = end
    return result


def _system_ram_intervals(iomem_text: str) -> tuple[tuple[int, int], ...]:
    spans = re.findall(r"^\s*([0-9a-fA-F]+)-([0-9a-fA-F]+)\s*:\s*System RAM\s*$",
                       iomem_text, re.MULTILINE)
    result = tuple(sorted((int(a, 16), int(b, 16) + 1) for a, b in spans))
    if result != (EXPECTED_DDR_RANGE,):
        raise PreflightError("System RAM must establish the exact PYNQ-Z2 512 MiB DDR boundary")
    return result


def _reject_iomem_conflicts(ranges: tuple[tuple[int, int], ...], iomem_text: str) -> None:
    for start, end, label in re.findall(r"^\s*([0-9a-fA-F]+)-([0-9a-fA-F]+)\s*:\s*(.+)$",
                                      iomem_text, re.MULTILINE):
        a, b = int(start, 16), int(end, 16) + 1
        if label.strip() != "System RAM" and any(a < stop and base < b for base, stop in ranges):
            raise PreflightError("admitted range conflicts with a kernel/reserved/peripheral iomem region")


def read_debugfs_cma_ranges(root_path: str | Path = "/sys/kernel/debug/cma", *,
                           page_size: int = PAGE_BYTES) -> tuple[tuple[int, int], ...]:
    """Read only base/count/accounting; never open alloc/free control nodes."""
    root = Path(root_path)
    if not root.is_dir():
        return ()
    if page_size != PAGE_BYTES:
        raise PreflightError("CMA PFN conversion requires the reviewed 4096-byte page size")
    ranges = []
    for area in sorted(root.iterdir()):
        if not area.is_dir():
            continue
        base, count = area / "base_pfn", area / "count"
        if not base.exists() and not count.exists():
            continue
        try:
            pfn = int(base.read_text().strip(), 0)
            pages = int(count.read_text().strip(), 0)
            used_path = area / "used"
            used = int(used_path.read_text().strip(), 0) if used_path.exists() else 0
        except (OSError, ValueError) as exc:
            raise PreflightError(f"invalid CMA debugfs metadata at {area}: {exc}") from exc
        if pfn < 0 or pages <= 0 or used < 0 or used > pages:
            raise PreflightError(f"invalid CMA debugfs page accounting at {area}")
        ranges.append((pfn * page_size, (pfn + pages) * page_size))
    return _checked_cma_intervals(ranges) if ranges else ()


def resolve_cma_sources(*, dt_ranges: Any = (), dmesg_text: str = "",
                        debugfs_ranges: Any = (), meminfo: Mapping[str, int],
                        cmdline: str = "", bootargs: str = "",
                        iomem_text: str) -> CmaAdmission:
    """Resolve exact CMA metadata, or an explicitly conditional RAM candidate."""
    total = meminfo.get("CmaTotal", 0)
    free = meminfo.get("CmaFree", 0)
    if total < MIN_CMA_KIB or free < MIN_CMA_KIB or free > total:
        raise PreflightError("CMA accounting is invalid or below the two-buffer minimum")
    ram = _system_ram_intervals(iomem_text)
    candidates: dict[str, tuple[tuple[int, int], ...]] = {}
    if dt_ranges:
        candidates["device_tree"] = _checked_cma_intervals(dt_ranges)
    log_ranges = []
    units = {"kib": 1024, "mib": 1024**2, "gib": 1024**3}
    for line in dmesg_text.splitlines():
        if not re.search(r"\bcma:\s*Reserved\b", line, re.IGNORECASE):
            continue
        match = re.search(r"\bcma:\s*Reserved\s+(\d+)\s+(KiB|MiB|GiB)\s+at\s+"
                          r"(0x[0-9a-fA-F]+)(?=\s|$)", line, re.IGNORECASE)
        if not match:
            raise PreflightError("malformed kernel CMA reservation log; refusing fallback")
        size = int(match[1]) * units[match[2].lower()]
        start = int(match[3], 16)
        log_ranges.append((start, start + size))
    if log_ranges:
        candidates["dmesg"] = _checked_cma_intervals(log_ranges)
    if debugfs_ranges:
        candidates["debugfs"] = _checked_cma_intervals(debugfs_ranges)
    for source, ranges in candidates.items():
        if sum(end - start for start, end in ranges) != total * 1024:
            raise PreflightError(f"{source} CMA interval size differs from CmaTotal")
        _reject_iomem_conflicts(ranges, iomem_text)
    if candidates:
        if len(set(candidates.values())) != 1:
            raise PreflightError("conflicting precise CMA metadata sources")
        source = next(s for s in ("device_tree", "dmesg", "debugfs") if s in candidates)
        return CmaAdmission(candidates[source], source, True,
                            {"corroborating_sources": list(candidates),
                             "system_ram_ranges": [list(r) for r in ram],
                             "CmaTotal_kib": total, "CmaFree_kib": free})

    # The captain's Two-Tier decision admits the DDR safety envelope from
    # kernel accounting. It does not infer where the CMA pool was placed.
    tokens = set(re.findall(r"(?:^|\s)cma=([^\s\x00]+)", cmdline + " " + bootargs))
    if len(tokens) > 1:
        raise PreflightError("conflicting cma= boot evidence")
    if tokens:
        token = next(iter(tokens))
        match = re.fullmatch(r"(\d+)([KMG])?", token)
        if not match:
            raise PreflightError("unsupported cma= syntax; explicit placement needs precise metadata")
        size = int(match[1]) * {None: 1, "K": 1024, "M": 1024**2, "G": 1024**3}[match[2]]
        if size != total * 1024:
            raise PreflightError("cma= size does not match CmaTotal")
    if total != 131072 or free < 32768:
        raise PreflightError("MEMINFO_IOMEM_VERIFIED requires CmaTotal=131072 KiB and CmaFree>=32768 KiB")
    allowed = ((0x00100000, 0x20000000),)
    return CmaAdmission(allowed, "MEMINFO_IOMEM_VERIFIED", False,
                        {"CmaTotal_kib": total, "CmaFree_kib": free,
                         "system_ram_ranges": [list(r) for r in ram],
                         "requires_authoritative_post_alloc_gate": True})


def read_cma_admission(root_path: str | Path = "/proc/device-tree/reserved-memory", *,
                       meminfo: Mapping[str, int] | None = None,
                       dmesg_text: str | None = None,
                       debugfs_root: str | Path = "/sys/kernel/debug/cma",
                       cmdline_path: str | Path = "/proc/cmdline",
                       bootargs_path: str | Path = "/proc/device-tree/chosen/bootargs",
                       iomem_path: str | Path = "/proc/iomem") -> CmaAdmission:
    dt = ()
    if Path(root_path).is_dir():
        try:
            dt = read_cma_ranges(root_path)
        except PreflightError as exc:
            if str(exc) != "device tree contains no verifiable CMA range":
                raise
    def optional_text(path: str | Path) -> str:
        try:
            return Path(path).read_bytes().replace(b"\x00", b" ").decode("ascii")
        except FileNotFoundError:
            return ""
        except (OSError, UnicodeError) as exc:
            raise PreflightError(f"cannot read CMA evidence {path}: {exc}") from exc
    # dmesg is already mandatory for the stage's independent health audit.
    log = "\n".join(read_kernel_log()) if dmesg_text is None else dmesg_text
    page_size = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else PAGE_BYTES
    debug = read_debugfs_cma_ranges(debugfs_root, page_size=page_size)
    return resolve_cma_sources(dt_ranges=dt, dmesg_text=log, debugfs_ranges=debug,
                               meminfo=read_cma_meminfo() if meminfo is None else meminfo,
                               cmdline=optional_text(cmdline_path), bootargs=optional_text(bootargs_path),
                               iomem_text=optional_text(iomem_path))


def validate_dma_buffer_pair(*, tx_address: int, tx_size: int,
                             rx_address: int, rx_size: int, cma_ranges: Any,
                             hp0_range: tuple[int, int],
                             hp1_range: tuple[int, int]) -> dict[str, Any]:
    """Validate actual allocated bytes/physical spans before any register write."""
    ranges = _checked_cma_intervals(cma_ranges or ())
    for label, address, size, expected_size, alignment, hp in (
            ("MM2S", tx_address, tx_size, TX_BYTES, 8, hp0_range),
            ("S2MM", rx_address, rx_size, RX_BYTES, 4, hp1_range)):
        if size != expected_size:
            raise PreflightError(f"{label} actual allocated length differs from its exact transfer size")
        end = address + size
        if address < 0x00100000 or address % alignment or end > EXPECTED_DDR_RANGE[1]:
            raise PreflightError(f"{label} physical address is below 1 MiB, unaligned or outside 512 MiB DDR")
        if not any(start <= address and end <= stop for start, stop in ranges):
            raise PreflightError(f"{label} CMA buffer falls outside the live CMA range or RAM candidate")
        if address < hp[0] or end > hp[1]:
            raise PreflightError(f"{label} CMA buffer is outside its HP-visible DDR range")
    if tx_address < rx_address + rx_size and rx_address < tx_address + tx_size:
        raise PreflightError("MM2S and S2MM CMA buffer physical ranges overlap")
    return {"status": "POST_ALLOC_PHYSICAL_GATE_PASS",
            "mm2s_physical_range": [tx_address, tx_address + tx_size],
            "s2mm_physical_range": [rx_address, rx_address + rx_size],
            "tx_actual_bytes": tx_size, "rx_actual_bytes": rx_size,
            "permitted_buffer_ranges": [list(r) for r in ranges]}


def claim_once_guard(path: str | Path, *, boot_id: str,
                     manifest_sha256: str, stage: str,
                     clock_mhz: int = 50) -> dict[str, Any]:
    """Atomically consume this WO-0015 stage guard; there is deliberately no reset."""
    if boot_id != AUTHORIZED_BOOT_ID:
        raise PreflightError("refusing to claim WO-0015 guard for an unapproved boot_id")
    manifest_sha = _require_digest(manifest_sha256, "manifest SHA-256")
    if clock_mhz != 50:
        raise PreflightError("WO-0015 board execution is locked to 50 MHz")
    if stage not in BOARD_STAGES:
        raise PreflightError(f"unknown WO-0015 guard stage {stage!r}")
    guard_path = Path(path)
    try:
        guard_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(guard_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise PreflightError("WO-0015 one-shot guard is already consumed") from exc
    except OSError as exc:
        raise PreflightError(f"cannot claim the fixed WO-0015 guard: {exc}") from exc
    receipt = {
        "schema": 1,
        "work_order": "WO-0015",
        "stage": stage,
        "boot_id": boot_id,
        "manifest_sha256": manifest_sha,
        "clock_mhz": clock_mhz,
        "consumed": True,
    }
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(receipt, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            directory_fd = os.open(str(guard_path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # O_EXCL already consumed the guard. Keep it fail-closed even if
            # this filesystem does not support directory fsync.
            pass
    except OSError as exc:
        raise PreflightError(f"WO-0015 guard was claimed but receipt write failed: {exc}") from exc
    return receipt


def _emit(result: Mapping[str, Any], output: str | None) -> None:
    encoded = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if output:
        target = Path(output).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WO-0015 offline artifact and HWH preflight")
    parser.add_argument("--manifest", required=True, help="reviewed WO-0015 run manifest")
    parser.add_argument("--manifest-sha256", help="optional separately reviewed manifest digest")
    parser.add_argument("--evidence-json", help="write the offline preflight result to this path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        plan = validate_manifest(args.manifest, args.manifest_sha256)
    except (OSError, ValueError, PreflightError) as exc:
        _emit({
            "task": "WO-0015",
            "status": "OFFLINE_PREFLIGHT_FAIL",
            "board_accessed": False,
            "pynq_imported": False,
            "error": f"{type(exc).__name__}: {exc}",
        }, args.evidence_json)
        return 2
    _emit({
        "task": "WO-0015",
        "status": "OFFLINE_PREFLIGHT_PASS",
        "board_accessed": False,
        "pynq_imported": False,
        "manifest_sha256": plan.manifest_sha256,
        "build_id": plan.build_id,
        "boot_id_bound": plan.boot_id,
        "clock_mhz": 50,
        "frame_count": len(plan.frames),
        "tx_bytes_each": TX_BYTES,
        "rx_bytes_each": RX_BYTES,
        "bit_sha256": plan.bit_sha256,
        "hwh_sha256": plan.hwh_sha256,
        "ready_for_board": plan.ready_for_board,
    }, args.evidence_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
