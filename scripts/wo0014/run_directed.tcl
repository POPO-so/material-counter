set bin [file dirname [info nameofexecutable]]
cd C:/fpga/material-counter/data/results/wo0014-offline-20261014/new-sim
exec [file join $bin xvlog.exe] -sv -d WO0014_WIDTH=2 -d WO0014_HEIGHT=1 C:/fpga/material-counter/fpga/sim/wo0014/tb_axis_dma_image_pipeline.sv -log compile-keep3.log
exec [file join $bin xelab.exe] tb_axis_dma_image_pipeline -s wo14_keep3 -log elaborate-keep3.log
set result [exec [file join $bin xsim.exe] wo14_keep3 -R -testplusarg INPUT=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/partial_keep3_packets.bin -testplusarg EXPECTED=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/partial_keep3_expected_th128.bin -testplusarg ACTUAL=C:/fpga/material-counter/data/results/wo0014-offline-20261014/keep3-actual.bin -testplusarg BASE_FRAME_ID=40980 -log C:/fpga/material-counter/data/results/wo0014-offline-20261014/keep3.log]
puts $result
if {![string match {*RESULT PASS:*} $result] || [string match {*Fatal:*} $result]} {error {Partial payload failed}}
exec [file join $bin xvlog.exe] -sv -d WO0014_WIDTH=1 -d WO0014_HEIGHT=1 C:/fpga/material-counter/fpga/sim/wo0014/tb_axis_dma_image_pipeline.sv -log compile-keep7.log
exec [file join $bin xelab.exe] tb_axis_dma_image_pipeline -s wo14_keep7 -log elaborate-keep7.log
set result [exec [file join $bin xsim.exe] wo14_keep7 -R -testplusarg INPUT=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/partial_keep7_packets.bin -testplusarg EXPECTED=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/partial_keep7_expected_th128.bin -testplusarg ACTUAL=C:/fpga/material-counter/data/results/wo0014-offline-20261014/keep7-actual.bin -testplusarg BASE_FRAME_ID=40980 -log C:/fpga/material-counter/data/results/wo0014-offline-20261014/keep7.log]
puts $result
if {![string match {*RESULT PASS:*} $result] || [string match {*Fatal:*} $result]} {error {Partial payload failed}}
exec [file join $bin xvlog.exe] -sv -d WO0014_SMALL -d WO0014_THRESHOLD=0 C:/fpga/material-counter/fpga/sim/wo0014/tb_axis_dma_image_pipeline.sv -log compile-th0.log
exec [file join $bin xelab.exe] tb_axis_dma_image_pipeline -s wo14_th0 -log elaborate-th0.log
set result [exec [file join $bin xsim.exe] wo14_th0 -R -testplusarg INPUT=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/directed_packets.bin -testplusarg EXPECTED=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/directed_expected_th0.bin -testplusarg ACTUAL=C:/fpga/material-counter/data/results/wo0014-offline-20261014/th0-actual.bin -testplusarg BASE_FRAME_ID=40980 -testplusarg READY_MODE=2 -testplusarg GAP_MODE=2 -log C:/fpga/material-counter/data/results/wo0014-offline-20261014/th0.log]
puts $result
if {![string match {*RESULT PASS:*} $result] || [string match {*Fatal:*} $result]} {error {Threshold failed}}
exec [file join $bin xvlog.exe] -sv -d WO0014_SMALL -d WO0014_THRESHOLD=73 C:/fpga/material-counter/fpga/sim/wo0014/tb_axis_dma_image_pipeline.sv -log compile-th73.log
exec [file join $bin xelab.exe] tb_axis_dma_image_pipeline -s wo14_th73 -log elaborate-th73.log
set result [exec [file join $bin xsim.exe] wo14_th73 -R -testplusarg INPUT=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/directed_packets.bin -testplusarg EXPECTED=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/directed_expected_th73.bin -testplusarg ACTUAL=C:/fpga/material-counter/data/results/wo0014-offline-20261014/th73-actual.bin -testplusarg BASE_FRAME_ID=40980 -testplusarg READY_MODE=2 -testplusarg GAP_MODE=2 -log C:/fpga/material-counter/data/results/wo0014-offline-20261014/th73.log]
puts $result
if {![string match {*RESULT PASS:*} $result] || [string match {*Fatal:*} $result]} {error {Threshold failed}}
exec [file join $bin xvlog.exe] -sv -d WO0014_SMALL -d WO0014_THRESHOLD=200 C:/fpga/material-counter/fpga/sim/wo0014/tb_axis_dma_image_pipeline.sv -log compile-th200.log
exec [file join $bin xelab.exe] tb_axis_dma_image_pipeline -s wo14_th200 -log elaborate-th200.log
set result [exec [file join $bin xsim.exe] wo14_th200 -R -testplusarg INPUT=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/directed_packets.bin -testplusarg EXPECTED=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/directed_expected_th200.bin -testplusarg ACTUAL=C:/fpga/material-counter/data/results/wo0014-offline-20261014/th200-actual.bin -testplusarg BASE_FRAME_ID=40980 -testplusarg READY_MODE=2 -testplusarg GAP_MODE=2 -log C:/fpga/material-counter/data/results/wo0014-offline-20261014/th200.log]
puts $result
if {![string match {*RESULT PASS:*} $result] || [string match {*Fatal:*} $result]} {error {Threshold failed}}
exec [file join $bin xvlog.exe] -sv -d WO0014_SMALL -d WO0014_THRESHOLD=255 C:/fpga/material-counter/fpga/sim/wo0014/tb_axis_dma_image_pipeline.sv -log compile-th255.log
exec [file join $bin xelab.exe] tb_axis_dma_image_pipeline -s wo14_th255 -log elaborate-th255.log
set result [exec [file join $bin xsim.exe] wo14_th255 -R -testplusarg INPUT=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/directed_packets.bin -testplusarg EXPECTED=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/directed_expected_th255.bin -testplusarg ACTUAL=C:/fpga/material-counter/data/results/wo0014-offline-20261014/th255-actual.bin -testplusarg BASE_FRAME_ID=40980 -testplusarg READY_MODE=2 -testplusarg GAP_MODE=2 -log C:/fpga/material-counter/data/results/wo0014-offline-20261014/th255.log]
puts $result
if {![string match {*RESULT PASS:*} $result] || [string match {*Fatal:*} $result]} {error {Threshold failed}}
