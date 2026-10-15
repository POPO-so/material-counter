open_run impl_1
report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose \
    -max_paths 10 -file [file join $wo14_output reports timing-implemented.rpt]
report_utilization -file [file join $wo14_output reports utilization-implemented.rpt]
report_utilization -hierarchical -file [file join $wo14_output reports utilization-hierarchical.rpt]
report_drc -file [file join $wo14_output reports drc.rpt]
report_methodology -file [file join $wo14_output reports methodology.rpt]
report_methodology -json [file join $wo14_output reports methodology.json]
report_clocks -file [file join $wo14_output reports clocks.rpt]
write_xdc -force -exclude_physical [file join $wo14_output reports implemented-constraints.xdc]
set setup_paths [get_timing_paths -delay_type max -max_paths 1]
set hold_paths [get_timing_paths -delay_type min -max_paths 1]
if {![llength $setup_paths] || ![llength $hold_paths]} {error "Missing analyzed setup/hold paths"}
set setup_slack [get_property SLACK $setup_paths]
set hold_slack [get_property SLACK $hold_paths]
if {$setup_slack < 0 || $hold_slack < 0} {error "Implemented timing failed: setup=$setup_slack hold=$hold_slack"}
foreach violation [get_drc_violations] {
    if {[get_property SEVERITY $violation] in {Error {Critical Warning}}} {error "Serious DRC: $violation"}
}
foreach violation [get_methodology_violations] {
    if {[get_property SEVERITY $violation] in {Error {Critical Warning}}} {error "Serious methodology: $violation"}
}
write_checkpoint -force [file join $wo14_output export wo0014_image_routed.dcp]
set bit_file [file join [get_property DIRECTORY [get_runs impl_1]] wo0014_image_wrapper.bit]
set hwh_file [file join [get_property DIRECTORY [current_project]] \
    ${wo14_project_name}.gen sources_1 bd wo0014_image hw_handoff wo0014_image.hwh]
foreach artifact [list $bit_file $hwh_file] {
    if {![file exists $artifact] || [file size $artifact] == 0} {error "Missing artifact: $artifact"}
}
file copy -force $bit_file [file join $wo14_output export wo0014_image.bit]
file copy -force $hwh_file [file join $wo14_output export wo0014_image.hwh]
set finalize [file join [file dirname [info script]] finalize_build.py]
if {![file exists $finalize]} {error "Missing mandatory BIT/HWH finalizer"}
if {[file exists $finalize]} {
    if {![llength [info commands ipcfg::python_exec]]} {
        source -notrace [file join [file dirname [info script]] ../../.agents/skills/vivado-ip-configurator/lib/ipcfg.tcl]
    }
    puts [ipcfg::python_exec $finalize --out $wo14_output --clock $wo14_clock_mhz]
}
puts "WO0014_EXPORT_COMPLETE clock=$wo14_clock_mhz bit=[file join $wo14_output export wo0014_image.bit] hwh=[file join $wo14_output export wo0014_image.hwh] board_tested=false"
source -notrace [file join [file dirname [info script]] capture_repro.tcl]
