"""Offline fail-closed tests for joint::check_jpeg (no Vivado process)."""

import tkinter
import unittest
import tempfile
from pathlib import Path


COMMON = Path(__file__).with_name("joint_common.tcl")


class JointReadbackTests(unittest.TestCase):
    def setUp(self):
        self.tcl = tkinter.Tcl()
        self.tcl.eval(f"source {{{COMMON.as_posix()}}}")
        self.tcl.eval(r"""
            set ::joint::cfg [dict create input_intf jpeg_axis_in output_intf gray_axis_out \
                control_intf s_axi_control clock_pin ap_clk reset_pin ap_rst_n \
                input_data_pin jpeg_axis_in_TDATA output_data_pin gray_axis_out_TDATA]
            proc get_bd_cells {name} {return $name}
            proc get_property {key obj} {
                if {$key eq "CONFIG.NUM_MI"} {return 13}
                if {$key eq "CONFIG.c_sg_length_width"} {return 19}
                if {$key in {CONFIG.c_include_mm2s_dre CONFIG.c_include_s2mm_dre}} {return 0}
                if {$key eq "CONFIG.c_m_axis_mm2s_tdata_width"} {return 32}
                if {$key eq "OFFSET"} {return [lindex [dict get $::addresses $obj] 0]}
                if {$key eq "RANGE"} {return [lindex [dict get $::addresses $obj] 1]}
                if {$key eq "LEFT"} {return [expr {[dict get $::widths $obj] - 1}]}
                if {$key eq "RIGHT"} {return 0}
                error "unexpected get_property $key $obj"
            }
            proc get_bd_pins {args} {
                if {[llength $args] == 1} {return [lindex $args 0]}
                set net [lindex $args end]
                return [dict get $::net_ends $net]
            }
            proc get_bd_nets {args} {
                set pin [lindex $args end]
                dict for {net ends} $::net_ends {
                    if {[lsearch -exact $ends $pin] >= 0} {return [list $net]}
                }
                error "no net for pin $pin"
            }
            proc get_bd_addr_spaces {name} {return $name}
            proc get_bd_addr_segs {args} {
                if {[llength $args] == 2 && [lindex $args 0] eq "-of_objects"} {
                    set object [lindex $args 1]
                    if {$object eq $::target_segment} {return $::mapped_segment}
                    if {$object eq "ps7/Data"} {return [dict keys $::addresses]}
                    return {}
                }
                if {[llength $args] == 1 && [string match "jpeg_axis_gray_0/s_axi_control/*" [lindex $args 0]]} {
                    return $::target_segment
                }
                return [dict keys $::addresses]
            }
            proc reset_fixture {} {
                set ::widths [dict create]
                set ::pin_net [dict create]
                set ::net_ends [dict create]
                set ::addresses [dict create]
                set ::target_segment /jpeg_axis_gray_0/s_axi_control/Reg
                set ::mapped_segment /ps7/Data/SEG_jpeg_axis_gray_0_Reg
                foreach {pin width} {
                    jpeg_axis_gray_0/jpeg_axis_in_TDATA 32 jpeg_axis_gray_0/jpeg_axis_in_TKEEP 4
                    jpeg_axis_gray_0/jpeg_axis_in_TLAST 1 jpeg_axis_gray_0/jpeg_axis_in_TVALID 1
                    jpeg_axis_gray_0/jpeg_axis_in_TREADY 1 jpeg_axis_gray_0/gray_axis_out_TDATA 32
                    jpeg_axis_gray_0/gray_axis_out_TKEEP 4 jpeg_axis_gray_0/gray_axis_out_TLAST 1
                    jpeg_axis_gray_0/gray_axis_out_TVALID 1 jpeg_axis_gray_0/gray_axis_out_TREADY 1
                    axi_dma_0/M_AXIS_MM2S_TDATA 32 axi_dma_0/M_AXIS_MM2S_TKEEP 4
                    axi_dma_0/M_AXIS_MM2S_TLAST 1 axi_dma_0/M_AXIS_MM2S_TVALID 1
                    axi_dma_0/M_AXIS_MM2S_TREADY 1 image_pipeline/s_axis_tdata 32
                    image_pipeline/s_axis_tkeep 4 image_pipeline/s_axis_tlast 1
                    image_pipeline/s_axis_tvalid 1 image_pipeline/s_axis_tready 1
                } {dict set ::widths $pin $width}
                foreach {pin ends} {
                    axi_dma_0/M_AXIS_MM2S_TDATA {axi_dma_0/M_AXIS_MM2S_TDATA jpeg_axis_gray_0/jpeg_axis_in_TDATA}
                    axi_dma_0/M_AXIS_MM2S_TKEEP {axi_dma_0/M_AXIS_MM2S_TKEEP jpeg_axis_gray_0/jpeg_axis_in_TKEEP}
                    axi_dma_0/M_AXIS_MM2S_TLAST {axi_dma_0/M_AXIS_MM2S_TLAST jpeg_axis_gray_0/jpeg_axis_in_TLAST}
                    axi_dma_0/M_AXIS_MM2S_TVALID {axi_dma_0/M_AXIS_MM2S_TVALID jpeg_axis_gray_0/jpeg_axis_in_TVALID}
                    axi_dma_0/M_AXIS_MM2S_TREADY {axi_dma_0/M_AXIS_MM2S_TREADY jpeg_axis_gray_0/jpeg_axis_in_TREADY}
                    jpeg_axis_gray_0/gray_axis_out_TDATA {jpeg_axis_gray_0/gray_axis_out_TDATA image_pipeline/s_axis_tdata}
                    jpeg_axis_gray_0/gray_axis_out_TKEEP {jpeg_axis_gray_0/gray_axis_out_TKEEP image_pipeline/s_axis_tkeep}
                    jpeg_axis_gray_0/gray_axis_out_TLAST {jpeg_axis_gray_0/gray_axis_out_TLAST image_pipeline/s_axis_tlast}
                    jpeg_axis_gray_0/gray_axis_out_TVALID {jpeg_axis_gray_0/gray_axis_out_TVALID image_pipeline/s_axis_tvalid}
                    jpeg_axis_gray_0/gray_axis_out_TREADY {jpeg_axis_gray_0/gray_axis_out_TREADY image_pipeline/s_axis_tready}
                    control/M12_ACLK {control/M12_ACLK jpeg_axis_gray_0/ap_clk}
                    jpeg_axis_gray_0/ap_clk {control/M12_ACLK jpeg_axis_gray_0/ap_clk}
                    control/M12_ARESETN {control/M12_ARESETN jpeg_axis_gray_0/ap_rst_n}
                    jpeg_axis_gray_0/ap_rst_n {control/M12_ARESETN jpeg_axis_gray_0/ap_rst_n}
                    ps7/FCLK_CLK0 {ps7/FCLK_CLK0 control/M12_ACLK jpeg_axis_gray_0/ap_clk image_pipeline/aclk}
                    reset_rle/peripheral_aresetn {reset_rle/peripheral_aresetn control/M12_ARESETN jpeg_axis_gray_0/ap_rst_n}
                } {
                    set net net_$pin
                    dict set ::pin_net $pin $net
                    dict set ::net_ends $net $ends
                }
                dict set ::addresses $::mapped_segment {0x43C80000 0x10000}
            }
            proc disconnect_test_pin {pin} {
                dict for {net ends} $::net_ends {
                    if {[lsearch -exact $ends $pin] >= 0} {
                        dict set ::net_ends $net [lsearch -all -inline -not -exact $ends $pin]
                        return
                    }
                }
                error "test pin not present: $pin"
            }
            proc joint::intf_ends {pin} {
                switch -- $pin {
                    axi_dma_0/M_AXIS_MM2S {return {axi_dma_0/M_AXIS_MM2S jpeg_axis_gray_0/jpeg_axis_in}}
                    jpeg_axis_gray_0/jpeg_axis_in {return {axi_dma_0/M_AXIS_MM2S jpeg_axis_gray_0/jpeg_axis_in}}
                    jpeg_axis_gray_0/gray_axis_out {return {image_pipeline/s_axis jpeg_axis_gray_0/gray_axis_out}}
                    image_pipeline/s_axis {return {image_pipeline/s_axis jpeg_axis_gray_0/gray_axis_out}}
                    control/M12_AXI {return {control/M12_AXI jpeg_axis_gray_0/s_axi_control}}
                    default {error "unexpected interface $pin"}
                }
            }
            reset_fixture
        """)

    def check_fails(self, expected_error):
        with self.assertRaises(tkinter.TclError) as caught:
            self.tcl.eval("joint::check_jpeg")
        self.assertTrue(
            str(caught.exception).startswith(expected_error),
            f"expected {expected_error}, received {caught.exception}",
        )

    def test_valid_readback_passes(self):
        self.assertEqual(self.tcl.eval("joint::check_jpeg"), "SUCCESS:joint_jpeg_readback")

    def test_connected_interface_does_not_require_separate_scalar_nets(self):
        self.tcl.eval(r"""
            rename get_bd_nets get_bd_nets_original
            proc get_bd_nets {args} {
                set pin [lindex $args end]
                if {[string match "*jpeg_axis_*" $pin] || [string match "*gray_axis_out_*" $pin]} {return {}}
                return [get_bd_nets_original {*}$args]
            }
        """)
        self.assertEqual(self.tcl.eval("joint::check_jpeg"), "SUCCESS:joint_jpeg_readback")

    def test_pipeline_axis_clock_is_on_fclk0(self):
        self.tcl.eval("disconnect_test_pin {image_pipeline/aclk}")
        self.check_fails("JOINT_NOT_ON_FCLK0:image_pipeline/aclk")

    def test_required_axis_interface_endpoints_are_checked(self):
        self.tcl.eval(r"""
            rename joint::intf_ends joint::intf_ends_original
            proc joint::intf_ends {pin} {
                if {$pin eq "axi_dma_0/M_AXIS_MM2S"} {return {axi_dma_0/M_AXIS_MM2S}}
                return [joint::intf_ends_original $pin]
            }
        """)
        self.check_fails("JOINT_INTF_NOT_CONNECTED:axi_dma_0/M_AXIS_MM2S:jpeg_axis_gray_0/jpeg_axis_in")

    def test_required_widths_are_enforced(self):
        for pin, width in (
            ("jpeg_axis_gray_0/jpeg_axis_in_TDATA", 16),
            ("jpeg_axis_gray_0/jpeg_axis_in_TKEEP", 2),
            ("jpeg_axis_gray_0/jpeg_axis_in_TLAST", 2),
            ("jpeg_axis_gray_0/jpeg_axis_in_TVALID", 2),
            ("jpeg_axis_gray_0/jpeg_axis_in_TREADY", 2),
            ("jpeg_axis_gray_0/gray_axis_out_TDATA", 16),
            ("jpeg_axis_gray_0/gray_axis_out_TKEEP", 2),
            ("jpeg_axis_gray_0/gray_axis_out_TLAST", 2),
            ("jpeg_axis_gray_0/gray_axis_out_TVALID", 2),
            ("jpeg_axis_gray_0/gray_axis_out_TREADY", 2),
        ):
            with self.subTest(pin=pin):
                self.tcl.eval(f"dict set ::widths {{{pin}}} {width}")
                self.check_fails("JOINT_AXIS_PIN_WIDTH_WRONG:")
                self.tcl.eval("reset_fixture")

    def test_scalar_axis_pins_without_left_right_ranges_are_one_bit(self):
        self.tcl.eval(r"""
            rename get_property get_property_original
            proc get_property {key obj} {
                if {$key in {LEFT RIGHT} && [dict get $::widths $obj] == 1} {return {}}
                return [get_property_original $key $obj]
            }
        """)
        self.assertEqual(self.tcl.eval("joint::check_jpeg"), "SUCCESS:joint_jpeg_readback")

    def test_optional_tstrb_is_not_required(self):
        self.assertEqual(self.tcl.eval("joint::check_jpeg"), "SUCCESS:joint_jpeg_readback")

    def test_address_mapping_and_overlap_are_checked(self):
        bad_maps = (
            ("dict set ::addresses other/segment {0x43C80000 0x10000}", "JOINT_JPEG_ADDRESS_WRONG:"),
            ("dict set ::addresses $::mapped_segment {0x43C80000 0x20000}", "JOINT_JPEG_ADDRESS_WRONG:"),
            ("dict set ::addresses other/segment {0x43C88000 0x1000}", "JOINT_ADDRESS_SEGMENTS_OVERLAP:"),
            ("dict set ::addresses other/segment {0x43C70000 0x20000}", "JOINT_ADDRESS_SEGMENTS_OVERLAP:"),
            ("dict set ::addresses $::mapped_segment {0x43D00000 0x10000}", "JOINT_JPEG_ADDRESS_WRONG:"),
        )
        for mutation, expected_error in bad_maps:
            with self.subTest(mutation=mutation):
                self.tcl.eval(mutation)
                self.check_fails(expected_error)
                self.tcl.eval("reset_fixture")

    def test_address_readback_follows_target_object_not_generated_name(self):
        self.tcl.eval(r"""
            dict unset ::addresses $::mapped_segment
            set ::mapped_segment /ps7/Data/a_vivado_generated_name
            dict set ::addresses $::mapped_segment {0x43C80000 0x10000}
        """)
        self.assertEqual(self.tcl.eval("joint::check_jpeg"), "SUCCESS:joint_jpeg_readback")

    def test_save_counter_baseline_records_nonzero_counts_without_mutating_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence"
            evidence.mkdir()
            native_log = root / "vivado.log"
            native_log.write_bytes(b"previous captured warnings\n")
            self.tcl.eval("namespace eval ipcfg {}")
            self.tcl.setvar("::joint::evidence", evidence.as_posix())
            self.tcl.setvar("::ipcfg::native_log_path", native_log.as_posix())
            self.tcl.eval(r"""
                namespace eval ipcfg {}
                set ::message_counts [dict create WARNING 60 {CRITICAL WARNING} 0 ERROR 0]
                proc ipcfg::_message_counts {} {return $::message_counts}
                proc reset_msg_count {args} {error "baseline recording must not reset Vivado counters"}
                proc ipcfg::write_evidence {path data} {
                    set channel [open $path {WRONLY CREAT EXCL}]
                    puts -nonewline $channel [dict create schema_version 1 data $data]
                    close $channel
                    return "EVIDENCE:$path"
                }
            """)

            result = self.tcl.eval("joint::record_save_counter_baseline")

            self.assertIn("JOINT_SAVE_COUNTER_BASELINE_RECORDED", result)
            self.assertEqual(self.tcl.eval("dict get $::message_counts WARNING"), "60")
            receipt = (evidence / "save_bd-counter-baseline.tcldict").read_text(encoding="utf-8")
            self.assertIn("WARNING 60", receipt)
            self.assertIn("native_log_offset", receipt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
