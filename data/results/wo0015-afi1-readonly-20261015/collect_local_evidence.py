"""Local files only: no subprocess, network, PYNQ, or device access."""
import ast
import datetime
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
RESULTS = ROOT / "data/results"

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

history = {}
for name in ["wo0015-step2-mismatch-audit-20261015", "wo0015-runonly-resume-retry2-20261015"]:
    folder = RESULTS / name
    index = json.loads((folder / "evidence-sha256.json").read_bytes())
    bad = [p for p, digest in index.items() if not (folder / p).is_file() or sha(folder / p) != digest]
    history[name] = {"indexed_files": len(index), "mismatches": bad}
    assert not bad, (name, bad)

old = RESULTS / "wo0015-runonly-resume-retry2-20261015"
saved = json.loads((old / "exit-os-result.json").read_bytes())
src = RESULTS / "wo0015-hw-50mhz-20261015"
artifacts = {}
for ext, expected in [("bit", "a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420"), ("hwh", "bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6")]:
    path = src / "export" / ("wo15." + ext)
    actual = sha(path)
    assert actual == expected
    artifacts[str(path.relative_to(ROOT))] = actual

extracts = {}
for rel, spans in {
    "scripts/wo0015/create_s2mm_bd.tcl": [(153, 171), (191, 194), (245, 253), (284, 288)],
    "data/results/wo0015-hw-50mhz-20261015/export/wo15.hwh": [(998, 1013), (4110, 4115), (5104, 5110)],
    "data/results/wo0015-hw-50mhz-20261015/project/wo15_50m.gen/sources_1/bd/wo15/ip/wo15_ps7_0/ps7_init.tcl": [(150, 160)],
    "data/results/wo0015-hw-50mhz-20261015/project/wo15_50m.gen/sources_1/bd/wo15/ip/wo15_ps7_0/ps7_init.html": [(28122, 28190), (28260, 28325)],
}.items():
    path = ROOT / rel
    lines = path.read_text(encoding="utf-8").splitlines()
    extracts[rel] = {"sha256": sha(path), "extracts": [{"start": a, "end": b, "text": "\n".join(f"{i}: {lines[i-1]}" for i in range(a, b + 1))} for a, b in spans]}

tree = ast.parse((OUT / "afi1_readonly_candidate.py").read_text(encoding="utf-8"))
imports = [n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)]
calls = sorted({ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)})
dump("source-and-script-audit.json", {"source_extracts": extracts, "candidate_sha256": sha(OUT / "afi1_readonly_candidate.py"), "imports": imports, "calls": calls, "live_verifier_available": False, "board_execution_cleared": False, "review": "Only O_RDONLY and PROT_READ. No transport, PYNQ import, initialization, writable mapping, register write, reset read, shutdown, CLI, or retry. External verifier is a trust boundary, NOT an implemented live safety check."})
dump("local-evidence.json", {"recorded_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "scope": "OFFLINE_ONLY", "board_connections": 0, "register_reads": 0, "register_writes": 0, "artifacts": artifacts, "history_integrity": history, "historical_exit_sha256": sha(old / "exit-os-result.json"), "historical_only": {k: saved[k] for k in ["boot_id", "cma", "ownership", "fpga_manager", "deployed_sha256", "final_registers"]}, "runtime_afi1_wr": None, "runtime_afi1_rd": None, "current_reset_state": None, "status": "STOPPED_PRECONDITION_UNVERIFIED"})
dump("candidate-default-result.json", __import__("afi1_readonly_candidate").read_once())
print(json.dumps({"history": history, "artifacts_match": True, "board_access": 0}))
