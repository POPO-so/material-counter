# Export complete routed evidence and the matching BIT/HWH pair from impl_1.
if {![info exists wo15_output] || ![info exists wo15_repo] || ![info exists wo15_xsim_gate] ||
    $wo15_xsim_gate ne "PASS"} {error "WO15_BUILD_CONTEXT_OR_XSIM_GATE_MISSING"}
if {![info exists ::ipcfg::native_log_path] || $::ipcfg::native_log_path eq ""} {
    error "Bind the active Vivado session's exact native log path before export"
}
set wo15_project_name wo15_50m
set wo15_bd_name wo15
set reports [file join $wo15_output reports]
set export [file join $wo15_output export]
set project_dir [file join $wo15_output project]
set impl_run [get_runs -quiet impl_1]
if {[llength $impl_run] != 1 || [get_property PROGRESS $impl_run] ne "100%" ||
    ![string match -nocase {*write_bitstream Complete*} [get_property STATUS $impl_run]]} {
    error "WO15_IMPL_NOT_COMPLETE"
}
open_run impl_1

report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose \
    -max_paths 20 -file [file join $reports timing-implemented.rpt]
report_timing -delay_type max -slack_lesser_than 0.000 -max_paths 100 \
    -file [file join $reports setup-failing-paths.rpt]
report_timing -delay_type min -slack_lesser_than 0.000 -max_paths 100 \
    -file [file join $reports hold-failing-paths.rpt]
report_utilization -file [file join $reports utilization-implemented.rpt]
report_utilization -hierarchical -file [file join $reports utilization-hierarchical.rpt]
report_drc -file [file join $reports drc.rpt]
report_methodology -file [file join $reports methodology.rpt]
report_methodology -json [file join $reports methodology.json]
report_clocks -file [file join $reports clocks.rpt]
report_clock_interaction -file [file join $reports clock-interaction.rpt]
report_exceptions -file [file join $reports exceptions.rpt]
write_xdc -force -exclude_physical [file join $reports implemented-constraints.xdc]

set setup_paths [get_timing_paths -delay_type max -max_paths 1]
set hold_paths [get_timing_paths -delay_type min -max_paths 1]
if {![llength $setup_paths] || ![llength $hold_paths]} {error "WO15_SETUP_OR_HOLD_PATH_MISSING"}
set setup_slack [get_property SLACK $setup_paths]
set hold_slack [get_property SLACK $hold_paths]
puts "WO15_WORST_PATHS setup_slack=$setup_slack hold_slack=$hold_slack"

# Preserve every report before rejecting serious findings so failures remain reviewable.
set serious_drc {}
foreach violation [get_drc_violations] {
    set severity [get_property SEVERITY $violation]
    if {$severity in {Error {Critical Warning}}} {lappend serious_drc "$severity: $violation"}
}
set serious_methodology {}
foreach violation [get_methodology_violations] {
    set severity [get_property SEVERITY $violation]
    if {$severity in {Error {Critical Warning}}} {lappend serious_methodology "$severity: $violation"}
}
if {[llength $serious_drc]} {error "WO15_SERIOUS_DRC:[join $serious_drc {; }]"}
if {[llength $serious_methodology]} {error "WO15_SERIOUS_METHODOLOGY:[join $serious_methodology {; }]"}

if {$setup_slack < 0 || $hold_slack < 0} {error "WO15_IMPLEMENTED_TIMING_FAILED:setup=$setup_slack hold=$hold_slack"}
set routed_dcp [file join $export wo15_routed.dcp]
if {[file exists $routed_dcp]} {error "WO15_EXPORT_EXISTS:$routed_dcp"}
write_checkpoint $routed_dcp

set bit_file [file join [get_property DIRECTORY $impl_run] wo15_wrapper.bit]
set hwh_file [file join $project_dir ${wo15_project_name}.gen sources_1 bd $wo15_bd_name hw_handoff ${wo15_bd_name}.hwh]
foreach artifact [list $bit_file $hwh_file] {
    if {![file isfile $artifact] || [file size $artifact] == 0} {error "WO15_REQUIRED_ARTIFACT_MISSING:$artifact"}
}
set out_bit [file join $export wo15.bit]
set out_hwh [file join $export wo15.hwh]
if {[file exists $out_bit] || [file exists $out_hwh]} {error "WO15_BIT_HWH_EXPORT_ALREADY_EXISTS"}
file copy $bit_file $out_bit
file copy $hwh_file $out_hwh

set finalize [file join [file dirname [info script]] finalize_build.py]
if {![file isfile $finalize]} {error "WO15_FINALIZER_MISSING:$finalize"}
if {![llength [info commands ipcfg::python_script]]} {
    set helper {C:/fpga/material-counter/.agents/skills/vivado-ip-configurator/lib/ipcfg.tcl}
    if {![file isfile $helper]} {error "IPCFG_HELPER_MISSING:$helper"}
    source -notrace $helper
}
set finalize_output [ipcfg::python_script $finalize --out $wo15_output --clock 50 --repo $wo15_repo \
    --native-log $::ipcfg::native_log_path]
puts $finalize_output
puts "WO15_EXPORT_COMPLETE clock=50 bit=$out_bit hwh=$out_hwh board_tested=false"
