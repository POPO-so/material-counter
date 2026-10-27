# Run only after the Manager's Gray8 XSim gate has passed and create_bd.tcl
# has produced a fresh project. This script builds the isolated 50 MHz project.
if {![info exists gray8_output] || ![info exists gray8_repo]} {
    error "Set gray8_output and gray8_repo, then source create_bd.tcl first"
}
if {![info exists gray8_xsim_gate] || $gray8_xsim_gate ne "PASS"} {
    error "GRAY8_XSIM_GATE_REQUIRED: set gray8_xsim_gate PASS only after the required XSim suite passes"
}
if {![info exists ::ipcfg::native_log_path] || $::ipcfg::native_log_path eq ""} {
    error "Bind the active Vivado session's exact native log path before building"
}
set gray8_project_name gray8_50m
set gray8_project_file [file join $gray8_output project ${gray8_project_name}.xpr]
if {![file isfile $gray8_project_file]} {error "GRAY8_PROJECT_MISSING:$gray8_project_file"}
if {[llength [get_projects -quiet]] != 1} {error "Open exactly the Gray8 project in the current session"}
if {[file normalize [get_property DIRECTORY [current_project]]] ne [file normalize [file dirname $gray8_project_file]]} {
    error "Current Vivado project does not match Gray8 output"
}
if {[get_property PART [current_project]] ne "xc7z020clg400-1"} {error "Unexpected target part"}
set gray8_runs [get_runs -quiet {synth_1 impl_1}]
if {[llength $gray8_runs] != 2} {error "GRAY8 synthesis and implementation runs are required"}

set reports [file join $gray8_output reports]
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
    error "GRAY8_SYNTHESIS_FAILED:$synth_status"
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
    error "GRAY8_IMPLEMENTATION_FAILED:$impl_status"
}
source -notrace [file join [file dirname [info script]] export.tcl]
