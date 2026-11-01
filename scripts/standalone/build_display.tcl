# Continue the G1 project after its monitor simulation and BD validation.
foreach name {DISPLAY_OUT MONITOR_SIM_OUT} {
    if {![info exists ::env($name)]} {error "Missing environment variable $name"}
}
set out [file normalize $::env(DISPLAY_OUT)]
set tag display
if {[info exists ::env(DISPLAY_TAG)]} {set tag $::env(DISPLAY_TAG)}
if {![regexp {^[a-z][a-z0-9-]*$} $tag]} {error "Invalid DISPLAY_TAG"}
set jobs 2
if {[info exists ::env(DISPLAY_BUILD_JOBS)]} {set jobs $::env(DISPLAY_BUILD_JOBS)}
if {![regexp {^[0-9]+$} $jobs] || ![expr {$jobs > 0}]} {
    error "Invalid DISPLAY_BUILD_JOBS: expected a positive integer"
}
set jobs [expr {$jobs + 0}]
set max_threads ""
if {[info exists ::env(DISPLAY_MAX_THREADS)]} {
    set max_threads $::env(DISPLAY_MAX_THREADS)
    if {![regexp {^[0-9]+$} $max_threads] || ![expr {$max_threads > 0}]} {
        error "Invalid DISPLAY_MAX_THREADS: expected a positive integer"
    }
    set max_threads [expr {$max_threads + 0}]
}
set reports [file join $out reports-$tag]
set repo [file normalize [file join [file dirname [info script]] ../..]]
set sim_log [file join $::env(MONITOR_SIM_OUT) monitor_sim.log]
set fd [open $sim_log]; set log [read $fd]; close $fd
if {[string first VIDEO_MONITOR_PASS $log] < 0} {error "MONITOR_SIMULATION_NOT_PASSED"}
foreach path [list [file join $repo fpga experimental standalone video_frame_monitor.v] \
    [file join $repo fpga sim standalone tb_video_frame_monitor.sv]] {
    if {[file mtime $path] > [file mtime $sim_log]} {error "MONITOR_SOURCE_NEWER_THAN_SIMULATION:$path"}
}
if {![llength [get_projects -quiet]]} {open_project [file join $out project display.xpr]}
file mkdir $reports
file mkdir [file join $out export]
set cdc_file [file join $out monitor-cdc.xdc]
set fd [open $cdc_file w]
puts $fd {# Request/acknowledge synchronizers terminate these CDC paths.}
puts $fd {set_false_path -to [get_pins -hier -regexp {.*frame_monitor.*(request_sync1|acknowledge_sync1|axi_reset_sync1)_reg/D}]}
puts $fd {# Mailbox is held until the next handshake; data settles before ack is used.}
puts $fd {set_max_delay -datapath_only 10 -from [get_cells -hier -regexp {.*frame_monitor.*mailbox_.*_reg.*}] -to [get_cells -hier -regexp {.*frame_monitor.*snap_.*_reg.*}]}
close $fd
add_files -fileset constrs_1 $cdc_file
set_property USED_IN_SYNTHESIS false [get_files $cdc_file]
set clock_file [file join $repo scripts standalone display_timing.xdc]
add_files -fileset constrs_1 $clock_file
set_property USED_IN_SYNTHESIS false [get_files $clock_file]
set_property PROCESSING_ORDER LATE [get_files $clock_file]
if {[info exists ::env(OFFLOAD_CANDIDATE)] && $::env(OFFLOAD_CANDIDATE) eq "1"} {
    set offload_xdc [file join $repo scripts standalone offload_cdc.xdc]
    add_files -fileset constrs_1 $offload_xdc
    set_property USED_IN_SYNTHESIS false [get_files $offload_xdc]
    set_property PROCESSING_ORDER LATE [get_files $offload_xdc]
}
# The IP's generic XDC models SerialClk as generated from PixelClk inside
# rgb2dvi. This design disables that generator and supplies SerialClk from the
# external axi_dynclk MMCM, so retain the real design-level clock constraint
# and exclude only the incorrect generated IP clock definition.
set rgb2dvi_clock_xdc [file normalize [file join $out project display.gen sources_1 bd display ip display_rgb2dvi_0_0 src rgb2dvi_clocks.xdc]]
set rgb2dvi_clock_file [get_files -quiet -all $rgb2dvi_clock_xdc]
if {[llength $rgb2dvi_clock_file] != 1} {error "RGB2DVI_CLOCK_XDC_NOT_FOUND:$rgb2dvi_clock_xdc"}
set_property USED_IN_SYNTHESIS false $rgb2dvi_clock_file
set_property USED_IN_IMPLEMENTATION false $rgb2dvi_clock_file
if {$max_threads ne ""} {set_param general.maxThreads $max_threads}
launch_runs synth_1 -jobs $jobs
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "DISPLAY_SYNTH_FAILED"}
open_run synth_1
set button_meta_first [get_pins -quiet -hier -regexp {.*button_debounce.*button_meta_reg\[[0-3]\]/D}]
if {[llength $button_meta_first] != 4} {error "OFFLOAD_BUTTON_META_FIRST_STAGE_PIN_COUNT:[llength $button_meta_first]"}
if {[info exists ::env(OFFLOAD_CANDIDATE)] && $::env(OFFLOAD_CANDIDATE) eq "1"} {
    set switch_meta_first [get_pins -quiet -hier -regexp {.*switch_synchronizer.*switch_meta_reg\[[0-1]\]/D}]
    if {[llength $switch_meta_first] != 2} {
        error "OFFLOAD_SWITCH_META_FIRST_STAGE_PIN_COUNT:[llength $switch_meta_first]"
    }
}
close_design
launch_runs impl_1 -to_step write_bitstream -jobs $jobs
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%" ||
    ![string match {*write_bitstream Complete*} [get_property STATUS [get_runs impl_1]]]} {error "DISPLAY_IMPL_FAILED"}
