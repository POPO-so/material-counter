"""Read-only joint preflight, or explicitly stage a separate build snapshot.

No Vivado run starts here. The Tcl entrypoints consume the generated data-only
spec only after Manager supplies source-bound HLS and region PASS receipts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import xml.etree.ElementTree as ET

BASE_COMMIT = "c5a86807c93e6ed572e2f75e13a6f9c7f1320321"
XPR_REL = "data/results/standalone-20261101-055148-g2-integration/project/display.xpr"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def entry(path: Path) -> dict:
    return {"path": path.resolve().as_posix(), "sha256": sha(path)}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.write("\n")


def component_contract(path: Path) -> dict:
    root = ET.parse(path).getroot()
    ns = {"s": root.tag.split("}")[0].lstrip("{")}
    uri = "{" + ns["s"] + "}"

    def text(node, query):
        return node.findtext(query, namespaces=ns)

    ports = {}
    for port in root.findall("s:model/s:ports/s:port", ns):
        vector = port.find("s:wire/s:vector", ns)
        width = 1 if vector is None else abs(int(text(vector, "s:left"), 0) - int(text(vector, "s:right"), 0)) + 1
        ports[text(port, "s:name")] = {"width": width, "direction": text(port, "s:wire/s:direction")}
    buses = []
    for bus in root.findall("s:busInterfaces/s:busInterface", ns):
        kind = bus.find("s:busType", ns).attrib[uri + "name"]
        mode = "master" if bus.find("s:master", ns) is not None else "slave"
        maps = {text(p, "s:logicalPort/s:name"): text(p, "s:physicalPort/s:name")
                for p in bus.findall("s:portMaps/s:portMap", ns)}
        parameters = {text(p, "s:name"): text(p, "s:value")
                      for p in bus.findall("s:parameters/s:parameter", ns)}
        buses.append({"name": text(bus, "s:name"), "kind": kind, "mode": mode,
                      "maps": maps, "parameters": parameters})

    def one(kind, mode):
        matches = [b for b in buses if b["kind"] == kind and b["mode"] == mode]
        require(len(matches) == 1, f"ambiguous/missing {kind} {mode}: {matches}")
        return matches[0]

    axis_in, axis_out, control = one("axis", "slave"), one("axis", "master"), one("aximm", "slave")
    require(not any(b["kind"] == "aximm" and b["mode"] == "master" for b in buses), "unexpected HLS memory master")
    for bus in (axis_in, axis_out):
        for logical, wanted in (("TDATA", 32), ("TKEEP", 4), ("TLAST", 1), ("TVALID", 1), ("TREADY", 1)):
            physical = bus["maps"].get(logical)
            require(physical in ports and ports[physical]["width"] == wanted,
                    f"{bus['name']} {logical} width mismatch")
    require(control["parameters"].get("PROTOCOL") == "AXI4LITE", "control is not AXI4LITE")
    clock, reset = one("clock", "slave"), one("reset", "slave")
    require(reset["parameters"].get("POLARITY") == "ACTIVE_LOW", "HLS reset polarity is not active low")
    associated = set(clock["parameters"].get("ASSOCIATED_BUSIF", "").split(":"))
    require({axis_in["name"], axis_out["name"], control["name"]} <= associated, "missing HLS clock association")
    regmap = {}
    blocks = root.findall("s:memoryMaps/s:memoryMap/s:addressBlock", ns)
    require(len(blocks) == 1 and int(text(blocks[0], "s:range"), 0) == 65536, "HLS register window changed")
    for reg in blocks[0].findall("s:register", ns):
        regmap[text(reg, "s:name")] = {"offset": int(text(reg, "s:addressOffset"), 0),
                                      "fields": {text(f, "s:name"): {"bit": int(text(f, "s:bitOffset"), 0),
                                                                            "width": int(text(f, "s:bitWidth"), 0)}
                                                 for f in reg.findall("s:field", ns)}}
    require({"CTRL", "jpeg_size", "frame_id", "status", "status_ctrl"} <= regmap.keys(), "required HLS registers missing")
    require(regmap["status"]["fields"].get("status", {}).get("width") == 32, "status register width mismatch")
    require("status_ap_vld" in regmap["status_ctrl"]["fields"], "status validity field missing")
    return {"component": entry(path), "vlnv": ":".join(text(root, "s:" + k) for k in ("vendor", "library", "name", "version")),
            "input": axis_in, "output": axis_out, "control": control, "clock": clock, "reset": reset,
            "ports": ports, "registers": regmap, "window": {"offset": "0x43C80000", "range": 65536}}


def preflight(args) -> dict:
    root = args.snapshot.resolve()
    manifest_path = root / "jpeg-joint-source.json"
    manifest = read_json(manifest_path)
    require(manifest["base_commit"] == BASE_COMMIT and len(manifest["files"]) == 21, "baseline commit/file count changed")
    pinned = []
    for name, expected in manifest["files"].items():
        path = (root / name).resolve()
        require(path.is_relative_to(root), "unsafe baseline path")
        wanted = manifest.get("overrides", {}).get(name, {}).get("staged_sha256", expected["sha256"])
        require(path.is_file() and sha(path) == wanted, f"baseline hash mismatch: {name}")
        pinned.append(entry(path))
    xpr = (args.xpr or root / XPR_REL).resolve()
    require(xpr.is_relative_to(root), "baseline xpr must be inside the fixed snapshot")
    files = []
    for node in ET.parse(xpr).findall(".//File"):
        name = node.attrib["Path"]
        for macro, replacement in (("$PPRDIR", xpr.parent), ("$PSRCDIR", xpr.parent / (xpr.stem + ".srcs")),
                                   ("$PGENDIR", xpr.parent / (xpr.stem + ".gen"))):
            name = name.replace(macro, replacement.as_posix())
        path = Path(name).resolve()
        require("$" not in name and path.is_file() and path.is_relative_to(root), f"unresolved/outside baseline source: {name}")
        files.append(path)
    bds = [p for p in files if p.suffix == ".bd"]
    require(len(bds) == 1, "expected exactly one baseline BD")
    design = read_json(bds[0])["design"]
    cells = design["components"]
    param = lambda c, k: cells[c]["parameters"][k]["value"]
    require(param("control", "NUM_MI") == "12", "baseline control must have 12 MI")
    require(param("axi_dma_0", "c_sg_length_width") == "18", "baseline DMA length width must be 18")
    require(param("axi_dma_0", "c_include_mm2s_dre") == "0" and param("axi_dma_0", "c_include_s2mm_dre") == "0", "DRE changed")
    require(param("axi_dma_0", "c_m_axis_mm2s_tdata_width") == "32", "MM2S is not 32 bits")
    for i, expected in enumerate(("50", "100", "142.857")):
        require(param("ps7", f"PCW_FPGA{i}_PERIPHERAL_FREQMHZ") == expected, f"FCLK{i} changed")
    segments = design["addressing"]["/ps7"]["address_spaces"]["Data"]["segments"]
    require(len(segments) == 12, "baseline GP0 map must have 12 segments")
    for segment in segments.values():
        low = int(segment["offset"], 0)
        require(segment["range"] == "64K", "unexpected baseline segment range")
        require(not (low < 0x43C90000 and 0x43C80000 < low + 65536), "JPEG control window overlaps baseline")
    nets = design["interface_nets"]
    require(any(set(n["interface_ports"]) == {"axi_dma_0/M_AXIS_MM2S", "image_pipeline/s_axis"} for n in nets.values()), "baseline image stream changed")
    region = args.region.resolve()
    region_text = region.read_text(encoding="utf-8")
    require("module rle_region_features" in region_text and "stats_mem" in region_text and "ram_style" in region_text,
            "region source does not match the expected BRAM module")
    jpeg_source = args.jpeg_source.resolve()
    pending = []
    component = args.hls_ip.resolve() / "component.xml"
    contract = component_contract(component) if component.is_file() else None
    if contract is None:
        pending.append("HLS component.xml not exported")
    return {"schema": "material-counter.jpeg-joint-preflight.v1", "status": "READY_FOR_STAGING" if not pending else "PENDING_HLS",
            "pending": pending, "base_commit": BASE_COMMIT, "baseline_manifest": entry(manifest_path),
            "snapshot": root.as_posix(), "xpr": entry(xpr), "bd": entry(bds[0]),
            "baseline_pinned_sources": pinned, "project_sources": [entry(p) for p in files],
            "region": entry(region), "jpeg_source": entry(jpeg_source), "hls": contract,
            "preserve": {"control_ports": "M00..M11", "address_map": segments,
                         "memory_paths": {k: v for k, v in nets.items() if any(p.startswith("ps7/") for p in v["interface_ports"])},
                         "clock_nets": {k: v for k, v in design["nets"].items() if "FCLK_CLK" in k or "PXL_CLK" in k}},
            "mask_accept": "completed JPEG status==0 AND status_ap_vld AND RLE valid AND FRG valid; PS decision",
            "board_ready": False, "route_authorized": False}


def pass_receipt(path: Path, identity_key: str, expected: str) -> dict:
    receipt = read_json(path)
    require(receipt.get("status") == "PASS", f"not a PASS receipt: {path}")
    require(receipt.get(identity_key) == expected, f"receipt source identity mismatch: {path}")
    require(bool(receipt.get("evidence")), f"receipt has no evidence: {path}")
    for evidence in receipt["evidence"]:
        require(sha(Path(evidence["path"])) == evidence["sha256"], f"receipt evidence mismatch: {evidence['path']}")
    return entry(path)


def check_region_receipt(receipt: dict) -> None:
    """Accept the committed region receipt's explicit evidence schema."""
    memory = receipt.get("memory_gate", {})
    require(receipt.get("reference_diff") == "PASS" and receipt.get("limits") == "PASS" and
            isinstance(memory.get("stats_bram36"), int) and memory["stats_bram36"] > 0 and
            isinstance(memory.get("row_bram36"), int) and memory["row_bram36"] > 0 and
            memory.get("stats_lutram") == 0,
            "region reference_diff/limits/memory_gate BRAM evidence is not PASS")


