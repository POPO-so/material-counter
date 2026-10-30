# Regress the actual integrated RLE IP checkpoint, including candidate counters.
# Use a fresh Vivado session and new RLE_OUT. This is not a top-level PS/HDMI sim.
foreach name {RLE_OUT RLE_CHECKPOINT RLE_VECTORS} {
    if {![info exists ::env($name)]} {error "Missing $name"}
}
set repo [file normalize [file join [file dirname [info script]] ../..]]
set work [file normalize $::env(RLE_OUT)]
file mkdir $work
set netlist [file join $work pipeline-netlist.v]
if {[file exists $netlist]} {error "Use a new RLE_OUT; never overwrite netlist evidence"}
open_checkpoint [file normalize $::env(RLE_CHECKPOINT)]
if {[get_property NAME [current_design]] ni {display_image_pipeline_0 checkpoint_display_image_pipeline_0}} {
    error "Unexpected IP checkpoint design"
}
write_verilog -mode funcsim $netlist
set source [open [file join $repo fpga sim rle tb_axis_rle_pipeline.sv] r]
set tb_text [read $source];close $source
if {[string first {axis_dma_rle_pipeline_bd dut(} $tb_text] < 0} {error "Unexpected testbench DUT name"}
set tb [file join $work tb_pipeline.sv]
set target [open $tb {WRONLY CREAT EXCL}]
puts $target [string map {{axis_dma_rle_pipeline_bd dut(} {display_image_pipeline_0 dut(}} $tb_text]
close $target
set bin [file dirname [info nameofexecutable]]
cd $work
exec [file join $bin xvlog.exe] -sv $netlist $tb \
    [file join $::env(XILINX_VIVADO) data verilog src glbl.v] -log compile.log
exec [file join $bin xelab.exe] -L unisims_ver tb_axis_rle_pipeline glbl -s full_pipeline -log elaborate.log
set vectors [file normalize $::env(RLE_VECTORS)]
set handle [open [file join $vectors cases.txt] r]
set cases [split [string trim [read $handle]] "\n"];close $handle
foreach line $cases {
    lassign $line name words mode offset x0 y0 x1 y1
    set result [exec [file join $bin xsim.exe] full_pipeline -R -log ${name}-sim.log \
        -testplusarg INPUT=[file join $vectors ${name}-input.hex] \
        -testplusarg EXPECTED=[file join $vectors ${name}-expected.hex] \
        -testplusarg OUTPUT=[file join $work ${name}-actual.hex] \
        -testplusarg WORDS=$words -testplusarg MORPH=$mode -testplusarg OFFSET=$offset \
        -testplusarg X0=$x0 -testplusarg Y0=$y0 -testplusarg X1=$x1 -testplusarg Y1=$y1]
    puts $result
    if {[string match {*Fatal:*} $result] || ![string match {*RLE_COUNTER_SNAPSHOT_PASS*} $result] ||
        ![string match {*RLE_PIPELINE_640X400_PASS*} $result]} {error "Full pipeline regression failed: $name"}
}
puts "RLE_FULL_PIPELINE_POST_SYNTH_NETLIST_PASS cases=[llength $cases]"
