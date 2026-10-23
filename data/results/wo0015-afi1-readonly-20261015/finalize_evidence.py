"""Finalize local evidence only; no board or production-file mutations."""
import datetime
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

out = Path(__file__).resolve().parent
suite = ET.parse(out / "offline-tests.xml").getroot().find("testsuite")
assert suite is not None and suite.attrib["tests"] == "14"
assert suite.attrib["failures"] == "0" and suite.attrib["errors"] == "0"
ev = json.loads((out / "local-evidence.json").read_bytes())
assert all(not x["mismatches"] for x in ev["history_integrity"].values())
summary = {"status": "STOPPED_PRECONDITION_UNVERIFIED", "recorded_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "phase1": "OFFLINE_REVIEW_COMPLETED_WITH_BLOCKERS", "phase2": "NOT_STARTED", "board_connections": 0, "register_reads": 0, "register_writes": 0, "afi1_wr_raw": None, "afi1_rd_raw": None, "static_32_consistent": None, "configuration_anomaly_confirmed": False, "root_cause_confirmed": False, "offline_candidate_tests_passed": 14, "candidate_is_live_executor": False, "production_logic_changed": False, "historical_files_verified": sum(x["indexed_files"] for x in ev["history_integrity"].values()), "script_sha256": hashlib.sha256((out / "afi1_readonly_candidate.py").read_bytes()).hexdigest(), "blockers": ["No trusted current AXDS reset-state evidence", "AXDS field applicability unresolved against generated reserved field", "No audited live precondition verifier"], "report": str(out / "verification-report.md")}
(out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
index = {p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob("*")) if p.is_file() and "__pycache__" not in p.parts and p.name != "evidence-sha256.json"}
(out / "evidence-sha256.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
assert all(hashlib.sha256((out / rel).read_bytes()).hexdigest() == digest for rel, digest in index.items())
print(json.dumps({"status": summary["status"], "tests": 14, "board_access": 0, "indexed_files": len(index), "manifest_verified": True, "script_sha256": summary["script_sha256"]}))
