"""Create a checked BIT/HWH identity manifest from one completed offline build."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from pynq_image_once import validate_inputs

ROOT = Path(__file__).resolve().parents[2]

def require(condition, message):
    # These are release gates; Python -O must never remove them.
    if not condition:
        raise RuntimeError(message)

parser = argparse.ArgumentParser()
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--clock", type=int, choices=(50, 100), required=True)
args = parser.parse_args()
out = args.out.resolve()
require(out.parent == ROOT / "data/results" and out.name.startswith("wo0014-hw-"), "Output outside experiment results")
export = out / "export"
bit, hwh = export / "wo0014_image.bit", export / "wo0014_image.hwh"

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

timing = (out / "reports/timing-implemented.rpt").read_text()
row = re.search(r"^\s*(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(\d+)\s+(\d+)\s+"
                r"(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(\d+)\s+(\d+)\s+"
                r"(-?\d+\.\d+)\s+(-?\d+\.\d+)", timing, re.M)
require(row is not None, "Timing summary absent")
wns, tns, setup_bad, setup_total, whs, ths, hold_bad, hold_total, wpws, tpws = row.groups()
require(min(float(wns), float(whs), float(wpws)) >= 0, "Negative implemented timing slack")
require(float(tns) == float(ths) == float(tpws) == 0 and int(setup_bad) == int(hold_bad) == 0, "Timing violations remain")
require(re.search(r"clk_fpga_0\s+\{[^}]+\}\s+[\d.]+\s+" + str(args.clock) + r"\.000", timing) is not None, "Implemented clock differs from requested build")
require("checking no_clock (0)" in timing and "checking unconstrained_internal_endpoints (0)" in timing, "Unconstrained internal endpoints")
require("checking no_input_delay (0)" in timing and "checking no_output_delay (0)" in timing, "Unconstrained IO paths")
drc = (out / "reports/drc.rpt").read_text()
methodology = json.loads((out / "reports/methodology.json").read_text())
for table in methodology.get("tables", {}).values():
    headers = table.get("headers", [])
    if "severity" in headers:
        for item in table.get("data", []):
            require(item[headers.index("severity")] not in ("Error", "Critical Warning"), "Serious methodology: " + str(item))
require(re.search(r"^\|[^|]+\|\s*(?:Error|Critical Warning)\s*\|.*\|\s*[1-9]\d*\s*\|\s*$", drc, re.M) is None, "Serious DRC findings")
util = (out / "reports/utilization-implemented.rpt").read_text()
resources = {}
for label, key in (("Slice LUTs", "lut"), ("Slice Registers", "ff"),
                   ("Block RAM Tile", "bram"), ("DSPs", "dsp")):
    value = re.search(re.escape(label) + r"\*?\s*\|\s*([\d.]+)", util)
    require(value is not None, "Resource summary absent: " + label)
    resources[key] = float(value.group(1)) if "." in value.group(1) else int(value.group(1))
require(bit.is_file() and bit.stat().st_size > 0 and hwh.is_file(), "BIT/HWH missing")
project = out / "project"
name = f"wo0014_image_{args.clock}m"
run_bit = project / (name + ".runs/impl_1/wo0014_image_wrapper.bit")
project_hwh = project / (name + ".gen/sources_1/bd/wo0014_image/hw_handoff/wo0014_image.hwh")
require(sha(bit) == sha(run_bit) and sha(hwh) == sha(project_hwh), "Exported BIT/HWH differ from this project run")
sources = {p.name: sha(p) for p in sorted((out / "rtl").glob("*.v"))}
for filename in sources:
    candidate = ROOT / "fpga/experimental/wo0014" / filename
    if not candidate.exists():
        candidate = ROOT / "fpga/rtl" / filename
    require(sha(candidate) == sources[filename], "Source snapshot changed: " + filename)
manifest = {
    "schema": 1, "build_id": out.name, "clock_mhz": args.clock, "part": "xc7z020clg400-1",
    "bit_path": str(bit), "hwh_path": str(hwh), "bit_sha256": sha(bit), "hwh_sha256": sha(hwh),
    "dma_base": "0x40400000", "checker_base": "0x43c20000", "dma_length_width": 18,
    "dma_mode": "simple", "dma_mm2s": True, "dma_s2mm": False,
    "dma_include_sg": False, "dma_micro_mode": False,
    "dma_stream_width_bits": 32, "dma_addr_width_bits": 32,
    "dma_mm2s_memory_width_bits": 64, "dma_mm2s_dre": False,
    "hp0_range_start": "0x00000000", "hp0_range_end_exclusive": "0x20000000",
    "optimized_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
    "rtl_sha256": sources, "board_tested": False,
    "implemented_timing": {"wns": float(wns), "tns": float(tns), "whs": float(whs),
                           "ths": float(ths), "wpws": float(wpws)}, "resources": resources,
}
manifest_path = export / "pair-manifest.json"
manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
packet = ROOT / "data/results/wo0014-hw-verification-20261014/rgb_packet.bin"
plan = validate_inputs(bit, hwh, packet, manifest_path)
require(plan.packet_bytes == 230432, "Wrong formal RGB packet size")
for filename in ("pynq_image_once.py",):
    shutil.copyfile(Path(__file__).resolve().parent / filename, export / filename)
shutil.copyfile(packet, export / "rgb_packet.bin")
summary = {"clock_mhz": args.clock, "pair_identity": "PASS", "hwh_contract": "PASS",
           "wns": float(wns), "tns": float(tns), "whs": float(whs), "ths": float(ths),
           "resources": resources, "bit": str(bit), "hwh": str(hwh), "manifest": str(manifest_path),
           "hardware_actions": 0}
(out / "reports/build-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(json.dumps(summary, indent=2))
