# Run only after the Manager's WO-0015 XSim gate has passed and create_s2mm_bd.tcl
# has produced a fresh project. This script builds the isolated 50 MHz project.
if {![info exists wo15_output] || ![info exists wo15_repo]} {
    error "Set wo15_output and wo15_repo, then source create_s2mm_bd.tcl first"
}
set wo15_expected_output [file normalize [file join $wo15_repo data results wo0015-hw-50mhz-20261015]]
if {[file normalize $wo15_output] ne $wo15_expected_output} {error "WO15_OUTPUT_MUST_BE:$wo15_expected_output"}
if {![info exists wo15_xsim_gate] || $wo15_xsim_gate ne "PASS"} {
    error "WO15_XSIM_GATE_REQUIRED: set wo15_xsim_gate PASS only after the required XSim suite passes"
}
if {![info exists ::ipcfg::native_log_path] || $::ipcfg::native_log_path eq ""} {
    error "Bind the active Vivado session's exact native log path before building"
}
set wo15_project_name wo15_50m
set wo15_project_file [file join $wo15_output project ${wo15_project_name}.xpr]
if {![file isfile $wo15_project_file]} {error "WO15_PROJECT_MISSING:$wo15_project_file"}
if {[llength [get_projects -quiet]] != 1} {error "Open exactly the WO-0015 project in the current session"}
if {[file normalize [get_property DIRECTORY [current_project]]] ne [file normalize [file dirname $wo15_project_file]]} {
    error "Current Vivado project does not match WO-0015 output"
}
if {[get_property PART [current_project]] ne "xc7z020clg400-1"} {error "Unexpected target part"}
set wo15_runs [get_runs -quiet {synth_1 impl_1}]
if {[llength $wo15_runs] != 2} {error "WO15 synthesis and implementation runs are required"}

set reports [file join $wo15_output reports]
set synth_status [get_property STATUS [get_runs synth_1]]
if {![string match -nocase {*not started*} $synth_status] &&
    ![string match -nocase {*not run*} $synth_status]} {
    error "SYNTH_RUN_NOT_FRESH:$synth_status"
}
launch_runs synth_1 -jobs 2
wait_on_run synth_1
set synth_status [get_property STATUS [get_runs synth_1]]
if {[get_property PROGRESS [get_runs synth_1]] ne "100%" ||
    ![string match -nocase {*complete*} $synth_status]} {
    error "WO15_SYNTHESIS_FAILED:$synth_status"
}
open_run synth_1
report_utilization -file [file join $reports utilization-synth.rpt]
report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose \
    -max_paths 10 -file [file join $reports timing-synth.rpt]
close_design

set impl_status [get_property STATUS [get_runs impl_1]]
if {![string match -nocase {*not started*} $impl_status] &&
    ![string match -nocase {*not run*} $impl_status]} {
    error "IMPLEMENTATION_RUN_NOT_FRESH:$impl_status"
}
launch_runs impl_1 -to_step write_bitstream -jobs 2
wait_on_run impl_1
set impl_status [get_property STATUS [get_runs impl_1]]
if {[get_property PROGRESS [get_runs impl_1]] ne "100%" ||
    ![string match -nocase {*write_bitstream Complete*} $impl_status]} {
    error "WO15_IMPLEMENTATION_FAILED:$impl_status"
}
source -notrace [file join [file dirname [info script]] export.tcl]
