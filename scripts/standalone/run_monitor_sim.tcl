set project_root [file normalize [file join [file dirname [info script]] ../..]]
if {![info exists ::env(MONITOR_SIM_OUT)] || $::env(MONITOR_SIM_OUT) eq ""} {
  error "MONITOR_SIM_OUT must name an isolated simulation output directory"
}
set out_dir [file normalize $::env(MONITOR_SIM_OUT)]
file mkdir $out_dir
cd $out_dir

if {![info exists ::env(XILINX_VIVADO)]} {
  error "Vivado did not expose XILINX_VIVADO in its environment"
}
set vivado_bin [file join $::env(XILINX_VIVADO) bin]
set dut_file [file join $project_root fpga experimental standalone video_frame_monitor.v]
set tb_file [file join $project_root fpga sim standalone tb_video_frame_monitor.sv]

if {![file exists $dut_file]} {
  error "DUT source is missing: $dut_file"
}
exec [file join $vivado_bin xvlog.bat] -sv $dut_file $tb_file
exec [file join $vivado_bin xelab.bat] tb_video_frame_monitor -s video_frame_monitor_sim
exec [file join $vivado_bin xsim.bat] video_frame_monitor_sim -runall -log monitor_sim.log

set fd [open [file join $out_dir monitor_sim.log] r]
set sim_log [read $fd]
close $fd
if {[string first "VIDEO_MONITOR_PASS" $sim_log] < 0} {
  error "Simulation finished without VIDEO_MONITOR_PASS; inspect $out_dir/monitor_sim.log"
}
puts "VIDEO_MONITOR_PASS verified in $out_dir/monitor_sim.log"
