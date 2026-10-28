from __future__ import annotations

import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "wo0015"))
import board_preflight as base

from scripts.rle.artifacts import (
    CONTRACT, build_manifest, load_manifest, sha256, validate_rle_hwh,
)


def _module(root, name, parameters=None, interfaces=None, modtype=""):
    attrs = {"INSTANCE": name}
    if modtype:
        attrs["MODTYPE"] = modtype
    module = ET.SubElement(root, "MODULE", attrs)
    if parameters is not None:
        group = ET.SubElement(module, "PARAMETERS")
        for key, value in parameters.items():
            ET.SubElement(group, "PARAMETER", {"NAME": key, "VALUE": str(value)})
    for name, (bus, width) in (interfaces or {}).items():
        attrs = {"NAME": name, "BUSNAME": bus, "DATAWIDTH": str(width)}
        bus_node = ET.SubElement(module, "BUSINTERFACE", attrs)
        if name.lower() in ("m_axis", "s_axis_s2mm"):
            del bus_node.attrib["DATAWIDTH"]
            for key, value in (("TDATA_NUM_BYTES", "4"), ("HAS_TKEEP", "1"),
                               ("HAS_TLAST", "1"), ("HAS_TREADY", "1")):
                ET.SubElement(bus_node, "PARAMETER", NAME=key, VALUE=value)


def _hwh_bytes(*, pipeline_type="axis_dma_rle_pipeline_bd", width="640",
               height="400", max_runs="16384", mm2s_width="64",
               control_base="0x43C20000"):
    root = ET.Element("SYSTEM")
    dma = {
        "C_INCLUDE_MM2S": "1", "C_INCLUDE_S2MM": "1", "C_INCLUDE_SG": "0",
        "C_MICRO_DMA": "0", "C_SG_LENGTH_WIDTH": "18",
        "C_M_AXI_MM2S_ADDR_WIDTH": "32", "C_M_AXI_S2MM_ADDR_WIDTH": "32",
        "C_M_AXI_MM2S_DATA_WIDTH": mm2s_width, "C_M_AXI_S2MM_DATA_WIDTH": "32",
        "C_M_AXIS_MM2S_TDATA_WIDTH": "32", "C_S_AXIS_S2MM_TDATA_WIDTH": "32",
        "C_INCLUDE_MM2S_DRE": "0", "C_INCLUDE_S2MM_DRE": "0",
    }
    _module(root, "axi_dma_0", dma, {
        "M_AXI_MM2S": ("dma_mm2s", 64), "M_AXI_S2MM": ("dma_s2mm", 32),
        "S_AXIS_S2MM": ("rle_stream", 32),
    })
    _module(root, "hp0_protocol", interfaces={
        "S_AXI": ("dma_mm2s", 64), "M_AXI": ("hp0", 64)})
    _module(root, "hp1_fabric", {"NUM_SI": "1", "NUM_MI": "1"}, {
        "S00_AXI": ("dma_s2mm", 32), "M00_AXI": ("hp1", 32)})
    _module(root, "image_pipeline", {"WIDTH": width, "HEIGHT": height,
        "MAX_RUNS": max_runs}, {"m_axis": ("rle_stream", 32)}, pipeline_type)
    _module(root, "ps7", {
        "PCW_FPGA0_PERIPHERAL_FREQMHZ": "50", "PCW_USE_S_AXI_HP0": "1",
        "PCW_S_AXI_HP0_DATA_WIDTH": "64", "PCW_USE_S_AXI_HP1": "1",
        "PCW_S_AXI_HP1_DATA_WIDTH": "32",
    }, {"S_AXI_HP0": ("hp0", 64), "S_AXI_HP1": ("hp1", 32)})
    for instance, basevalue, highvalue, interface in (
        ("axi_dma_0", "0x40400000", "0x4040FFFF", "S_AXI_LITE"),
        ("image_pipeline", control_base, "0x43C2FFFF", "s_axi"),
        ("ps7", "0", "0x1FFFFFFF", "S_AXI_HP0"),
        ("ps7", "0", "0x1FFFFFFF", "S_AXI_HP1"),
    ):
        ET.SubElement(root, "MEMRANGE", {"INSTANCE": instance,
            "BASEVALUE": basevalue, "HIGHVALUE": highvalue,
            "SLAVEBUSINTERFACE": interface})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _build_tree(tmp_path, *, hwh=None, include_rtl=True):
    out = tmp_path / "build"
    (out / "export").mkdir(parents=True)
    (out / "rtl").mkdir()
    (out / "export" / "rle.bit").write_bytes(b"rle-bitstream")
    (out / "export" / "rle.hwh").write_bytes(hwh or _hwh_bytes())
    if include_rtl:
        (out / "rtl" / "axis_rle_encoder.v").write_text("module encoder; endmodule\n")
    return out


