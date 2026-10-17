# Source only after create_image_bd.tcl in its dedicated experiment session.
if {![info exists wo14_output]} {error "Create the isolated image BD first"}
launch_runs synth_1 -jobs 2
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Synthesis failed: [get_property STATUS [get_runs synth_1]]"}
open_run synth_1
report_utilization -file [file join $wo14_output reports utilization-synth.rpt]
report_timing_summary -file [file join $wo14_output reports timing-synth.rpt]
close_design
launch_runs impl_1 -to_step write_bitstream -jobs 2
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%" ||
    ![string match {*write_bitstream Complete*} [get_property STATUS [get_runs impl_1]]]} {
    error "Implementation failed: [get_property STATUS [get_runs impl_1]]"
}
source -notrace [file join [file dirname [info script]] export_image.tcl]
