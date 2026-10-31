set repo [file normalize [file join [file dirname [info script]] ../..]]
if {![info exists ::env(OVERLAY_SIM_OUT)]} {error "OVERLAY_SIM_OUT required"}
set out [file normalize $::env(OVERLAY_SIM_OUT)]
file mkdir $out
cd $out
set bin [file join $::env(XILINX_VIVADO) bin]
exec [file join $bin xvlog.bat] -sv [file join $repo fpga experimental rle rle_region_features.v] [file join $repo fpga experimental rle rle_feature_tap.v] [file join $repo fpga experimental standalone video_binary_overlay.v] [file join $repo fpga experimental standalone video_binary_overlay_bd.v] [file join $repo fpga sim standalone tb_video_binary_overlay.sv] [file join $repo fpga sim standalone tb_video_binary_overlay_bd.sv]
foreach top {tb_video_binary_overlay tb_video_binary_overlay_scaled tb_video_binary_overlay_bd} {
 exec [file join $bin xelab.bat] $top -s ${top}_sim
 exec [file join $bin xsim.bat] ${top}_sim -runall -log ${top}.log
 set f [open ${top}.log]; set log [read $f]; close $f
 if {[string first BINARY_OVERLAY_PASS $log]<0 && [string first BINARY_OVERLAY_BD_PASS $log]<0} {error "BINARY_OVERLAY_SIM_FAILED:$top:$out"}
 puts "BINARY_OVERLAY_PASS:$top:$out"
}