def _write_manifest(output):
    manifest = build_manifest(output, repo=ROOT)
    path = output / "export" / "manifest.json"
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path, manifest


def test_contract_describes_640x400_rle_and_variable_rx():
    assert CONTRACT["protocol"] == "RLE_PACKET_V1"
    assert (CONTRACT["width"], CONTRACT["height"]) == (640, 400)
    assert CONTRACT["tx_bytes"] == 256032
    assert CONTRACT["rx_capacity_bytes"] == 67744


def test_valid_hwh_checks_rle_geometry_dma_and_routes(tmp_path):
    path = tmp_path / "rle.hwh"
    path.write_bytes(_hwh_bytes())
    validate_rle_hwh(path)


@pytest.mark.parametrize("kwargs,match", [
    ({"pipeline_type": "axis_dma_gray_s2mm_pipeline_bd"}, "RLE pipeline"),
    ({"width": "320"}, "WIDTH"),
    ({"height": "240"}, "HEIGHT"),
    ({"max_runs": "8192"}, "MAX_RUNS"),
    ({"mm2s_width": "32"}, "MM2S"),
    ({"control_base": "0x43C30000"}, "base for image_pipeline"),
])
def test_hwh_rejects_wrong_overlay_contract(tmp_path, kwargs, match):
    path = tmp_path / "wrong.hwh"
    path.write_bytes(_hwh_bytes(**kwargs))
    with pytest.raises(ValueError, match=match):
        validate_rle_hwh(path)


def test_build_manifest_hashes_bit_hwh_and_rtl_snapshot(tmp_path):
    output = _build_tree(tmp_path)
    manifest = build_manifest(output, repo=ROOT)
    assert manifest["bit_sha256"] == sha256(output / "export" / "rle.bit")
    assert manifest["hwh_sha256"] == sha256(output / "export" / "rle.hwh")
    assert manifest["rtl_snapshot_sha256"] == {
        "rtl/axis_rle_encoder.v": sha256(output / "rtl" / "axis_rle_encoder.v")}
    assert manifest["build_evidence"] == "ROUTED_NOT_BOARD_VERIFIED"


def test_build_manifest_requires_nonempty_rtl_snapshot(tmp_path):
    output = _build_tree(tmp_path, include_rtl=False)
    with pytest.raises(ValueError, match="RTL snapshot"):
        build_manifest(output, repo=ROOT)


def test_load_manifest_checks_digest_and_real_hwh(tmp_path):
    output = _build_tree(tmp_path)
    path, manifest = _write_manifest(output)
    digest = sha256(path)
    assert load_manifest(path, expected_sha256=digest) == manifest
    with pytest.raises(ValueError, match="SHA-256"):
        load_manifest(path, expected_sha256="0" * 64)


def test_load_manifest_rejects_path_traversal_and_modified_bit(tmp_path):
    output = _build_tree(tmp_path)
    path, manifest = _write_manifest(output)
    manifest["bit_path"] = "../../outside.bit"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="artifact path"):
        load_manifest(path)

    manifest["bit_path"] = "rle.bit"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    (output / "export" / "rle.bit").write_bytes(b"changed")
    with pytest.raises(ValueError, match="bit SHA-256"):
        load_manifest(path)


def test_existing_gray_hwh_fixture_is_rejected():
    gray_hwh = ROOT / "data" / "results" / "gray8-dma-20261027" / "export" / "gray8.hwh"
    if not gray_hwh.is_file():
        pytest.skip("checked-in Gray8 HWH not available")
    with pytest.raises(ValueError, match="RLE pipeline"):
        validate_rle_hwh(gray_hwh)
