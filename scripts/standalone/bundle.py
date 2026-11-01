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

OFFLOAD_CAPABILITIES = {
    "abi": 2,
    "preview": {
        "signature": "PBO1",
        "base": 0x43C50000,
        "source_geometry": [640, 400],
        "viewport": [384, 146, 512, 320],
    },
    "input": {"signature": "RAW1", "mode": "raw"},
    "buttons": {"interrupt": True, "intc_index": 3, "status_signature": "BTN1"},
    "features": {"signature": "FRG2"},
}


def _hwh_instance(root, name):
    matches = [module for module in root.findall(".//MODULE")
               if module.get("INSTANCE") == name]
    if len(matches) != 1:
        raise ValueError(f"offload HWH must contain exactly one {name} module")
    return matches[0]


def _hwh_parameter(module, name):
    matches = [item for item in module.findall(".//PARAMETER")
               if item.get("NAME") == name]
    if len(matches) != 1:
        raise ValueError(f"offload HWH parameter is missing or ambiguous: {name}")
    return matches[0].get("VALUE")


def verify_offload_capabilities(manifest, hwh):
    """Check the declared offload contract against the exported HWH.

    PBO1 and RAW1 signatures are manifest declarations only. The actual
    register signatures are checked by the runtime before DMA is used.
    """
    try:
        actual_contract = json.dumps(manifest["offload"], sort_keys=True,
                                     separators=(",", ":"), allow_nan=False)
        expected_contract = json.dumps(OFFLOAD_CAPABILITIES, sort_keys=True,
                                       separators=(",", ":"), allow_nan=False)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("offload manifest capabilities do not match the fixed contract") from exc
    if actual_contract != expected_contract:
        raise ValueError("offload manifest capabilities do not match the fixed contract")
    try:
        root = ET.parse(hwh).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError("offload HWH is missing or invalid") from exc

    ranges = []
    for item in root.findall(".//MEMRANGE"):
        try:
            address = int(item.get("BASEVALUE"), 0)
        except (TypeError, ValueError):
            continue
        ranges.append((item.get("INSTANCE", ""), address))
    for instance_token, address, label in (
        ("binary_overlay", 0x43C50000, "PBO1"),
        ("image_pipeline", 0x43C20000, "RAW1"),
    ):
        matches = [instance for instance, base in ranges
                   if base == address and instance_token in instance]
        if len(matches) != 1:
            raise ValueError(f"offload {label} HWH memory range mismatch")

    gpio = _hwh_instance(root, "btns_gpio")
    if _hwh_parameter(gpio, "C_INTERRUPT_PRESENT") not in ("1", "0x1", "true", "True"):
        raise ValueError("offload GPIO interrupt is not enabled in HWH")
    intc = _hwh_instance(root, "video_interrupts")
    if _hwh_parameter(intc, "NUM_PORTS") != "4":
        raise ValueError("offload interrupt controller must expose four ports")

    irq_ports = [port for port in gpio.findall(".//PORT")
                 if port.get("NAME") == "ip2intc_irpt"]
    connections = [connection for port in irq_ports
                   for connection in port.findall(".//CONNECTION")]
    if len(irq_ports) != 1 or not any(
        item.get("INSTANCE") == "video_interrupts" and item.get("PORT") == "In3"
        for item in connections
    ):
        raise ValueError("offload GPIO IRQ connection to video_interrupts/In3 is unverified")
    return {
        "gpio_irq_connection_verified": True,
        "register_signatures": "runtime_only",
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
    if "offload" in value:
        verify_offload_capabilities(value, files["hwh"])
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
