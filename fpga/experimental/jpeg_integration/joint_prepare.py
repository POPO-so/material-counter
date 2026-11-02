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
OVERLAY_SOURCE_REL = Path(XPR_REL).parent.parent / "rtl/video_binary_overlay.v"
OVERLAY_PINNED_REFERENCE_REL = Path("fpga/experimental/standalone/video_binary_overlay.v")
OVERLAY_WRAPPER_SOURCE_REL = Path(XPR_REL).parent.parent / "rtl/video_binary_overlay_bd.v"
OVERLAY_WRAPPER_PINNED_REFERENCE_REL = Path("fpga/experimental/standalone/video_binary_overlay_bd.v")
FRAME_MONITOR_SOURCE_REL = Path("fpga/experimental/standalone/video_frame_monitor.v")
FRAME_MONITOR_PINNED_REFERENCE_REL = Path("fpga/experimental/standalone/video_frame_monitor.v")
TIMING_XDC_REL = Path("scripts/standalone/display_timing.xdc")
OFFLOAD_CDC_XDC_REL = Path("scripts/standalone/offload_cdc.xdc")


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
    region_receipt = read_json(args.region_green) if args.region_green else None
    region_schema = region_receipt.get("schema") if region_receipt else None
    check_region_source(region, region_schema)
    if region_receipt:
        check_region_receipt(region_receipt, sha(region))
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


def _check_bram_g2_region_receipt(receipt: dict,
                                  expected_source_sha256: str | None = None) -> None:
    """Validate the independent source-bound BRAM region evidence bundle."""
    require(receipt.get("schema") == "material-counter.rle-region-bram-g2-green.v1",
            "unsupported region receipt schema")
    require(receipt.get("status") == "PASS", "BRAM G2 region receipt is not PASS")
    source_sha256 = receipt.get("source_sha256")
    require(isinstance(source_sha256, str) and re.fullmatch(r"[0-9a-fA-F]{64}", source_sha256),
            "BRAM G2 region receipt has no valid source SHA-256")
    if expected_source_sha256 is not None:
        require(source_sha256 == expected_source_sha256,
                "BRAM G2 region receipt source does not match preflight")

    evidence = receipt.get("evidence", [])
    require(isinstance(evidence, list) and evidence,
            "BRAM G2 region receipt has no evidence")

    required = (
        "rle_region_features.v", "tb_region_candidate_filters.sv",
        "rle_feature_tap.v", "region_candidates.log", "region_cases.log",
        "region_diff.log", "region_limits.log", "region_driver.log",
        "region_inputs.sha256", "region_memory.rpt", "region_util.rpt",
        "region_elab_tap.log",
    )
    by_name: dict[str, list[tuple[Path, str]]] = {name: [] for name in required}
    for item in evidence:
        require(isinstance(item, dict) and isinstance(item.get("path"), str) and
                isinstance(item.get("sha256"), str) and
                re.fullmatch(r"[0-9a-fA-F]{64}", item["sha256"]),
                "BRAM G2 evidence entry has an invalid path or SHA-256")
        path = Path(item["path"]).resolve()
        if path.name in by_name:
            by_name[path.name].append((path, item["sha256"]))

    def one_evidence(name: str) -> tuple[Path, str]:
        matches = by_name[name]
        require(len(matches) == 1,
                f"BRAM G2 receipt must bind exactly one {name}")
        path, expected_sha = matches[0]
        require(sha(path) == expected_sha,
                f"BRAM G2 evidence hash mismatch: {name}")
        return path, expected_sha

    bound = {name: one_evidence(name) for name in required}
    source, source_evidence_sha = bound["rle_region_features.v"]
    require(source_evidence_sha == source_sha256,
            "BRAM G2 region source evidence hash mismatch")

    manifest_path, _ = bound["region_inputs.sha256"]
    manifest_hashes = {}
    for line in manifest_path.read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*", line)
        require(match is not None, "region input manifest has a malformed hash line")
        digest, name = match.groups()
        require(name not in manifest_hashes, f"duplicate input in region manifest: {name}")
        manifest_hashes[name] = digest.lower()
    for name in required[:3]:
        require(manifest_hashes.get(name) == bound[name][1].lower(),
                f"region input manifest hash mismatch: {name}")

    markers = {
        "region_candidates.log": r"REGION_CANDIDATE_PASS cases=7",
        "region_cases.log": r"RLE_FEATURE_PASS cases=12",
        "region_diff.log": r"REGION_DIFF_PASS compared=\d+",
        "region_limits.log": r"REGION_LIMITS_PASS row_runs=320 labels=512 overflow=513",
    }
    for name, marker in markers.items():
        text = bound[name][0].read_text(encoding="utf-8")
        require(re.search(rf"(?m)^{marker}$", text) is not None,
                f"region PASS marker missing or invalid: {name}")
    driver = bound["region_driver.log"][0].read_text(encoding="utf-8")
    driver_markers = {
        "region_candidates.log": "REGION_CANDIDATE_PASS cases=7",
        "region_cases.log": "RLE_FEATURE_PASS cases=12",
        "region_diff.log": "REGION_DIFF_PASS",
        "region_limits.log": "REGION_LIMITS_PASS",
    }
    require(re.search(r"(?m)^REGION_TAP_LINK_PASS$", driver) is not None and
            all(re.search(rf"(?m)^REGION_XSIM_PASS: {re.escape(name)} {re.escape(marker)}$", driver)
                is not None for name, marker in driver_markers.items()),
            "region driver log is missing an exact PASS marker")

    for name in ("region_candidates.log", "region_cases.log", "region_diff.log",
                 "region_limits.log", "region_driver.log", "region_elab_tap.log"):
        text = bound[name][0].read_text(encoding="utf-8")
        failure = re.search(
            r"^[ \t]*(?:Fatal:|ERROR:|REGION_XSIM_FAIL\b|REGION_BRAM_FAIL\b)|"
            r"^(?![ \t]*#).*watchdog timeout",
            text, re.IGNORECASE | re.MULTILINE)
        require(failure is None,
                f"region log contains a failure marker: {name}")
    require(bound["region_elab_tap.log"][0].stat().st_size > 0,
            "region tap elaboration log is empty")

    memory = bound["region_memory.rpt"][0].read_text(encoding="utf-8")
    for name, count in (("stats_bram", 4), ("row_bram", 1), ("stats_lutram", 0)):
        require(re.search(rf"(?m)^{name}={count}(?:\s|:|$)", memory) is not None,
                f"region memory report must contain {name}={count}")
    require(bound["region_util.rpt"][0].stat().st_size > 0,
            "region utilization report is empty")


