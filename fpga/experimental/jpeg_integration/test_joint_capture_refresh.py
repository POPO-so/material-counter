"""Regression tests for the narrow refresh_region capture-review bridge."""

import tempfile
import tkinter
import unittest
from pathlib import Path


COMMON = Path(__file__).with_name("joint_common.tcl")
CONNECT = Path(__file__).with_name("joint_connect.tcl")


class RefreshCaptureBridgeTests(unittest.TestCase):
    def test_ip_repository_setup_is_idempotent(self):
        tcl = tkinter.Tcl()
        tcl.eval(f"source {{{COMMON.as_posix()}}}")
        tcl.eval(r"""
            set ::repositories [list C:/stage/jpeg_ip]
            set ::catalog_updates 0
            set ::repository_sets 0
            proc current_project {} {return project}
            proc get_property {key object} {return $::repositories}
            proc set_property {key value object} {
                incr ::repository_sets
                set ::repositories $value
            }
            proc update_ip_catalog {} {incr ::catalog_updates}
        """)

        result = tcl.eval("joint::ensure_ip_repository C:/stage/jpeg_ip")

        self.assertEqual(result, "SUCCESS:ip_repository_ready")
        self.assertEqual(tcl.eval("set ::catalog_updates"), "0")
        self.assertEqual(tcl.eval("set ::repository_sets"), "0")

    def test_ip_catalog_is_updated_before_opening_block_design(self):
        common = COMMON.read_text(encoding="utf-8")
        connect = CONNECT.read_text(encoding="utf-8")

        self.assertLess(
            common.index("joint::capture ip_repository"),
            common.index("open_bd_design $bd"),
        )
        self.assertNotIn("update_ip_catalog", connect)

    def test_interface_readback_uses_the_vivado_rtl_abstraction_vlnv(self):
        connect = CONNECT.read_text(encoding="utf-8")

        self.assertIn(
            'set expected_vlnv "xilinx.com:interface:${protocol}_rtl:*"',
            connect,
        )

    def test_module_reference_expected_result_is_the_xci_path(self):
        tcl = tkinter.Tcl()
        tcl.eval(f"source {{{COMMON.as_posix()}}}")
        tcl.eval("set ::joint::cfg [dict create xpr {T:/j3/snapshot/project/display.xpr}]")

        result = tcl.eval("joint::module_reference_xci_path display_image_pipeline_0")

        self.assertEqual(
            result,
            "T:/j3/snapshot/project/display.srcs/sources_1/bd/display/ip/"
            "display_image_pipeline_0/display_image_pipeline_0.xci",
        )

    def test_reads_full_persisted_receipt_when_capture_summary_omits_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence"
            evidence.mkdir()
            native_log = root / "vivado.log"
            native_log.write_bytes(b"WARNING: [IP_Flow 19-11770] expected refresh warning.\n")
            expected_result = "C:/stage/display_image_pipeline_0.xci"
            warning = "WARNING: [IP_Flow 19-11770] expected refresh warning."

            tcl = tkinter.Tcl()
            tcl.eval(f"source {{{COMMON.as_posix()}}}")
            tcl.eval("namespace eval ipcfg {}")
            tcl.setvar("::joint::evidence", evidence.as_posix())
            tcl.setvar("::joint::sequence", 1)
            tcl.setvar("::ipcfg::native_log_path", native_log.as_posix())
            tcl.setvar("::expected_result", expected_result)
            tcl.setvar("::native_log", native_log.as_posix())
            tcl.setvar("::warning", warning)
            tcl.eval(r"""
                namespace eval ipcfg {}
                set ::joint::cfg [dict create capture_review {C:/stage/joint_capture_review.py}]
                set ::full_receipt [dict create \
                    status FAIL code 0 raw $::expected_result options {} \
                    warning_count 1 critical_count 0 error_count 0 \
                    messages [list $::warning] evidence_path {C:/stage/002-refresh_region.tcldict} \
                    before_log {C:/stage/002-refresh_region.tcldict.before.log} \
                    log_path {C:/stage/002-refresh_region.tcldict.after.log} \
                    native_counts_before [dict create WARNING 9 {CRITICAL WARNING} 0 ERROR 0] \
                    native_counts_after [dict create WARNING 4 {CRITICAL WARNING} 0 ERROR 0] \
                    native_log $::native_log native_log_source $::native_log \
                    native_log_offsets {0 54} \
                    capture_error {CAPTURE_FAIL:native message export incomplete or counters changed: WARNING emitted=-5 exported=1} \
                    elapsed_ms 1]
                set ::summary_receipt [dict remove $::full_receipt raw options output messages capture_options]
                proc ipcfg::capture {path script} {
                    set channel [open $path {WRONLY CREAT EXCL}]
                    puts -nonewline $channel [dict create schema_version 1 data $::full_receipt]
                    close $channel
                    return $::summary_receipt
                }
                proc ipcfg::python_script {script args} {
                    set ::review_args $args
                    return "JOINT_CAPTURE_REVIEW_PASS test"
                }
            """)

            result = tcl.eval(
                "joint::capture_refresh_region {update_module_reference image_pipeline} $::expected_result"
            )

            self.assertIn("status FAIL", result)
            raw_index = int(tcl.eval("lsearch -exact $::review_args --raw-result"))
            self.assertEqual(
                tcl.eval(f"lindex $::review_args {raw_index + 1}"), expected_result
            )
            warning_indexes = tcl.eval(
                "lsearch -all -exact $::review_args --warning-message"
            )
            self.assertEqual(len(tcl.splitlist(warning_indexes)), 1)
            receipt_path = evidence / "002-refresh_region.tcldict"
            receipt_text = receipt_path.read_text(encoding="utf-8")
            self.assertIn("schema_version 1 data", receipt_text)
            self.assertIn("status FAIL", receipt_text)

    def test_run_capture_audits_counter_reset_without_downgrading_critical_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            warning = "WARNING: [Synth 8-3332] expected optimization warning."
            critical = "CRITICAL WARNING: [Timing 38-249] generated clock requires review."
            prefix = b"previous log\n"
            payload = (warning + "\n" + critical + "\n").encode("utf-8")
            source_log = root / "vivado.log"
            source_log.write_bytes(prefix + payload)
            capture_log = root / "run.native.log"
            capture_log.write_bytes(payload)

            tcl = tkinter.Tcl()
            tcl.eval(f"source {{{COMMON.as_posix()}}}")
            for key, value in {
                "source_log": source_log.as_posix(),
                "capture_log": capture_log.as_posix(),
                "warning": warning,
                "critical": critical,
                "start": str(len(prefix)),
                "end": str(len(prefix) + len(payload)),
            }.items():
                tcl.setvar(f"::{key}", value)
            tcl.eval(r"""
                set receipt [dict create \
                    status FAIL code 0 raw RUN_COMPLETE expected {RUN_COMPLETE} \
                    warning_count 1 critical_count 1 error_count 0 \
                    messages [list $::warning $::critical] \
                    capture_error {CAPTURE_FAIL:native message export incomplete or counters changed: WARNING emitted=-8 exported=1} \
                    native_log $::capture_log native_log_source $::source_log \
                    native_log_offsets [list $::start $::end] \
                    native_counts_before [dict create WARNING 12 {CRITICAL WARNING} 0 ERROR 0] \
                    native_counts_after [dict create WARNING 4 {CRITICAL WARNING} 1 ERROR 0]]
                set audit [joint::audit_run_capture $receipt]
            """)

            self.assertEqual(tcl.call("dict", "get", tcl.eval("set audit"), "status"),
                             "VERIFIED_FOR_REPORTING_REVIEW_REQUIRED")
            self.assertEqual(tcl.call("dict", "get", tcl.eval("set audit"), "critical_count"), "1")
            self.assertEqual(tcl.call("dict", "get", tcl.eval("set audit"), "error_count"), "0")

    def test_run_capture_accepts_nonseverity_save_output_after_counter_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_log = root / "vivado.log"
            prefix = b"prior warning already captured\n"
            save_info = b"Wrote  : <display.bd>\n"
            source_log.write_bytes(prefix + save_info)
            capture_log = root / "capture.native.log"
            capture_log.write_bytes(save_info)

            tcl = tkinter.Tcl()
            tcl.eval(f"source {{{COMMON.as_posix()}}}")
            tcl.setvar("::source_log", source_log.as_posix())
            tcl.setvar("::capture_log", capture_log.as_posix())
            tcl.setvar("::start", len(prefix))
            tcl.setvar("::end", len(prefix) + len(save_info))
            tcl.eval(r"""
                set receipt [dict create \
                    status FAIL code 0 raw SUCCESS:joint_bd_saved expected SUCCESS:joint_bd_saved \
                    warning_count 0 critical_count 0 error_count 0 messages {} \
                    capture_error {CAPTURE_FAIL:native message export incomplete or counters changed: WARNING emitted=-59 exported=0} \
                    native_log $::capture_log native_log_source $::source_log \
                    native_log_offsets [list $::start $::end] \
                    native_counts_before [dict create WARNING 59 {CRITICAL WARNING} 0 ERROR 0] \
                    native_counts_after [dict create WARNING 0 {CRITICAL WARNING} 0 ERROR 0]]
                set audit [joint::audit_run_capture $receipt]
            """)

            self.assertEqual(tcl.call("dict", "get", tcl.eval("set audit"), "status"),
                             "VERIFIED_FOR_REPORTING")
            self.assertEqual(tcl.call("dict", "get", tcl.eval("set audit"), "warning_count"), "0")

    def test_save_capture_stops_when_supplemental_review_requires_attention(self):
        tcl = tkinter.Tcl()
        tcl.eval(f"source {{{COMMON.as_posix()}}}")
        tcl.eval(r"""
            proc joint::capture_run {label script expected} {
                return [dict create status FAIL \
                    reporting_status VERIFIED_FOR_REPORTING_REVIEW_REQUIRED \
                    supplemental_review save.supplement.json]
            }
        """)

        with self.assertRaises(tkinter.TclError) as caught:
            tcl.eval("joint::capture_save_design")

        self.assertTrue(str(caught.exception).startswith(
            "JOINT_SAVE_CRITICAL_WARNING_REVIEW_REQUIRED"))

    def test_save_capture_accepts_clean_capture_or_verified_warning_reset(self):
        tcl = tkinter.Tcl()
        tcl.eval(f"source {{{COMMON.as_posix()}}}")
        tcl.eval(r"""
            set ::joint::capture_result [dict create status OK]
            proc joint::capture_run {label script expected} {return $::joint::capture_result}
        """)

        self.assertEqual(tcl.call("dict", "get", tcl.eval("joint::capture_save_design"), "status"),
                         "OK")
        tcl.eval(r"""
            set ::joint::capture_result [dict create status FAIL \
                reporting_status VERIFIED_FOR_REPORTING supplemental_review save.supplement.json]
        """)
        self.assertEqual(tcl.call("dict", "get", tcl.eval("joint::capture_save_design"), "reporting_status"),
                         "VERIFIED_FOR_REPORTING")

    def test_capture_run_preserves_fail_receipt_and_requires_review_for_critical_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence"
            evidence.mkdir()
            warning = "WARNING: [Synth 8-3332] expected optimization warning."
            critical = "CRITICAL WARNING: [Timing 38-249] generated clock requires review."
            prefix = b"prior log\n"
            payload = (warning + "\n" + critical + "\n").encode("utf-8")
            source_log = root / "vivado.log"
            source_log.write_bytes(prefix + payload)
            capture_log = root / "run.native.log"
            capture_log.write_bytes(payload)

            tcl = tkinter.Tcl()
            tcl.eval(f"source {{{COMMON.as_posix()}}}")
            tcl.setvar("::joint::evidence", evidence.as_posix())
            tcl.setvar("::joint::sequence", 0)
            tcl.setvar("::source_log", source_log.as_posix())
            tcl.setvar("::capture_log", capture_log.as_posix())
            tcl.setvar("::warning", warning)
            tcl.setvar("::critical", critical)
            tcl.setvar("::start", len(prefix))
            tcl.setvar("::end", len(prefix) + len(payload))
            tcl.eval(r"""
                namespace eval ipcfg {}
                set ::joint::cfg [dict create capture_review {C:/stage/joint_capture_review.py}]
                set ::full_receipt [dict create \
                    status FAIL code 0 raw RUN_COMPLETE options {} \
                    warning_count 1 critical_count 1 error_count 0 \
                    messages [list $::warning $::critical] evidence_path {C:/stage/001-test_run.tcldict} \
                    before_log {C:/stage/001-test_run.tcldict.before.log} \
                    log_path {C:/stage/001-test_run.tcldict.after.log} \
                    native_log $::capture_log native_log_source $::source_log \
                    native_log_offsets [list $::start $::end] \
                    native_counts_before [dict create WARNING 12 {CRITICAL WARNING} 0 ERROR 0] \
                    native_counts_after [dict create WARNING 4 {CRITICAL WARNING} 1 ERROR 0] \
                    capture_error {CAPTURE_FAIL:native message export incomplete or counters changed: WARNING emitted=-8 exported=1} \
                    elapsed_ms 1]
                set ::summary_receipt [dict remove $::full_receipt raw options output messages]
                proc ipcfg::capture {path script expect} {
                    set channel [open $path {WRONLY CREAT EXCL}]
                    puts -nonewline $channel [dict create schema_version 1 data $::full_receipt]
                    close $channel
                    set ::capture_expect $expect
                    return $::summary_receipt
                }
                proc ipcfg::python_script {script args} {
                    set ::review_args $args
                    return {JOINT_CAPTURE_REVIEW_REQUIRED C:/stage/review.json}
                }
                set result [joint::capture_run test_run {set ::run_body_executed 1} RUN_COMPLETE]
            """)

            self.assertEqual(tcl.eval("set ::capture_expect"), "RUN_COMPLETE")
            self.assertEqual(tcl.call("dict", "get", tcl.eval("set result"), "status"), "FAIL")
            self.assertEqual(tcl.call("dict", "get", tcl.eval("set result"), "reporting_status"),
                             "VERIFIED_FOR_REPORTING_REVIEW_REQUIRED")
            scope_index = int(tcl.eval("lsearch -exact $::review_args --scope"))
            self.assertGreaterEqual(scope_index, 0)
            self.assertEqual(tcl.eval(f"lindex $::review_args {scope_index + 1}"), "run")
            self.assertTrue((evidence / "001-test_run.tcldict").is_file())
            self.assertTrue((evidence / "001-test_run.supplement.json").is_file() is False)

    def test_hierarchical_resource_parser_reads_top_design_totals(self):
        tcl = tkinter.Tcl()
        tcl.eval(f"source {{{COMMON.as_posix()}}}")
        report = (
            "| display_wrapper | (top) | 42184 | 35675 | 4643 | 1866 | "
            "35955 | 30 | 55 | 81 |\n"
        )
        tcl.setvar("::report", report)

        resources = tcl.eval(
            "joint::parse_hierarchical_resources $::report display_wrapper"
        )

        expected = {
            "lut": "42184",
            "logic_lut": "35675",
            "lutram": "4643",
            "srl": "1866",
            "ff": "35955",
            "bram36": "30",
            "bram18": "55",
            "dsp": "81",
        }
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertEqual(tcl.call("dict", "get", resources, key), value)

    def test_route_status_parser_uses_routable_and_fully_routed_counts(self):
        tcl = tkinter.Tcl()
        tcl.eval(f"source {{{COMMON.as_posix()}}}")
        report = (
            "# of routable nets..................... : 69364 :\n"
            "# of fully routed nets................. : 69364 :\n"
            "# of nets with routing errors.......... : 0 :\n"
        )
        tcl.setvar("::report", report)

        counts = tcl.eval("joint::parse_route_status $::report")

        self.assertEqual(tcl.call("dict", "get", counts, "routable_nets"), "69364")
        self.assertEqual(tcl.call("dict", "get", counts, "fully_routed_nets"), "69364")
        self.assertEqual(tcl.call("dict", "get", counts, "routing_errors"), "0")

        tcl.setvar("::incomplete", report.replace("fully routed nets................. : 69364", "fully routed nets................. : 69363"))
        with self.assertRaises(tkinter.TclError):
            tcl.eval("joint::parse_route_status $::incomplete")

        tcl.setvar("::routing_errors", report.replace("routing errors.......... : 0", "routing errors.......... : 1"))
        with self.assertRaises(tkinter.TclError):
            tcl.eval("joint::parse_route_status $::routing_errors")


