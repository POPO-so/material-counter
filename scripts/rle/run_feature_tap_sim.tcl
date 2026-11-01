# Isolated RTL simulation and reduced OOC synthesis for rle_feature_tap.
set script_dir [file dirname [file normalize [info script]]]
set repo_root [file normalize [file join $script_dir ../..]]
set rtl_file [file join $repo_root fpga experimental rle rle_feature_tap.v]
set core_file [file join $repo_root fpga experimental rle rle_region_features.v]
set tb_file [file join $repo_root fpga sim rle tb_rle_feature_tap.sv]
foreach f [list $rtl_file $core_file $tb_file] {
    if {![file isfile $f]} { error "RLE_FEATURE_TAP_SOURCE_MISSING:$f" }
}
set run_root [file join $repo_root .runtime]
set run_dir [file join $run_root "rft-[pid]-[clock clicks]"]
file mkdir $run_dir
cd $run_dir
catch {close_sim -force}
catch {close_project -quiet}
create_project -force rft $run_dir -part xc7z020clg400-1
add_files -fileset sim_1 [list $core_file $rtl_file $tb_file]
set_property top tb_rle_feature_tap [get_filesets sim_1]
set_property xsim.simulate.runtime {} [get_filesets sim_1]
update_compile_order -fileset sim_1
launch_simulation -simset sim_1 -mode behavioral
run all
set transcript [file join $run_dir rft.sim sim_1 behav xsim simulate.log]
if {![file isfile $transcript]} { error "RLE_FEATURE_TAP_TRANSCRIPT_MISSING:$transcript" }
set fd [open $transcript r]
set transcript_text [read $fd]
close $fd
if {![regexp {RLE_FEATURE_TAP_PASS} $transcript_text]} {
    error "RLE_FEATURE_TAP_PASS_MARKER_MISSING:$transcript"
}
if {[regexp {Fatal:|TEST_FAIL|watchdog} $transcript_text]} {
    error "RLE_FEATURE_TAP_RUNTIME_FAILURE:$transcript"
}

# Small wrapper/FIFO parameters, full 4096x32 record RAM retained to check its
# synthesizable single-port template and BRAM inference without a full build.
add_files -fileset sources_1 [list $core_file $rtl_file]
update_compile_order -fileset sources_1
synth_design -top rle_feature_tap -part xc7z020clg400-1 \
    -generic WIDTH=8 -generic HEIGHT=4 -generic FIFO_DEPTH=16 \
    -mode out_of_context -name rle_feature_tap_ooc
set util_report [file join $run_dir rle_feature_tap_utilization.rpt]
report_utilization -file $util_report
if {![file isfile $util_report] || [file size $util_report] == 0} {
    error "RLE_FEATURE_TAP_OOC_REPORT_MISSING:$util_report"
}
set util_fd [open $util_report r]
set util_text [read $util_fd]
close $util_fd
if {![regexp -nocase {RAMB18|RAMB36} $util_text]} {
    error "RLE_FEATURE_TAP_RECORD_RAM_NOT_INFERRED_AS_BRAM:$util_report"
}
puts "RLE_FEATURE_TAP_OOC_PASS part=xc7z020clg400-1 WIDTH=8 HEIGHT=4 FIFO_DEPTH=16 report=$util_report"
puts "RLE_FEATURE_TAP_PASS transcript=$transcript"
