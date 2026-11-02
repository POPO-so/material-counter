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


def test_preflight_accepts_current_g2_interface_when_receipt_schema_matches():
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / "rle_region_features.v"
        source.write_text(
            "module rle_region_features; output candidate_flag; "
            "output candidate_reason; input cfg_max_area; input cfg_max_w; "
            "input cfg_max_h; input cfg_aspect_x16; endmodule\n",
            encoding="utf-8")
        joint_prepare.check_region_source(
            source, "material-counter.rle-region-g2-green.v1")


def test_preflight_rejects_current_g2_source_under_legacy_bram_gate():
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / "rle_region_features.v"
        source.write_text(
            "module rle_region_features; output candidate_flag; endmodule\n",
            encoding="utf-8")
        try:
            joint_prepare.check_region_source(source)
        except ValueError as exc:
            assert "BRAM" in str(exc)
        else:
            raise AssertionError("current G2 source was accepted by the legacy gate")


BRAM_G2_SCHEMA = "material-counter.rle-region-bram-g2-green.v1"
BRAM_G2_EVIDENCE = (
    "rle_region_features.v", "tb_region_candidate_filters.sv", "rle_feature_tap.v",
    "region_candidates.log", "region_cases.log", "region_diff.log", "region_limits.log",
    "region_driver.log", "region_inputs.sha256", "region_memory.rpt", "region_util.rpt",
    "region_elab_tap.log",
)


def _write_bram_g2_receipt(root: Path):
    source = root / "rle_region_features.v"
    contents = {
        "rle_region_features.v": (
            "module rle_region_features(input cfg_max_area, input cfg_max_w, input cfg_max_h, "
            "input cfg_aspect_x16, output candidate_flag, output candidate_reason); "
            "reg [135:0] stats_mem; reg [19:0] row_mem; (* ram_style = \"block\" *) endmodule\n"
        ),
        "tb_region_candidate_filters.sv": "module tb_region_candidate_filters; endmodule\n",
        "rle_feature_tap.v": "module rle_feature_tap; endmodule\n",
        "region_candidates.log": "REGION_CANDIDATE_PASS cases=7\n",
        "region_cases.log": "RLE_FEATURE_PASS cases=12\n",
        "region_diff.log": "REGION_DIFF_PASS compared=500\n",
        "region_limits.log": "REGION_LIMITS_PASS row_runs=320 labels=512 overflow=513\n",
        "region_driver.log": (
            "# if {[regexp {Fatal:|ERROR:|watchdog timeout} $transcript]} { error \"REGION_XSIM_FAIL\" }\n"
            "REGION_TAP_LINK_PASS\n"
            "REGION_XSIM_PASS: region_cases.log RLE_FEATURE_PASS cases=12\n"
            "REGION_XSIM_PASS: region_diff.log REGION_DIFF_PASS\n"
            "REGION_XSIM_PASS: region_limits.log REGION_LIMITS_PASS\n"
            "REGION_XSIM_PASS: region_candidates.log REGION_CANDIDATE_PASS cases=7\n"
            "REGION_BRAM_PASS\nREGION_RUN_PASS: synthetic\n"
        ),
        "region_memory.rpt": "stats_bram=4: cells\nrow_bram=1: cells\nstats_lutram=0: cells\n",
        "region_util.rpt": "Block RAM Tile 5\n",
        "region_elab_tap.log": "Elaboration successful\n",
    }
    hashes = {}
    for name, content in contents.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "region_inputs.sha256"
    manifest.write_text("".join(f"{hashes[name]}  {name}\n" for name in BRAM_G2_EVIDENCE[:3]),
                        encoding="ascii")
    hashes["region_inputs.sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    receipt = {
        "schema": BRAM_G2_SCHEMA,
        "status": "PASS",
        "source_sha256": hashes["rle_region_features.v"],
        "evidence": [{"path": str(root / name), "sha256": hashes[name]}
                     for name in BRAM_G2_EVIDENCE],
    }
    return receipt, hashes


def _refresh_bram_g2_evidence(receipt: dict, name: str) -> None:
    item = next(e for e in receipt["evidence"] if Path(e["path"]).name == name)
    item["sha256"] = hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()


def test_bram_g2_schema_accepts_source_bound_candidate_filter_bram_evidence():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        receipt, hashes = _write_bram_g2_receipt(root)

        joint_prepare.check_region_receipt(receipt, hashes["rle_region_features.v"])
        joint_prepare.check_region_source(root / "rle_region_features.v", BRAM_G2_SCHEMA)


def test_bram_g2_schema_rejects_incomplete_or_bad_receipts():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        receipt, _ = _write_bram_g2_receipt(root)
        (root / "region_memory.rpt").write_text(
            "stats_bram=3: cells\nrow_bram=1: cells\nstats_lutram=0: cells\n", encoding="utf-8")
        _refresh_bram_g2_evidence(receipt, "region_memory.rpt")
        try:
            joint_prepare.check_region_receipt(receipt)
        except ValueError as exc:
            assert "memory" in str(exc).lower() or "bram" in str(exc).lower()
        else:
            raise AssertionError("a receipt with the wrong BRAM count was accepted")

        receipt, _ = _write_bram_g2_receipt(root / "missing")
        receipt["evidence"] = [item for item in receipt["evidence"]
                               if Path(item["path"]).name != "region_candidates.log"]
        try:
            joint_prepare.check_region_receipt(receipt)
        except ValueError as exc:
            assert "region_candidates.log" in str(exc)
        else:
            raise AssertionError("a receipt missing candidate evidence was accepted")

        receipt, _ = _write_bram_g2_receipt(root / "hash")
        receipt["evidence"][0]["sha256"] = "0" * 64
        try:
            joint_prepare.check_region_receipt(receipt)
        except ValueError as exc:
            assert "hash" in str(exc).lower()
        else:
            raise AssertionError("a receipt with a forged evidence hash was accepted")


def test_bram_g2_schema_rejects_manifest_mismatch_and_non_pass_log():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        receipt, _ = _write_bram_g2_receipt(root)
        manifest = root / "region_inputs.sha256"
        manifest.write_text("0" * 64 + "  rle_region_features.v\n", encoding="ascii")
        _refresh_bram_g2_evidence(receipt, "region_inputs.sha256")
        try:
            joint_prepare.check_region_receipt(receipt)
        except ValueError as exc:
            assert "manifest" in str(exc).lower() or "hash" in str(exc).lower()
        else:
            raise AssertionError("a mismatched frozen-input manifest was accepted")

        receipt, _ = _write_bram_g2_receipt(root / "marker")
        diff = root / "marker" / "region_diff.log"
        diff.write_text("REGION_DIFF_FAIL\n", encoding="utf-8")
        _refresh_bram_g2_evidence(receipt, "region_diff.log")
        try:
            joint_prepare.check_region_receipt(receipt)
        except ValueError as exc:
            assert "pass" in str(exc).lower() or "marker" in str(exc).lower()
        else:
            raise AssertionError("a non-PASS simulation log was accepted")
