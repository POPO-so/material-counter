# Isolated RLE overlay, reusing the audited PS/DMA/HP interconnect topology.
set repo [file normalize [file join [file dirname [info script]] ../..]]
if {![info exists ::env(RLE_OUT)]} {error "Set RLE_OUT to a fresh gray8-rle-* result directory"}
set gray8_output [file normalize $::env(RLE_OUT)]
set gate_file [file join $gray8_output xsim-gate.txt]
if {![file isfile $gate_file]} {error "RLE_XSIM_GATE_MISSING"}
set fd [open $gate_file];set gate [string trim [read $fd]];close $fd
if {$gate ne "RLE_XSIM_ALL_PASS cases=8"} {error "RLE_XSIM_GATE_INVALID"}
set netlist_gate_file [file join $gray8_output netlist-gate.txt]
if {![file isfile $netlist_gate_file]} {error "RLE_NETLIST_GATE_MISSING"}
set fd [open $netlist_gate_file];set netlist_gate [string trim [read $fd]];close $fd
if {$netlist_gate ne "RLE_POST_SYNTHESIS_PASS"} {error "RLE_NETLIST_GATE_INVALID"}
set gray8_repo $repo
set gray8_profile rle
set gray8_board_repo C:/FPGA/vivado_board_repos
set helper [file normalize [file join $::env(USERPROFILE) .codex skills vivado-ip-configurator lib ipcfg.tcl]]
source -notrace $helper
if {![info exists ::env(RLE_NATIVE_LOG)]} {error "Set RLE_NATIVE_LOG to the batch -log path"}
set ::ipcfg::native_log_path [file normalize $::env(RLE_NATIVE_LOG)]
puts [ipcfg::python_status]
puts [ipcfg::python_exec -c {import argparse, hashlib, json, pathlib, xml.etree.ElementTree; print("PYTHON_IMPORTS:OK")}]
source [file join $repo scripts gray8 create_bd.tcl]
launch_runs synth_1 -jobs 2
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "RLE_SYNTH_FAILED"}
launch_runs impl_1 -to_step write_bitstream -jobs 2
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%" ||
    ![string match {*write_bitstream Complete*} [get_property STATUS [get_runs impl_1]]]} {
    error "RLE_IMPL_FAILED"
}
open_run impl_1
set reports [file join $gray8_output reports]
report_timing_summary -delay_type min_max -report_unconstrained -file [file join $reports timing.rpt]
report_utilization -file [file join $reports utilization.rpt]
report_utilization -hierarchical -file [file join $reports utilization-hierarchical.rpt]
report_drc -file [file join $reports drc.rpt]
report_methodology -file [file join $reports methodology.rpt]
set setup [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set hold [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
if {$setup eq "" || $hold eq "" || $setup<0 || $hold<0} {error "RLE_TIMING_FAIL setup=$setup hold=$hold"}
foreach v [concat [get_drc_violations] [get_methodology_violations]] {
    if {[get_property SEVERITY $v] in {Error {Critical Warning}}} {error "RLE_SERIOUS_VIOLATION:$v"}
}
file copy [file join [get_property DIRECTORY [get_runs impl_1]] gray8_wrapper.bit] [file join $gray8_output export rle.bit]
file copy [file join $gray8_output project gray8_50m.gen sources_1 bd gray8 hw_handoff gray8.hwh] [file join $gray8_output export rle.hwh]
puts "RLE_BUILD_PASS setup=$setup hold=$hold board_verified=false"
