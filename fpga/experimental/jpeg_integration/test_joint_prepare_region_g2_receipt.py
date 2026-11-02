from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile


SCRIPT = Path(__file__).with_name("joint_prepare.py")
SPEC = importlib.util.spec_from_file_location("joint_prepare_region_g2_tests", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
joint_prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(joint_prepare)


def _write(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _green_receipt(root: Path, *, source_sha: str | None = None, wns: float = 0.052):
    rtl = root / "rtl" / "rle_region_features.v"
    actual_source_sha = _write(rtl, "module rle_region_features; endmodule\n")
    bound_source_sha = source_sha or actual_source_sha
    testbench = root / "tb_rle_region_features.sv"
    _write(testbench, "module tb_rle_region_features; endmodule\n")
    xsim = root / "tb_rle_region_features.xsim.log"
    _write(xsim, "CASE candidate area upper bound\nRLE_FEATURE_PASS cases=16\n")

    review = root / "cdc-review.md"
    _write(review, "复核日期：2026-11-02\n")
    reports = root / "reports-g2-integrated"
    report_paths = {}
    for name in ("cdc.rpt", "timing.rpt", "utilization.rpt", "drc.rpt"):
        report = reports / name
        contents = ("RAMB36E1 23\nLUT as Logic 23524\n"
                    if name == "utilization.rpt" else f"{name} reviewed\n")
        report_paths[name] = _write(report, contents)

    build_manifest = root / "export" / "build-manifest.json"
    manifest = {
        "schema": "material-counter.g2-sw1-candidate.v1",
        "rtl_sha256": {"rle_region_features.v": actual_source_sha},
        "timing": {
            "wns_ns": wns,
            "whs_ns": 0.023,
            "all_user_constraints_met": True,
            "unrouted_nets": 0,
            "partially_routed_nets": 0,
        },
        "drc": {"errors": 0, "critical_warnings": 0},
        "cdc": {
            "review": "manual_review_complete_2026-11-02",
            "review_record": "../cdc-review.md",
        },
    }
    build_manifest.parent.mkdir(parents=True, exist_ok=True)
    build_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    paths = [rtl, testbench, xsim, build_manifest, review]
    paths.extend(reports / name for name in report_paths)
    receipt = {
        "schema": "material-counter.rle-region-g2-green.v1",
        "status": "PASS",
        "source_sha256": bound_source_sha,
        "checks": {
            "xsim_expected_outputs": True,
            "limits_and_candidate_thresholds": True,
            "g2_integrated_build_reviewed": True,
        },
        "evidence": [
            {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in paths
        ],
    }
    receipt_path = root / "region-green.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    return receipt_path, receipt


def test_region_gate_accepts_source_bound_g2_xsim_and_routed_build_evidence():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        receipt_path, receipt = _green_receipt(root)

        joint_prepare.pass_receipt(
            receipt_path, "source_sha256", receipt["source_sha256"])
        joint_prepare.check_region_receipt(receipt)


def test_region_gate_rejects_g2_build_for_a_different_region_source():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        receipt_path, receipt = _green_receipt(root, source_sha="0" * 64)

        joint_prepare.pass_receipt(
            receipt_path, "source_sha256", receipt["source_sha256"])
        try:
            joint_prepare.check_region_receipt(receipt)
        except ValueError as exc:
            assert "source" in str(exc).lower()
        else:
            raise AssertionError("a G2 build for different RTL was accepted")


def test_region_gate_rejects_negative_g2_timing():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        receipt_path, receipt = _green_receipt(root, wns=-0.001)

        joint_prepare.pass_receipt(
            receipt_path, "source_sha256", receipt["source_sha256"])
        try:
            joint_prepare.check_region_receipt(receipt)
        except ValueError as exc:
            assert "timing" in str(exc).lower()
        else:
            raise AssertionError("a G2 build with negative WNS was accepted")
