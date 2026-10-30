set work [file normalize $::env(RLE_OUT)]
set bin [file dirname [info nameofexecutable]]
cd $work
set netlist [file join $work axis_dma_rle_pipeline_bd_netlist.v]
set tb [file join $work tb_axis_rle_pipeline.sv]
set glbl [file join $::env(XILINX_VIVADO) data verilog src glbl.v]
exec [file join $bin xvlog.exe] -sv $netlist $tb $glbl -log compile.log
exec [file join $bin xelab.exe] -L unisims_ver tb_axis_rle_pipeline glbl -s rle_pipeline_netlist -log elaborate.log
set ch [open [file join $work vectors cases.txt] r]
set cases [split [string trim [read $ch]] "\n"]
close $ch
foreach line $cases {
    lassign $line name words mode offset x0 y0 x1 y1
    set argv [list [file join $bin xsim.exe] rle_pipeline_netlist -R -log ${name}-sim.log \
        -testplusarg INPUT=[file join $work vectors ${name}-input.hex] \
        -testplusarg EXPECTED=[file join $work vectors ${name}-expected.hex] \
        -testplusarg OUTPUT=[file join $work ${name}-actual.hex] \
        -testplusarg WORDS=$words -testplusarg MORPH=$mode -testplusarg OFFSET=$offset \
        -testplusarg X0=$x0 -testplusarg Y0=$y0 -testplusarg X1=$x1 -testplusarg Y1=$y1]
    set result [exec {*}$argv]
    puts $result
    if {[string match {*Fatal:*} $result] || ![string match {*RLE_PIPELINE_640X400_PASS*} $result]} {
        error "Full integration netlist functional regression failed: $name"
    }
}
puts "RLE_FULL_PIPELINE_POST_SYNTH_NETLIST_PASS cases=[llength $cases]"