def tcl_scalar(value) -> str:
    value = str(value).replace("\\", "/")
    require(not any(c in value for c in "{}\n\r"), "unsupported Tcl data character")
    return "{" + value + "}"


def verify_manifest(path: Path) -> None:
    manifest = read_json(path)
    for item in manifest["immutable_inputs"] + manifest["snapshot_sources"]:
        require(sha(Path(item["path"])) == item["sha256"], f"joint input changed: {item['path']}")
    print("JOINT_SOURCE_HASHES_OK")


def verify_gates(path: Path, route_review: Path | None = None) -> None:
    manifest = read_json(path)
    gates = manifest["gate_receipts"]
    require(gates.get("hls") and gates.get("region"), "HLS RTL/region green receipts are not bound; restage after both pass")
    hls_path, region_path = Path(gates["hls"]["path"]), Path(gates["region"]["path"])
    pass_receipt(hls_path, "source_sha256", manifest["preflight"]["jpeg_source"]["sha256"])
    pass_receipt(region_path, "source_sha256", manifest["preflight"]["region"]["sha256"])
    hls, region = read_json(hls_path), read_json(region_path)
    require(hls.get("component_sha256") == manifest["preflight"]["hls"]["component"]["sha256"], "HLS component is not green snapshot")
    require(all(hls.get("checks", {}).get(k) is True for k in ("csim", "csynth", "cosim")), "HLS csim/csynth/cosim PASS is required")
    check_region_receipt(region)
    print("JOINT_GREEN_GATES_OK")
    if route_review:
        review = read_json(route_review)
        require(review.get("status") == "PASS" and review.get("manifest_sha256") == sha(path), "route review does not approve this manifest")
        require(review.get("cdc_reviewed") is True and review.get("constraints_reviewed") is True, "Manager CDC/constraint review is required")
        reports = path.parent / "reports" / "synth"
        for report in ("cdc.rpt", "clock-interaction.rpt", "timing.rpt", "drc.rpt", "methodology.rpt"):
            require(review.get("report_sha256", {}).get(report) == sha(reports / report), f"route review report changed: {report}")
        print("JOINT_ROUTE_REVIEW_OK")


