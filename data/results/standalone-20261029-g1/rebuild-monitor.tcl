open_project C:/Users/14173/Documents/material-counter/data/results/standalone-20261029-g1/project/display.xpr
open_bd_design [get_files */display.bd]
update_compile_order -fileset sources_1
update_module_reference [get_ips *frame_monitor*]
validate_bd_design
save_bd_design
generate_target all [get_files display.bd]
set monitor_runs [get_runs -quiet *frame_monitor*synth*]
if {[llength $monitor_runs]} {reset_run $monitor_runs}
reset_run synth_1
source scripts/standalone/build_display.tcl
exit
