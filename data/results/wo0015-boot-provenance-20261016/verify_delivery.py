"""Finalize local evidence. No board, device, network or target-code execution."""
import ast
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
def sha(p):
    with p.open("rb") as f: return hashlib.file_digest(f, "sha256").hexdigest()
def dump(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

history = {}
for name in ("wo0015-afi1-preconditions-20261015", "wo0015-afi1-readonly-20261015", "wo0015-step2-mismatch-audit-20261015", "wo0015-runonly-resume-retry2-20261015", "wo0015-first-board-retry3-20261015"):
    folder = ROOT / "data/results" / name
    index = json.loads((folder / "evidence-sha256.json").read_bytes())
    bad = [p for p, h in index.items() if not (folder / p).is_file() or sha(folder / p) != h]
    assert not bad, (name, bad)
    history[name] = {"files": len(index), "mismatches": bad}

hardware_locks = {"wo15.bit": "a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420", "wo15.hwh": "bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6"}
for name, digest in hardware_locks.items():
    assert sha(ROOT / "data/results/wo0015-hw-50mhz-20261015/export" / name) == digest

parsed = json.loads((OUT / "BOOT.BIN-analysis.json").read_bytes())
raw = (OUT / "extracted/BOOT.BIN").read_bytes()
for p in parsed["partitions"]:
    assert sha(OUT / "extracted" / f"partition-{p['index']:02d}.raw.bin") == p["sha256_total_partition"]
    assert hashlib.sha256(raw[p["data_offset"]:p["data_offset"] + p["total_nbytes"]]).hexdigest() == p["sha256_total_partition"]

image_hash = json.loads((OUT / "image-sha256-verified.json").read_bytes())
assert image_hash["sha256"] == "4a6ac1cdbb413d768fcb059b7182e1f53aa7f728b898b7f5061cbb0b7cc93aab"
assert sha(OUT / "restricted-raw/REVISION") == "7988c8799349762dae727464388ad952f42a3bdc74e3897b50f57bf35809ce25"

source_meta = []
files = [OUT / "extracted/BOOT.BIN", *sorted((OUT / "extracted").glob("*.raw.bin")), OUT / "extracted/BOOT.PY", OUT / "extracted/IMAGE.UB", OUT / "extracted/BOOT.SCR"]
ps7 = ROOT / "data/results/wo0015-hw-50mhz-20261015/project/wo15_50m.gen/sources_1/bd/wo15/ip/wo15_ps7_0"
files += [ps7 / name for name in ("ps7_init.c", "ps7_init.tcl", "ps7_init.h")]
for p in files:
    st = p.stat()
    source_meta.append({"path": str(p), "sha256": sha(p), "nbytes": st.st_size, "creation_utc": datetime.datetime.fromtimestamp(st.st_birthtime, datetime.timezone.utc).isoformat(), "mtime_utc": datetime.datetime.fromtimestamp(st.st_mtime, datetime.timezone.utc).isoformat(), "actual_generation_time": "UNKNOWN unless separately represented by an embedded build label; host extraction timestamps are not build times"})
dump("artifact-provenance.json", source_meta)

suite = ET.parse(OUT / "final-tests.xml").getroot().find("testsuite")
assert suite is not None and suite.attrib["tests"] == "43" and suite.attrib["errors"] == suite.attrib["failures"] == "0"
plan = json.loads((OUT / "future-command-plan.json").read_bytes())
digest = sha(OUT / "os_file_proposal.py")
for cmd in plan["commands"]: assert cmd["source_sha256"] == digest
source = ast.parse((OUT / "os_file_proposal.py").read_text(encoding="utf-8"))
imports = [node.names[0].name for node in ast.walk(source) if isinstance(node, ast.Import)]
assert not set(imports) & {"pynq", "mmap", "socket", "paramiko", "subprocess"}
calls = sorted({ast.unparse(n.func) for n in ast.walk(source) if isinstance(n, ast.Call)})
assert not set(calls) & {"os.write", "os.system", "exec", "eval"}
dump("proposal-source-audit.json", {"imports": imports, "calls": calls, "sha256": digest, "review_boundary": "Static/manual review plus fake kernel IO tests; real backend never run. Future wrapper exec is restricted to this hash-pinned reviewed module, never image contents."})

for name, path in (("main", Path("C:/fpga/material-counter")), ("worktree", ROOT)):
    for label, args in (("status", ["status", "--porcelain=v1"]), ("head", ["rev-parse", "HEAD"]), ("diff-check", ["diff", "--check"])):
        result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, check=True)
        (OUT / f"{name}-{label}.txt").write_bytes(result.stdout)

summary = {"status": "STOPPED_PRECONDITION_UNVERIFIED", "completed_local": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(), "board_connection": "USER_REPORTED_DISCONNECTED; no connection attempted", "board_connections": 0, "mmio_reads_or_writes": 0, "overlay_loads": 0, "dma_runs": 0, "target_binary_executions": 0, "images_mounted": 0, "sd_writes": 0, "commits_or_merges": 0, "hardware_locks": hardware_locks, "local_image_sha256": image_hash["sha256"], "boot_bin_sha256": sha(OUT / "extracted/BOOT.BIN"), "fsbl_raw_sha256": parsed["partitions"][0]["sha256_total_partition"], "local_boot_partitions": len(parsed["partitions"]), "historical_boot_binding": "UNKNOWN", "rootfs_uuid_equality": "VERIFIED equality; shared lineage INFERRED, unique boot identity UNKNOWN", "ps7_post_config_execution": "UNKNOWN", "afi_read_authorized": False, "tests_passed": 43, "previous_evidence_integrity": history, "next_minimal_proposal": "B1: six proc files only; no boot files, MMIO or automatic B2", "proposal_source_sha256": digest, "sensitive_evidence": "Raw REVISION and its sidecar restricted; report copy redacted; credential never used", "report": str(OUT / "verification-report.md")}
dump("summary.json", summary)
index = {p.relative_to(OUT).as_posix(): sha(p) for p in sorted(OUT.rglob("*")) if p.is_file() and "__pycache__" not in p.parts and p.name != "evidence-sha256.json"}
dump("evidence-sha256.json", index)
assert all(sha(OUT / p) == h for p, h in index.items())
print(json.dumps({"status": summary["status"], "tests": 43, "old_indexed_files_unchanged": sum(v["files"] for v in history.values()), "new_index_files": len(index), "index_verified": True, "board_operations": 0}))
