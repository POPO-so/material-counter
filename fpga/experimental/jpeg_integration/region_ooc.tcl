# Source from a Vivado 2026.1 TCL session whose cwd is C:/w/mc-jpeg-regions-20261101.
set src $::env(REGION_SRC_DIR)
read_verilog [file join $src rle_region_features.v]
read_xdc [file join $src region_clock.xdc]
synth_design -top rle_region_features -part xc7z020clg400-1 -mode out_of_context -flatten_hierarchy none
report_utilization -file region_util.rpt
report_timing_summary -delay_type max -file region_timing.rpt
set stats_bram [get_cells -hier -filter {NAME =~ *stats_mem* && REF_NAME =~ RAMB*}]
set row_bram [get_cells -hier -filter {NAME =~ *row_mem* && REF_NAME =~ RAMB*}]
set stats_lutram [get_cells -quiet -hier -filter {NAME =~ *stats_mem* && REF_NAME =~ RAM* && REF_NAME !~ RAMB*}]
set memlog [open region_memory.rpt w]
puts $memlog "stats_bram=[llength $stats_bram]: $stats_bram"
puts $memlog "row_bram=[llength $row_bram]: $row_bram"
puts $memlog "stats_lutram=[llength $stats_lutram]: $stats_lutram"
close $memlog
write_checkpoint -force region_synth.dcp
if {[info exists ::env(REGION_REQUIRE_BRAM)] && $::env(REGION_REQUIRE_BRAM) eq "1"} {
    if {[llength $stats_bram] == 0 || [llength $row_bram] == 0 || [llength $stats_lutram] != 0} {
        error "REGION_BRAM_FAIL: stats and row must be BRAM; see region_memory.rpt"
    }
    puts "REGION_BRAM_PASS"
}
