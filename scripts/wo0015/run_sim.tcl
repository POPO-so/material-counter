# Offline WO-0015 simulation entrypoint. Source this file from Vivado Tcl.
# It runs XSim only; no synthesis, implementation, board, SSH, or DMA action.
set script_dir [file dirname [file normalize [info script]]]
set default_root [file normalize [file join $script_dir .. ..]]
if {[info exists ::env(WO0015_ROOT)]} {
    set root [file normalize $::env(WO0015_ROOT)]
} else {
    set root $default_root
}

if {[info exists ::env(WO0015_OUT)]} {
    set out [file normalize $::env(WO0015_OUT)]
} else {
    set stamp [clock format [clock seconds] -format %Y%m%d-%H%M%S]
    set out [file join $root data results wo0015-xsim-$stamp]
}
set vectors [file join $out vectors]
set work [file join $out xsim]
file mkdir $out
file mkdir $work

set source_root C:/fpga/material-counter
if {[info exists ::env(WO0015_SOURCE_ROOT)]} {
    set source_root [file normalize $::env(WO0015_SOURCE_ROOT)]
}
set h3_root [file join $source_root data synthetic h3_full]
set source_manifest [file join $source_root data results wo0014-offline-20261014 vectors manifest.json]
set python [file join $root .venv Scripts python.exe]
if {![file isfile $python]} {
    set python C:/fpga/material-counter/.venv/Scripts/python.exe
}
if {![file isfile $python]} {
    error "WO-0015 Python interpreter not found: $python"
}

set generator [file join $root scripts wo0015 prepare_vectors.py]
set checker [file join $root scripts wo0015 check_sim.py]
set gen_cmd [list $python -E $generator --root $root --out $vectors \
    --h3-root $h3_root --source-manifest $source_manifest]
set gen_output [exec {*}$gen_cmd]
puts $gen_output

set bin [file dirname [info nameofexecutable]]
set xvlog [file join $bin xvlog.exe]
set xelab [file join $bin xelab.exe]
set xsim [file join $bin xsim.exe]
foreach tool [list $xvlog $xelab $xsim] {
    if {![file isfile $tool]} { error "Vivado simulation executable not found: $tool" }
}

cd $work
set packer_rtl [file join $root fpga experimental wo0015 axis_s2mm_frame_packer.v]
set packer_tb [file join $root fpga sim wo0015 tb_axis_s2mm_frame_packer.sv]
set pipeline_sources [list \
    [file join $root fpga rtl axis_gray2binary.v] \
    [file join $root fpga experimental wo0014 axis_dma_gray_adapter.v] \
    [file join $root fpga experimental wo0014 axis_binary_output_checker.v] \
    [file join $root fpga experimental wo0014 axis_dma_image_pipeline.v] \
    $packer_rtl \
    [file join $root fpga experimental wo0015 axis_dma_s2mm_pipeline_bd.v] \
    [file join $root fpga sim wo0015 tb_axis_dma_s2mm_pipeline.sv]]

set compile_packer [list $xvlog -sv $packer_rtl $packer_tb -log [file join $work compile-packer.log]]
exec {*}$compile_packer
exec $xelab tb_axis_s2mm_frame_packer -s wo15_packer -log [file join $work elaborate-packer.log]
set packer_log [file join $work packer.log]
set packer_output [exec $xsim wo15_packer -R -log $packer_log]
puts $packer_output
if {[string match {*Fatal:*} $packer_output] ||
    ![string match {*WO0015_PACKER_PASS*} $packer_output]} {
    error "WO-0015 packer test failed; see $packer_log"
}

set compile_pipeline [concat [list $xvlog -sv] $pipeline_sources \
    [list -log [file join $work compile-pipeline.log]]]
exec {*}$compile_pipeline
exec $xelab tb_axis_dma_s2mm_pipeline -s wo15_pipeline -log [file join $work elaborate-pipeline.log]

# Exercise direct format guards and the AXI-Lite busy-arm rejection separately.
exec $xvlog -sv [file join $root fpga sim wo0015 tb_axis_s2mm_guards.sv] -log [file join $work compile-guards.log]
exec $xelab tb_axis_s2mm_guards -s wo15_guards -log [file join $work elaborate-guards.log]
set guards_output [exec $xsim wo15_guards -R -log [file join $work guards.log]]
puts $guards_output
if {[string match {*Fatal:*} $guards_output] || ![string match {*WO0015_GUARDS_PASS*} $guards_output]} {
    error "WO-0015 format/busy-arm guard test failed"
}

proc run_case {xsim python checker snapshot case_dir name mode frames seed work} {
    set actual [file join $work ${name}-actual.hex]
    set metrics [file join $work ${name}-metrics.tsv]
    set sim_log [file join $work ${name}.log]
    set result [file join $work ${name}-result.json]
    set command [list $xsim $snapshot -R \
        -testplusarg "INPUT=[file join $case_dir beats.hex]" \
        -testplusarg "GOLDEN=[file join $case_dir golden.hex]" \
        -testplusarg "ACTUAL=$actual" \
        -testplusarg "METRICS=$metrics" \
        -testplusarg "MODE=$mode" \
        -testplusarg "FRAMES=$frames" \
        -testplusarg "SEED=$seed" \
        -testplusarg "CASE=$name" \
        -log $sim_log]
    set output [exec {*}$command]
    puts $output
    if {[string match {*Fatal:*} $output]} { error "$name XSim fatal; see $sim_log" }
    set check_cmd [list $python -E $checker --case-dir $case_dir --actual $actual \
        --metrics $metrics --result $result --sim-log $sim_log --seed $seed]
    set check_output [exec {*}$check_cmd]
    puts $check_output
    return $result
}

foreach item {
    {A 0 1 1}
    {B 0 3 1}
} {
    lassign $item case_name mode frame_count seed
    run_case $xsim $python $checker wo15_pipeline \
        [file join $vectors $case_name] $case_name $mode $frame_count $seed $work
}
foreach seed {1 7 42 20261015} {
    set case_name C
    set run_name C-seed$seed
    run_case $xsim $python $checker wo15_pipeline \
        [file join $vectors $case_name] $run_name 1 3 $seed $work
}
foreach case_name {D_short D_long D_checksum} {
    run_case $xsim $python $checker wo15_pipeline \
        [file join $vectors $case_name] $case_name 2 1 1 $work
}
run_case $xsim $python $checker wo15_pipeline \
    [file join $vectors D_reset] D_reset 3 1 1 $work

puts "WO0015_XSIM_SUITE_PASS output=$out"
