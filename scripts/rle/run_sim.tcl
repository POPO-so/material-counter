set root [file normalize [file join [file dirname [info script]] ../..]]
if {![info exists ::env(RLE_OUT)]} {error "Set RLE_OUT to a new evidence directory"}
set work [file normalize [file join $::env(RLE_OUT) xsim]]
file mkdir $work
set bin [file dirname [info nameofexecutable]]
cd $work
set sources [list [file join $root fpga experimental rle axis_rle_encoder.v] \
    [file join $root fpga experimental gray8 axis_dma_gray_adapter.v] \
    [file join $root fpga rtl axis_morphology3x3.v] \
    [file join $root fpga experimental rle axis_dma_rle_pipeline_bd.v] \
    [file join $root fpga sim rle tb_axis_rle_encoder.sv] \
    [file join $root fpga sim rle tb_axis_rle_pipeline.sv]]
exec [file join $bin xvlog.exe] -sv {*}$sources -log compile.log
exec [file join $bin xelab.exe] tb_axis_rle_encoder -s rle_encoder -log elaborate.log
set result [exec [file join $bin xsim.exe] rle_encoder -R -log sim.log]
puts $result
if {[string match {*Fatal:*} $result] || ![string match {*RLE_ENCODER_PASS*} $result]} {
    error "RLE encoder simulation failed"
}
exec [file join $bin xelab.exe] tb_axis_rle_pipeline -s rle_pipeline -log pipeline-elaborate.log
set vectors [file normalize [file join $::env(RLE_OUT) vectors]]
set stream [open [file join $vectors cases.txt] r]
set cases [split [string trim [read $stream]] "\n"]
close $stream
foreach line $cases {
    lassign $line name words mode offset x0 y0 x1 y1
    set result [exec [file join $bin xsim.exe] rle_pipeline -R -log ${name}-sim.log \
        -testplusarg INPUT=[file join $vectors ${name}-input.hex] \
        -testplusarg EXPECTED=[file join $vectors ${name}-expected.hex] \
        -testplusarg OUTPUT=[file join $work ${name}-actual.hex] \
        -testplusarg WORDS=$words -testplusarg MORPH=$mode -testplusarg OFFSET=$offset \
        -testplusarg X0=$x0 -testplusarg Y0=$y0 -testplusarg X1=$x1 -testplusarg Y1=$y1]
    puts $result
    if {[string match {*Fatal:*} $result] || ![string match {*RLE_PIPELINE_640X400_PASS*} $result]} {
        error "RLE full-resolution simulation failed: $name"
    }
}
puts "RLE_XSIM_ALL_PASS cases=[llength $cases]"
