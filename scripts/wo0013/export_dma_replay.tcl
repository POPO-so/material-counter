# Run only after impl_1 has completed write_bitstream in the isolated project.
if {![info exists wo13_output]} {error {wo13_output is not set}}
set wo13_output [file normalize $wo13_output]
if {[get_property PROGRESS [get_runs impl_1]] ne "100%" ||
    ![string match {*write_bitstream Complete*} [get_property STATUS [get_runs impl_1]]]} {
    error "Implementation did not complete bitstream: [get_property STATUS [get_runs impl_1]]"
}
open_run impl_1
file mkdir [file join $wo13_output reports]
report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose \
    -max_paths 10 -file [file join $wo13_output reports timing_summary.rpt]
report_utilization -hierarchical -file [file join $wo13_output reports utilization.rpt]
report_drc -file [file join $wo13_output reports drc.rpt]
report_methodology -file [file join $wo13_output reports methodology.rpt]
report_methodology -json [file join $wo13_output reports methodology.json]
write_xdc -force -exclude_physical [file join $wo13_output reports implemented_constraints.xdc]
set bit_file [file join [get_property DIRECTORY [get_runs impl_1]] wo0013_dma_wrapper.bit]
set hwh_file [file join [get_property DIRECTORY [current_project]] \
    wo0013_phase_a.gen sources_1 bd wo0013_dma hw_handoff wo0013_dma.hwh]
foreach artifact [list $bit_file $hwh_file] {
    if {![file exists $artifact] || [file size $artifact] == 0} {error "Missing artifact: $artifact"}
}
file mkdir [file join $wo13_output export]
file copy -force $bit_file [file join $wo13_output export wo0013_phase_a.bit]
file copy -force $hwh_file [file join $wo13_output export wo0013_phase_a.hwh]
set repo [file normalize [file join [file dirname [info script]] ../..]]
file copy -force [file join $repo python boardtests wo0013 dma_replay_verify.py] \
    [file join $wo13_output export dma_replay_verify.py]
file copy -force [file join $repo python boardtests wo0013 README.md] \
    [file join $wo13_output export PYNQ_README.md]
puts "WO0013_EXPORT_COMPLETE bit=[file join $wo13_output export wo0013_phase_a.bit] hwh=[file join $wo13_output export wo0013_phase_a.hwh]"
