# Functional post-synthesis check for inferred projection RAM read ports.
set root [file normalize [file join [file dirname [info script]] ../..]]
if {![info exists ::env(RLE_OUT)]} {error "Set RLE_OUT"}
set work [file normalize $::env(RLE_OUT)]
file mkdir $work
set bin [file dirname [info nameofexecutable]]
cd $work
read_verilog [file join $root fpga experimental rle axis_rle_encoder.v]
synth_design -mode out_of_context -top axis_rle_encoder -part xc7z020clg400-1 \
    -generic WIDTH=5 -generic HEIGHT=3 -generic MAX_RUNS=2
write_verilog -force -mode funcsim encoder-netlist.v
exec [file join $bin xvlog.exe] -sv -d POST_SYNTH encoder-netlist.v \
    [file join $root fpga sim rle tb_axis_rle_encoder.sv] \
    [file join $::env(XILINX_VIVADO) data verilog src glbl.v] -log compile.log
exec [file join $bin xelab.exe] -L unisims_ver tb_axis_rle_encoder glbl -s netlist -log elaborate.log
set result [exec [file join $bin xsim.exe] netlist -R -log sim.log]
puts $result
if {[string match {*Fatal:*} $result] || ![string match {*RLE_ENCODER_PASS*} $result]} {
    error "RLE_POST_SYNTHESIS_SIM_FAILED"
}
puts "RLE_POST_SYNTHESIS_PASS"
