import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("joint_capture_review.py")
if not MODULE_PATH.is_file():
    raise AssertionError("joint_capture_review.py is not implemented yet")
SPEC = importlib.util.spec_from_file_location("joint_capture_review", MODULE_PATH)
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


WARNING = b"WARNING: [IP_Flow 19-11770] Clock interface 'aclk' has no FREQ_HZ parameter."


def inputs(tmp_path, log_bytes=None, **overrides):
    log = tmp_path / "native.log"
    log.write_bytes(log_bytes if log_bytes is not None else b"INFO: start\n" + WARNING + b"\n" + WARNING + b"\nINFO: done\n")
    receipt = {
        "status": "FAIL",
        "code": 0,
        "capture_error": "CAPTURE_FAIL:native message export incomplete or counters changed: WARNING emitted=-5 exported=2",
        "raw": "c:/stage/display_image_pipeline_0.xci",
        "expected_result": "C:/stage/display_image_pipeline_0.xci",
        "warning_before": 9,
        "warning_after": 4,
        "warning_messages": [WARNING.decode(), WARNING.decode()],
        "native_log": str(log),
        "start": 12,
        "end": 12 + len(WARNING) * 2 + 2,
    }
    if log_bytes is None:
        receipt["start"] = len(b"INFO: start\n")
        receipt["end"] = receipt["start"] + len(WARNING) * 2 + 2
    else:
        receipt["start"] = 0
        receipt["end"] = len(log_bytes)
    receipt.update(overrides)
    return receipt, log


