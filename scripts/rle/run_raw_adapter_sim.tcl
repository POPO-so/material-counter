set root [file normalize [file join [file dirname [info script]] ../..]]
set out [file normalize [file join $root .runtime evidence raw-gray-adapter]]
file mkdir $out
cd $out
set bin [file dirname [info nameofexecutable]]
set rtl [file join $root fpga experimental gray8 axis_raw_gray_adapter.v]
set tb [file join $root fpga sim rle tb_axis_raw_gray_adapter.sv]
exec [file join $bin xvlog.exe] -sv $rtl $tb -log compile.log
exec [file join $bin xelab.exe] tb_axis_raw_gray_adapter -s raw_gray_adapter -log elaborate.log
set result [exec [file join $bin xsim.exe] raw_gray_adapter -runall -log sim.log]
puts $result
if {[string match {*Fatal:*} $result] || ![string match {*RAW_GRAY_ADAPTER_PASS*} $result]} {
    error "raw Gray8 adapter simulation failed"
}

set enc [file join $root fpga experimental rle axis_rle_encoder.v]
set packet_adapter [file join $root fpga experimental gray8 axis_dma_gray_adapter.v]
set raw_adapter [file join $root fpga experimental gray8 axis_raw_gray_adapter.v]
set morphology [file join $root fpga rtl axis_morphology3x3.v]
set pipeline [file join $root fpga experimental rle axis_dma_rle_pipeline_bd.v]
set pipeline_tb [file join $root fpga sim rle tb_axis_rle_pipeline.sv]
exec [file join $bin xvlog.exe] -sv $enc $packet_adapter $raw_adapter $morphology $pipeline $pipeline_tb -log packet-compile.log
exec [file join $bin xelab.exe] tb_axis_rle_pipeline -s rle_pipeline -log packet-elaborate.log
set vectors [file join $root .runtime evidence rle-vectors]
set cases_file [open [file join $vectors cases.txt] r]
set cases [split [string trim [read $cases_file]] "\n"]
close $cases_file
foreach line $cases {
    lassign $line name words mode offset x0 y0 x1 y1
    set result [exec [file join $bin xsim.exe] rle_pipeline -runall -log packet-${name}.log \
        -testplusarg INPUT=[file join $vectors ${name}-input.hex] \
        -testplusarg EXPECTED=[file join $vectors ${name}-expected.hex] \
        -testplusarg OUTPUT=[file join $out ${name}-actual.hex] \
        -testplusarg WORDS=$words -testplusarg MORPH=$mode -testplusarg OFFSET=$offset \
        -testplusarg X0=$x0 -testplusarg Y0=$y0 -testplusarg X1=$x1 -testplusarg Y1=$y1]
    puts $result
    if {[string match {*Fatal:*} $result] || ![string match {*RLE_PIPELINE_640X400_PASS*} $result]} {
        error "legacy packet pipeline regression failed: $name"
    }
}
puts "RAW_AND_PACKET_RLE_SIM_PASS packet_cases=[llength $cases]"
