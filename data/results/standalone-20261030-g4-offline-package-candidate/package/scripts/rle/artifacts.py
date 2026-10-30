"""Offline integrity and HWH admission for the 640x400 RLE overlay."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "wo0015"))
import board_preflight as base

WIDTH = 640
HEIGHT = 400
MAX_RUNS = 16384
PROJECTION_WORDS = (WIDTH + HEIGHT + 1) // 2
RX_CAPACITY = (16 + MAX_RUNS + PROJECTION_WORDS + 16) * 4

CONTRACT = {
    "protocol": "RLE_PACKET_V1",
    "width": WIDTH,
    "height": HEIGHT,
    "max_runs": MAX_RUNS,
    "tx_bytes": 16 + WIDTH * HEIGHT + 16,
    "rx_capacity_bytes": RX_CAPACITY,
    "clock_mhz": 50,
    "part": "xc7z020clg400-1",
    "dma_base": 0x40400000,
    "control_base": 0x43C20000,
}
_MANIFEST_FIELDS = set(CONTRACT) | {
    "schema", "bit_path", "bit_sha256", "hwh_path", "hwh_sha256",
    "rtl_snapshot_sha256", "build_evidence",
}
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parameters(module, instance: str) -> dict[str, str]:
    return base._module_parameters(module, instance)


def validate_rle_hwh(path: str | Path) -> None:
    """Verify RLE wrapper, PS/DMA topology and the fixed register map."""
    try:
        root = ET.parse(path).getroot()
        dma_module = base._find_module(root, "axi_dma_0")
        ps_module = base._find_module(root, "ps7")
        hp0 = base._find_module(root, "hp0_protocol")
        hp1 = base._find_module(root, "hp1_fabric")
        pipeline = base._find_module(root, "image_pipeline")

        if pipeline.attrib.get("MODTYPE") != "axis_dma_rle_pipeline_bd":
            raise ValueError("HWH image_pipeline is not the RLE pipeline")
        params = _parameters(pipeline, "image_pipeline")
        for name, expected in (("WIDTH", WIDTH), ("HEIGHT", HEIGHT), ("MAX_RUNS", MAX_RUNS)):
            actual = params.get(name)
            if actual is None or base._parse_int(actual, f"image_pipeline.{name}") != expected:
                raise ValueError(f"HWH image_pipeline {name} is {actual!r}; expected {expected}")

        dma = _parameters(dma_module, "axi_dma_0")
        for name, expected in {
            "C_INCLUDE_MM2S": "1", "C_INCLUDE_S2MM": "1", "C_INCLUDE_SG": "0",
            "C_MICRO_DMA": "0", "C_SG_LENGTH_WIDTH": "18",
            "C_M_AXI_MM2S_ADDR_WIDTH": "32", "C_M_AXI_S2MM_ADDR_WIDTH": "32",
            "C_M_AXI_MM2S_DATA_WIDTH": "64", "C_M_AXI_S2MM_DATA_WIDTH": "32",
            "C_M_AXIS_MM2S_TDATA_WIDTH": "32", "C_S_AXIS_S2MM_TDATA_WIDTH": "32",
            "C_INCLUDE_MM2S_DRE": "0", "C_INCLUDE_S2MM_DRE": "0",
        }.items():
            base._require_parameter(dma, "axi_dma_0", name, expected)

        ps = _parameters(ps_module, "ps7")
        for name, expected in {
            "PCW_FPGA0_PERIPHERAL_FREQMHZ": "50", "PCW_USE_S_AXI_HP0": "1",
            "PCW_S_AXI_HP0_DATA_WIDTH": "64", "PCW_USE_S_AXI_HP1": "1",
            "PCW_S_AXI_HP1_DATA_WIDTH": "32",
        }.items():
            base._require_parameter(ps, "ps7", name, expected)
        hp1_params = _parameters(hp1, "hp1_fabric")
        base._require_parameter(hp1_params, "hp1_fabric", "NUM_SI", "1")
        base._require_parameter(hp1_params, "hp1_fabric", "NUM_MI", "1")

        for module, instance, interface, width in (
            (dma_module, "axi_dma_0", "M_AXI_MM2S", "64"),
            (dma_module, "axi_dma_0", "M_AXI_S2MM", "32"),
            (hp0, "hp0_protocol", "S_AXI", "64"),
            (hp0, "hp0_protocol", "M_AXI", "64"),
            (hp1, "hp1_fabric", "S00_AXI", "32"),
            (hp1, "hp1_fabric", "M00_AXI", "32"),
            (ps_module, "ps7", "S_AXI_HP0", "64"),
            (ps_module, "ps7", "S_AXI_HP1", "32"),
        ):
            base._require_bus_width(module, instance, interface, width)
        # AXI-Stream uses TDATA_NUM_BYTES, whereas AXI memory interfaces
        # advertise DATAWIDTH. Real Vivado HWH does not add DATAWIDTH to AXIS.
        for module, instance, interface in (
            (dma_module, "axi_dma_0", "S_AXIS_S2MM"),
            (pipeline, "image_pipeline", "m_axis"),
        ):
            nodes = [b for b in module.iter("BUSINTERFACE") if b.get("NAME") == interface]
            if len(nodes) != 1:
                raise ValueError(f"HWH {instance}.{interface} AXIS interface missing")
            parameters = {p.get("NAME"): p.get("VALUE") for p in nodes[0].findall("PARAMETER")}
            for key, expected in (("TDATA_NUM_BYTES", "4"), ("HAS_TKEEP", "1"),
                                  ("HAS_TLAST", "1"), ("HAS_TREADY", "1")):
                if parameters.get(key) != expected:
                    raise ValueError(f"HWH {instance}.{interface} AXIS {key} mismatch")

        routes = (
            (base._bus_name(dma_module, "axi_dma_0", "M_AXI_MM2S"),
             base._bus_name(hp0, "hp0_protocol", "S_AXI"), "MM2S to HP0"),
            (base._bus_name(hp0, "hp0_protocol", "M_AXI"),
             base._bus_name(ps_module, "ps7", "S_AXI_HP0"), "HP0 to PS"),
            (base._bus_name(dma_module, "axi_dma_0", "M_AXI_S2MM"),
             base._bus_name(hp1, "hp1_fabric", "S00_AXI"), "S2MM to HP1"),
            (base._bus_name(hp1, "hp1_fabric", "M00_AXI"),
             base._bus_name(ps_module, "ps7", "S_AXI_HP1"), "HP1 to PS"),
            (base._bus_name(pipeline, "image_pipeline", "m_axis"),
             base._bus_name(dma_module, "axi_dma_0", "S_AXIS_S2MM"), "RLE stream to S2MM"),
        )
        for left, right, label in routes:
            if left != right:
                raise ValueError(f"HWH {label} route differs: {left!r} vs {right!r}")

        base._require_memrange(root, "axi_dma_0", CONTRACT["dma_base"])
        base._require_memrange(root, "image_pipeline", CONTRACT["control_base"])
        for port in ("S_AXI_HP0", "S_AXI_HP1"):
            base._require_memrange(root, "ps7", *base.EXPECTED_DDR_RANGE,
                                   slave_interface=port)
    except (OSError, ET.ParseError, base.PreflightError, KeyError) as exc:
        raise ValueError(f"RLE HWH admission failed: {exc}") from exc


def build_manifest(output: str | Path, repo: str | Path = REPO) -> dict:
    """Create a manifest from one fresh routed build output directory."""
    del repo  # The self-contained RTL source snapshot is staged by the build.
    output = Path(output).resolve()
    export = output / "export"
    bit_path, hwh_path = export / "rle.bit", export / "rle.hwh"
    for artifact in (bit_path, hwh_path):
        if not artifact.is_file() or artifact.stat().st_size == 0:
            raise ValueError(f"Missing nonempty RLE artifact: {artifact.name}")
    validate_rle_hwh(hwh_path)

    rtl_root = output / "rtl"
    if not rtl_root.is_dir():
        raise ValueError("RLE RTL snapshot directory is missing")
    snapshot = {
        p.relative_to(output).as_posix(): sha256(p)
        for p in sorted(rtl_root.rglob("*.v")) if p.is_file() and p.stat().st_size
    }
    if not snapshot:
        raise ValueError("RLE RTL snapshot is empty")
    return {
        "schema": 1,
        **CONTRACT,
        "bit_path": "rle.bit",
        "bit_sha256": sha256(bit_path),
        "hwh_path": "rle.hwh",
        "hwh_sha256": sha256(hwh_path),
        "rtl_snapshot_sha256": snapshot,
        "build_evidence": "ROUTED_NOT_BOARD_VERIFIED",
    }


def _snapshot_root(manifest_path: Path) -> Path:
    # Manifest is stored under <build>/export/manifest.json.
    if manifest_path.parent.name != "export":
        raise ValueError("RLE manifest must live under a build export directory")
    return manifest_path.parent.parent.resolve()


def load_manifest(path: str | Path, expected_sha256: str | None = None) -> dict:
    """Validate a manifest and all artifacts without importing or touching PYNQ."""
    manifest_path = Path(path).expanduser().resolve()
    try:
        raw = manifest_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"Cannot read RLE manifest: {exc}") from exc
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not _DIGEST.fullmatch(expected_sha256.lower()):
            raise ValueError("Expected manifest SHA-256 must be 64 hexadecimal characters")
        if hashlib.sha256(raw).hexdigest() != expected_sha256.lower():
            raise ValueError("Manifest SHA-256 differs from the expected digest")
    try:
        manifest = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"RLE manifest is invalid JSON: {exc}") from exc
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_FIELDS:
        raise ValueError("RLE manifest fields differ from the strict schema")
    expected = {"schema": 1, **CONTRACT}
    if any(type(manifest.get(key)) is not type(value) or manifest.get(key) != value
           for key, value in expected.items()):
        raise ValueError("RLE manifest contract mismatch")
    if manifest.get("build_evidence") != "ROUTED_NOT_BOARD_VERIFIED":
        raise ValueError("RLE build evidence status is invalid")

    artifact_specs = (("bit_path", "rle.bit", "bit_sha256", "bit"),
                      ("hwh_path", "rle.hwh", "hwh_sha256", "HWH"))
    for path_key, required_name, digest_key, label in artifact_specs:
        if manifest.get(path_key) != required_name:
            raise ValueError(f"RLE artifact path is invalid: {path_key}")
        digest = manifest.get(digest_key)
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest.lower()):
            raise ValueError(f"RLE {label} SHA-256 is invalid")
        artifact = (manifest_path.parent / required_name).resolve()
        if artifact.parent != manifest_path.parent or not artifact.is_file():
            raise ValueError(f"RLE {label} artifact path escapes export or is missing")
        if sha256(artifact) != digest.lower():
            raise ValueError(f"RLE {label} SHA-256 mismatch")
    validate_rle_hwh(manifest_path.parent / "rle.hwh")

    snapshot = manifest.get("rtl_snapshot_sha256")
    if not isinstance(snapshot, dict) or not snapshot:
        raise ValueError("RLE RTL snapshot is empty or invalid")
    snapshot_root = _snapshot_root(manifest_path)
    for name, digest in snapshot.items():
        if not isinstance(name, str) or not name.startswith("rtl/") or ".." in Path(name).parts:
            raise ValueError("RLE RTL snapshot path is invalid")
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest.lower()):
            raise ValueError("RLE RTL snapshot SHA-256 is invalid")
        source = (snapshot_root / name).resolve()
        if snapshot_root not in source.parents or not source.is_file():
            raise ValueError("RLE RTL snapshot path escapes build directory or is missing")
        if sha256(source) != digest.lower():
            raise ValueError(f"RLE RTL snapshot SHA-256 mismatch: {name}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.out.resolve()
    manifest = build_manifest(output)
    path = output / "export" / "manifest.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, sort_keys=True, indent=2)
        stream.write("\n")
    print(f"RLE_ARTIFACTS_VALID manifest={path} sha256={sha256(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
