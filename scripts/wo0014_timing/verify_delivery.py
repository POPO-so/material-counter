"""Verify offline evidence and protected boundaries after the timing experiment."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "data/results/wo0014-timing-20261014"
BASE_RESULT = ROOT / "data/results/wo0014-offline-20261014"
BASE = "ca6a68c7e454d87aeda414c65579271ffa948362"

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode().strip()

subprocess.run(["git", "merge-base", "--is-ancestor", BASE, "HEAD"], cwd=ROOT, check=True)
assert git("branch", "--show-current") == "feat/pl-stream-skeleton"
assert not git("diff", "--cached", "--name-only")
for changed in git("diff", "--name-only", BASE).splitlines():
    assert changed == "fpga/experimental/wo0014/axis_dma_rgb_adapter.v" or changed.startswith((
        "scripts/wo0014_timing/", "data/results/wo0014-timing-20261014/",
        "docs/verification/wo-0014-timing-acceptance-20261014.md",
        "docs/verification/wo-0014-dma-length-assessment-20261014.md")), changed
inputs = json.loads((RESULT / "input-hashes.json").read_text())
adapter_path = "fpga/experimental/wo0014/axis_dma_rgb_adapter.v"
assert sha(ROOT / adapter_path) == inputs["sources"][adapter_path.replace("/", "\\")]
subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
before = json.loads((BASE_RESULT / "protected-before.json").read_text(encoding="utf-8-sig"))
after = {name: sha(ROOT / name) for name in before}
assert after == before
board_artifacts = {}
for name in ("wo0013_phase_a.bit", "wo0013_phase_a.hwh"):
    path = "data/results/wo0013-resetfix-20261014/export/" + name
    committed = subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT)
    assert hashlib.sha256(committed).hexdigest() == sha(ROOT / path)
    board_artifacts[name] = sha(ROOT / path)

cases = ["h3-final", "directed-reviewed", "keep3", "keep7", "th0", "th73", "th200", "th255",
         "bad_header", "bad_length", "bad_tkeep", "early_tlast", "missing_tlast", "bad_checksum", "extra_payload"]
markers = {}
for name in cases:
    log = (RESULT / (name + ".log")).read_text()
    assert "RESULT PASS:" in log and "Fatal:" not in log, name
    assert "tb_cycle_equivalence.sv" in log, name
    markers[name] = next(line for line in log.splitlines() if line.startswith("RESULT PASS:"))
legacy = {}
for name in ("legacy-rgb", "legacy-binary-fixed", "legacy-morph-h3", "legacy-monitor"):
    log = (RESULT / (name + ".log")).read_text()
    assert "Fatal:" not in log and ("TEST_PASS" in log or "WO0013_AXIS_RX_PASS" in log)
    legacy[name] = next(line for line in log.splitlines() if "TEST_PASS" in line or "WO0013_AXIS_RX_PASS" in line)
legacy_pixels = {}
for name, expected, count in (
    ("legacy-rgb-output.hex", ROOT / "data/results/rgb2gray-vectors-20261012/expected_gray.hex", 230912),
    ("legacy-binary-output.hex", BASE_RESULT / "legacy-binary-vectors/threshold-128/expected_binary.hex", 231168),
    ("legacy-morph-output.hex", ROOT / "data/results/morphology-vectors-20261013-final/h3_threshold128/expected-mode2.hex", 230400),
):
    actual_words = [int(v, 16) for v in (RESULT / name).read_text().split()]
    expected_words = [int(v, 16) for v in expected.read_text().split()]
    if name == "legacy-rgb-output.hex":
        # The legacy RGB dump stores Gray8 only; its TB separately checks SOF/EOL.
        expected_words = [v & 0xff for v in expected_words]
    assert len(actual_words) == len(expected_words) == count
    assert actual_words == expected_words
    legacy_pixels[name] = {"pixels": count, "mismatches": 0}
h3 = json.loads((RESULT / "reports/h3-comparison.json").read_text())
directed = json.loads((RESULT / "reports/directed-comparison.json").read_text())
assert h3["status"] == directed["status"] == "PASS"
assert (h3["frames"], h3["pixels"], h3["word_mismatches"]) == (487, 37401600, 0)
assert "134 passed" in (RESULT / "pytest.txt").read_text(encoding="utf-8-sig")
timing = (RESULT / "reports/timing-ooc.rpt").read_text()
wns = float(re.search(r"Worst Slack\s+([-\d.]+)ns", timing).group(1))
assert wns >= 0 and "0  Failing Endpoints" in timing
utilization = (RESULT / "reports/utilization.rpt").read_text()
luts = int(re.search(r"Slice LUTs\*\s*\|\s*(\d+)", utilization).group(1))
ffs = int(re.search(r"Slice Registers\s*\|\s*(\d+)", utilization).group(1))
assert (luts, ffs) == (668, 377)
summary = {
    "baseline_commit": BASE, "optimization_committed": not bool(git("diff", "--name-only", "HEAD", "--", adapter_path)),
    "protected_and_production_unchanged": True, "protected_sha256": after,
    "wo0013_artifacts_sha256": board_artifacts,
    "xsim_cases": 15, "cycle_equivalence": "PASS for all 15 recorded scenarios; not formal proof",
    "xsim_markers": markers, "h3_frames": 487, "h3_pixels": 37401600, "h3_mismatches": 0,
    "h3_actual_sha256": h3["actual_sha256"], "directed_golden": directed["status"],
    "python_passed": 134, "legacy_markers": legacy, "legacy_pixel_comparisons": legacy_pixels,
    "ooc": {"clock_period_ns": 10, "wns_ns": wns, "tns_ns": 0, "lut": luts, "ff": ffs,
            "bram": 0, "dsp": 0, "scope": "synthesized OOC register paths; system IO and implementation unverified"},
    "hardware_actions": 0, "push_or_merge": False, "diff_check": "PASS",
}
(RESULT / "delivery-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
sources = [ROOT / "fpga/experimental/wo0014/axis_dma_rgb_adapter.v",
           *sorted((ROOT / "scripts/wo0014_timing").glob("*.py")),
           *sorted((ROOT / "scripts/wo0014_timing").glob("*.tcl")),
           ROOT / "docs/verification/wo-0014-timing-acceptance-20261014.md",
           ROOT / "docs/verification/wo-0014-dma-length-assessment-20261014.md"]
evidence = [p for p in RESULT.glob("*.log")] + [RESULT / "pytest.txt", RESULT / "delivery-summary.json"]
evidence += list((RESULT / "reports").glob("*.json")) + list((RESULT / "reports").glob("*.rpt"))
evidence += list((RESULT / "generated").glob("*"))
manifest = {str(p.relative_to(ROOT)): sha(p) for p in sources + evidence if p.is_file()}
(RESULT / "evidence-sha256.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
(RESULT / "git-status.txt").write_text(git("status", "--short") + "\n", encoding="utf-8")
print(json.dumps({"baseline_commit": BASE, "xsim": 15, "python": 134, "h3_pixels": 37401600,
                  "h3_mismatches": 0, "legacy": 4, "wns_ns": wns, "lut": luts, "ff": ffs,
                  "protected_unchanged": True, "diff_check": "PASS"}, indent=2))
