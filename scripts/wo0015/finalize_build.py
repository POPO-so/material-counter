"""Validate the exported WO-0015 implementation pair and write its manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


def require(condition: bool, message: str) -> None:
    # Build acceptance must remain active under Python -O.
    if not condition:
        raise RuntimeError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_tcl_list(text: str) -> list[str]:
    """Parse the braced, non-evaluating Tcl list format used by ipcfg evidence."""
    values: list[str] = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break

        word: list[str] = []
        if text[index] == "{":
            index += 1
            depth = 1
            while index < len(text) and depth:
                char = text[index]
                if char == "\\" and index + 1 < len(text):
                    next_char = text[index + 1]
                    if next_char == "\n":
                        index += 2
                        while index < len(text) and text[index] in " \t":
                            index += 1
                        word.append(" ")
                    else:
                        word.extend((char, next_char))
                        index += 2
                elif char == "{":
                    depth += 1
                    word.append(char)
                    index += 1
                elif char == "}":
                    depth -= 1
                    if depth:
                        word.append(char)
                    index += 1
                else:
                    word.append(char)
                    index += 1
            require(depth == 0, "Malformed Tcl list evidence: unclosed brace")
            require(index == len(text) or text[index].isspace(),
                    "Malformed Tcl list evidence: text follows a braced word")
        elif text[index] == '"':
            index += 1
            while index < len(text) and text[index] != '"':
                if text[index] == "\\" and index + 1 < len(text):
                    index += 1
                word.append(text[index])
                index += 1
            require(index < len(text), "Malformed Tcl list evidence: unclosed quote")
            index += 1
            require(index == len(text) or text[index].isspace(),
                    "Malformed Tcl list evidence: text follows a quoted word")
        else:
            while index < len(text) and not text[index].isspace():
                if text[index] == "\\" and index + 1 < len(text):
                    index += 1
                word.append(text[index])
                index += 1
        values.append("".join(word))
    return values


def parse_tcl_dict(text: str, label: str) -> dict[str, str]:
    values = parse_tcl_list(text)
    require(len(values) % 2 == 0, f"Malformed Tcl dictionary evidence: {label}")
    return dict(zip(values[::2], values[1::2]))


parser = argparse.ArgumentParser()
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--clock", type=int, choices=(50,), required=True)
parser.add_argument("--repo", type=Path, required=True)
parser.add_argument("--native-log", type=Path, required=True)
args = parser.parse_args()

repo = args.repo.resolve()
out = args.out.resolve()
require(out == repo / "data" / "results" / "wo0015-hw-50mhz-20261015",
        "Output path must be the dedicated WO-0015 50 MHz results directory")
reports = out / "reports"
export = out / "export"
project = out / "project"
bit = export / "wo15.bit"
hwh = export / "wo15.hwh"
native_log = args.native_log.resolve()

required_reports = (
    "timing-synth.rpt", "utilization-synth.rpt", "timing-implemented.rpt",
    "setup-failing-paths.rpt", "hold-failing-paths.rpt",
    "utilization-implemented.rpt", "utilization-hierarchical.rpt", "drc.rpt",
    "methodology.rpt", "methodology.json", "clocks.rpt",
    "clock-interaction.rpt", "exceptions.rpt", "implemented-constraints.xdc",
)
for name in required_reports:
    path = reports / name
    require(path.is_file() and path.stat().st_size > 0, f"Required report missing or empty: {path}")

timing = (reports / "timing-implemented.rpt").read_text(encoding="utf-8", errors="replace")
row = re.search(
    r"^\s*(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(\d+)\s+(\d+)\s+"
    r"(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(\d+)\s+(\d+)\s+"
    r"(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(\d+)\s+(\d+)\s*$",
    timing,
    re.MULTILINE,
)
require(row is not None, "Implemented timing summary row was not found")
wns, tns, setup_bad, setup_total, whs, ths, hold_bad, hold_total, wpws, tpws, pulse_bad, pulse_total = row.groups()
timing_values = {
    "wns_ns": float(wns), "tns_ns": float(tns), "setup_failing_endpoints": int(setup_bad),
    "setup_total_endpoints": int(setup_total), "whs_ns": float(whs), "ths_ns": float(ths),
    "hold_failing_endpoints": int(hold_bad), "hold_total_endpoints": int(hold_total),
    "wpws_ns": float(wpws), "tpws_ns": float(tpws),
    "pulse_width_failing_endpoints": int(pulse_bad), "pulse_width_total_endpoints": int(pulse_total),
}
require(min(timing_values[key] for key in ("wns_ns", "whs_ns", "wpws_ns")) >= 0,
        "Negative implemented timing slack")
require(all(timing_values[key] == 0 for key in ("tns_ns", "ths_ns", "tpws_ns")),
        "Non-zero implemented timing violation sum")
require(all(timing_values[key] == 0 for key in (
    "setup_failing_endpoints", "hold_failing_endpoints", "pulse_width_failing_endpoints")),
    "Implemented timing has failing endpoints")
for check in (
    "checking no_clock (0)", "checking unconstrained_internal_endpoints (0)",
    "checking no_input_delay (0)", "checking no_output_delay (0)",
):
    require(check in timing, f"Timing report has unconstrained endpoints: missing {check}")
require(re.search(r"clk_fpga_0\s+\{[^}]+\}\s+[\d.]+\s+50\.000", timing) is not None,
        "Implemented clock is not 50 MHz")

drc_text = (reports / "drc.rpt").read_text(encoding="utf-8", errors="replace")
drc_severity = {name: len(re.findall(rf"\|\s*{re.escape(name)}\s*\|", drc_text))
                 for name in ("Error", "Critical Warning", "Warning", "Advisory")}
require(drc_severity["Error"] == 0 and drc_severity["Critical Warning"] == 0,
        f"Serious DRC findings: {drc_severity}")
methodology_path = reports / "methodology.json"
methodology_data = json.loads(methodology_path.read_text(encoding="utf-8"))
methodology_severity = {name: 0 for name in ("Error", "Critical Warning", "Warning", "Advisory")}

def count_methodology_severity(value: object) -> None:
    if isinstance(value, dict):
        severity = value.get("severity")
        if isinstance(severity, str) and severity in methodology_severity:
            methodology_severity[severity] += 1
        headers = value.get("headers")
        if isinstance(headers, list) and "severity" in headers:
            index = headers.index("severity")
            for item in value.get("data", []):
                if isinstance(item, list) and len(item) > index and item[index] in methodology_severity:
                    methodology_severity[item[index]] += 1
        for child in value.values():
            count_methodology_severity(child)
    elif isinstance(value, list):
        for child in value:
            count_methodology_severity(child)

count_methodology_severity(methodology_data)
if not any(methodology_severity.values()):
    methodology_text = (reports / "methodology.rpt").read_text(encoding="utf-8", errors="replace")
    methodology_severity = {
        name: len(re.findall(rf"\|\s*{re.escape(name)}\s*\|", methodology_text))
        for name in methodology_severity
    }
require(methodology_severity["Error"] == 0 and methodology_severity["Critical Warning"] == 0,
        f"Serious methodology findings: {methodology_severity}")

for path in (bit, hwh):
    require(path.is_file() and path.stat().st_size > 0, f"Exported artifact missing: {path}")
run_bit = project / "wo15_50m.runs" / "impl_1" / "wo15_wrapper.bit"
project_hwh = project / "wo15_50m.gen" / "sources_1" / "bd" / "wo15" / "hw_handoff" / "wo15.hwh"
require(run_bit.is_file() and project_hwh.is_file(), "Original Vivado run BIT/HWH pair is missing")
require(sha256(bit) == sha256(run_bit), "Exported BIT does not match the implementation run")
require(sha256(hwh) == sha256(project_hwh), "Exported HWH does not match the generated handoff")
require(native_log.is_file() and native_log.stat().st_size > 0, "Bound native Vivado log is missing")

tree = ET.parse(hwh)
root = tree.getroot()
modules = {node.get("INSTANCE"): node for node in root.findall(".//MODULE") if node.get("INSTANCE")}
require("axi_dma_0" in modules and "ps7" in modules and "hp1_fabric" in modules,
        "HWH must contain DMA, PS7 and the HP1 interconnect")

readback_file = reports / "parameter-readback.tcldict"
require(readback_file.is_file() and readback_file.stat().st_size > 0,
        f"IP configuration readback evidence missing or empty: {readback_file}")
readback_root = parse_tcl_dict(readback_file.read_text(encoding="utf-8"), "root")
require(readback_root.get("schema_version") == "1" and "data" in readback_root,
        "Unsupported IP configuration readback evidence schema")
readback = parse_tcl_dict(readback_root["data"], "data")
for name, wanted in {
    "clock_mhz": "50", "packet_bytes": "76800", "packet_beats": "19200",
    "hp0_memory_width_bits": "64", "hp1_memory_width_bits": "32",
}.items():
    require(readback.get(name) == wanted,
            f"IP configuration readback mismatch {name}: {readback.get(name)!r} != {wanted!r}")

readback_interfaces = parse_tcl_dict(readback.get("interfaces", ""), "interfaces")

def check_readback_axi(name: str, width: str, protocol: str) -> dict[str, str]:
    require(name in readback_interfaces, f"IP configuration readback interface missing: {name}")
    actual = parse_tcl_dict(readback_interfaces[name], name)
    require(actual.get("data_width") == width and actual.get("protocol") == protocol,
            f"IP configuration readback mismatch {name}: "
            f"width={actual.get('data_width')!r}/{width}, protocol={actual.get('protocol')!r}/{protocol}")
    return {"data_width_bits": actual["data_width"], "protocol": actual["protocol"]}

hp1_auto_pc_readback = {
    "s_axi": check_readback_axi(
        "hp1_fabric/s00_couplers/auto_pc/S_AXI", "32", "AXI4"),
    "m_axi": check_readback_axi(
        "hp1_fabric/s00_couplers/auto_pc/M_AXI", "32", "AXI3"),
}

def module_parameters(instance: str) -> dict[str, str]:
    module = modules[instance]
    return {parameter.get("NAME", "").lower(): parameter.get("VALUE", "")
            for parameter in module.findall(".//PARAMETER") if parameter.get("NAME")}

def check_parameters(instance: str, expected: dict[str, str]) -> dict[str, str]:
    actual = module_parameters(instance)
    for name, wanted in expected.items():
        require(actual.get(name.lower()) == wanted,
                f"HWH parameter mismatch {instance}.{name}: {actual.get(name.lower())!r} != {wanted!r}")
    return {name: actual[name.lower()] for name in expected}

dma_parameters = check_parameters("axi_dma_0", {
    "c_include_sg": "0", "c_include_mm2s": "1", "c_include_s2mm": "1",
    "c_micro_dma": "0", "c_sg_length_width": "18", "c_addr_width": "32",
    "c_m_axi_mm2s_data_width": "64", "c_m_axis_mm2s_tdata_width": "32",
    "c_include_mm2s_dre": "0", "c_include_s2mm_dre": "0",
    "c_mm2s_burst_size": "16", "c_m_axi_s2mm_data_width": "32",
    "c_s_axis_s2mm_tdata_width": "32", "c_s2mm_burst_size": "16",
})
ps_parameters = check_parameters("ps7", {
    "pcw_use_s_axi_hp0": "1", "pcw_s_axi_hp0_data_width": "64",
    "pcw_use_s_axi_hp1": "1", "pcw_s_axi_hp1_data_width": "32",
    "pcw_fpga0_peripheral_freqmhz": "50",
})
fabric_parameters = check_parameters("hp1_fabric", {"num_si": "1", "num_mi": "1"})

def bus_interface(instance: str, name: str) -> ET.Element:
    interfaces = [node for node in modules[instance].findall(".//BUSINTERFACE") if node.get("NAME") == name]
    require(len(interfaces) == 1, f"HWH interface missing or ambiguous: {instance}.{name}")
    return interfaces[0]

def bus_link(left: tuple[str, str], right: tuple[str, str]) -> str:
    left_node = bus_interface(*left)
    right_node = bus_interface(*right)
    left_name, right_name = left_node.get("BUSNAME"), right_node.get("BUSNAME")
    require(left_name and left_name == right_name,
            f"HWH bus connection mismatch: {left}={left_name}, {right}={right_name}")
    return left_name

def port_connection(instance: str, name: str) -> list[tuple[str, str]]:
    ports = [node for node in modules[instance].findall(".//PORT") if node.get("NAME") == name]
    require(len(ports) == 1, f"HWH clock/reset port missing or ambiguous: {instance}.{name}")
    return [(node.get("INSTANCE", ""), node.get("PORT", ""))
            for node in ports[0].findall(".//CONNECTION")]

def require_single_source(ports: list[tuple[str, str]], source: tuple[str, str], kind: str) -> dict[str, str]:
    for instance, port in ports:
        actual = port_connection(instance, port)
        require(actual == [source], f"HWH {kind} source mismatch for {instance}.{port}: {actual}")
    return {f"{instance}/{port}": f"{source[0]}/{source[1]}" for instance, port in ports}

clock_ports = [
    ("ps7", "S_AXI_HP1_ACLK"), ("axi_dma_0", "s_axi_lite_aclk"),
    ("axi_dma_0", "m_axi_mm2s_aclk"), ("axi_dma_0", "m_axi_s2mm_aclk"),
    ("image_pipeline", "aclk"), ("reset_pl", "slowest_sync_clk"),
    ("hp1_fabric", "ACLK"), ("hp1_fabric", "S00_ACLK"), ("hp1_fabric", "M00_ACLK"),
]
reset_ports = [
    ("axi_dma_0", "axi_resetn"), ("image_pipeline", "aresetn"),
    ("hp1_fabric", "ARESETN"), ("hp1_fabric", "S00_ARESETN"), ("hp1_fabric", "M00_ARESETN"),
]
clock_connections = require_single_source(clock_ports, ("ps7", "FCLK_CLK0"), "clock")
reset_connections = require_single_source(reset_ports, ("reset_pl", "peripheral_aresetn"), "reset")

def check_axi(instance: str, name: str, width: str, protocol: str | None) -> dict[str, str | None]:
    node = bus_interface(instance, name)
    values = {parameter.get("NAME", "").upper(): parameter.get("VALUE", "")
              for parameter in node.findall(".//PARAMETER") if parameter.get("NAME")}
    actual_width = node.get("DATAWIDTH", "")
    actual_protocol = values.get("PROTOCOL", "")
    require(actual_width == width,
            f"HWH AXI width mismatch {instance}.{name}: width={actual_width}/{width}")
    if protocol is not None:
        require(actual_protocol == protocol,
                f"HWH AXI protocol mismatch {instance}.{name}: protocol={actual_protocol}/{protocol}")
    return {"data_width_bits": actual_width, "protocol": actual_protocol or None}

links = {
    "mm2s_dma_to_hp0_converter": bus_link(("axi_dma_0", "M_AXI_MM2S"), ("hp0_protocol", "S_AXI")),
    "mm2s_hp0_converter_to_ps": bus_link(("hp0_protocol", "M_AXI"), ("ps7", "S_AXI_HP0")),
    "s2mm_dma_to_hp1_fabric": bus_link(("axi_dma_0", "M_AXI_S2MM"), ("hp1_fabric", "S00_AXI")),
    "s2mm_hp1_fabric_to_ps": bus_link(("hp1_fabric", "M00_AXI"), ("ps7", "S_AXI_HP1")),
    "mm2s_stream_to_wrapper": bus_link(("axi_dma_0", "M_AXIS_MM2S"), ("image_pipeline", "s_axis")),
    "wrapper_to_s2mm_stream": bus_link(("image_pipeline", "m_axis"), ("axi_dma_0", "S_AXIS_S2MM")),
}
hp1_dma_outer_bus = bus_interface("axi_dma_0", "M_AXI_S2MM").get("BUSNAME", "")
hp1_ps_outer_bus = bus_interface("ps7", "S_AXI_HP1").get("BUSNAME", "")
require(hp1_dma_outer_bus and hp1_ps_outer_bus,
        "HWH must preserve external DMA and PS HP1 BUSNAME links")
require(hp1_ps_outer_bus == "hp1_fabric_M00_AXI",
        f"HWH PS HP1 bus is not bound to hp1_fabric/M00_AXI: {hp1_ps_outer_bus}")
axi_interfaces = {
    "dma_mm2s": check_axi("axi_dma_0", "M_AXI_MM2S", "64", "AXI4"),
    "hp0_converter_si": check_axi("hp0_protocol", "S_AXI", "64", "AXI4"),
    "hp0_converter_mi": check_axi("hp0_protocol", "M_AXI", "64", "AXI3"),
    "ps_hp0": check_axi("ps7", "S_AXI_HP0", "64", "AXI3"),
    "dma_s2mm": check_axi("axi_dma_0", "M_AXI_S2MM", "32", "AXI4"),
    "hp1_fabric_si": check_axi("hp1_fabric", "S00_AXI", "32", None),
    "hp1_fabric_mi": check_axi("hp1_fabric", "M00_AXI", "32", None),
    "ps_hp1": check_axi("ps7", "S_AXI_HP1", "32", "AXI3"),
}
memory_ranges = [node for node in root.findall(".//MEMRANGE")
                 if node.get("INSTANCE") == "ps7" and node.get("MEMTYPE") == "MEMORY"]
memory_master_interfaces: dict[str, str] = {}
for hp in ("HP0", "HP1"):
    candidates = [node for node in memory_ranges if node.get("SLAVEBUSINTERFACE") == f"S_AXI_{hp}"]
    require(len(candidates) == 1, f"HWH DDR memory range missing or ambiguous for {hp}")
    node = candidates[0]
    require(node.get("BASEVALUE", "").lower() == "0x00000000" and
            node.get("HIGHVALUE", "").lower() == "0x1fffffff",
            f"HWH {hp} DDR range is not 512 MiB from address zero")
    expected_master = "M_AXI_MM2S" if hp == "HP0" else "M_AXI_S2MM"
    require(node.get("MASTERBUSINTERFACE") == expected_master,
            f"HWH {hp} DDR range is assigned to {node.get('MASTERBUSINTERFACE')!r}, expected {expected_master}")
    memory_master_interfaces[hp.lower()] = expected_master

def axis_parameters(instance: str, name: str) -> dict[str, str]:
    node = bus_interface(instance, name)
    values = {parameter.get("NAME", "").upper(): parameter.get("VALUE", "")
              for parameter in node.findall(".//PARAMETER") if parameter.get("NAME")}
    expected = {"TDATA_NUM_BYTES": "4", "HAS_TKEEP": "1", "HAS_TLAST": "1", "HAS_TREADY": "1"}
    for key, value in expected.items():
        require(values.get(key) == value, f"HWH AXIS property mismatch {instance}.{name}.{key}")
    return expected

axis = {
    "dma_mm2s": axis_parameters("axi_dma_0", "M_AXIS_MM2S"),
    "wrapper_input": axis_parameters("image_pipeline", "s_axis"),
    "wrapper_s2mm_output": axis_parameters("image_pipeline", "m_axis"),
    "dma_s2mm": axis_parameters("axi_dma_0", "S_AXIS_S2MM"),
}

expected_sources = {
    "axis_gray2binary.v": "fpga/rtl/axis_gray2binary.v",
    "axis_binary_output_checker.v": "fpga/experimental/wo0014/axis_binary_output_checker.v",
    "axis_dma_gray_adapter.v": "fpga/experimental/gray8/axis_dma_gray_adapter.v",
    "axis_dma_gray_image_pipeline.v": "fpga/experimental/gray8/axis_dma_gray_image_pipeline.v",
    "axis_s2mm_frame_packer.v": "fpga/experimental/wo0015/axis_s2mm_frame_packer.v",
    "axis_dma_gray_s2mm_pipeline_bd.v": "fpga/experimental/gray8/axis_dma_gray_s2mm_pipeline_bd.v",
}
staged = {path.name: path for path in (out / "rtl").glob("*.v")}
require(set(staged) == set(expected_sources), "Staged RTL set differs from the approved WO-0015 source list")
source_hashes: dict[str, str] = {}
for filename, relative in expected_sources.items():
    source_path = (repo / relative).resolve()
    staged_path = staged[filename]
    require(source_path.is_file() and staged_path.is_file(), f"RTL source missing: {relative}")
    source_hashes[relative] = sha256(source_path)
    require(sha256(staged_path) == source_hashes[relative], f"Staged RTL differs from repository source: {relative}")
wrapper_text = (repo / expected_sources["axis_dma_gray_s2mm_pipeline_bd.v"]).read_text(encoding="utf-8", errors="replace").lower()
require("8'h04: s_axi_rdata <= magic" in wrapper_text and "32'h47525938" in wrapper_text,
        "Gray8 register signature at offset 0x04 is absent")
require("8'h60: s_axi_rdata <= 32'h574f3135" in wrapper_text,
        "WO-0015 signature at AXI-Lite offset 0x60 is absent")

script_hashes = {}
for filename in ("create_s2mm_bd.tcl", "build.tcl", "export.tcl", "finalize_build.py", "README-build.md"):
    path = Path(__file__).resolve().parent / filename
    require(path.is_file(), f"Build provenance file missing: {path}")
    script_hashes[filename] = sha256(path)

try:
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
except (OSError, subprocess.CalledProcessError) as exc:
    raise RuntimeError(f"Cannot record source Git commit: {exc}") from exc

manifest = {
    "schema": 1,
    "build_id": out.name,
    "implementation_status": "implemented_offline",
    "board_tested": False,
    "part": "xc7z020clg400-1",
    "clock_mhz": 50,
    "contract": {"format": "Binary8", "width": 320, "height": 240,
                  "bytes_per_frame": 76800, "stream_width_bits": 32, "beats_per_frame": 19200,
                  "eof_tlast": "only on beat 19200"},
    "dma": {"parameters": dma_parameters, "mm2s_memory_width_bits": 64,
            "mm2s_stream_width_bits": 32, "s2mm_memory_width_bits": 32,
            "s2mm_stream_width_bits": 32, "length_register_bits": 18,
            "address_width_bits": 32, "scatter_gather": False, "micro_dma": False},
    "ps_hp": {"parameters": ps_parameters, "hp0_data_width_bits": 64, "hp1_data_width_bits": 32,
              "ddr_address_window": {"base": "0x00000000", "size_bytes": 536870912},
              "memory_master_interfaces": memory_master_interfaces},
    "hp1_fabric_parameters": fabric_parameters,
    "ip_config_readback": {
        "path": str(readback_file), "bytes": readback_file.stat().st_size,
        "sha256": sha256(readback_file), "hp1_auto_pc_interfaces": hp1_auto_pc_readback,
    },
    "hwh_axi_interfaces": axi_interfaces,
    "hwh_hp1_external_busnames": {"dma_m_axi_s2mm": hp1_dma_outer_bus, "ps_s_axi_hp1": hp1_ps_outer_bus},
    "hwh_bus_links": links,
    "hwh_clock_connections": clock_connections,
    "hwh_reset_connections": reset_connections,
    "hwh_axis_interfaces": axis,
    "bit": {"path": str(bit), "bytes": bit.stat().st_size, "sha256": sha256(bit)},
    "hwh": {"path": str(hwh), "bytes": hwh.stat().st_size, "sha256": sha256(hwh)},
    "vivado_native_log": {"path": str(native_log), "bytes": native_log.stat().st_size,
                          "sha256": sha256(native_log)},
    "implemented_timing": timing_values,
    "drc_severity_counts": drc_severity,
    "methodology_severity_counts": methodology_severity,
    "source_commit": commit,
    "rtl_sha256": source_hashes,
    "build_script_sha256": script_hashes,
}
manifest_path = export / "pair-manifest.json"
summary_path = reports / "build-summary.json"
require(not manifest_path.exists() and not summary_path.exists(), "WO-0015 manifest already exists; refusing overwrite")
manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
summary = {
    "status": "PASS",
    "clock_mhz": 50,
    "bit_sha256": manifest["bit"]["sha256"],
    "hwh_sha256": manifest["hwh"]["sha256"],
    "hwh_contract": "PASS",
    "hwh_bus_links": "PASS",
    "hp1_auto_converter_readback": "PASS",
    "implemented_timing": timing_values,
    "drc_severity_counts": drc_severity,
    "methodology_severity_counts": methodology_severity,
    "board_tested": False,
    "pair_manifest": str(manifest_path),
}
summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(json.dumps(summary, indent=2))