def _check_g2_region_receipt(receipt: dict,
                             expected_source_sha256: str | None = None) -> None:
    """Accept current RLE RTL only with source-bound XSim and routed G2 evidence."""
    require(receipt.get("status") == "PASS", "current G2 region receipt is not PASS")
    source_sha256 = receipt.get("source_sha256")
    require(isinstance(source_sha256, str) and re.fullmatch(r"[0-9a-fA-F]{64}", source_sha256),
            "current G2 region receipt has no valid source SHA-256")
    if expected_source_sha256 is not None:
        require(source_sha256 == expected_source_sha256,
                "current G2 region receipt source does not match preflight")

    checks = receipt.get("checks", {})
    require(all(checks.get(name) is True for name in (
        "xsim_expected_outputs", "limits_and_candidate_thresholds",
        "g2_integrated_build_reviewed")),
        "current G2 region checks are not all reviewed PASS")

    evidence = receipt.get("evidence", [])
    require(isinstance(evidence, list) and evidence,
            "current G2 region receipt has no evidence")

    def one_evidence(name: str) -> Path:
        matches = [Path(item["path"]).resolve() for item in evidence
                   if isinstance(item, dict) and isinstance(item.get("path"), str)
                   and Path(item["path"]).name == name]
        require(len(matches) == 1,
                f"current G2 region receipt must bind exactly one {name}")
        return matches[0]

    source = one_evidence("rle_region_features.v")
    require(sha(source) == source_sha256,
            "current G2 region source evidence hash mismatch")
    one_evidence("tb_rle_region_features.sv")
    xsim = one_evidence("tb_rle_region_features.xsim.log")
    require("RLE_FEATURE_PASS cases=16" in xsim.read_text(encoding="utf-8"),
            "current G2 region XSim evidence did not pass all 16 cases")

    build_manifest_path = one_evidence("build-manifest.json")
    build = read_json(build_manifest_path)
    require(build.get("schema") == "material-counter.g2-sw1-candidate.v1" and
            build.get("rtl_sha256", {}).get("rle_region_features.v") == source_sha256,
            "current G2 build manifest does not bind this region source")

    timing = build.get("timing", {})
    require(timing.get("all_user_constraints_met") is True and
            isinstance(timing.get("wns_ns"), (int, float)) and timing["wns_ns"] >= 0 and
            isinstance(timing.get("whs_ns"), (int, float)) and timing["whs_ns"] >= 0 and
            timing.get("unrouted_nets") == 0 and timing.get("partially_routed_nets") == 0,
            "current G2 region source has failing routed timing or unrouted nets")
    drc = build.get("drc", {})
    require(drc.get("errors") == 0 and drc.get("critical_warnings") == 0,
            "current G2 region source has DRC errors or Critical Warnings")
    cdc = build.get("cdc", {})
    require(isinstance(cdc.get("review"), str) and
            cdc["review"].startswith("manual_review_complete_") and
            isinstance(cdc.get("review_record"), str),
            "current G2 region source has no completed CDC review")
    review_path = (build_manifest_path.parent / cdc["review_record"]).resolve()
    require(review_path == one_evidence("cdc-review.md"),
            "current G2 CDC review record is missing from region evidence")
    for name in ("cdc.rpt", "timing.rpt", "utilization.rpt", "drc.rpt"):
        report = one_evidence(name)
        require(report.stat().st_size > 0,
                f"current G2 region report is empty: {name}")
    utilization = one_evidence("utilization.rpt").read_text(encoding="utf-8")
    require("RAMB36" in utilization and "LUT as Logic" in utilization,
            "current G2 region utilization report is incomplete")


def check_region_receipt(receipt: dict,
                         expected_source_sha256: str | None = None) -> None:
    """Validate either the legacy BRAM proof or current routed-G2 region proof."""
    if receipt.get("schema") == "material-counter.rle-region-bram-g2-green.v1":
        _check_bram_g2_region_receipt(receipt, expected_source_sha256)
        return
    if receipt.get("schema") == "material-counter.rle-region-g2-green.v1":
        _check_g2_region_receipt(receipt, expected_source_sha256)
        return
    memory = receipt.get("memory_gate", {})
    require(receipt.get("reference_diff") == "PASS" and receipt.get("limits") == "PASS" and
            isinstance(memory.get("stats_bram36"), int) and memory["stats_bram36"] > 0 and
            isinstance(memory.get("row_bram36"), int) and memory["row_bram36"] > 0 and
            memory.get("stats_lutram") == 0,
            "region reference_diff/limits/memory_gate BRAM evidence is not PASS")


def check_region_source(path: Path, receipt_schema: str | None = None) -> None:
    """Check the selected RTL shape for its explicitly declared evidence schema."""
    source = path.read_text(encoding="utf-8")
    require("module rle_region_features" in source,
            "region source does not define rle_region_features")
    if receipt_schema == "material-counter.rle-region-bram-g2-green.v1":
        header = re.search(r"module\s+rle_region_features\b([\s\S]*?);", source)
        require(header is not None and all(re.search(rf"\b{port}\b", header.group(1))
                                           for port in (
            "candidate_flag", "candidate_reason", "cfg_max_area", "cfg_max_w",
            "cfg_max_h", "cfg_aspect_x16")),
            "BRAM G2 region source is missing candidate configuration ports")
        require(all(name in source for name in ("stats_mem", "row_mem", "ram_style")),
                "BRAM G2 region source is missing BRAM memory structures")
    elif receipt_schema == "material-counter.rle-region-g2-green.v1":
        require(all(port in source for port in (
            "candidate_flag", "candidate_reason", "cfg_max_area", "cfg_max_w",
            "cfg_max_h", "cfg_aspect_x16")),
            "current G2 region source is missing candidate configuration ports")
    else:
        require("stats_mem" in source and "ram_style" in source,
                "region source does not match the expected BRAM module")


def tcl_scalar(value) -> str:
    value = str(value).replace("\\", "/")
    require(not any(c in value for c in "{}\n\r"), "unsupported Tcl data character")
    return "{" + value + "}"


def verify_manifest(path: Path) -> None:
    manifest = read_json(path)
    for item in manifest["immutable_inputs"] + manifest["snapshot_sources"]:
        require(sha(Path(item["path"])) == item["sha256"], f"joint input changed: {item['path']}")
    verify_overlay_binding(path, manifest)
    verify_overlay_wrapper_binding(path, manifest)
    verify_frame_monitor_binding(path, manifest)
    verify_timing_xdc_binding(path, manifest)
    verify_offload_cdc_binding(path, manifest)
    print("JOINT_SOURCE_HASHES_OK")


