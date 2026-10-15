# Isolated OOC estimate at the same 10 ns clock as the committed baseline.
set root C:/fpga/material-counter
set result [file join $root data results wo0014-timing-20261014]
if {![catch {get_property NAME [current_project]} open_name]} {
    if {$open_name ne "wo14_timing"} {error "Use a dedicated Vivado session; another project is open"}
    close_project
}
create_project -in_memory wo14_timing -part xc7z020clg400-1
add_files [list $root/fpga/rtl/axis_rgb2gray.v $root/fpga/rtl/axis_gray2binary.v $root/fpga/experimental/wo0014/axis_dma_rgb_adapter.v $root/fpga/experimental/wo0014/axis_binary_output_checker.v $root/fpga/experimental/wo0014/axis_dma_image_pipeline.v]
set_property top axis_dma_image_pipeline [current_fileset]
synth_design -top axis_dma_image_pipeline -part xc7z020clg400-1 -mode out_of_context
create_clock -name wo14_clk -period 10 [get_ports aclk]
report_methodology -json $result/reports/methodology.json
write_xdc -force -exclude_physical $result/reports/constraints.xdc
report_utilization -file $result/reports/utilization.rpt
report_timing_summary -file $result/reports/timing-ooc.rpt
report_timing -max_paths 10 -file $result/reports/worst-paths.rpt
write_checkpoint -force $result/reports/optimized-ooc.dcp
