open_checkpoint C:/Users/14173/Documents/material-counter/data/results/standalone-20261029-g1/project/display.runs/impl_1/display_wrapper_routed.dcp
read_xdc C:/Users/14173/Documents/material-counter/scripts/standalone/display_timing.xdc
report_timing_summary -delay_type min_max -report_unconstrained -file C:/Users/14173/Documents/material-counter/data/results/standalone-20261029-g1/reports/timing-reviewed.rpt
report_methodology -file C:/Users/14173/Documents/material-counter/data/results/standalone-20261029-g1/reports/methodology-reviewed.rpt
report_cdc -details -file C:/Users/14173/Documents/material-counter/data/results/standalone-20261029-g1/reports/cdc-reviewed.rpt
report_bus_skew -file C:/Users/14173/Documents/material-counter/data/results/standalone-20261029-g1/reports/bus-skew-reviewed.rpt
set setup [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set hold [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
if {$setup < 0 || $hold < 0} {error "TIMING_FAILED"}
report_drc -file C:/Users/14173/Documents/material-counter/data/results/standalone-20261029-g1/reports/drc-reviewed.rpt
foreach v [concat [get_drc_violations] [get_methodology_violations]] {if {[get_property SEVERITY $v] in {Error {Critical Warning}}} {error "REVIEW_FAILED:$v"}}
write_checkpoint C:/Users/14173/Documents/material-counter/data/results/standalone-20261029-g1/export/display-reviewed.dcp
write_bitstream C:/Users/14173/Documents/material-counter/data/results/standalone-20261029-g1/export/display-reviewed.bit
puts "DISPLAY_REVIEW_PASS setup=$setup hold=$hold"
exit