def verify_live_files(path: Path, listing: Path, ip_repos: Path | None = None) -> None:
    manifest = read_json(path)
    root = (path.parent / "snapshot").resolve()
    ip_root = (path.parent / "ip").resolve()
    allowed = {Path(i["path"]).resolve(): i["sha256"] for i in manifest["snapshot_sources"]}
    expected = {Path(p).resolve() for p in manifest["required_live_sources"]}
    live = {Path(line).resolve() for line in listing.read_text(encoding="utf-8-sig").splitlines() if line.strip()}
    require(expected <= live, f"live Vivado files omit staged manual sources: {expected - live}")
    for source in live:
        # Generated project products are inside the clone, not baseline RTL.
        # Official IP catalog roots are permitted read-only external inputs.
        if source in allowed:
            require(sha(source) == allowed[source], f"live source hash mismatch: {source}")
        elif source.is_relative_to(root) or source.is_relative_to(ip_root):
            continue
        elif any(source.is_relative_to(Path(repo)) for repo in manifest["external_ip_repositories"]):
            continue
        else:
            raise ValueError(f"live source escapes clone/approved official IP repositories: {source}")
    if ip_repos:
        actual = {Path(line).resolve() for line in ip_repos.read_text(encoding="utf-8-sig").splitlines() if line.strip()}
        wanted = {Path(p).resolve() for p in manifest["external_ip_repositories"]}
        require(wanted <= actual and actual <= wanted | {ip_root / "jpeg_axis_gray"}, f"unexpected live IP_REPO_PATHS: {actual}")
    print("JOINT_LIVE_SOURCE_ASSOCIATIONS_OK")


