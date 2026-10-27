# Standalone Gray8 XSim entrypoint; source paths resolve from this script.
set script_dir [file dirname [file normalize [info script]]]
set root [file normalize [file join $script_dir .. ..]]
if {[info exists ::env(GRAY8_OUT)]} { set out [file normalize $::env(GRAY8_OUT)] } else {
    set stamp [clock format [clock seconds] -format %Y%m%d-%H%M%S]
    set out [file join $root data results gray8-xsim-$stamp]
}
set work [file join $out xsim]
file mkdir $work
set bin [file dirname [info nameofexecutable]]
set xvlog [file join $bin xvlog.exe]; set xelab [file join $bin xelab.exe]; set xsim [file join $bin xsim.exe]
foreach tool [list $xvlog $xelab $xsim] { if {![file isfile $tool]} { error "Vivado XSim executable not found: $tool" } }
cd $work
set sources [list \
 [file join $root fpga rtl axis_gray2binary.v] \
 [file join $root fpga experimental wo0014 axis_binary_output_checker.v] \
 [file join $root fpga experimental gray8 axis_dma_gray_adapter.v] \
 [file join $root fpga experimental gray8 axis_dma_gray_image_pipeline.v] \
 [file join $root fpga experimental wo0015 axis_s2mm_frame_packer.v] \
 [file join $root fpga experimental gray8 axis_dma_gray_s2mm_pipeline_bd.v] \
 [file join $root fpga sim gray8 tb_axis_dma_gray_adapter.sv] \
 [file join $root fpga sim gray8 tb_axis_dma_gray_wrapper.sv] \
 [file join $root fpga sim wo0015 tb_axis_s2mm_guards.sv]]
exec $xvlog -sv {*}$sources -log [file join $work compile.log]
exec $xelab tb_axis_dma_gray_adapter -s gray8_pipeline -log [file join $work elaborate.log]
exec $xelab tb_axis_dma_gray_wrapper -s gray8_wrapper_default -log [file join $work wrapper-elaborate.log]
exec $xelab tb_axis_s2mm_guards -s gray8_guards -log [file join $work guards-elaborate.log]
set logfile [file join $work sim.log]
set output [exec $xsim gray8_pipeline -R -log $logfile]
puts $output
if {[string match {*Fatal:*} $output] || [string match {*simulation timeout*} $output] || ![string match {*GRAY8_ADAPTER_PASS*} $output]} {
    error "Gray8 adapter/pipeline simulation failed; see $logfile"
}
set wrapper_log [file join $work wrapper-sim.log]
set wrapper_output [exec $xsim gray8_wrapper_default -R -log $wrapper_log]
puts $wrapper_output
if {[string match {*Fatal:*} $wrapper_output] || [string match {*simulation timeout*} $wrapper_output] || ![string match {*GRAY8_WRAPPER_320X240_PASS*} $wrapper_output]} {
    error "Gray8 wrapper simulation failed; see $wrapper_log"
}
puts "GRAY8_XSIM_PASS output=$out"
set guard_output [exec $xsim gray8_guards -R -log [file join $work guards-sim.log]]
puts $guard_output
if {[string match {*Fatal:*} $guard_output] || ![string match {*WO0015_GUARDS_PASS*} $guard_output]} {
    error "Gray8 S2MM guard simulation failed"
}
puts "GRAY8_GUARDS_PASS"
