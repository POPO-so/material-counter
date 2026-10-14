# Source create_dma_replay_bd.tcl first in a fresh session, then this file.
if {![info exists wo13_output]} {error {Create the isolated project first}}
launch_runs synth_1 -jobs 2
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
    error "Synthesis failed: [get_property STATUS [get_runs synth_1]]"
}
launch_runs impl_1 -to_step write_bitstream -jobs 2
wait_on_run impl_1
source -notrace [file join [file dirname [info script]] export_dma_replay.tcl]