class JointCaptureReviewTests(unittest.TestCase):
    def test_accepts_only_exact_warning_counter_reset_and_expected_result(self):
        with self.subTest("strict native byte range review"):
            import tempfile
            with tempfile.TemporaryDirectory() as directory:
                receipt, log = inputs(Path(directory))
                result = review.audit_capture(receipt)
                self.assertEqual(result["status"], "PASS_SUPPLEMENTAL_REVIEW")
                self.assertEqual(result["severity_counts"], {"WARNING": 2, "CRITICAL WARNING": 0, "ERROR": 0})
                self.assertEqual(result["native_log"], str(log))
                self.assertIn("snapshot at this review time", result["native_log_sha256_scope_at_review"])

    def test_range_hash_is_stable_when_native_log_later_grows(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            receipt, log = inputs(Path(directory))
            first = review.audit_capture(receipt)
            log.write_bytes(log.read_bytes() + b"INFO: later Vivado output\n")
            later = review.audit_capture(receipt)
            self.assertEqual(first["native_log_range_sha256"], later["native_log_range_sha256"])
            self.assertNotEqual(first["native_log_sha256"], later["native_log_sha256"])

    def test_rejects_nonzero_tcl_code(self):
        with self._expect_audit_error(code=1):
            pass

    def test_rejects_nonmatching_capture_error(self):
        with self._expect_audit_error(capture_error="CAPTURE_FAIL:ERROR emitted=0 exported=0"):
            pass

    def test_rejects_unverifiable_byte_range(self):
        with self._expect_audit_error(start=-1):
            pass

    def test_rejects_range_ending_past_log(self):
        with self._expect_audit_error(end=10_000):
            pass

    def test_rejects_range_ending_mid_line(self):
        with self._expect_audit_error(end=12 + len(WARNING) - 1):
            pass

    def test_rejects_range_starting_mid_line(self):
        with self._expect_audit_error(start=1):
            pass

    def test_rejects_missing_expected_warning_lines(self):
        with self._expect_audit_error(log_bytes=b"INFO: no warning\n"):
            pass

    def test_rejects_warning_line_mismatch(self):
        with self._expect_audit_error(warning_messages=["WARNING: unexpected warning"]):
            pass

    def test_rejects_native_error(self):
        with self._expect_audit_error(log_bytes=b"ERROR: [BD 1-1] failed\n"):
            pass

    def test_rejects_native_critical_warning(self):
        with self._expect_audit_error(log_bytes=b"CRITICAL WARNING: [BD 1-1] risky\n"):
            pass

    def test_rejects_unexpected_result(self):
        with self._expect_audit_error(raw="C:/stage/other.xci"):
            pass

    def test_rejects_counter_delta_export_count_mismatch(self):
        with self._expect_audit_error(capture_error="CAPTURE_FAIL:native message export incomplete or counters changed: WARNING emitted=-5 exported=1"):
            pass

    def test_cli_writes_a_pass_supplement_without_changing_capture(self):
        import json
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt, _ = inputs(root)
            original = root / "original.tcldict"
            original.write_text("preserve original FAIL receipt", encoding="utf-8")
            out = root / "supplement.json"
            argv = ["joint_capture_review.py", "--capture-receipt", str(original),
                    "--status", receipt["status"], "--code", "0",
                    "--capture-error", receipt["capture_error"], "--raw-result", receipt["raw"],
                    "--expected-result", receipt["expected_result"], "--warning-before", "9",
                    "--warning-after", "4", "--native-log", receipt["native_log"],
                    "--start", str(receipt["start"]), "--end", str(receipt["end"]),
                    "--warning-message", WARNING.decode(), "--warning-message", WARNING.decode(),
                    "--out", str(out)]
            with patch("sys.argv", argv):
                self.assertEqual(review.main(), 0)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["status"], "PASS_SUPPLEMENTAL_REVIEW")
            self.assertEqual(original.read_text(encoding="utf-8"), "preserve original FAIL receipt")

    def test_synth_scope_accepts_warning_only_reset_without_result_pair(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            receipt, _ = inputs(Path(directory))
            receipt.pop("raw")
            receipt.pop("expected_result")
            result = review.audit_synth_capture(receipt)
            self.assertEqual(result["status"], "PASS_SUPPLEMENTAL_REVIEW")
            self.assertIn("synth only", result["scope"])
            self.assertNotIn("expected_result", result)
            self.assertEqual(result["severity_counts"], {"WARNING": 2, "CRITICAL WARNING": 0, "ERROR": 0})

    def test_synth_scope_rejects_critical_warning_in_range(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            receipt, _ = inputs(Path(directory))
            log = Path(receipt["native_log"])
            critical = (b"CRITICAL WARNING: [Timing 38-249] Generated clock has no "
                        b"logical paths from master clock hdmi_out_PXL_CLK_O.\n")
            log.write_bytes(log.read_bytes() + critical)
            receipt["end"] = len(log.read_bytes())
            with self.assertRaises(review.AuditError):
                review.audit_synth_capture(receipt)

    def test_synth_scope_rejects_native_error_in_range(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            receipt, _ = inputs(Path(directory))
            log = Path(receipt["native_log"])
            error = b"ERROR: [Synth 8-439] module not found\n"
            log.write_bytes(log.read_bytes() + error)
            receipt["end"] = len(log.read_bytes())
            with self.assertRaises(review.AuditError):
                review.audit_synth_capture(receipt)

    def test_synth_cli_writes_pass_supplement_without_result_pair(self):
        import json
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt, _ = inputs(root)
            original = root / "original.tcldict"
            original.write_text("preserve original FAIL receipt", encoding="utf-8")
            out = root / "synth-supplement.json"
            argv = ["joint_capture_review.py", "--scope", "synth",
                    "--capture-receipt", str(original),
                    "--status", receipt["status"], "--code", "0",
                    "--capture-error", receipt["capture_error"],
                    "--warning-before", "9", "--warning-after", "4",
                    "--native-log", receipt["native_log"],
                    "--start", str(receipt["start"]), "--end", str(receipt["end"]),
                    "--warning-message", WARNING.decode(), "--warning-message", WARNING.decode(),
                    "--out", str(out)]
            with patch("sys.argv", argv):
                self.assertEqual(review.main(), 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "PASS_SUPPLEMENTAL_REVIEW")
            self.assertIn("synth only", payload["scope"])
            self.assertEqual(original.read_text(encoding="utf-8"), "preserve original FAIL receipt")

    def test_synth_cli_reads_warning_messages_from_file(self):
        import json
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt, _ = inputs(root)
            messages = root / "messages.txt"
            messages.write_text(WARNING.decode() + "\n" + WARNING.decode() + "\n", encoding="utf-8")
            original = root / "original.tcldict"
            original.write_text("preserve original FAIL receipt", encoding="utf-8")
            out = root / "synth-supplement.json"
            argv = ["joint_capture_review.py", "--scope", "synth",
                    "--capture-receipt", str(original),
                    "--status", receipt["status"], "--code", "0",
                    "--capture-error", receipt["capture_error"],
                    "--warning-before", "9", "--warning-after", "4",
                    "--native-log", receipt["native_log"],
                    "--start", str(receipt["start"]), "--end", str(receipt["end"]),
                    "--messages-file", str(messages),
                    "--out", str(out)]
            with patch("sys.argv", argv):
                self.assertEqual(review.main(), 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "PASS_SUPPLEMENTAL_REVIEW")
            self.assertEqual(payload["warning_messages_file"], str(messages.resolve()))
            self.assertIn("warning_messages_sha256", payload)

    class _expect_audit_error:
        def __init__(self, **overrides):
            self.overrides = overrides

        def __enter__(self):
            import tempfile
            self.temp = tempfile.TemporaryDirectory()
            receipt, _ = inputs(Path(self.temp.name), **self.overrides)
            with self.assertRaises(review.AuditError):
                review.audit_capture(receipt)
            return self

        def __exit__(self, exc_type, exc, tb):
            self.temp.cleanup()
            return False

        @staticmethod
        def assertRaises(exception):
            return unittest.TestCase().assertRaises(exception)


if __name__ == "__main__":
    unittest.main()
