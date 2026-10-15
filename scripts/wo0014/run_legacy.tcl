# Reproduce the four unchanged legacy RTL regressions with recorded fixtures.
set root C:/fpga/material-counter
set result [file join $root data results wo0014-offline-20261014]
set bin [file dirname [info nameofexecutable]]
set simdir [file join $result legacy]
file mkdir $simdir
cd $simdir
exec [file join $bin xvlog.exe] -sv [file join $root fpga rtl axis_rgb2gray.v] [file join $root fpga rtl axis_gray2binary.v] [file join $root fpga rtl axis_morphology3x3.v] [file join $root fpga experimental wo0013 axis_dma_rx_monitor.v] [file join $root fpga sim tb_axis_rgb2gray.sv] [file join $root fpga sim tb_axis_gray2binary.sv] [file join $root fpga sim tb_axis_morphology3x3.sv] [file join $root fpga sim wo0013 tb_axis_dma_rx_monitor.sv] -log compile.log
exec [file join $bin xelab.exe] tb_axis_rgb2gray -s legacy_rgb_repro -log elaborate-rgb.log
exec [file join $bin xelab.exe] tb_axis_gray2binary -generic_top CHAIN=1 -generic_top THRESHOLD=128 -s legacy_binary_repro -log elaborate-binary.log
exec [file join $bin xelab.exe] tb_axis_morphology3x3 -generic_top CHAIN=1 -generic_top MODE=2 -generic_top FRAME_WIDTH=320 -generic_top FRAME_HEIGHT=240 -s legacy_morph_repro -log elaborate-morph.log
exec [file join $bin xelab.exe] tb_axis_dma_rx_monitor -s legacy_monitor_repro -log elaborate-monitor.log
proc checked_run {bin snapshot args} {
    set output [exec [file join $bin xsim.exe] $snapshot -R {*}$args]
    puts $output
    if {(![string match {*TEST_PASS*} $output] && ![string match {*WO0013_AXIS_RX_PASS*} $output]) || [string match {*Fatal:*} $output]} {error {Legacy regression failed}}
}
checked_run $bin legacy_rgb_repro -testplusarg READY_MODE=1 -testplusarg RGB=$root/data/results/rgb2gray-vectors-20261012/vectors_rgb.hex -testplusarg EXPECTED=$root/data/results/rgb2gray-vectors-20261012/expected_gray.hex -testplusarg OUTPUT=$result/legacy-rgb-output.hex -log $result/legacy-rgb.log
checked_run $bin legacy_binary_repro -testplusarg READY_MODE=1 -testplusarg GRAY=$result/legacy-binary-vectors/vectors_gray.hex -testplusarg RGB=$result/legacy-binary-vectors/vectors_rgb2binary.hex -testplusarg EXPECTED=$result/legacy-binary-vectors/threshold-128/expected_binary.hex -testplusarg OUTPUT=$result/legacy-binary-output.hex -log $result/legacy-binary-fixed.log
checked_run $bin legacy_morph_repro -testplusarg READY_MODE=1 -testplusarg N=230400 -testplusarg RGB=$root/data/results/morphology-vectors-20261013-final/h3_threshold128/input_rgb.hex -testplusarg BINARY=$root/data/results/morphology-vectors-20261013-final/h3_threshold128/input_binary.hex -testplusarg EXPECTED=$root/data/results/morphology-vectors-20261013-final/h3_threshold128/expected-mode2.hex -testplusarg OUTPUT=$result/legacy-morph-output.hex -log $result/legacy-morph-h3.log
checked_run $bin legacy_monitor_repro -log $result/legacy-monitor.log