open_run impl_1
if {[llength [get_pins -quiet display_i/hdmi_out/frontend/axi_dynclk/U0/Inst_mmcme2_drp/mmcm_adv_inst/CLKOUT0]] != 1 ||
    [llength [get_pins -quiet display_i/hdmi_out/frontend/rgb2dvi_0/U0/SerialClk]] != 1} {error "VIDEO_CLOCK_TOPOLOGY_CHANGED"}
if {[info exists ::env(OFFLOAD_CANDIDATE)] && $::env(OFFLOAD_CANDIDATE) eq "1"} {
    if {[llength [get_pins -hier -regexp {.*btns_gpio.*syncstages_ff_reg\[0\]\[[0-5]\]/D}]] != 6} {
        error "GPIO_INPUT_SYNCHRONIZERS_CHANGED"
    }
    if {[llength [get_pins -hier -regexp {.*switch_synchronizer.*switch_meta_reg\[[0-1]\]/D}]] != 2 ||
        [llength [get_pins -hier -regexp {.*switch_synchronizer.*switch_sync_reg\[[0-1]\]/D}]] != 2} {
        error "SWITCH_SYNCHRONIZER_TOPOLOGY_CHANGED"
    }
} elseif {[llength [get_pins -hier -regexp {.*btns_gpio.*syncstages_ff_reg\[0\]\[[0-3]\]/D}]] != 4} {
    error "BUTTON_SYNCHRONIZERS_CHANGED"
}
report_timing_summary -delay_type min_max -report_unconstrained -file [file join $reports timing.rpt]
report_utilization -file [file join $reports utilization.rpt]
report_drc -file [file join $reports drc.rpt]
report_methodology -file [file join $reports methodology.rpt]
report_cdc -details -file [file join $reports cdc.rpt]
report_bus_skew -file [file join $reports bus-skew.rpt]
report_exceptions -coverage -file [file join $reports exception-coverage.rpt]
set setup [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set hold [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
if {$setup eq "" || $hold eq "" || $setup < 0 || $hold < 0} {error "DISPLAY_TIMING_FAILED setup=$setup hold=$hold"}
foreach violation [get_drc_violations] {
    if {[get_property SEVERITY $violation] in {Error {Critical Warning}}} {error "DISPLAY_DRC_FAILED:$violation"}
}
foreach violation [get_methodology_violations] {
    if {[get_property SEVERITY $violation] in {Error {Critical Warning}}} {error "DISPLAY_METHODOLOGY_FAILED:$violation"}
}
file copy [file join $out project display.runs impl_1 display_wrapper.bit] [file join $out export ${tag}.bit]
file copy [file join $out project display.gen sources_1 bd display hw_handoff display.hwh] [file join $out export ${tag}.hwh]
puts "DISPLAY_BUILD_COMPLETE:CDC_review_and_board_acceptance_pending"
