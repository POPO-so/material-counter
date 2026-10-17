# Self-check the integration register window and full one-packet datapath offline.
set root C:/fpga/material-counter
set result $root/data/results/wo0014-hw-verification-20261014
set bin [file dirname [info nameofexecutable]]
file mkdir $result/sim
cd $result/sim
exec [file join $bin xvlog.exe] -sv $root/fpga/rtl/axis_rgb2gray.v $root/fpga/rtl/axis_gray2binary.v $root/fpga/experimental/wo0014/axis_dma_rgb_adapter.v $root/fpga/experimental/wo0014/axis_binary_output_checker.v $root/fpga/experimental/wo0014/axis_dma_image_pipeline.v $root/fpga/experimental/wo0014/axis_dma_image_pipeline_bd.v $root/fpga/sim/wo0014/tb_axis_dma_image_pipeline_bd.sv -log $result/compile.log
exec [file join $bin xelab.exe] tb_axis_dma_image_pipeline_bd -s wo14_wrapper_small -log $result/elaborate-small.log
set response [exec [file join $bin xsim.exe] wo14_wrapper_small -R -log $result/wrapper-small.log]
puts $response
if {![string match {*RESULT PASS:*} $response] || [string match {*Fatal:*} $response]} {error "Wrapper AXI-Lite/recovery regression failed"}
exec [file join $bin xelab.exe] tb_axis_dma_image_pipeline_bd -generic_top WIDTH=320 -generic_top HEIGHT=240 -s wo14_wrapper_full -log $result/elaborate-full.log
# Use relative paths for the XSim fixture arguments on Windows.
set response [exec [file join $bin xsim.exe] wo14_wrapper_full -R -testplusarg INPUT=../first-frame-beats.bin -testplusarg EXPECTED=../first-frame-golden.bin -testplusarg EXPECTED_FRAMES=1 -log $result/wrapper-full.log]
puts $response
if {![string match {*RESULT PASS:*} $response] || [string match {*Fatal:*} $response]} {error "Wrapper full-frame golden regression failed"}
puts "WO14_WRAPPER_REGRESSION_COMPLETE hardware=false"