def stage(args, result: dict) -> None:
    require(result["status"] == "READY_FOR_STAGING", "preflight is not ready")
    hls_receipt = pass_receipt(args.hls_green.resolve(), "source_sha256", result["jpeg_source"]["sha256"]) if args.hls_green else None
    region_receipt = pass_receipt(args.region_green.resolve(), "source_sha256", result["region"]["sha256"]) if args.region_green else None
    if region_receipt:
        check_region_receipt(read_json(args.region_green))
    if hls_receipt:
        require(read_json(args.hls_green).get("component_sha256") == result["hls"]["component"]["sha256"], "HLS receipt component hash mismatch")
    out = args.stage.resolve()
    baseline = Path(result["snapshot"])
    require(not out.exists() and not out.is_relative_to(baseline) and not baseline.is_relative_to(out), "use a fresh, separate build directory")
    selected = {Path(i["path"]) for i in result["project_sources"] + result["baseline_pinned_sources"]}
    selected.add(Path(result["xpr"]["path"]))
    require(all(p.is_relative_to(baseline) for p in selected), "source outside baseline snapshot")
    proposed_bd = out / "snapshot" / Path(result["bd"]["path"]).relative_to(baseline)
    proposed_xpr = out / "snapshot" / Path(result["xpr"]["path"]).relative_to(baseline)
    generated_paths = []

    def check_generated_paths(node):
        if isinstance(node, dict):
            if "xci_path" in node:
                generated_paths.append(proposed_bd.parent / node["xci_path"].replace("\\", "/"))
            if "reference_info" in node:
                ref = node["reference_info"]["ref_name"]
                generated_paths.append(proposed_xpr.parent / (proposed_xpr.stem + ".gen") / "sources_1/bd/mref" / ref / "xgui")
            for value in node.values():
                check_generated_paths(value)
        elif isinstance(node, list):
            for value in node:
                check_generated_paths(value)

    check_generated_paths(read_json(Path(result["bd"]["path"])))
    max_path = max(len(p.as_posix().encode("utf-8")) for p in generated_paths)
    require(sys.platform != "win32" or max_path < 240,
            f"predicted Vivado generated path is {max_path} bytes; use a short build root such as C:/w/jj1101a")
    hls_root = ET.parse(args.hls_ip.resolve() / "component.xml").getroot()
    hls_ns = {"s": hls_root.tag.split("}")[0].lstrip("{")}
    hls_generated_root = proposed_xpr.parent / (proposed_xpr.stem + ".gen") / "sources_1/bd/display/ip/display_jpeg_axis_gray_0_0"
    hls_generated_paths = []
    for fileset in hls_root.findall("s:fileSets/s:fileSet", hls_ns):
        name = fileset.findtext("s:name", namespaces=hls_ns)
        if name in ("xilinx_verilogsynthesis_view_fileset", "xilinx_vhdlsynthesis_view_fileset"):
            for item in fileset.findall("s:file", hls_ns):
                relative = item.findtext("s:name", namespaces=hls_ns).replace("\\", "/")
                hls_generated_paths.append(hls_generated_root / relative)
    hls_longest = max(hls_generated_paths, key=lambda p: len(p.as_posix().encode("utf-8")))
    hls_max = len(hls_longest.as_posix().encode("utf-8"))
    hls_dir_max = max(len(p.parent.as_posix().encode("utf-8")) for p in hls_generated_paths)
    require(sys.platform != "win32" or (hls_max < 260 and hls_dir_max < 248),
            f"HLS fileset generated path exceeds Windows limit: {hls_max} file/{hls_dir_max} directory bytes; use a shorter project layout")
    out.mkdir(parents=True, exist_ok=False)
    staged = out / "snapshot"
    replacements = []
    snapshot_sources = []
    for source in sorted(selected):
        target = staged / source.relative_to(baseline)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.name == "rle_region_features.v":
            target.write_bytes(Path(result["region"]["path"]).read_bytes())
            replacements.append({"source": entry(source), "replacement": entry(target), "reason": "Manager-selected BRAM region core"})
        else:
            shutil.copy2(source, target)
        if target.suffix not in (".xpr", ".bd") and not any(p.endswith(".gen") for p in target.parts):
            snapshot_sources.append(entry(target))
    require(bool(replacements), "no region source was replaced")
    xpr = staged / Path(result["xpr"]["path"]).relative_to(baseline)
    project = ET.parse(xpr)
    project.getroot().set("Path", xpr.as_posix())
    required_live = []
    for node, old_source in zip(project.findall(".//File"), result["project_sources"]):
        target = staged / Path(old_source["path"]).relative_to(baseline)
        node.set("Path", target.as_posix())
        if target.suffix.lower() in (".v", ".sv", ".vhd", ".vhdl", ".xdc"):
            required_live.append(target.as_posix())
    top = [n.attrib["Val"] for n in project.findall(".//Option") if n.attrib.get("Name") == "TopModule"]
    require(top == ["display_wrapper"], f"unexpected XPR top: {top}")
    wrapper = next(p for p in required_live if Path(p).name == "display_wrapper.v")
    require(re.search(r"\bmodule\s+display_wrapper\b", Path(wrapper).read_text(encoding="utf-8")), "staged wrapper does not declare display_wrapper")
    external_repos = [n.attrib["Val"] for n in project.findall(".//Option") if n.attrib.get("Name") == "IPRepoPath"]
    require(all(Path(p).is_dir() for p in external_repos), "an official baseline IP repository is missing")
    for option in project.findall(".//Option"):
        if option.attrib.get("Name") == "TopAutoSet":
            option.set("Val", "FALSE")
    project.write(xpr, encoding="utf-8", xml_declaration=True)
    # The saved baseline BD contains relative generated paths and no absolute
    # manual sources. Reject future absolute drift instead of patching blindly.
    bd = staged / Path(result["bd"]["path"]).relative_to(baseline)
    require(not re.search(r'["\s][A-Za-z]:[/\\]', bd.read_text(encoding="utf-8")), "absolute path in baseline BD requires explicit relocation review")
    ip_target = out / "ip" / "jpeg_axis_gray"
    shutil.copytree(args.hls_ip.resolve(), ip_target)
    snapshot_sources.extend(entry(p) for p in sorted(ip_target.rglob("*")) if p.is_file())
    monitor_xdc = staged / "constraints" / "joint_monitor_cdc.xdc"
    monitor_xdc.parent.mkdir(parents=True, exist_ok=True)
    monitor_xdc.write_text(
        '# Same monitor CDC constraints as pinned scripts/standalone/build_display.tcl.\n'
        'set_false_path -to [get_pins -hier -regexp {.*frame_monitor.*(request_sync1|acknowledge_sync1|axi_reset_sync1)_reg/D}]\n'
        'set_max_delay -datapath_only 10 -from [get_cells -hier -regexp {.*frame_monitor.*mailbox_.*_reg.*}] -to [get_cells -hier -regexp {.*frame_monitor.*snap_.*_reg.*}]\n',
        encoding="utf-8")
    snapshot_sources.append(entry(monitor_xdc))
    code_files = [Path(__file__)] + [Path(__file__).with_name(name) for name in
                                  ("joint_common.tcl", "joint_connect.tcl", "joint_build.tcl", "joint_probe.tcl",
                                   "joint_capture_review.py")]
    immutable = result["baseline_pinned_sources"] + result["project_sources"] + [result["baseline_manifest"], result["xpr"],
                result["region"], result["jpeg_source"]] + [entry(p) for p in code_files]
    immutable.extend(i for i in (hls_receipt, region_receipt) if i)
    immutable.extend(entry(p) for p in args.hls_ip.resolve().rglob("*") if p.is_file())
    manifest = {"schema": "material-counter.jpeg-joint-build.v1", "preflight": result,
                "immutable_inputs": immutable, "snapshot_sources": snapshot_sources, "region_replacements": replacements,
                "gate_receipts": {"hls": hls_receipt, "region": region_receipt},
                "required_live_sources": required_live, "external_ip_repositories": external_repos,
                "cloned_xpr": entry(xpr), "cloned_bd_seed": entry(bd), "top": "display_wrapper",
                "predicted_max_generated_path_bytes": max_path,
                "predicted_hls_generated_path_bytes": hls_max,
                "predicted_hls_generated_directory_bytes": hls_dir_max,
                "predicted_hls_longest_generated_path": hls_longest.as_posix(),
                "stages": "STAGED_ONLY; Manager launches connect, synth, review CDC, then route separately", "board_ready": False}
    manifest_path = out / "joint_manifest.json"
    write_new(manifest_path, manifest)
    write_new(out / "joint_preflight.json", result)
    h = result["hls"]
    spec = {"schema": "jpeg_joint_spec_v1", "xpr": xpr, "out": out, "manifest": manifest_path,
            "python": Path(sys.executable).resolve(), "verifier": Path(__file__).resolve(),
            "capture_review": Path(__file__).with_name("joint_capture_review.py").resolve(),
            "ipcfg": args.ipcfg.resolve(), "hls_ip": ip_target, "vlnv": h["vlnv"],
            "input_intf": h["input"]["name"], "output_intf": h["output"]["name"], "control_intf": h["control"]["name"],
            "clock_pin": h["clock"]["maps"]["CLK"], "reset_pin": h["reset"]["maps"]["RST"],
            "input_data_pin": h["input"]["maps"]["TDATA"], "output_data_pin": h["output"]["maps"]["TDATA"],
            "source_root": staged, "max_lut": args.max_lut, "max_ff": args.max_ff,
            "max_bram36": args.max_bram36, "max_dsp": args.max_dsp}
    with (out / "joint_spec.tcldict").open("x", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(tcl_scalar(k) + " " + tcl_scalar(v) for k, v in spec.items()) + "\n")
    print(json.dumps({"status": "STAGED_ONLY", "spec": str(out / "joint_spec.tcldict"), "manifest_sha256": sha(manifest_path)}))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    repo = Path(__file__).resolve().parents[3]
    p.add_argument("--snapshot", type=Path, default=repo / ".runtime/jpeg-joint-snapshot-20261101")
    p.add_argument("--xpr", type=Path)
    p.add_argument("--hls-ip", type=Path, default=None)
    p.add_argument("--region", type=Path, default=Path(__file__).with_name("rle_region_features.v"))
    p.add_argument("--jpeg-source", type=Path, default=repo / "fpga/experimental/jpeg_axis/jpeg_axis_gray.cpp")
    p.add_argument("--result", type=Path, help="Fresh JSON path; omitted means stdout only")
    p.add_argument("--stage", type=Path, help="Explicitly stage a new build directory; never starts Vivado")
    p.add_argument("--hls-green", type=Path)
    p.add_argument("--region-green", type=Path)
    p.add_argument("--ipcfg", type=Path,
        default=Path(__file__).resolve().parents[3] / ".agents/skills/vivado-ip-configurator/lib/ipcfg.tcl")
    p.add_argument("--max-lut", type=int, default=53200)
    p.add_argument("--max-ff", type=int, default=106400)
    p.add_argument("--max-bram36", type=int, default=140)
    p.add_argument("--max-dsp", type=int, default=220)
    p.add_argument("--verify-manifest", type=Path)
    p.add_argument("--verify-gates", action="store_true")
    p.add_argument("--route-review", type=Path)
    p.add_argument("--live-files", type=Path)
    p.add_argument("--live-ip-repos", type=Path)
    args = p.parse_args()
    try:
        if args.verify_manifest:
            verify_manifest(args.verify_manifest)
            if args.live_files:
                verify_live_files(args.verify_manifest.resolve(), args.live_files, args.live_ip_repos)
            if args.verify_gates or args.route_review:
                verify_gates(args.verify_manifest.resolve(), args.route_review)
            return 0
        result = preflight(args)
        if args.result:
            write_new(args.result.resolve(), result)
        print(json.dumps({"status": result["status"], "baseline_files": len(result["baseline_pinned_sources"]),
                          "hls_vlnv": result["hls"]["vlnv"] if result["hls"] else None,
                          "register_offsets": {k: hex(v["offset"]) for k, v in result["hls"]["registers"].items()} if result["hls"] else None,
                          "jpeg_source_sha256": result["jpeg_source"]["sha256"], "pending": result["pending"],
                          "board_ready": False}, ensure_ascii=False))
        if args.stage:
            stage(args, result)
        return 0 if result["status"] == "READY_FOR_STAGING" else 2
    except (OSError, ValueError, KeyError, ET.ParseError) as error:
        print(f"JOINT_PREFLIGHT_FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