def resolve_overlay_override(candidate_path: Path | None, baseline: Path,
                             pinned_sources: list[dict], project_sources: list[dict]) -> dict | None:
    """Bind one optional overlay candidate to the fixed, pinned project RTL."""
    if candidate_path is None:
        return None

    candidate_arg = Path(candidate_path)
    require(not candidate_arg.is_symlink(), "overlay RTL override must be an ordinary file")
    candidate = candidate_arg.resolve(strict=True)
    require(candidate.is_file(), "overlay RTL override must be an ordinary file")
    baseline = baseline.resolve()
    require(not candidate.is_relative_to(baseline), "overlay RTL override must be outside the fixed snapshot")
    require(re.search(r"\bmodule\s+video_binary_overlay\b",
                      candidate.read_text(encoding="utf-8")) is not None,
            "overlay RTL override must declare module video_binary_overlay")

    def matches_target(items: list[dict], relative_path: Path) -> list[dict]:
        matches = []
        for item in items:
            source = Path(item["path"]).resolve()
            try:
                relative = source.relative_to(baseline)
            except ValueError:
                continue
            if relative.as_posix() == relative_path.as_posix():
                matches.append(item)
        return matches

    pinned = matches_target(pinned_sources, OVERLAY_PINNED_REFERENCE_REL)
    project = matches_target(project_sources, OVERLAY_SOURCE_REL)
    require(len(pinned) == 1, "standalone overlay reference is not uniquely pinned")
    require(len(project) == 1, "fixed XPR overlay RTL is not a unique project source")
    source_path = Path(project[0]["path"]).resolve()
    pinned_path = Path(pinned[0]["path"]).resolve()
    require(source_path.is_file() and pinned_path.is_file() and
            sha(source_path) == project[0]["sha256"] == pinned[0]["sha256"] == sha(pinned_path),
            "fixed XPR overlay RTL does not match its pinned standalone reference")
    return {"source": project[0], "pinned_reference": pinned[0], "candidate": entry(candidate),
            "target_relative_path": OVERLAY_SOURCE_REL.as_posix()}


def verify_overlay_binding(path: Path, manifest: dict) -> None:
    """Check that an overlay manifest entry binds the candidate to its staged source."""
    if "overlay_replacements" not in manifest:
        return
    replacements = manifest["overlay_replacements"]
    require(isinstance(replacements, list) and len(replacements) == 1,
            "overlay replacement manifest must contain exactly one source")
    replacement = replacements[0]
    require(replacement.get("target_relative_path") == OVERLAY_SOURCE_REL.as_posix(),
            "overlay replacement target changed")
    source = replacement.get("source", {})
    pinned_reference = replacement.get("pinned_reference", {})
    candidate = replacement.get("candidate", {})
    staged = replacement.get("replacement", {})
    preflight = manifest.get("preflight", {})
    baseline = Path(preflight.get("snapshot", "")).resolve()
    source_path = Path(source.get("path", "")).resolve()
    try:
        source_relative = source_path.relative_to(baseline)
    except ValueError as error:
        raise ValueError("overlay replacement source is outside the fixed snapshot") from error
    require(source_relative.as_posix() == OVERLAY_SOURCE_REL.as_posix(),
            "overlay replacement source changed")
    pinned_reference_path = Path(pinned_reference.get("path", "")).resolve()
    try:
        pinned_relative = pinned_reference_path.relative_to(baseline)
    except ValueError as error:
        raise ValueError("overlay pinned reference is outside the fixed snapshot") from error
    require(pinned_relative.as_posix() == OVERLAY_PINNED_REFERENCE_REL.as_posix(),
            "overlay pinned reference changed")
    pinned = [item for item in preflight.get("baseline_pinned_sources", [])
              if Path(item.get("path", "")).resolve() == pinned_reference_path]
    project = [item for item in preflight.get("project_sources", [])
               if Path(item.get("path", "")).resolve() == source_path]
    require(len(pinned) == 1 and len(project) == 1 and
            pinned[0].get("sha256") == pinned_reference.get("sha256") and
            source.get("sha256") == project[0].get("sha256") == pinned_reference.get("sha256"),
            "overlay replacement source does not match its unique pinned project reference")

    candidate_path = Path(candidate.get("path", "")).resolve()
    staged_path = Path(staged.get("path", "")).resolve()
    expected_staged = (path.parent / "snapshot" / OVERLAY_SOURCE_REL).resolve()
    require(staged_path == expected_staged,
            "overlay replacement is not bound to the fixed staged project path")
    immutable = [item for item in manifest.get("immutable_inputs", [])
                 if Path(item.get("path", "")).resolve() == candidate_path]
    staged_sources = [item for item in manifest.get("snapshot_sources", [])
                      if Path(item.get("path", "")).resolve() == staged_path]
    require(len(immutable) == 1 and len(staged_sources) == 1 and
            immutable[0].get("sha256") == candidate.get("sha256") and
            staged_sources[0].get("sha256") == staged.get("sha256"),
            "overlay replacement candidate or staged source is absent from hash manifest")
    require(candidate.get("sha256") == staged.get("sha256"),
            "overlay replacement hash differs from the immutable candidate")
    require(re.search(r"\bmodule\s+video_binary_overlay\b",
                      Path(candidate_path).read_text(encoding="utf-8")) is not None and
            re.search(r"\bmodule\s+video_binary_overlay\b",
                      Path(staged_path).read_text(encoding="utf-8")) is not None,
            "overlay replacement candidate must declare module video_binary_overlay")
    required_live = {Path(item).resolve() for item in manifest.get("required_live_sources", [])}
    require(staged_path in required_live,
            "overlay replacement is missing from required live project sources")


def resolve_overlay_wrapper_override(candidate_path: Path | None, baseline: Path,
                                     pinned_sources: list[dict], project_sources: list[dict]) -> dict | None:
    """Bind one optional AXI-Lite wrapper candidate to its pinned project source."""
    if candidate_path is None:
        return None
    candidate_arg = Path(candidate_path)
    require(not candidate_arg.is_symlink(), "overlay wrapper override must be an ordinary file")
    candidate = candidate_arg.resolve(strict=True)
    require(candidate.is_file(), "overlay wrapper override must be an ordinary file")
    baseline = baseline.resolve()
    require(not candidate.is_relative_to(baseline), "overlay wrapper override must be outside the fixed snapshot")
    require(re.search(r"\bmodule\s+video_binary_overlay_bd\b",
                      candidate.read_text(encoding="utf-8")) is not None,
            "overlay wrapper override must declare module video_binary_overlay_bd")

    def matches_target(items: list[dict], relative_path: Path) -> list[dict]:
        matches = []
        for item in items:
            source = Path(item["path"]).resolve()
            try:
                relative = source.relative_to(baseline)
            except ValueError:
                continue
            if relative.as_posix() == relative_path.as_posix():
                matches.append(item)
        return matches

    pinned = matches_target(pinned_sources, OVERLAY_WRAPPER_PINNED_REFERENCE_REL)
    project = matches_target(project_sources, OVERLAY_WRAPPER_SOURCE_REL)
    require(len(pinned) == 1, "standalone overlay wrapper reference is not uniquely pinned")
    require(len(project) == 1, "fixed XPR overlay wrapper is not a unique project source")
    source_path = Path(project[0]["path"]).resolve()
    pinned_path = Path(pinned[0]["path"]).resolve()
    require(source_path.is_file() and pinned_path.is_file() and
            sha(source_path) == project[0]["sha256"] == pinned[0]["sha256"] == sha(pinned_path),
            "fixed XPR overlay wrapper does not match its pinned standalone reference")
    return {"source": project[0], "pinned_reference": pinned[0], "candidate": entry(candidate),
            "target_relative_path": OVERLAY_WRAPPER_SOURCE_REL.as_posix()}


