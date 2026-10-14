# Offline elaboration/lint and OOC resource estimate; no implementation or board IO.
set root C:/fpga/material-counter
set result [file join $root data results wo0014-offline-20261014]
catch {close_design}
set_property top axis_dma_image_pipeline [current_fileset]
set_param synth.elaboration.rodinMoreOptions {rt::set_parameter linterCsvFile true}
synth_design -lint -file [file join $result reports linter-final.csv]
catch {close_design}
synth_design -top axis_dma_image_pipeline -part xc7z020clg400-1 -mode out_of_context
create_clock -name wo14_clk -period 10 [get_ports aclk]
report_utilization -file [file join $result reports utilization-final.rpt]
report_timing_summary -file [file join $result reports timing-ooc-final.rpt]
write_checkpoint -force [file join $result reports wo0014-ooc-final.dcp]
