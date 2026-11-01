# Isolated synthesis of the actual exported jpeg_axis_gray RTL; never a BD.
set spec_ch [open [lindex $argv 0] r]
set spec [read $spec_ch]
close $spec_ch
dict size $spec
set out [dict get $spec out]
set_param general.maxThreads 1
if {[get_param general.maxThreads] != 1} {error "JPEG_OOC_THREAD_LIMIT_FAILED"}
create_project -in_memory -part xc7z020clg400-1
set_property include_dirs [list [dict get $spec include_dir]] [current_fileset]
# The vendor ROM uses a relative $readmemh filename. Read only the snapshot.
cd [dict get $spec include_dir]
read_verilog [dict get $spec rtl]
read_xdc [dict get $spec xdc]
synth_design -top jpeg_axis_gray -part xc7z020clg400-1 -mode out_of_context -flatten_hierarchy rebuilt
if {[llength [get_ports ap_clk]] != 1 || [get_property PERIOD [get_clocks ap_clk]] != 20} {error "JPEG_OOC_CLOCK_READBACK_FAILED"}
set blackboxes [get_cells -hier -filter {IS_BLACKBOX == 1}]
if {[llength $blackboxes]} {error "JPEG_OOC_UNRESOLVED_BLACKBOXES:$blackboxes"}
report_utilization -file [file join $out utilization.rpt]
report_utilization -hierarchical -file [file join $out hierarchy.rpt]
report_timing_summary -delay_type min_max -report_unconstrained -file [file join $out timing.rpt]
report_drc -file [file join $out drc.rpt]
write_checkpoint [file join $out jpeg_axis_gray_ooc.dcp]
puts "JPEG_OOC_SYNTH_DONE: top=jpeg_axis_gray part=xc7z020clg400-1 period=20ns threads=1; area only, no route/functional acceptance"
close_project