def verify_overlay_wrapper_binding(path: Path, manifest: dict) -> None:
    """Check staged wrapper identity and its immutable source/reference binding."""
    if "overlay_wrapper_replacements" not in manifest:
        return
    replacements = manifest["overlay_wrapper_replacements"]
    require(isinstance(replacements, list) and len(replacements) == 1,
            "overlay wrapper replacement manifest must contain exactly one source")
    replacement = replacements[0]
    require(replacement.get("target_relative_path") == OVERLAY_WRAPPER_SOURCE_REL.as_posix(),
            "overlay wrapper replacement target changed")
    source = replacement.get("source", {})
    pinned_reference = replacement.get("pinned_reference", {})
    candidate = replacement.get("candidate", {})
    staged = replacement.get("replacement", {})
    preflight = manifest.get("preflight", {})
    baseline = Path(preflight.get("snapshot", "")).resolve()
    source_path = Path(source.get("path", "")).resolve()
    try:
        source_relative = source_path.relative_to(baseline)
    except ValueError as error:
        raise ValueError("overlay wrapper source is outside the fixed snapshot") from error
    require(source_relative.as_posix() == OVERLAY_WRAPPER_SOURCE_REL.as_posix(),
            "overlay wrapper source changed")
    pinned_path = Path(pinned_reference.get("path", "")).resolve()
    try:
        pinned_relative = pinned_path.relative_to(baseline)
    except ValueError as error:
        raise ValueError("overlay wrapper reference is outside the fixed snapshot") from error
    require(pinned_relative.as_posix() == OVERLAY_WRAPPER_PINNED_REFERENCE_REL.as_posix(),
            "overlay wrapper pinned reference changed")
    pinned = [item for item in preflight.get("baseline_pinned_sources", [])
              if Path(item.get("path", "")).resolve() == pinned_path]
    project = [item for item in preflight.get("project_sources", [])
               if Path(item.get("path", "")).resolve() == source_path]
    require(len(pinned) == 1 and len(project) == 1 and
            pinned[0].get("sha256") == pinned_reference.get("sha256") and
            source.get("sha256") == project[0].get("sha256") == pinned_reference.get("sha256"),
            "overlay wrapper source does not match its unique pinned reference")
    candidate_path = Path(candidate.get("path", "")).resolve()
    staged_path = Path(staged.get("path", "")).resolve()
    expected_staged = (path.parent / "snapshot" / OVERLAY_WRAPPER_SOURCE_REL).resolve()
    require(staged_path == expected_staged,
            "overlay wrapper replacement is not bound to the fixed staged project path")
    immutable = [item for item in manifest.get("immutable_inputs", [])
                 if Path(item.get("path", "")).resolve() == candidate_path]
    staged_sources = [item for item in manifest.get("snapshot_sources", [])
                      if Path(item.get("path", "")).resolve() == staged_path]
    require(len(immutable) == 1 and len(staged_sources) == 1 and
            immutable[0].get("sha256") == candidate.get("sha256") and
            staged_sources[0].get("sha256") == staged.get("sha256"),
            "overlay wrapper candidate or staged source is absent from hash manifest")
    require(candidate.get("sha256") == staged.get("sha256"),
            "overlay wrapper replacement hash differs from the immutable candidate")
    for source_file in (candidate_path, staged_path):
        require(re.search(r"\bmodule\s+video_binary_overlay_bd\b",
                          source_file.read_text(encoding="utf-8")) is not None,
                "overlay wrapper replacement must declare module video_binary_overlay_bd")
    required_live = {Path(item).resolve() for item in manifest.get("required_live_sources", [])}
    require(staged_path in required_live,
            "overlay wrapper replacement is missing from required live project sources")


def resolve_frame_monitor_override(candidate_path: Path | None, baseline: Path,
                                   pinned_sources: list[dict], project_sources: list[dict]) -> dict | None:
    """Bind one optional frame-monitor candidate to its pinned project source."""
    if candidate_path is None:
        return None
    candidate_arg = Path(candidate_path)
    require(not candidate_arg.is_symlink(), "frame monitor RTL override must be an ordinary file")
    candidate = candidate_arg.resolve(strict=True)
    require(candidate.is_file(), "frame monitor RTL override must be an ordinary file")
    baseline = baseline.resolve()
    require(not candidate.is_relative_to(baseline), "frame monitor RTL override must be outside the fixed snapshot")
    require(re.search(r"\bmodule\s+video_frame_monitor\b",
                      candidate.read_text(encoding="utf-8")) is not None,
            "frame monitor RTL override must declare module video_frame_monitor")

    def matches_target(items: list[dict]) -> list[dict]:
        matches = []
        for item in items:
            source = Path(item["path"]).resolve()
            try:
                relative = source.relative_to(baseline)
            except ValueError:
                continue
            if relative.as_posix() == FRAME_MONITOR_SOURCE_REL.as_posix():
                matches.append(item)
        return matches

    pinned = matches_target(pinned_sources)
    project = matches_target(project_sources)
    require(len(pinned) == 1, "standalone frame monitor reference is not uniquely pinned")
    require(len(project) == 1, "fixed XPR frame monitor is not a unique project source")
    source_path = Path(project[0]["path"]).resolve()
    pinned_path = Path(pinned[0]["path"]).resolve()
    require(source_path.is_file() and pinned_path.is_file() and
            sha(source_path) == project[0]["sha256"] == pinned[0]["sha256"] == sha(pinned_path),
            "fixed XPR frame monitor does not match its pinned standalone reference")
    return {"source": project[0], "pinned_reference": pinned[0], "candidate": entry(candidate),
            "target_relative_path": FRAME_MONITOR_SOURCE_REL.as_posix()}


