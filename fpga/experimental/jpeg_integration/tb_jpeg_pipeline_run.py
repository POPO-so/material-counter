"""Check or run the pinned HLS JPEG RTL into the frozen 640x400 RLE path.

Default mode is input/manifest validation only. Pass --run-xsim to launch
Vivado/XSim explicitly; all generated evidence is contained in --build-root.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

WIDTH, HEIGHT = 640, 400
PIXELS = WIDTH * HEIGHT
EXPECTED_COUNTS = {".v": 142, ".vh": 3, ".dat": 1}
EXPECTED_COMPONENT_SHA256 = "0bdfce509b1f51829aa1c2b12536fba14088925f89ce8c7da2558707246725d5"
PIPELINE = [
    "fpga/experimental/gray8/axis_dma_gray_adapter.v",
    "fpga/experimental/gray8/axis_raw_gray_adapter.v",
    "fpga/rtl/axis_morphology3x3.v",
    "fpga/experimental/rle/axis_rle_encoder.v",
    "fpga/experimental/rle/axis_dma_rle_pipeline_bd.v",
    "fpga/experimental/rle/rle_feature_tap.v",
    "fpga/experimental/standalone/video_binary_overlay.v",
    "fpga/experimental/standalone/video_binary_overlay_bd.v",
]
REGION_REL = Path("fpga/experimental/jpeg_integration/rle_region_features.v")
REGION_SHA256 = "19439a2c666696d48521b112705763df6ac48012cfacd9476fcd6850a3551911"
JPEG_REL = Path("data/results/jpeg-probe-20261101/camera-120-frames/frame_000001.jpg")
GOLDEN_REL = Path("data/results/jpeg-probe-20261101/hls/frame_000001/planar_padded_yuv.bin")
GOLDEN_Y_SHA256 = "45461b3a80e010cf77537b1cc2204ff5d6bc63cc70850fbbcca6e6c549501fe3"
GOLDEN_FILE_SHA256 = "b57c6893ec1976c390a2bdbfc76b099125661688bf99669afd4e6bb1f45e7fa7"
JPEG_SHA256 = "34e34e90690fb1b50262d62e585406cb23f34ecdae5ac4e6d6ae1408efdf5fd0"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def tcl_path(path: Path) -> str:
    # Preserve SUBST drive aliases. Path.resolve() expands them back to the
    # long physical path, which can make Vivado's project Tcl apps fail on
    # Windows before source compilation begins.
    return "{" + path.absolute().as_posix() + "}"


def tcl_word(value: str) -> str:
    if "}" in value:
        raise ValueError("Tcl argument contains an unsupported closing brace")
    return "{" + value + "}"


def expected_pipeline_inputs(snapshot: Path, source_root: Path) -> tuple[dict, list[dict]]:
    manifest_path = snapshot / "jpeg-joint-source.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("base_commit") != "c5a86807c93e6ed572e2f75e13a6f9c7f1320321":
        # Fail closed if the frozen pipeline snapshot identity differs.
        raise ValueError(f"unexpected pipeline snapshot commit: {manifest.get('base_commit')}")
    files = []
    for relative in PIPELINE:
        source = snapshot / relative
        expected = manifest.get("files", {}).get(relative, {}).get("sha256")
        if not expected or sha256(source) != expected:
            raise ValueError(f"pipeline snapshot hash mismatch: {relative}")
        files.append({"relative": relative, "source": str(source.absolute()), "sha256": expected})
    region = source_root / REGION_REL
    if sha256(region) != REGION_SHA256:
        raise ValueError(f"optimized BRAM region RTL hash mismatch: {region}")
    files.append({"relative": REGION_REL.as_posix(), "source": str(region.absolute()),
                  "sha256": REGION_SHA256, "source_kind": "optimized branch RTL"})
    return manifest, files


def resolve_hls_files(component: Path) -> tuple[Path, list[Path], dict[str, int]]:
    component = component.absolute()
    tree = ET.parse(component)
    root = tree.getroot()
    ns = {"s": "http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009"}
    refs = [node.findtext("s:localName", namespaces=ns) for node in root.findall(".//s:fileSetRef", ns)]
    chosen = "xilinx_verilogbehavioralsimulation_view_fileset"
    if refs.count(chosen) != 1:
        raise ValueError(f"component must reference {chosen} exactly once; found {refs.count(chosen)}")
    sets = [node for node in root.findall(".//s:fileSet", ns)
            if node.findtext("s:name", namespaces=ns) == chosen]
    if len(sets) != 1:
        raise ValueError(f"expected one fileset definition for {chosen}; found {len(sets)}")
    entries = [node.findtext("s:name", namespaces=ns) for node in sets[0].findall("s:file", ns)]
    if any(not name for name in entries) or len(entries) != 146 or len(set(entries)) != 146:
        raise ValueError(f"behavioral fileset must contain 146 unique paths; found {len(entries)}")
    root_dir = component.parent
    sources = [root_dir / Path(name.replace("/", os.sep)) for name in entries]
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(f"component fileset source missing: {source}")
    counts = {ext: sum(path.suffix.lower() == ext for path in sources) for ext in EXPECTED_COUNTS}
    if counts != EXPECTED_COUNTS:
        raise ValueError(f"behavioral fileset extension counts mismatch: {counts}")
    if any(not name.startswith("hdl/verilog/") for name in entries):
        raise ValueError("behavioral fileset contains an unexpected path outside hdl/verilog")
    for header in (path for path in sources if path.suffix.lower() == ".vh"):
        if not any(f'`include "{header.name}"' in path.read_text(encoding="utf-8", errors="ignore")
                   for path in sources if path.suffix.lower() in (".v", ".vh")):
            raise ValueError(f"fileset header is not included by any Verilog source: {header.name}")
    return root_dir / "hdl/verilog", sources, counts


def check_golden(jpeg: Path, golden: Path, source_root: Path) -> dict:
    jpeg_hash, full_hash = sha256(jpeg), sha256(golden)
    raw = golden.read_bytes()
    y_hash = hashlib.sha256(raw[:PIXELS]).hexdigest()
    if len(jpeg.read_bytes()) != 13420 or jpeg_hash != JPEG_SHA256:
        raise ValueError(f"JPEG input does not match recorded frame 1 source/hash: {jpeg}")
    if len(raw) != 512000 or full_hash != GOLDEN_FILE_SHA256 or y_hash != GOLDEN_Y_SHA256:
        raise ValueError("frame 1 CSim golden length/hash mismatch")
    camera_manifest = json.loads((source_root / "data/results/jpeg-probe-20261101/camera-120.json").read_text())
    frame = camera_manifest["capture"]["frames"][0]
    if frame.get("index") != 1 or frame.get("sha256") != jpeg_hash:
        raise ValueError("JPEG does not match camera-120 frame 1 manifest")
    receipt_path = source_root / "data/results/jpeg-axis-integration-20261101-143535/raw/jpeg-hls-recovery-receipt.json"
    receipt = json.loads(receipt_path.read_text())
    csim = receipt.get("csim", {})
    if 1 not in csim.get("vendorY_frames", []) or csim.get("vendorY_maxdiff") != 0:
        raise ValueError("HLS CSim receipt does not establish frame 1 vendor-Y identity")
    if not receipt.get("checks", {}).get("csim"):
        raise ValueError("HLS CSim receipt is not PASS")
    # The referenced CSim comparator treats vendorY as a 512000-byte planar
    # file and compares pixel i with reference[i], establishing Y at [0:256000].
    comparator = source_root / "scripts/experiments/jpeg_axis_verify/jpeg_axis_gray_tb.cpp"
    text = comparator.read_text(encoding="utf-8")
    if 'reference_kind == "vendorY"' not in text or 'reference[i]' not in text or 'reference.size() == 512000' not in text:
        raise ValueError("known vendor-Y comparator semantics could not be verified")
    return {
        "jpeg": {"path": str(jpeg.absolute()), "bytes": len(jpeg.read_bytes()), "sha256": jpeg_hash,
                 "source_manifest": str((source_root / "data/results/jpeg-probe-20261101/camera-120.json").absolute())},
        "golden": {"path": str(golden.absolute()), "bytes": len(raw), "sha256": full_hash,
                   "first_256000_bytes_are_y": True, "y_bytes": PIXELS, "y_sha256": y_hash,
                   "source": "frame_000001 CSim planar_padded_yuv.bin; vendorY C++ comparator compares reference[i] for i in 0..255999; HLS receipt frame 1 maxdiff=0",
                   "receipt": str(receipt_path.absolute()), "comparator": str(comparator.absolute())},
    }


def write_hex(path: Path, data: bytes) -> None:
    path.write_text("".join(f"{value:02x}\n" for value in data), encoding="ascii")


def run(args: argparse.Namespace) -> int:
    script = Path(__file__).resolve()
    default_ref = script.parents[3]
    source_root = args.source_root.absolute() if args.source_root else default_ref
    snapshot = args.snapshot.absolute() if args.snapshot else source_root / ".runtime/jpeg-joint-snapshot-20261101"
    hls_component = args.component.absolute() if args.component else source_root / ".runtime/j9/ip/jpeg_axis_gray/component.xml"
    jpeg = args.jpeg.absolute() if args.jpeg else source_root / JPEG_REL
    golden = args.golden.absolute() if args.golden else source_root / GOLDEN_REL
    build_root = args.build_root.absolute() if args.build_root else source_root / ".runtime/j9/t7"

    manifest, pipeline_files = expected_pipeline_inputs(snapshot, source_root)
    hdl_dir, hls_sources, counts = resolve_hls_files(hls_component)
    component_hash = sha256(hls_component)
    if component_hash != EXPECTED_COMPONENT_SHA256:
        raise ValueError(f"HLS component hash mismatch: {component_hash}")
    if not (hdl_dir / "jpeg_axis_gray.v").is_file():
        raise FileNotFoundError("component fileset does not contain jpeg_axis_gray top")
    if not (hdl_dir / "jpeg_axis_gray_parser_jpg_top_Pipeline_VITIS_LOOP_301_3_xf_codec_details_hls_jpeg_zigzag_to_bkb.dat").is_file():
        raise FileNotFoundError("required HLS readmemh .dat file missing from hdl/verilog")
    input_info = check_golden(jpeg, golden, source_root)
    tb = script.with_name("tb_jpeg_pipeline.sv")
    check = {
        "status": "INPUTS_PASS", "xsim_run": False, "component": str(hls_component),
        "component_sha256": component_hash, "component_fileset": "xilinx_verilogbehavioralsimulation_view_fileset",
        "hls_files": [{"path": str(p.absolute()), "sha256": sha256(p)} for p in hls_sources],
        "hls_file_counts": counts, "include_dirs": [str(hdl_dir.absolute())],
        "snapshot_commit": manifest["base_commit"], "pipeline_files": pipeline_files,
        "pipeline_sha256": manifest["files"]["fpga/experimental/rle/axis_dma_rle_pipeline_bd.v"]["sha256"],
        "region_sha256": REGION_SHA256,
        "golden_evidence": input_info, "testbench_sha256": sha256(tb), "runner_sha256": sha256(script),
        "sim_cwd": "<build-root>/<run-id>/vivado_project/jpeg_pipeline_sim.sim/sim_1/behav/xsim (HLS .dat staged)",
        "watchdogs": {"cycles": 2000000, "sim_time_ns": 200000000},
        "testbench_pass_marker": "JPEG_PIPELINE_PASS", "scope": "HLS RTL decode + frozen 640x400 packet/RLE/FRG/preview identity",
    }
    if not args.run_xsim:
        print(json.dumps(check, indent=2))
        return 0

    import datetime
    from uuid import uuid4
    build = build_root / f"jpeg-pipeline-{datetime.datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
    vectors, inputs, sim = build / "vectors", build / "inputs", build / "sim"
    staged_hdl = build / "hls" / "hdl" / "verilog"
    oracle_dir = vectors / "pipeline_oracle"
    for directory in (vectors, inputs, sim, staged_hdl, oracle_dir):
        directory.mkdir(parents=True, exist_ok=False)
    jpeg_bytes = jpeg.read_bytes()
    golden_bytes = golden.read_bytes()[:PIXELS]
    write_hex(vectors / "jpeg_bytes.hex", jpeg_bytes)
    write_hex(vectors / "golden_y.hex", golden_bytes)
    # Reuse the independently implemented raster/RLE/FRG oracle from the
    # packet-boundary regression. The ROI matches the full-frame configuration
    # programmed by tb_jpeg_pipeline.sv.
    from tb_joint_packet_run import make_case
    oracle = make_case(oracle_dir, 0, "jpeg_pipeline_frame", golden_bytes, 1,
                       (0, 0, WIDTH, HEIGHT))
    if oracle["rle_words"] > 20000 or oracle["frg_records"] > 512:
        raise ValueError(f"pipeline oracle exceeds testbench capacity: {oracle}")
    shutil.copy2(jpeg, inputs / "frame_000001.jpg")
    shutil.copy2(golden, inputs / "planar_padded_yuv.bin")
    tb_copy = inputs / tb.name
    shutil.copy2(tb, tb_copy)
    frozen = []
    for entry in pipeline_files:
        src = Path(entry["source"])
        dst = inputs / entry["relative"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        frozen.append(dst)
    frozen += [tb_copy]
    # Stage the exact component fileset under a private build-root hdl/verilog
    # directory. This preserves the HLS $readmemh relative path and keeps all
    # simulator-generated files away from source and snapshot directories.
    staged_hls_sources = []
    for original in hls_sources:
        relative = original.relative_to(hdl_dir)
        staged = staged_hdl / relative
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, staged)
        if sha256(staged) != sha256(original):
            raise ValueError(f"staged HLS source hash mismatch: {original.name}")
        staged_hls_sources.append(staged)
    hls_verilog = [path for path in staged_hls_sources if path.suffix.lower() == ".v"]
    design_sources = hls_verilog + [path for path in frozen if path.suffix.lower() == ".v"]
    data_file = next(path for path in staged_hls_sources if path.suffix.lower() == ".dat")
    project_name = "jpeg_pipeline_sim"
    project_dir = build / "vivado_project"
    sim_run = project_dir / f"{project_name}.sim" / "sim_1" / "behav" / "xsim"
    export_dir = sim / "export_simulation"
    tcl = sim / "run_jpeg_pipeline_project.tcl"
    plusargs = [
        ("JPEG", (vectors / "jpeg_bytes.hex").absolute()),
        ("GOLDEN_Y", (vectors / "golden_y.hex").absolute()),
        ("RLE_LOG", (sim / "observed_rle.hex").absolute()),
        ("RLE_EXPECTED", (oracle_dir / "case0_rle.hex").absolute()),
        ("FRG_EXPECTED", (oracle_dir / "case0_frg.hex").absolute()),
        ("RLE_WORDS", str(oracle["rle_words"])),
        ("FRG_RECORDS", str(oracle["frg_records"])),
        ("FG_PIXELS", str(oracle["foreground"])),
        ("JPEG_BYTES", str(len(jpeg_bytes))),
        ("FRAME_ID", "1"),
    ]
    source_list = " ".join(tcl_path(path) for path in design_sources)
    plusarg_list = " ".join(
        f"-testplusarg {tcl_word(f'{name}={value.absolute().as_posix() if isinstance(value, Path) else value}')}"
        for name, value in plusargs
    )
    lines = [
        f"create_project -force {project_name} {tcl_path(project_dir)} -part xc7z020clg400-1",
        f"add_files -norecurse -fileset sources_1 [list {source_list}]",
        f"add_files -norecurse -fileset sim_1 {tcl_path(tb_copy)}",
        f"set_property include_dirs [list {tcl_path(staged_hdl)}] [get_filesets sources_1]",
        f"set_property include_dirs [list {tcl_path(staged_hdl)}] [get_filesets sim_1]",
        "set_property target_language Verilog [current_project]",
        "set_property simulator_language Mixed [current_project]",
        "set_property target_simulator XSim [current_project]",
        "set_property top tb_jpeg_pipeline [get_filesets sim_1]",
        "set_property -dict [list xsim.elaborate.xelab.more_options {-v 2}] [get_filesets sim_1]",
        "set_property xsim.simulate.runtime all [get_filesets sim_1]",
        f"set_property -dict [list xsim.simulate.xsim.more_options [list {plusarg_list}]] [get_filesets sim_1]",
        "update_compile_order -fileset sources_1",
        "update_compile_order -fileset sim_1",
        f"export_simulation -simulator xsim -directory {tcl_path(export_dir)}",
        f"file mkdir {tcl_path(sim_run)}",
        f"file copy -force {tcl_path(data_file)} {tcl_path(sim_run / data_file.name)}",
        "launch_simulation -simset sim_1 -mode behavioral",
        "close_sim -force",
        "close_project",
    ]
    tcl.write_text("\n".join(lines) + "\n", encoding="ascii")
    vivado = args.vivado.resolve()
    command = [
        str(vivado), "-mode", "batch", "-source", str(tcl),
        "-log", str((sim / "vivado.log").absolute()),
        "-journal", str((sim / "vivado.jou").absolute()),
    ]
    vivado_cwd = default_ref
    (sim / "command.json").write_text(json.dumps({"argv": command, "cwd": str(vivado_cwd)}, indent=2) + "\n", encoding="utf-8")
    timeout = args.timeout
    with (sim / "driver_stdout.log").open("wb") as stream:
        proc = subprocess.run(command, cwd=vivado_cwd, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
    sim_logs = list(project_dir.rglob("simulate.log"))
    if len(sim_logs) != 1:
        raise RuntimeError(f"expected one Vivado simulate.log, found {len(sim_logs)}; inspect {sim}")
    sim_log = sim_logs[0]
    if proc.returncode != 0:
        raise RuntimeError(f"Vivado/XSim failed ({proc.returncode}); inspect {sim}")
    output = sim_log.read_text(encoding="utf-8", errors="replace")
    if len(re.findall(r"^JPEG_PIPELINE_PASS ", output, re.M)) != 1:
        raise RuntimeError(f"expected exactly one JPEG_PIPELINE_PASS marker; inspect {sim}")
    if re.search(r"^\s*(Fatal:|ERROR:)", output, re.M):
        raise RuntimeError(f"simulation log contains a fatal/error; inspect {sim}")
    check["sim_cwd"] = str(sim_run.absolute())
    check["simulation_export_dir"] = str(export_dir.absolute())
    check["pipeline_oracle"] = oracle
    check["staged_hls_files"] = [{"path": str(p.absolute()), "sha256": sha256(p)} for p in staged_hls_sources]
    check["include_dirs"] = [str(staged_hdl.absolute())]
    evidence = []
    for path in sorted(p for p in build.rglob("*") if p.is_file()):
        evidence.append({"path": str(path.absolute()), "bytes": path.stat().st_size, "sha256": sha256(path)})
    check.update({"status": "PASS", "xsim_run": True, "build_dir": str(build.absolute()),
                  "sim_exit_code": proc.returncode, "pass_marker": "JPEG_PIPELINE_PASS", "evidence": evidence})
    receipt = build / "tb_jpeg_pipeline_receipt.json"
    receipt.write_text(json.dumps(check, indent=2) + "\n", encoding="utf-8")
    check["receipt_sha256"] = sha256(receipt)
    (build / "tb_jpeg_pipeline_receipt.sha256").write_text(
        f"{check['receipt_sha256']}  {receipt.name}\n", encoding="ascii")
    print(json.dumps({"status": "PASS", "build_dir": str(build.absolute()), "receipt": str(receipt.absolute()),
                      "receipt_sha256": check["receipt_sha256"], "pass_marker": "JPEG_PIPELINE_PASS"}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, help="JPEG probe worktree root")
    parser.add_argument("--snapshot", type=Path, help="fixed joint RTL snapshot root")
    parser.add_argument("--component", type=Path, help="HLS export component.xml")
    parser.add_argument("--jpeg", type=Path, help="JPEG byte input")
    parser.add_argument("--golden", type=Path, help="512000-byte planar CSim golden")
    parser.add_argument("--build-root", type=Path, help="runtime output directory (default: worktree .runtime/j9/xsim-jpeg-pipeline)")
    parser.add_argument("--vivado", type=Path, default=Path(r"C:\AMDDesignTools\2026.1\Vivado\bin\vivado.bat"))
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--run-xsim", action="store_true", help="explicitly launch Vivado/XSim")
    args = parser.parse_args()
    try:
        return run(args)
    except Exception as exc:
        print(f"JPEG_PIPELINE_FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
