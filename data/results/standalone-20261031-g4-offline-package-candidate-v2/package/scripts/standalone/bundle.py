"""Offline integrity and topology gate for the independent G1 display build."""
import hashlib
import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from .verify_sources import _valid_relative_path


required_addresses = {
    "system_interrupts": 0x40000000,
    "btns_gpio": 0x41200000,
    "hdmi_out_frontend_hdmi_out_hpd_video": 0x41210000,
    "axi_vdma": 0x43000000,
    "hdmi_out_frontend_axi_dynclk": 0x43C10000,
    "hdmi_out_frontend_vtc_out": 0x43C30000,
    "frame_monitor": 0x43C40000,
    "hdmi_out_color_convert": 0x43C60000,
    "hdmi_out_pixel_unpack": 0x43C70000,
}


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verify_bundle(manifest_path, expected_sha256):
    manifest = Path(manifest_path).resolve(strict=True)
    if sha256(manifest) != expected_sha256:
        raise ValueError("display manifest hash mismatch")
    value = json.loads(manifest.read_text())
    if value.get("schema") != "material-counter.display-build.v1":
        raise ValueError("unknown display manifest schema")
    if value.get("ready_for_board") is not True:
        raise ValueError("build has not passed Manager review")
    files = {}
    for kind in ("bit", "hwh"):
        item = value["files"][kind]
        if not _valid_relative_path(item["path"])[0]:
            raise ValueError("unsafe bundle path")
        path = (manifest.parent / item["path"]).resolve(strict=True)
        if not path.is_relative_to(manifest.parent):
            raise ValueError("bundle path escapes manifest directory")
        if sha256(path) != item["sha256"]:
            raise ValueError(f"{kind} hash mismatch")
        files[kind] = path
    if files["hwh"] != files["bit"].with_suffix(".hwh"):
        raise ValueError("BIT and HWH must have matching names")
    tree = ET.parse(files["hwh"])
    ranges = {}
    for item in tree.findall(".//MEMRANGE"):
        name = item.get("INSTANCE")
        if name in required_addresses:
            address = int(item.get("BASEVALUE"), 0)
            if name in ranges and ranges[name] != address:
                raise ValueError(f"conflicting HWH address for {name}")
            ranges[name] = address
    if ranges != required_addresses:
        raise ValueError("HWH display address map mismatch")
    vdma = next((m for m in tree.findall(".//MODULE") if m.get("INSTANCE") == "axi_vdma"), None)
    if vdma is None:
        raise ValueError("HWH lacks VDMA")
    parameters = {p.get("NAME"): p.get("VALUE") for p in vdma.findall(".//PARAMETER")}
    expected = {"C_NUM_FSTORES": "4", "C_INCLUDE_S2MM": "0",
                "C_M_AXIS_MM2S_TDATA_WIDTH": "32", "C_M_AXI_MM2S_DATA_WIDTH": "32"}
    if any(parameters.get(k) != v for k, v in expected.items()):
        raise ValueError("HWH VDMA parameters mismatch")
    return files


def verify_runtime(runtime, font_path, latin_font_path):
    """Hash installed files without importing PYNQ or touching hardware."""
    spec = importlib.util.find_spec("pynq")
    if spec is None or not spec.submodule_search_locations:
        raise ValueError("PYNQ package cannot be located")
    root = Path(next(iter(spec.submodule_search_locations))).resolve(strict=True)
    if sha256(font_path) != runtime["font_sha256"]:
        raise ValueError("installed font hash mismatch")
    if sha256(latin_font_path) != runtime["latin_font_sha256"]:
        raise ValueError("installed Latin font hash mismatch")
    if not runtime["sources"]:
        raise ValueError("runtime source lock is empty")
    for item in runtime["sources"]:
        if not _valid_relative_path(item["path"])[0]:
            raise ValueError("unsafe runtime source path")
        path = (root / item["path"]).resolve(strict=True)
        if not path.is_relative_to(root) or sha256(path) != item["sha256"]:
            raise ValueError("installed PYNQ source/native library hash mismatch")
    return root