def verify_frame_monitor_binding(path: Path, manifest: dict) -> None:
    """Check that the staged frame monitor matches its immutable candidate and pinned source."""
    if "frame_monitor_replacements" not in manifest:
        return
    replacements = manifest["frame_monitor_replacements"]
    require(isinstance(replacements, list) and len(replacements) == 1,
            "frame monitor replacement manifest must contain exactly one source")
    replacement = replacements[0]
    require(replacement.get("target_relative_path") == FRAME_MONITOR_SOURCE_REL.as_posix(),
            "frame monitor replacement target changed")
    source = replacement.get("source", {})
    pinned_reference = replacement.get("pinned_reference", {})
    candidate = replacement.get("candidate", {})
    staged = replacement.get("replacement", {})
    preflight = manifest.get("preflight", {})
    baseline = Path(preflight.get("snapshot", "")).resolve()
    source_path = Path(source.get("path", "")).resolve()
    pinned_path = Path(pinned_reference.get("path", "")).resolve()
    for bound_path, expected, message in (
        (source_path, FRAME_MONITOR_SOURCE_REL, "frame monitor source is outside the fixed snapshot"),
        (pinned_path, FRAME_MONITOR_PINNED_REFERENCE_REL, "frame monitor reference is outside the fixed snapshot"),
    ):
        try:
            relative = bound_path.relative_to(baseline)
        except ValueError as error:
            raise ValueError(message) from error
        require(relative.as_posix() == expected.as_posix(), "frame monitor source/reference path changed")
    pinned = [item for item in preflight.get("baseline_pinned_sources", [])
              if Path(item.get("path", "")).resolve() == pinned_path]
    project = [item for item in preflight.get("project_sources", [])
               if Path(item.get("path", "")).resolve() == source_path]
    require(len(pinned) == 1 and len(project) == 1 and
            pinned[0].get("sha256") == pinned_reference.get("sha256") and
            source.get("sha256") == project[0].get("sha256") == pinned_reference.get("sha256"),
            "frame monitor source does not match its unique pinned project reference")
    candidate_path = Path(candidate.get("path", "")).resolve()
    staged_path = Path(staged.get("path", "")).resolve()
    expected_staged = (path.parent / "snapshot" / FRAME_MONITOR_SOURCE_REL).resolve()
    require(staged_path == expected_staged,
            "frame monitor replacement is not bound to the fixed staged project path")
    immutable = [item for item in manifest.get("immutable_inputs", [])
                 if Path(item.get("path", "")).resolve() == candidate_path]
    staged_sources = [item for item in manifest.get("snapshot_sources", [])
                      if Path(item.get("path", "")).resolve() == staged_path]
    require(len(immutable) == 1 and len(staged_sources) == 1 and
            immutable[0].get("sha256") == candidate.get("sha256") and
            staged_sources[0].get("sha256") == staged.get("sha256"),
            "frame monitor candidate or staged source is absent from hash manifest")
    require(candidate.get("sha256") == staged.get("sha256"),
            "frame monitor replacement hash differs from the immutable candidate")
    for source_file in (candidate_path, staged_path):
        require(re.search(r"\bmodule\s+video_frame_monitor\b",
                          source_file.read_text(encoding="utf-8")) is not None,
                "frame monitor replacement must declare module video_frame_monitor")
    required_live = {Path(item).resolve() for item in manifest.get("required_live_sources", [])}
    require(staged_path in required_live,
            "frame monitor replacement is missing from required live project sources")


