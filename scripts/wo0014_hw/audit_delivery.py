"""Audit the completed offline integration without connecting to any board."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "data/results/wo0014-hw-verification-20261014"
OPT = "6264b324385eb8d90033d14eec3f471c1317273f"

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode().strip()

require(git("rev-parse", "HEAD") == OPT, "Optimization commit changed")
require(git("branch", "--show-current") == "feat/pl-stream-skeleton", "Wrong branch")
require(not git("diff", "--name-only") and not git("diff", "--cached", "--name-only"), "Unexpected tracked/staged changes")
subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
protected = json.loads((ROOT / "data/results/wo0014-offline-20261014/protected-before.json").read_text(encoding="utf-8-sig"))
for name, digest in protected.items():
    require(sha(ROOT / name) == digest, "Protected file changed: " + name)
wo13 = {}
for name in ("wo0013_phase_a.bit", "wo0013_phase_a.hwh"):
    relative = "data/results/wo0013-resetfix-20261014/export/" + name
    expected = subprocess.check_output(["git", "show", f"47103338:{relative}"], cwd=ROOT)
    wo13[name] = sha(ROOT / relative)
    require(hashlib.sha256(expected).hexdigest() == wo13[name], "WO-0013 artifact changed")
for name in ("wrapper-small.log", "wrapper-full.log"):
    log = (RESULT / name).read_text()
    require("RESULT PASS:" in log and "Fatal:" not in log and "ERROR:" not in log, "Wrapper regression failed: " + name)
require("Ran 11 tests" in (RESULT / "board-script-tests.txt").read_text() and
        "\nOK" in (RESULT / "board-script-tests.txt").read_text(), "Board-script mock tests incomplete")
require("Negative implemented timing slack" in (RESULT / "negative-gate-test.txt").read_text(), "Negative timing control absent")
builds = {}
evidence = []
for clock in (50, 100):
    build = ROOT / f"data/results/wo0014-hw-{clock}mhz-20261014"
    summary = json.loads((build / "reports/build-summary.json").read_text())
    manifest = json.loads((build / "export/pair-manifest.json").read_text())
    dry = json.loads((build / "reports/pynq-dry-run.json").read_text())
    require(dry["status"] == "DRY_RUN_READY" and not dry["hardware_attempted"] and dry["send_attempt_count"] == 0, "Hardware dry-run contract violated")
    for key in ("bit", "hwh"):
        require(sha(Path(summary[key])) == manifest[key + "_sha256"], "Export artifact hash changed")
    for filename, digest in manifest["rtl_sha256"].items():
        require(sha(build / "rtl" / filename) == digest, "Build source changed")
    require(summary["wns"] >= 0 and summary["whs"] >= 0 and summary["tns"] == summary["ths"] == 0, "Implemented timing did not close")
    builds[str(clock)] = summary
    evidence += [p for directory in ("reports", "export", "rtl", "ipcfg_evidence")
                 for p in (build / directory).glob("*") if p.is_file()]
    evidence += list((build / ".vivado-ai").glob("*.log"))
    evidence += list((build / "project" / f"wo0014_image_{clock}m.runs").glob("*/runme.log"))
sources = [ROOT / "fpga/experimental/wo0014/axis_dma_image_pipeline_bd.v",
           ROOT / "fpga/sim/wo0014/tb_axis_dma_image_pipeline_bd.sv",
           *sorted((ROOT / "scripts/wo0014_hw").glob("*.py")),
           *sorted((ROOT / "scripts/wo0014_hw").glob("*.tcl")),
           ROOT / "docs/verification/wo-0014-hw-integration-20261014.md"]
for path in sources:
    require(path.is_file(), "Missing delivery source: " + str(path))
    for index, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        require(line == line.rstrip(), f"Trailing whitespace: {path}:{index}")
summary = {"optimization_commit": OPT, "functional_baseline_commit": "ca6a68c7e454d87aeda414c65579271ffa948362",
           "protected_sha256": protected, "wo0013_artifact_sha256": wo13,
           "builds": builds, "wrapper_xsim": "PASS small/recovery/AXI-Lite and full 320x240 golden",
           "board_script_mock_tests": 11, "negative_gate_under_python_O": "PASS",
           "hardware_actions": 0, "push_or_merge": False, "integration_committed": False,
           "ready_for_approval": "One 50MHz legal-packet diagnostic board test; residual DRC/reset risks retained",
           "diff_check": "PASS"}
(RESULT / "delivery-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
evidence += [p for p in RESULT.glob("*") if p.is_file()]
digests = {str(p.relative_to(ROOT)): sha(p) for p in sources + evidence if p.name != "evidence-sha256.json"}
(RESULT / "evidence-sha256.json").write_text(json.dumps(digests, indent=2), encoding="utf-8")
(RESULT / "git-status.txt").write_text(git("status", "--short") + "\n", encoding="utf-8")
print(json.dumps({"optimization_commit": OPT, "protected_unchanged": True, "builds": builds,
                  "hardware_actions": 0, "diff_check": "PASS"}, indent=2))
