"""Offline tests for the scoped joint Vivado capture review."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from joint_capture_review import AuditError, audit_capture, main


class SynthCaptureAuditTests(unittest.TestCase):
    def make_receipt(self, root: Path, lines: list[str]) -> dict:
        native_log = root / "vivado.log"
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        native_log.write_bytes(payload)
        warnings = [line for line in lines if line.startswith("WARNING:")]
        return {
            "scope": "synth",
            "status": "FAIL",
            "code": 0,
            "capture_error": (
                "CAPTURE_FAIL:native message export incomplete or counters changed: "
                f"WARNING emitted=-8 exported={len(warnings)}"
            ),
            "warning_before": 12,
            "warning_after": 4,
            "critical_before": 0,
            "critical_after": sum(line.startswith("CRITICAL WARNING:") for line in lines),
            "error_before": 0,
            "error_after": sum(line.startswith("ERROR:") for line in lines),
            "warning_count": len(warnings),
            "critical_count": sum(line.startswith("CRITICAL WARNING:") for line in lines),
            "error_count": sum(line.startswith("ERROR:") for line in lines),
            "start": 0,
            "end": len(payload),
            "native_log": native_log,
            "raw": "synth_design Complete",
            "messages": lines,
            "warning_messages": warnings,
        }

    def test_synth_scope_accepts_only_exact_safe_warning_slice(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.make_receipt(
                Path(directory), ["WARNING: [Synth 8-3333] unused signal removed."]
            )

            audited = audit_capture(receipt)

            self.assertEqual(audited["status"], "PASS_SUPPLEMENTAL_REVIEW")
            self.assertEqual(
                audited["scope"],
                "synth only; original ipcfg capture receipt remains FAIL",
            )

    def test_run_scope_accepts_positive_warning_counter_delta(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.make_receipt(
                Path(directory), ["WARNING: [Synth 8-3333] unused signal removed."]
            )
            receipt.update({
                "scope": "run",
                "warning_before": 0,
                "warning_after": 1,
                "capture_error": (
                    "CAPTURE_FAIL:native message export incomplete or counters changed: "
                    "WARNING emitted=1 exported=1"
                ),
                "expected_result": "JOINT_SYNTH_RUN_COMPLETE",
                "raw": "JOINT_SYNTH_RUN_COMPLETE",
            })

            audited = audit_capture(receipt)

            self.assertEqual(audited["status"], "PASS_SUPPLEMENTAL_REVIEW")
            self.assertEqual(audited["warning_counter_delta"], 1)

    def test_synth_scope_rejects_any_critical_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.make_receipt(
                Path(directory),
                [
                    "WARNING: [Synth 8-3333] unused signal removed.",
                    "CRITICAL WARNING: [Timing 38-249] no logical paths.",
                ],
            )

            with self.assertRaisesRegex(AuditError, "ERROR or CRITICAL WARNING"):
                audit_capture(receipt)

    def test_run_scope_keeps_critical_warning_as_manager_review_required(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.make_receipt(
                Path(directory),
                [
                    "WARNING: [Synth 8-3333] unused signal removed.",
                    "CRITICAL WARNING: [Timing 38-249] no logical paths.",
                ],
            )
            receipt["scope"] = "run"
            receipt["expected_result"] = "JOINT_SYNTH_RUN_COMPLETE"
            receipt["raw"] = receipt["expected_result"]

            audited = audit_capture(receipt)

            self.assertEqual(audited["status"], "REVIEW_REQUIRED")
            self.assertTrue(audited["manager_review_required"])
            self.assertEqual(audited["severity_counts"]["CRITICAL WARNING"], 1)

    def test_run_scope_rejects_error_severity(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.make_receipt(
                Path(directory),
                [
                    "WARNING: [Synth 8-3333] unused signal removed.",
                    "ERROR: [Synth 8-9999] failed synthesis.",
                ],
            )
            receipt["scope"] = "run"
            receipt["expected_result"] = "JOINT_SYNTH_RUN_COMPLETE"
            receipt["raw"] = receipt["expected_result"]

            with self.assertRaisesRegex(AuditError, "ERROR"):
                audit_capture(receipt)

    def test_run_scope_accepts_nonseverity_save_output_when_counters_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native_log = root / "vivado.log"
            prefix = b"earlier warning already captured\n"
            saved_info = b"Wrote  : <display.bd>\n"
            native_log.write_bytes(prefix + saved_info)
            receipt = {
                "scope": "run",
                "status": "FAIL",
                "code": 0,
                "capture_error": (
                    "CAPTURE_FAIL:native message export incomplete or counters changed: "
                    "WARNING emitted=-59 exported=0"
                ),
                "warning_before": 59,
                "warning_after": 0,
                "critical_before": 0,
                "critical_after": 0,
                "error_before": 0,
                "error_after": 0,
                "warning_count": 0,
                "critical_count": 0,
                "error_count": 0,
                "raw": "SUCCESS:joint_bd_saved",
                "expected_result": "SUCCESS:joint_bd_saved",
                "messages": [],
                "native_log": native_log,
                "start": len(prefix),
                "end": len(prefix) + len(saved_info),
            }

            audited = audit_capture(receipt)

            self.assertEqual(audited["status"], "PASS_SUPPLEMENTAL_REVIEW")
            self.assertEqual(audited["severity_counts"], {
                "WARNING": 0, "CRITICAL WARNING": 0, "ERROR": 0,
            })

    def test_synth_scope_rejects_ipcfg_unsafe_warning_categories(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.make_receipt(
                Path(directory),
                ["WARNING: [Vivado 12-4739] Ignoring constraint because pin does not exist."],
            )

            with self.assertRaisesRegex(AuditError, "unsafe warning"):
                audit_capture(receipt)

    def test_refresh_region_scope_keeps_its_expected_path_check(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.make_receipt(
                Path(directory), ["WARNING: [IP_Flow 19-11770] expected refresh warning."]
            )
            receipt.pop("scope")
            receipt["expected_result"] = "C:/stage/display_image_pipeline_0.xci"
            receipt["raw"] = receipt["expected_result"]

            audited = audit_capture(receipt)

            self.assertEqual(audited["status"], "PASS_SUPPLEMENTAL_REVIEW")
            self.assertEqual(
                audited["scope"],
                "refresh_region only; original ipcfg capture receipt remains FAIL",
            )

    def test_cli_reads_messages_from_file_and_records_its_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            warning = "WARNING: [Synth 8-3333] unused signal removed."
            receipt = self.make_receipt(root, [warning])
            capture_path = root / "capture.tcldict"
            capture_path.write_text("status FAIL\n", encoding="utf-8")
            messages_file = root / "messages.txt"
            messages_file.write_text(warning + "\n", encoding="utf-8")
            output = root / "review.json"
            payload = receipt["native_log"].read_bytes()
            arguments = [
                "joint_capture_review.py",
                "--capture-receipt", str(capture_path),
                "--scope", "synth",
                "--status", "FAIL",
                "--code", "0",
                "--capture-error", receipt["capture_error"],
                "--raw-result", receipt["raw"],
                "--warning-before", str(receipt["warning_before"]),
                "--warning-after", str(receipt["warning_after"]),
                "--critical-before", "0",
                "--critical-after", "0",
                "--error-before", "0",
                "--error-after", "0",
                "--warning-count", "1",
                "--critical-count", "0",
                "--error-count", "0",
                "--native-log", str(receipt["native_log"]),
                "--start", "0",
                "--end", str(len(payload)),
                "--messages-file", str(messages_file),
                "--out", str(output),
            ]

            with patch.object(sys, "argv", arguments), contextlib.redirect_stdout(io.StringIO()):
                exit_code = main()

            self.assertEqual(exit_code, 0)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "PASS_SUPPLEMENTAL_REVIEW")
            self.assertEqual(
                result["capture_messages_sha256"],
                hashlib.sha256(messages_file.read_bytes()).hexdigest(),
            )

    def test_run_cli_reports_critical_warning_as_review_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = self.make_receipt(
                root,
                [
                    "WARNING: [Synth 8-3333] unused signal removed.",
                    "CRITICAL WARNING: [Timing 38-249] no logical paths.",
                ],
            )
            receipt["scope"] = "run"
            receipt["expected_result"] = "JOINT_SYNTH_RUN_COMPLETE"
            receipt["raw"] = receipt["expected_result"]
            capture_path = root / "capture.tcldict"
            capture_path.write_text("status FAIL\n", encoding="utf-8")
            messages_path = root / "messages.txt"
            messages_path.write_text("\n".join(receipt["messages"]) + "\n", encoding="utf-8")
            output = root / "review.json"
            payload = receipt["native_log"].read_bytes()
            arguments = [
                "joint_capture_review.py", "--capture-receipt", str(capture_path),
                "--scope", "run", "--status", "FAIL", "--code", "0",
                "--capture-error", receipt["capture_error"], "--raw-result", receipt["raw"],
                "--expected-result", receipt["expected_result"],
                "--warning-before", "12", "--warning-after", "4",
                "--critical-before", "0", "--critical-after", "1",
                "--error-before", "0", "--error-after", "0",
                "--warning-count", "1", "--critical-count", "1", "--error-count", "0",
                "--native-log", str(receipt["native_log"]), "--start", "0",
                "--end", str(len(payload)), "--messages-file", str(messages_path),
                "--out", str(output),
            ]

            with patch.object(sys, "argv", arguments), contextlib.redirect_stdout(io.StringIO()) as stdout:
                exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertIn("JOINT_CAPTURE_REVIEW_REQUIRED", stdout.getvalue())
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "REVIEW_REQUIRED")
            self.assertTrue(result["manager_review_required"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