def _validate_timing_xdc(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    active = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    require(re.search(r"\bcreate_generated_clock\b", active, re.IGNORECASE) is None,
            "display timing override must not declare another generated clock")
    require(re.search(r"\bdelete_clocks\b", active, re.IGNORECASE) is None,
            "display timing override must leave clock-file selection to project Tcl")
    require("btns_4bits_tri_i" in active and "hdmi_out_hpd" in active,
            "display timing override must retain the button and HPD exceptions")


def resolve_timing_xdc_override(candidate_path: Path | None, baseline: Path,
                                pinned_sources: list[dict]) -> dict | None:
    """Bind a timing candidate that leaves duplicate-clock gating to project Tcl."""
    if candidate_path is None:
        return None
    candidate_arg = Path(candidate_path)
    require(not candidate_arg.is_symlink(), "timing XDC override must be an ordinary file")
    candidate = candidate_arg.resolve(strict=True)
    require(candidate.is_file(), "timing XDC override must be an ordinary file")
    baseline = baseline.resolve()
    require(not candidate.is_relative_to(baseline), "timing XDC override must be outside the fixed snapshot")
    _validate_timing_xdc(candidate)
    matches = []
    for item in pinned_sources:
        source = Path(item["path"]).resolve()
        try:
            relative = source.relative_to(baseline)
        except ValueError:
            continue
        if relative.as_posix() == TIMING_XDC_REL.as_posix():
            matches.append(item)
    require(len(matches) == 1, "fixed timing XDC is not uniquely pinned")
    source = Path(matches[0]["path"]).resolve()
    require(source.is_file() and sha(source) == matches[0]["sha256"],
            "fixed timing XDC differs from its pinned hash")
    return {"source": matches[0], "candidate": entry(candidate),
            "target_relative_path": TIMING_XDC_REL.as_posix()}


def verify_timing_xdc_binding(path: Path, manifest: dict) -> None:
    """Check that the staged timing XDC is the reviewed immutable candidate."""
    if "timing_xdc_replacements" not in manifest:
        return
    replacements = manifest["timing_xdc_replacements"]
    require(isinstance(replacements, list) and len(replacements) == 1,
            "timing XDC replacement manifest must contain exactly one source")
    replacement = replacements[0]
    require(replacement.get("target_relative_path") == TIMING_XDC_REL.as_posix(),
            "timing XDC replacement target changed")
    source = replacement.get("source", {})
    candidate = replacement.get("candidate", {})
    staged = replacement.get("replacement", {})
    baseline = Path(manifest.get("preflight", {}).get("snapshot", "")).resolve()
    source_path = Path(source.get("path", "")).resolve()
    try:
        relative = source_path.relative_to(baseline)
    except ValueError as error:
        raise ValueError("timing XDC source is outside the fixed snapshot") from error
    require(relative.as_posix() == TIMING_XDC_REL.as_posix(), "timing XDC source changed")
    pinned = [item for item in manifest.get("preflight", {}).get("baseline_pinned_sources", [])
              if Path(item.get("path", "")).resolve() == source_path]
    require(len(pinned) == 1 and pinned[0].get("sha256") == source.get("sha256"),
            "timing XDC source is not pinned by preflight")
    candidate_path = Path(candidate.get("path", "")).resolve()
    staged_path = Path(staged.get("path", "")).resolve()
    expected_staged = (path.parent / "snapshot" / TIMING_XDC_REL).resolve()
    require(staged_path == expected_staged, "timing XDC replacement staged at an unexpected path")
    immutable = [item for item in manifest.get("immutable_inputs", [])
                 if Path(item.get("path", "")).resolve() == candidate_path]
    staged_sources = [item for item in manifest.get("snapshot_sources", [])
                      if Path(item.get("path", "")).resolve() == staged_path]
    require(len(immutable) == 1 and len(staged_sources) == 1 and
            immutable[0].get("sha256") == candidate.get("sha256") == staged.get("sha256") ==
            staged_sources[0].get("sha256"),
            "timing XDC candidate or staged source hash mismatch")
    _validate_timing_xdc(candidate_path)
    _validate_timing_xdc(staged_path)
    require(staged_path in {Path(item).resolve() for item in manifest.get("required_live_sources", [])},
            "timing XDC replacement is missing from required live project sources")


def _validate_offload_cdc_xdc(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    active = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    require(active.count("set_max_delay -datapath_only 10") == 2,
            "offload CDC XDC must constrain only the two reviewed bundled-data paths")
    require(re.search(r"_reg\.\*", active) is None,
            "offload CDC XDC must not use wildcard suffixes after register names")
    require(active.count(r"_reg(\[[0-9]+\])?$") == 4,
            "offload CDC XDC must anchor source and destination collections to register cells")
    require("publish_sync1|ack_sync1|cfg_ack_s1|cfg_req_s1" in active,
            "offload CDC XDC must retain the reviewed single-bit synchronizer cuts")


def resolve_offload_cdc_override(candidate_path: Path | None, baseline: Path,
                                 pinned_sources: list[dict]) -> dict | None:
    """Bind the reviewed sequential-endpoint CDC XDC to the fixed snapshot."""
    if candidate_path is None:
        return None
    candidate_arg = Path(candidate_path)
    require(not candidate_arg.is_symlink(), "offload CDC XDC override must be an ordinary file")
    candidate = candidate_arg.resolve(strict=True)
    require(candidate.is_file(), "offload CDC XDC override must be an ordinary file")
    baseline = baseline.resolve()
    require(not candidate.is_relative_to(baseline), "offload CDC XDC override must be outside the fixed snapshot")
    _validate_offload_cdc_xdc(candidate)
    matches = [item for item in pinned_sources
               if Path(item["path"]).resolve().is_relative_to(baseline)
               and Path(item["path"]).resolve().relative_to(baseline).as_posix() == OFFLOAD_CDC_XDC_REL.as_posix()]
    require(len(matches) == 1, "fixed offload CDC XDC is not uniquely pinned")
    source = Path(matches[0]["path"]).resolve()
    require(source.is_file() and sha(source) == matches[0]["sha256"],
            "fixed offload CDC XDC differs from its pinned hash")
    return {"source": matches[0], "candidate": entry(candidate),
            "target_relative_path": OFFLOAD_CDC_XDC_REL.as_posix()}


def verify_offload_cdc_binding(path: Path, manifest: dict) -> None:
    """Verify the offload CDC candidate, fixed source and staged XDC hashes."""
    if "offload_cdc_replacements" not in manifest:
        return
    replacements = manifest["offload_cdc_replacements"]
    require(isinstance(replacements, list) and len(replacements) == 1,
            "offload CDC replacement manifest must contain exactly one source")
    replacement = replacements[0]
    require(replacement.get("target_relative_path") == OFFLOAD_CDC_XDC_REL.as_posix(),
            "offload CDC replacement target changed")
    source, candidate, staged = (replacement.get(key, {})
                                 for key in ("source", "candidate", "replacement"))
    baseline = Path(manifest.get("preflight", {}).get("snapshot", "")).resolve()
    source_path = Path(source.get("path", "")).resolve()
    try:
        relative = source_path.relative_to(baseline)
    except ValueError as error:
        raise ValueError("offload CDC XDC source is outside the fixed snapshot") from error
    require(relative.as_posix() == OFFLOAD_CDC_XDC_REL.as_posix(),
            "offload CDC XDC source changed")
    pinned = [item for item in manifest.get("preflight", {}).get("baseline_pinned_sources", [])
              if Path(item.get("path", "")).resolve() == source_path]
    require(len(pinned) == 1 and pinned[0].get("sha256") == source.get("sha256"),
            "offload CDC XDC source is not pinned by preflight")
    candidate_path = Path(candidate.get("path", "")).resolve()
    staged_path = Path(staged.get("path", "")).resolve()
    expected_staged = (path.parent / "snapshot" / OFFLOAD_CDC_XDC_REL).resolve()
    require(staged_path == expected_staged,
            "offload CDC XDC replacement staged at an unexpected path")
    immutable = [item for item in manifest.get("immutable_inputs", [])
                 if Path(item.get("path", "")).resolve() == candidate_path]
    snapshot = [item for item in manifest.get("snapshot_sources", [])
                if Path(item.get("path", "")).resolve() == staged_path]
    require(len(immutable) == 1 and len(snapshot) == 1 and
            immutable[0].get("sha256") == candidate.get("sha256") == staged.get("sha256") ==
            snapshot[0].get("sha256"),
            "offload CDC candidate or staged source hash mismatch")
    _validate_offload_cdc_xdc(candidate_path)
    _validate_offload_cdc_xdc(staged_path)
    require(staged_path in {Path(item).resolve() for item in manifest.get("required_live_sources", [])},
            "offload CDC replacement is missing from required live project sources")


def register_timing_xdc_in_project(project: ET.ElementTree, timing_xdc: Path) -> None:
    """Register a staged XDC before Vivado's live-file audit."""
    target = Path(timing_xdc).absolute()
    require(target.is_file(), "staged timing XDC is missing")
    target_key = target.resolve().as_posix().casefold()
    filesets = project.findall(".//FileSet[@Name='constrs_1']")
    require(len(filesets) == 1, "XPR must contain exactly one constrs_1 fileset")
    all_matches = []
    for node in project.findall(".//File"):
        raw = node.attrib.get("Path", "")
        try:
            same_file = Path(raw).resolve().as_posix().casefold() == target_key
        except (OSError, ValueError):
            same_file = False
        if same_file:
            all_matches.append(node)
    require(len(all_matches) <= 1, "timing XDC is duplicated in the project")
    if all_matches:
        require(all_matches[0] in filesets[0].findall("File"),
                "timing XDC is registered outside the constrs_1 fileset")
        file_node = all_matches[0]
    else:
        file_node = ET.Element("File", {"Path": target.as_posix()})
        config_positions = [index for index, child in enumerate(filesets[0])
                            if child.tag == "Config"]
        filesets[0].insert(min(config_positions) if config_positions else len(filesets[0]), file_node)
    info = file_node.find("FileInfo")
    if info is None:
        info = ET.SubElement(file_node, "FileInfo")
    used_in = {node.attrib.get("Val") for node in info.findall("Attr")
               if node.attrib.get("Name") == "UsedIn"}
    for purpose in ("synthesis", "implementation"):
        if purpose not in used_in:
            ET.SubElement(info, "Attr", {"Name": "UsedIn", "Val": purpose})


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
    check_region_receipt(region, manifest["preflight"]["region"]["sha256"])
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
        check_region_receipt(read_json(args.region_green), result["region"]["sha256"])
    if hls_receipt:
        require(read_json(args.hls_green).get("component_sha256") == result["hls"]["component"]["sha256"], "HLS receipt component hash mismatch")
    # Keep a caller-provided short Windows alias (for example a SUBST drive)
    # for paths Vivado will create, while resolving the same location for
    # containment and overwrite checks.
    out = args.stage.absolute()
    physical_out = args.stage.resolve()
    baseline = Path(result["snapshot"]).resolve()
    overlay_binding = resolve_overlay_override(
        getattr(args, "overlay_rtl", None), baseline,
        result["baseline_pinned_sources"], result["project_sources"])
    overlay_wrapper_binding = resolve_overlay_wrapper_override(
        getattr(args, "overlay_wrapper_rtl", None), baseline,
        result["baseline_pinned_sources"], result["project_sources"])
    frame_monitor_binding = resolve_frame_monitor_override(
        getattr(args, "frame_monitor_rtl", None), baseline,
        result["baseline_pinned_sources"], result["project_sources"])
    timing_xdc_binding = resolve_timing_xdc_override(
        getattr(args, "timing_xdc", None), baseline, result["baseline_pinned_sources"])
    offload_cdc_binding = resolve_offload_cdc_override(
        getattr(args, "offload_cdc_xdc", None), baseline, result["baseline_pinned_sources"])
    require(not physical_out.exists() and not physical_out.is_relative_to(baseline) and
            not baseline.is_relative_to(physical_out), "use a fresh, separate build directory")
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
    overlay_replacements = []
    overlay_wrapper_replacements = []
    frame_monitor_replacements = []
    timing_xdc_replacements = []
    offload_cdc_replacements = []
    timing_xdc_target = None
    offload_cdc_target = None
    snapshot_sources = []
    for source in sorted(selected):
        target = staged / source.relative_to(baseline)
        target.parent.mkdir(parents=True, exist_ok=True)
        if overlay_binding and source.resolve() == Path(overlay_binding["source"]["path"]).resolve():
            candidate = Path(overlay_binding["candidate"]["path"])
            require(sha(candidate) == overlay_binding["candidate"]["sha256"],
                    "overlay RTL override changed after validation")
            shutil.copy2(candidate, target)
            replacement = entry(target)
            require(replacement["sha256"] == overlay_binding["candidate"]["sha256"],
                    "staged overlay source hash differs from the immutable candidate")
            overlay_replacements.append({"source": overlay_binding["source"],
                                         "pinned_reference": overlay_binding["pinned_reference"],
                                         "replacement": replacement,
                                         "candidate": overlay_binding["candidate"],
                                         "target_relative_path": overlay_binding["target_relative_path"],
                                         "reason": "Manager-selected pixel-domain timing fix"})
        elif overlay_wrapper_binding and source.resolve() == Path(overlay_wrapper_binding["source"]["path"]).resolve():
            candidate = Path(overlay_wrapper_binding["candidate"]["path"])
            require(sha(candidate) == overlay_wrapper_binding["candidate"]["sha256"],
                    "overlay wrapper RTL override changed after validation")
            shutil.copy2(candidate, target)
            replacement = entry(target)
            require(replacement["sha256"] == overlay_wrapper_binding["candidate"]["sha256"],
                    "staged overlay wrapper hash differs from the immutable candidate")
            overlay_wrapper_replacements.append({"source": overlay_wrapper_binding["source"],
                "pinned_reference": overlay_wrapper_binding["pinned_reference"],
                "replacement": replacement, "candidate": overlay_wrapper_binding["candidate"],
                "target_relative_path": overlay_wrapper_binding["target_relative_path"],
                "reason": "Manager-selected count-glyph AXI-Lite ABI"})
        elif frame_monitor_binding and source.resolve() == Path(frame_monitor_binding["source"]["path"]).resolve():
            candidate = Path(frame_monitor_binding["candidate"]["path"])
            require(sha(candidate) == frame_monitor_binding["candidate"]["sha256"],
                    "frame monitor RTL override changed after validation")
            shutil.copy2(candidate, target)
            replacement = entry(target)
            require(replacement["sha256"] == frame_monitor_binding["candidate"]["sha256"],
                    "staged frame monitor hash differs from the immutable candidate")
            frame_monitor_replacements.append({"source": frame_monitor_binding["source"],
                "pinned_reference": frame_monitor_binding["pinned_reference"],
                "replacement": replacement, "candidate": frame_monitor_binding["candidate"],
                "target_relative_path": frame_monitor_binding["target_relative_path"],
                "reason": "Manager-selected frame-boundary timing pipeline"})
        elif timing_xdc_binding and source.resolve() == Path(timing_xdc_binding["source"]["path"]).resolve():
            candidate = Path(timing_xdc_binding["candidate"]["path"])
            require(sha(candidate) == timing_xdc_binding["candidate"]["sha256"],
                    "timing XDC override changed after validation")
            shutil.copy2(candidate, target)
            replacement = entry(target)
            require(replacement["sha256"] == timing_xdc_binding["candidate"]["sha256"],
                    "staged timing XDC hash differs from the immutable candidate")
            timing_xdc_target = target
            timing_xdc_replacements.append({"source": timing_xdc_binding["source"],
                "replacement": replacement, "candidate": timing_xdc_binding["candidate"],
                "target_relative_path": timing_xdc_binding["target_relative_path"],
                "reason": "Remove rgb2dvi's duplicate SerialClk and retain real MMCM propagation"})
        elif offload_cdc_binding and source.resolve() == Path(offload_cdc_binding["source"]["path"]).resolve():
            candidate = Path(offload_cdc_binding["candidate"]["path"])
            require(sha(candidate) == offload_cdc_binding["candidate"]["sha256"],
                    "offload CDC XDC override changed after validation")
            shutil.copy2(candidate, target)
            replacement = entry(target)
            require(replacement["sha256"] == offload_cdc_binding["candidate"]["sha256"],
                    "staged offload CDC XDC hash differs from the immutable candidate")
            offload_cdc_target = target
            offload_cdc_replacements.append({"source": offload_cdc_binding["source"],
                "replacement": replacement, "candidate": offload_cdc_binding["candidate"],
                "target_relative_path": offload_cdc_binding["target_relative_path"],
                "reason": "Use static joint CDC constraints; the pinned switch XDC already covers GPIO/SW inputs"})
        elif source.name == "rle_region_features.v":
            target.write_bytes(Path(result["region"]["path"]).read_bytes())
            replacements.append({"source": entry(source), "replacement": entry(target), "reason": "Manager-selected G2 region feature core"})
        else:
            shutil.copy2(source, target)
        if target.suffix not in (".xpr", ".bd") and not any(p.endswith(".gen") for p in target.parts):
            snapshot_sources.append(entry(target))
    require(bool(replacements), "no region source was replaced")
    require(not overlay_binding or len(overlay_replacements) == 1,
            "overlay RTL override did not replace exactly one pinned project source")
    require(not overlay_wrapper_binding or len(overlay_wrapper_replacements) == 1,
            "overlay wrapper override did not replace exactly one pinned project source")
    require(not frame_monitor_binding or len(frame_monitor_replacements) == 1,
            "frame monitor RTL override did not replace exactly one pinned project source")
    require(not timing_xdc_binding or len(timing_xdc_replacements) == 1,
            "timing XDC override did not replace exactly one pinned source")
    require(not offload_cdc_binding or len(offload_cdc_replacements) == 1,
            "offload CDC XDC override did not replace exactly one pinned source")
    xpr = staged / Path(result["xpr"]["path"]).relative_to(baseline)
    project = ET.parse(xpr)
    project.getroot().set("Path", xpr.as_posix())
    required_live = []
    for node, old_source in zip(project.findall(".//File"), result["project_sources"]):
        target = staged / Path(old_source["path"]).relative_to(baseline)
        node.set("Path", target.as_posix())
        if target.suffix.lower() in (".v", ".sv", ".vhd", ".vhdl", ".xdc"):
            required_live.append(target.as_posix())
    if timing_xdc_target is not None:
        register_timing_xdc_in_project(project, timing_xdc_target)
        if timing_xdc_target.as_posix() not in required_live:
            required_live.append(timing_xdc_target.as_posix())
    if offload_cdc_target is not None and offload_cdc_target.as_posix() not in required_live:
        required_live.append(offload_cdc_target.as_posix())
        register_timing_xdc_in_project(project, offload_cdc_target)
    synth_fileset = project.find(".//FileSet[@Name='sources_1']")
    require(synth_fileset is not None, "XPR is missing the sources_1 synthesis fileset")
    top = [n.attrib["Val"] for n in synth_fileset.findall(".//Option")
           if n.attrib.get("Name") == "TopModule"]
    require(top == ["display_wrapper"], f"unexpected sources_1 XPR top: {top}")
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
        'set_max_delay -datapath_only 10 -from [get_cells -hier -regexp {.*frame_monitor.*mailbox_.*_reg(\\[[0-9]+\\])?$}] -to [get_cells -hier -regexp {.*frame_monitor.*snap_.*_reg(\\[[0-9]+\\])?$}]\n',
        encoding="utf-8")
    snapshot_sources.append(entry(monitor_xdc))
    code_files = [Path(__file__)] + [Path(__file__).with_name(name) for name in
                                  ("joint_common.tcl", "joint_connect.tcl", "joint_build.tcl", "joint_probe.tcl",
                                   "joint_capture_review.py")]
    immutable = result["baseline_pinned_sources"] + result["project_sources"] + [result["baseline_manifest"], result["xpr"],
                result["region"], result["jpeg_source"]] + [entry(p) for p in code_files]
    if overlay_binding:
        immutable.append(overlay_binding["candidate"])
    if overlay_wrapper_binding:
        immutable.append(overlay_wrapper_binding["candidate"])
    if frame_monitor_binding:
        immutable.append(frame_monitor_binding["candidate"])
    if timing_xdc_binding:
        immutable.append(timing_xdc_binding["candidate"])
    if offload_cdc_binding:
        immutable.append(offload_cdc_binding["candidate"])
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
    if overlay_replacements:
        manifest["overlay_replacements"] = overlay_replacements
    if overlay_wrapper_replacements:
        manifest["overlay_wrapper_replacements"] = overlay_wrapper_replacements
    if frame_monitor_replacements:
        manifest["frame_monitor_replacements"] = frame_monitor_replacements
    if timing_xdc_replacements:
        manifest["timing_xdc_replacements"] = timing_xdc_replacements
    if offload_cdc_replacements:
        manifest["offload_cdc_replacements"] = offload_cdc_replacements
    manifest_path = out / "joint_manifest.json"
    write_new(manifest_path, manifest)
    write_new(out / "joint_preflight.json", result)
    h = result["hls"]
    python_executable = Path(shutil.which("python") or sys.executable).resolve()
    require(python_executable.is_file(),
            f"resolved Python executable is not a file: {python_executable}")
    spec = {"schema": "jpeg_joint_spec_v1", "xpr": xpr, "out": out, "manifest": manifest_path,
            "python": python_executable, "verifier": Path(__file__).resolve(),
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
    p.add_argument("--hls-ip", type=Path, default=Path("C:/w/mc-jpeg-axis-hls-20261101/hls/impl/ip"))
    p.add_argument("--region", type=Path, default=Path(__file__).with_name("rle_region_features.v"))
    p.add_argument("--overlay-rtl", type=Path, help="Optional pixel overlay RTL override; only replaces the pinned staged source")
    p.add_argument("--overlay-wrapper-rtl", type=Path, help="Optional AXI-Lite overlay wrapper override; only replaces the pinned staged source")
    p.add_argument("--frame-monitor-rtl", type=Path, help="Optional frame-monitor RTL override; only replaces the pinned staged source")
    p.add_argument("--timing-xdc", type=Path,
                   default=Path(__file__).resolve().parents[3] / "scripts/standalone/display_timing.xdc",
                   help="Reviewed display timing XDC; staging replaces the fixed snapshot copy")
    p.add_argument("--offload-cdc-xdc", type=Path,
                   default=Path(__file__).resolve().parents[3] / "scripts/standalone/jpeg_joint_cdc.xdc",
                   help="Reviewed static JPEG joint CDC XDC; staging replaces the fixed snapshot copy")
    p.add_argument("--jpeg-source", type=Path, default=repo / "fpga/experimental/jpeg_axis/jpeg_axis_gray.cpp")
    p.add_argument("--result", type=Path, help="Fresh JSON path; omitted means stdout only")
    p.add_argument("--stage", type=Path, help="Explicitly stage a new build directory; never starts Vivado")
    p.add_argument("--hls-green", type=Path)
    p.add_argument("--region-green", type=Path)
    p.add_argument("--ipcfg", type=Path, default=Path("C:/Users/14173/.codex/skills/vivado-ip-configurator/lib/ipcfg.tcl"))
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
