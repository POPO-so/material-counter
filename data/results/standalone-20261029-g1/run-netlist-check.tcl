set repo [pwd]
cd C:/Users/14173/Documents/material-counter/data/results/standalone-20261029-g1/netlist-check
set bin [file join $::env(XILINX_VIVADO) bin]
exec [file join $bin xvlog.bat] -sv [file join $repo data results standalone-20261029-g1 project display.gen sources_1 bd display ip display_frame_monitor_0 display_frame_monitor_0_sim_netlist.v] [file join $repo fpga sim standalone tb_video_monitor_netlist.sv]
exec [file join $bin xelab.bat] tb_video_monitor_netlist glbl -L unisims_ver -s monitor_netlist
puts [exec [file join $bin xsim.bat] monitor_netlist -runall -log monitor-netlist.log]
exit
