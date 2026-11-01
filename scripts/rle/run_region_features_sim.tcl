# Isolated behavioral XSim run for the standalone RLE region feature candidate.
set script_dir [file dirname [file normalize [info script]]]
set repo_root [file normalize [file join $script_dir ../..]]
set rtl_file [file join $repo_root fpga experimental rle rle_region_features.v]
set tb_file [file join $repo_root fpga sim rle tb_rle_region_features.sv]
if {![file isfile $rtl_file] || ![file isfile $tb_file]} {
    error "RLE_REGION_FEATURES_SOURCE_MISSING"
}
set result_root [file join $repo_root .runtime]
set run_dir [file join $result_root "rf-[pid]-[clock clicks]"]
file mkdir $run_dir
cd $run_dir
catch {close_project -quiet}
create_project -force rf $run_dir
add_files -fileset sim_1 [list $rtl_file $tb_file]
set_property top tb_rle_region_features [get_filesets sim_1]
set_property xsim.simulate.runtime {} [get_filesets sim_1]
update_compile_order -fileset sim_1
catch {close_sim -force}
launch_simulation -simset sim_1 -mode behavioral
run all
set transcript [file join $run_dir rf.sim sim_1 behav xsim simulate.log]
if {![file isfile $transcript]} { error "RLE_FEATURES_TRANSCRIPT_MISSING:$transcript" }
set fd [open $transcript r]
set transcript_text [read $fd]
close $fd
if {![regexp {RLE_FEATURE_PASS cases=12} $transcript_text]} {
    error "RLE_FEATURE_PASS_MARKER_MISSING:$transcript"
}
if {[regexp {Fatal:|TEST_FAIL|watchdog timeout} $transcript_text]} {
    error "RLE_FEATURES_RUNTIME_FAILURE:$transcript"
}
add_files -fileset sources_1 [list $rtl_file]
update_compile_order -fileset sources_1
# PYNQ-Z2 is XC7Z020; reduced parameters keep this OOC run a structural check
# of the exact same source without requesting the full default-size resources.
synth_design -top rle_region_features -part xc7z020clg400-1 \
    -generic MAX_LABELS=8 -generic MAX_RUNS_PER_ROW=4 \
    -mode out_of_context -name rle_region_features_ooc
set util_report [file join $run_dir rle_region_features_utilization.rpt]
report_utilization -file $util_report
if {![file isfile $util_report] || [file size $util_report] == 0} {
    error "RLE_OOC_UTILIZATION_REPORT_MISSING:$util_report"
}
puts "RLE_OOC_SYNTH_PASS part=xc7z020clg400-1 MAX_LABELS=8 MAX_RUNS_PER_ROW=4 report=$util_report"
puts "RLE_FEATURE_PASS cases=12 transcript=$transcript"
