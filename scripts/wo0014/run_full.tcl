# Offline only. Source in Vivado Tcl mode after generating image_vectors.py output.
set root C:/fpga/material-counter
set result [file join $root data results wo0014-offline-20261014]
set bin [file dirname [info nameofexecutable]]
file mkdir [file join $result new-sim]
cd [file join $result new-sim]
exec [file join $bin xvlog.exe] -sv [file join $root fpga rtl axis_rgb2gray.v] [file join $root fpga rtl axis_gray2binary.v] [file join $root fpga experimental wo0014 axis_dma_rgb_adapter.v] [file join $root fpga experimental wo0014 axis_binary_output_checker.v] [file join $root fpga experimental wo0014 axis_dma_image_pipeline.v] [file join $root fpga sim wo0014 tb_axis_dma_image_pipeline.sv] -log compile-final.log
exec [file join $bin xelab.exe] tb_axis_dma_image_pipeline -s wo14_h3_final -log elaborate-final.log
set output [exec [file join $bin xsim.exe] wo14_h3_final -R -testplusarg INPUT=$result/vectors/h3_packets.bin -testplusarg EXPECTED=$result/vectors/h3_expected_th128.bin -testplusarg ACTUAL=$result/h3-final-actual.bin -testplusarg FRAME_COUNT=487 -testplusarg BASE_FRAME_ID=0 -testplusarg READY_MODE=1 -testplusarg GAP_MODE=1 -log $result/h3-final.log]
puts $output
if {![string match {*RESULT PASS:*} $output] || [string match {*Fatal:*} $output]} {error {Full H3 failed}}
exec [file join $bin xvlog.exe] -sv -d WO0014_SMALL [file join $root fpga sim wo0014 tb_axis_dma_image_pipeline.sv] -log compile-small-final.log
exec [file join $bin xelab.exe] tb_axis_dma_image_pipeline -s wo14_small_reviewed -log elaborate-small-final.log
set output [exec [file join $bin xsim.exe] wo14_small_reviewed -R -testplusarg INPUT=$result/vectors/directed_packets.bin -testplusarg EXPECTED=$result/vectors/directed_expected_th128.bin -testplusarg ACTUAL=$result/directed-reviewed-actual.bin -testplusarg BASE_FRAME_ID=40980 -testplusarg RESET_STRESS=1 -log $result/directed-reviewed.log]
puts $output
if {![string match {*RESULT PASS:*} $output] || [string match {*Fatal:*} $output]} {error {Directed/reset failed}}
source [file join $root scripts wo0014 run_negative.tcl]
source [file join $root scripts wo0014 run_directed.tcl]