class SynthCaptureBridgeTests(unittest.TestCase):
    def test_synth_review_keeps_original_fail_and_uses_message_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence"
            evidence.mkdir()
            native_log = root / "vivado.log"
            warning = "WARNING: [Synth 8-3333] unused signal removed."
            payload = (warning + "\n").encode("utf-8")
            native_log.write_bytes(payload)

            tcl = tkinter.Tcl()
            tcl.eval(f"source {{{COMMON.as_posix()}}}")
            tcl.eval("namespace eval ipcfg {}")
            tcl.setvar("::joint::evidence", evidence.as_posix())
            tcl.setvar("::joint::sequence", 0)
            tcl.setvar("::joint::cfg", "capture_review C:/stage/joint_capture_review.py")
            tcl.setvar("::ipcfg::native_log_path", native_log.as_posix())
            tcl.setvar("::native_log", native_log.as_posix())
            tcl.setvar("::warning", warning)
            tcl.setvar("::native_end", len(payload))
            tcl.eval(r"""
                set ::full_receipt [dict create \
                    status FAIL code 0 raw design_1 options {} \
                    warning_count 1 critical_count 0 error_count 0 \
                    messages [list $::warning] evidence_path {C:/stage/001-synth.tcldict} \
                    before_log {C:/stage/001-synth.tcldict.before.log} \
                    log_path {C:/stage/001-synth.tcldict.after.log} \
                    native_counts_before [dict create WARNING 12 {CRITICAL WARNING} 0 ERROR 0] \
                    native_counts_after [dict create WARNING 4 {CRITICAL WARNING} 0 ERROR 0] \
                    native_log $::native_log native_log_source $::native_log \
                    native_log_offsets [list 0 $::native_end] \
                    capture_error {CAPTURE_FAIL:native message export incomplete or counters changed: WARNING emitted=-8 exported=1} \
                    elapsed_ms 1]
                set ::summary_receipt [dict remove $::full_receipt raw options output messages]
                proc ipcfg::capture {path script} {
                    set channel [open $path {WRONLY CREAT EXCL}]
                    puts -nonewline $channel [dict create schema_version 1 data $::full_receipt]
                    close $channel
                    set ::captured_script $script
                    return $::summary_receipt
                }
                proc ipcfg::python_script {script args} {
                    set ::review_script $script
                    set ::review_args $args
                    return "JOINT_CAPTURE_REVIEW_PASS test"
                }
            """)

            result = tcl.eval("joint::capture_synth {set ::run_body_executed 1}")

            self.assertIn("JOINT_SYNTH_CAPTURE_REVIEW_PASS", result)
            self.assertEqual(tcl.eval("info exists ::run_body_executed"), "0")
            messages_arg = int(tcl.eval("lsearch -exact $::review_args --messages-file"))
            self.assertGreaterEqual(messages_arg, 0)
            messages_path = Path(tcl.eval(f"lindex $::review_args {messages_arg + 1}"))
            self.assertEqual(messages_path.read_text(encoding="utf-8"), warning + "\n")
            self.assertEqual(tcl.eval("lsearch -exact $::review_args --warning-message"), "-1")
            receipt_path = evidence / "001-synth.tcldict"
            self.assertIn("status FAIL", receipt_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
