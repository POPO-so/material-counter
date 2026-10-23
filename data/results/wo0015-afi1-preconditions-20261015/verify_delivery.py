"""Verify local delivery/evidence only. Never imports any board IO backend."""
import ast
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

checked = {}
for name in ("wo0015-afi1-readonly-20261015", "wo0015-step2-mismatch-audit-20261015", "wo0015-runonly-resume-retry2-20261015"):
    folder = ROOT / "data/results" / name
    index = json.loads((folder / "evidence-sha256.json").read_bytes())
    bad = [p for p, h in index.items() if not (folder / p).is_file() or sha(folder / p) != h]
    assert not bad, (name, bad)
    checked[name] = {"files": len(index), "mismatches": bad}

locks = {"wo15.bit": "a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420", "wo15.hwh": "bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6"}
for name, expected in locks.items():
    assert sha(ROOT / "data/results/wo0015-hw-50mhz-20261015/export" / name) == expected

init = json.loads((OUT / "init-research/source-evidence.json").read_bytes())
for source in init["sources"]:
    if "sha256" in source:
        assert sha(ROOT / source["path"]) == source["sha256"], source["path"]
docs = json.loads((OUT / "docs-research/official-sources.json").read_bytes())
doc = docs["local_official_document"]
assert sha(OUT / "docs-research" / doc["local_pdf"]).upper() == doc["pdf_sha256"].upper()
assert sha(OUT / "docs-research" / doc["local_text_extract"]).upper() == doc["extract_sha256"].upper()

tree = ast.parse((OUT / "readonly_admission_collector.py").read_text(encoding="utf-8"))
imports = [n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)]
assert not set(imports) & {"pynq", "mmap", "socket", "subprocess", "paramiko"}
calls = sorted({ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)})
assert not set(calls) & {"os.write", "os.system", "eval", "exec", "open"}
write("source-audit.json", {"imports": imports, "calls": calls, "collector_sha256": sha(OUT / "readonly_admission_collector.py"), "method": "AST plus manual independent review and fake backend tests; AST alone is not proof of all runtime behavior", "device_or_register_backend": False, "real_backend_executed": False})
suite = ET.parse(OUT / "final-offline-tests.xml").getroot().find("testsuite")
assert suite is not None and suite.attrib["tests"] == "76"
assert suite.attrib["failures"] == suite.attrib["errors"] == "0"

for name, directory in (("worktree", ROOT), ("main", Path("C:/fpga/material-counter"))):
    info = {}
    for label, args in (("status", ["status", "--porcelain=v1"]), ("head", ["rev-parse", "HEAD"]), ("tracked_diff_check", ["diff", "--check"])):
        proc = subprocess.run(["git", "-C", str(directory), *args], capture_output=True, check=True)
        (OUT / f"{name}-{label}.txt").write_bytes(proc.stdout)
        info[label] = proc.stdout.decode("utf-8")
    write(f"{name}-git.json", info)

summary = {"status": "STOPPED_PRECONDITION_UNVERIFIED", "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "board_connections": 0, "register_reads": 0, "register_writes": 0, "vivado_executions": 0, "overlay_loads": 0, "dma_runs": 0, "commits_or_merges": 0, "hardware_artifact_changes": False, "collector_new_tests_passed": 62, "prior_reader_regression_passed": 14, "total_targeted_passed": 76, "historical_files_integrity": checked, "hardware_locks": locks, "collector_sha256": sha(OUT / "readonly_admission_collector.py"), "current_afi1_raw": None, "current_reset_raw": None, "latest_portal_version_verified": "UG585 v1.15 / 2026-02-06", "current_reset_fragment_exact_version": "UNKNOWN (index says All; v1.15 collection association INFERRED)", "conflict": "Named AXDS reset precondition vs reserved field definition", "current_hardware_access_admission": "UNKNOWN / NOT_GRANTED", "report": str(OUT / "verification-report.md")}
summary["completed_local"] = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()
write("summary.json", summary)
index = {p.relative_to(OUT).as_posix(): sha(p) for p in sorted(OUT.rglob("*")) if p.is_file() and "__pycache__" not in p.parts and p.name != "evidence-sha256.json"}
write("evidence-sha256.json", index)
assert all(sha(OUT / p) == h for p, h in index.items())
print(json.dumps({"status": summary["status"], "targeted_tests_passed": 76, "previous_evidence_files_unchanged": sum(x["files"] for x in checked.values()), "indexed_files": len(index), "index_verified": True, "collector_sha256": summary["collector_sha256"]}))
