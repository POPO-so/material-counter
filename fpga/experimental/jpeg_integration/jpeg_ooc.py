"""Snapshot the exported JPEG IP's Verilog synthesis view and measure OOC area.

Only a new, short --out directory is written. The HLS export and simulation
workspace are read-only. No joined BD, implementation, bitstream or RTL edits.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def need(ok: bool, message: str):
    if not ok:
        raise ValueError(message)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def brace(value) -> str:
    text = str(value).replace("\\", "/")
    need(not any(c in text for c in "{}\r\n"), "unsupported Tcl path character")
    return "{" + text + "}"


def write_json(path: Path, value):
    with path.open("x", encoding="utf-8", newline="\n") as out:
        json.dump(value, out, indent=2)
        out.write("\n")


def verify(path: Path):
    manifest = read_json(path)
    for item in manifest["files"]:
        need(sha(Path(item["original"])) == item["sha256"], "export changed: " + item["original"])
        need(sha(Path(item["snapshot"])) == item["sha256"], "snapshot changed: " + item["snapshot"])
    need(sha(Path(manifest["cpp"]["path"])) == manifest["cpp"]["sha256"], "JPEG cpp changed")
    for item in manifest["driver_files"]:
        need(sha(Path(item["path"])) == item["sha256"], "OOC driver changed: " + item["path"])
    print("JPEG_OOC_SOURCES_UNCHANGED")


def prepare(args):
    source, out = args.ip.resolve(), args.out.resolve()
    need(not out.exists() and not out.is_relative_to(source) and not source.is_relative_to(out), "use a fresh OOC output outside the HLS workspace")
    need(len(out.as_posix()) < 70, "use a short OOC output path under C:/w")
    component = source / "component.xml"
    root = ET.parse(component).getroot()
    ns = {"s": root.tag.split("}")[0].lstrip("{")}
    view = [v for v in root.findall("s:model/s:views/s:view", ns)
            if v.findtext("s:name", namespaces=ns) == "xilinx_verilogsynthesis"]
    need(len(view) == 1 and view[0].findtext("s:modelName", namespaces=ns) == "jpeg_axis_gray", "not the exported jpeg_axis_gray synthesis view")
    names = [v.findtext("s:localName", namespaces=ns) for v in view[0].findall("s:fileSetRef", ns)]
    selected = []
    for fs in root.findall("s:fileSets/s:fileSet", ns):
        if fs.findtext("s:name", namespaces=ns) in names:
            selected.extend(f.findtext("s:name", namespaces=ns) for f in fs.findall("s:file", ns))
    need(selected and len(selected) == len(set(selected)), "missing or duplicate synthesis files")
    need(any("guard_coefficients" in name and name.endswith(".v") for name in selected), "guard module absent; do not measure an older core")
    need("hdl/verilog/jpeg_axis_gray.v" in selected, "new top RTL not in component synthesis fileset")
    cpp_hash = sha(args.cpp.resolve())
    state = read_json(args.hls_state.resolve())
    design_hashes = {i["path"]: i["sha256"] for i in state["design_hashes"]}
    need(state.get("result") == "PASS" and design_hashes.get("fpga/experimental/jpeg_axis/jpeg_axis_gray.cpp") == cpp_hash,
         "HLS synthesis receipt is not PASS for the current JPEG cpp")
    out.mkdir(parents=True, exist_ok=False)
    ip_snapshot = out / "ip"
    files = []
    for name in ["component.xml", *selected]:
        old = (source / name.replace("\\", "/")).resolve()
        need(old.is_relative_to(source) and old.is_file(), "missing/outside component file: " + name)
        new = ip_snapshot / old.relative_to(source)
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old, new)
        files.append({"original": old.as_posix(), "snapshot": new.as_posix(), "sha256": sha(old), "bytes": old.stat().st_size})
        need(sha(new) == files[-1]["sha256"], "copy hash mismatch: " + name)
    # Preserve source-binding evidence in the isolated run as well.
    state_copy = out / "hls-synthesis-state.json"
    shutil.copy2(args.hls_state, state_copy)
    files.append({"original": args.hls_state.resolve().as_posix(), "snapshot": state_copy.as_posix(), "sha256": sha(state_copy), "bytes": state_copy.stat().st_size})
    cpp_copy = out / "source-binding" / args.cpp.name
    cpp_copy.parent.mkdir()
    shutil.copy2(args.cpp, cpp_copy)
    files.append({"original": args.cpp.resolve().as_posix(), "snapshot": cpp_copy.as_posix(), "sha256": cpp_hash, "bytes": cpp_copy.stat().st_size})
    verilog_dir = ip_snapshot / "hdl/verilog"
    rtl = [ip_snapshot / name for name in selected if Path(name).suffix in (".v", ".sv")]
    top_text = (verilog_dir / "jpeg_axis_gray.v").read_text(encoding="utf-8")
    need(re.search(r"\bmodule\s+jpeg_axis_gray\s*\(", top_text) is not None, "wrong top module")
    for path in rtl + list(verilog_dir.glob("*.vh")):
        text = path.read_text(encoding="utf-8")
        for reference in re.findall(r'\$readmem[hb]\s*\(\s*"([^"]+)"', text):
            need((verilog_dir / reference).is_file(), "missing initialization data: " + reference)
        for reference in re.findall(r'`include\s+"([^"]+)"', text):
            need((verilog_dir / reference).is_file(), "missing Verilog include: " + reference)
    xdc = ip_snapshot / "constraints/jpeg_axis_gray_ooc.xdc"
    need(xdc.is_file() and re.search(r"-period\s+20(?:\.0+)?\s", xdc.read_text()) is not None, "OOC XDC is not 20 ns")
    driver_files = [{"path": p.resolve().as_posix(), "sha256": sha(p)} for p in [Path(__file__), Path(__file__).with_suffix(".tcl")]]
    manifest = {"schema": "material-counter.jpeg-axis-ooc.v1", "top": "jpeg_axis_gray", "part": "xc7z020clg400-1",
                "clock_period_ns": 20, "threads": 1, "mode": "out_of_context_synthesis_only",
                "component_sha256": sha(component), "files": files,
                "cpp": {"path": args.cpp.resolve().as_posix(), "sha256": cpp_hash}, "driver_files": driver_files,
                "hls_input_area_estimate_only": {"lut": 51598, "ff": 24650, "memory": 56, "dsp": 0},
                "board_ready": False, "rtl_functional_acceptance": "pending separate cosim"}
    write_json(out / "inputs.json", manifest)
    (out / "spec.tcldict").write_text(
        "out " + brace(out) + "\nrtl {" + " ".join(brace(p) for p in rtl) + "}\ninclude_dir " + brace(verilog_dir) +
        "\nxdc " + brace(xdc) + "\n", encoding="utf-8")
    print(json.dumps({"status": "SNAPSHOT_READY", "out": str(out), "rtl_units": len(rtl), "files": len(files),
                      "component_sha256": sha(component), "source_sha256": cpp_hash}))


def finalize(out: Path, exit_code: int, elapsed: float):
    log = out / "vivado.log"
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    messages = []
    for match in re.finditer(r"(?m)^(WARNING|CRITICAL WARNING|ERROR):\s*\[([^\]]+)\]\s*(.*)$", text):
        messages.append({"severity": match[1], "id": match[2], "text": match[3]})
    warnings = {severity: dict(collections.Counter(m["id"] for m in messages if m["severity"] == severity))
                for severity in ("WARNING", "CRITICAL WARNING", "ERROR")}
    resources = None
    if (out / "utilization.rpt").is_file():
        report = (out / "utilization.rpt").read_text(encoding="utf-8", errors="replace")

        def number(label):
            match = re.search(r"\|\s*" + label + r"\s*\|\s*([0-9.]+)\s*\|", report)
            need(match is not None, "missing utilization row: " + label)
            return float(match[1]) if "." in match[1] else int(match[1])

        resources = {"lut": number(r"Slice LUTs\*?"), "ff": number("Slice Registers"),
                     "bram36_equivalent": number("Block RAM Tile"), "dsp": number("DSPs"),
                     "lut_as_logic": number("LUT as Logic"), "lut_as_memory": number("LUT as Memory")}
    source_error = None
    try:
        verify(out / "inputs.json")
    except (OSError, ValueError) as error:
        source_error = str(error)
    manifest = read_json(out / "inputs.json")
    status = "PASS_OOC_AREA_ONLY" if exit_code == 0 and "JPEG_OOC_SYNTH_DONE" in text and resources and not source_error else "FAIL"
    artifacts = [{"path": p.as_posix(), "sha256": sha(p)} for p in sorted(out.iterdir())
                 if p.is_file() and p.suffix in (".rpt", ".log", ".dcp")]
    result = {"status": status, "top": manifest["top"], "part": manifest["part"], "clock_period_ns": 20,
              "source_sha256": manifest["cpp"]["sha256"], "component_sha256": manifest["component_sha256"],
              "input_manifest_sha256": sha(out / "inputs.json"), "resources": resources, "warning_counts_by_id": warnings,
              "messages": messages, "source_error": source_error, "exit_code": exit_code, "elapsed_seconds": elapsed,
              "artifacts": artifacts, "board_ready": False, "routed": False, "rtl_functional_acceptance": "pending separate cosim"}
    write_json(out / "results.json", result)
    print(json.dumps({k: v for k, v in result.items() if k not in ("messages", "artifacts")}, indent=2))
    return 0 if status == "PASS_OOC_AREA_ONLY" else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    repo = Path(__file__).resolve().parents[3]
    p.add_argument("--ip", type=Path, default=None)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--cpp", type=Path, default=repo / "fpga/experimental/jpeg_axis/jpeg_axis_gray.cpp")
    p.add_argument("--hls-state", type=Path, default=None)
    p.add_argument("--vivado", type=Path, default=Path("C:/AMDDesignTools/2026.1/Vivado/bin/vivado.bat"))
    p.add_argument("--run", action="store_true", help="Explicitly run only OOC synthesis in this new snapshot")
    p.add_argument("--verify-only", action="store_true")
    p.add_argument("--timeout-seconds", type=int, default=1800)
    args = p.parse_args()
    out = args.out.resolve()
    try:
        if args.verify_only:
            verify(out / "inputs.json")
            return 0
        prepare(args)
        if not args.run:
            return 0
        verify(out / "inputs.json")
        cmd = [str(args.vivado.resolve()), "-mode", "batch", "-notrace", "-source", str(Path(__file__).with_suffix(".tcl").resolve()),
               "-log", str(out / "vivado.log"), "-journal", str(out / "vivado.jou"), "-tclargs", str(out / "spec.tcldict")]
        start = time.monotonic()
        with (out / "console.log").open("x", encoding="utf-8") as console:
            process = subprocess.Popen(cmd, cwd=out, stdout=console, stderr=subprocess.STDOUT)
            try:
                code = process.wait(timeout=args.timeout_seconds)
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                process.wait()
                code = 124
        return finalize(out, code, time.monotonic() - start)
    except (OSError, ValueError, KeyError, ET.ParseError) as error:
        print("JPEG_OOC_FAIL: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
