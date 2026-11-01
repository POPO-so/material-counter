# Explicit stages: -stage synth OR -stage route. Neither writes a bitstream.
source -notrace [file join [file dirname [file normalize [info script]]] joint_common.tcl]
set options [dict create {*}$argv]
if {![dict exists $options -stage] || [dict get $options -stage] ni {synth route}} {error "JOINT_STAGE_REQUIRED:synth|route"}
set stage [dict get $options -stage]
joint::init $argv $stage
set cfg $joint::cfg
set out [dict get $cfg out]
if {![file isfile [file join $out joint-connected.tcldict]]} {error "JOINT_CONNECTION_READBACK_REQUIRED"}
joint::capture architecture_readback {joint::check_jpeg} {SUCCESS:joint_jpeg_readback}
set reports [file join $out reports $stage]
file mkdir $reports

proc joint::thread_hooks {stage} {
    variable cfg
    set hook [file join [dict get $cfg out] thread-limit-$stage.tcl]
    set ch [open $hook {WRONLY CREAT EXCL}]
    puts $ch {set_param general.maxThreads 2}
    puts $ch {if {[get_param general.maxThreads] != 2} {error "JOINT_CHILD_THREAD_LIMIT_READBACK_FAILED"}}
    close $ch
    set targets [expr {$stage eq "synth" ? [list synth_1 STEPS.SYNTH_DESIGN.TCL.PRE] :
        [list impl_1 STEPS.OPT_DESIGN.TCL.PRE impl_1 STEPS.PLACE_DESIGN.TCL.PRE impl_1 STEPS.ROUTE_DESIGN.TCL.PRE]}]
    foreach {name key} $targets {
        set run [get_runs $name]
        if {[lsearch -exact [list_property $run] $key] < 0} {error "JOINT_RUN_HOOK_UNAVAILABLE:$name:$key"}
        if {[get_property $key $run] ne ""} {error "JOINT_PRESERVE_EXISTING_RUN_HOOK:$name:$key"}
        set_property $key $hook $run
        if {[file normalize [get_property $key $run]] ne [file normalize $hook]} {error "JOINT_RUN_HOOK_READBACK_FAILED:$name:$key"}
    }
    return SUCCESS:joint_thread_hooks
}

proc joint::resource_gate {reports} {
    variable cfg
    set text [report_utilization -return_string]
    set ch [open [file join $reports utilization.rpt] {WRONLY CREAT EXCL}]
    puts $ch $text
    close $ch
    if {![regexp {\|\s*(?:Slice|CLB) LUTs\*?\s*\|\s*([0-9]+)} $text -> lut]} {error "JOINT_LUT_UTILIZATION_UNPARSED"}
    if {![regexp {\|\s*(?:Slice|CLB) Registers\s*\|\s*([0-9]+)} $text -> ff]} {error "JOINT_FF_UTILIZATION_UNPARSED"}
    set bram36 [llength [get_cells -hier -filter {REF_NAME =~ RAMB36*}]]
    set bram18 [llength [get_cells -hier -filter {REF_NAME =~ RAMB18*}]]
    set dsp [llength [get_cells -hier -filter {REF_NAME =~ DSP48*}]]
    set stats [get_cells -hier -filter {NAME =~ *stats_mem* && REF_NAME =~ RAMB*}]
    set rows [get_cells -hier -filter {NAME =~ *row_mem* && REF_NAME =~ RAMB*}]
    if {![llength $stats] || ![llength $rows]} {error "JOINT_REGION_BRAM_MAPPING_MISSING:stats=$stats rows=$rows"}
    foreach {key value} [list max_lut $lut max_ff $ff max_bram36 [expr {$bram36 + $bram18/2.0}] max_dsp $dsp] {
        if {$value > [dict get $cfg $key]} {error "JOINT_RESOURCE_LIMIT:$key:$value>[dict get $cfg $key]"}
    }
    return [dict create lut $lut ff $ff bram36 $bram36 bram18 $bram18 dsp $dsp region_stats_bram $stats region_rows_bram $rows]
}

if {$stage eq "synth"} {
    joint::capture child_thread_limits {joint::thread_hooks synth} {SUCCESS:joint_thread_hooks}
    joint::capture generate_bd {generate_target all [joint::one [get_files */display.bd] display.bd]}
    joint::capture synth {
        launch_runs synth_1 -jobs 1
        wait_on_run synth_1
        set run [get_runs synth_1]
        if {[get_property PROGRESS $run] ne "100%" || ![string match {*synth_design Complete*} [get_property STATUS $run]]} {
            error "JOINT_SYNTH_FAILED:[get_property STATUS $run]"
        }
        open_run synth_1
    }
    set resources [joint::resource_gate $reports]
    joint::capture reports {
        report_cdc -details -file [file join $reports cdc.rpt]
        report_drc -file [file join $reports drc.rpt]
        report_methodology -file [file join $reports methodology.rpt]
        report_clock_interaction -file [file join $reports clock-interaction.rpt]
        report_timing_summary -report_unconstrained -file [file join $reports timing.rpt]
        write_checkpoint [file join $out joint-synth.dcp]
    }
    puts [ipcfg::write_evidence [file join $out joint-synth.tcldict] [dict create status SYNTHESIZED_CDC_REVIEW_PENDING resources $resources reports $reports]]
    puts "JOINT_SYNTH_COMPLETE: route requires explicit Manager CDC review and HLS cosim PASS receipt"
} else {
    if {![file isfile [file join $out joint-synth.tcldict]] || ![dict exists $options -cdc-review]} {error "JOINT_SYNTH_AND_CDC_REVIEW_REQUIRED"}
    joint::capture route_gate {
        set result [ipcfg::python_script [dict get $cfg verifier] --verify-manifest [dict get $cfg manifest] \
            --route-review [dict get $options -cdc-review]]
        if {$result ne "JOINT_SOURCE_HASHES_OK\nJOINT_GREEN_GATES_OK\nJOINT_ROUTE_REVIEW_OK"} {error "JOINT_ROUTE_REVIEW_FAILED:$result"}
    }
    joint::capture child_thread_limits {joint::thread_hooks route} {SUCCESS:joint_thread_hooks}
    joint::capture route {
        launch_runs impl_1 -to_step route_design -jobs 1
        wait_on_run impl_1
        set run [get_runs impl_1]
        if {[get_property PROGRESS $run] ne "100%" || ![string match {*route_design Complete*} [get_property STATUS $run]]} {
            error "JOINT_ROUTE_FAILED:[get_property STATUS $run]"
        }
        open_run impl_1
    }
    set resources [joint::resource_gate $reports]
    joint::capture route_reports {
        set route_status [report_route_status -return_string]
        set route_ch [open [file join $reports route-status.rpt] {WRONLY CREAT EXCL}]
        puts $route_ch $route_status
        close $route_ch
        report_timing_summary -delay_type min_max -report_unconstrained -file [file join $reports timing.rpt]
        report_drc -file [file join $reports drc.rpt]
        report_methodology -file [file join $reports methodology.rpt]
        report_cdc -details -file [file join $reports cdc.rpt]
        report_bus_skew -file [file join $reports bus-skew.rpt]
    }
    foreach type {max min} {
        set path [joint::one [get_timing_paths -delay_type $type -max_paths 1] timing_$type]
        if {[get_property SLACK $path] < 0} {error "JOINT_TIMING_FAIL:$type:[get_property SLACK $path]"}
    }
    foreach violation [concat [get_drc_violations] [get_methodology_violations]] {
        if {[get_property SEVERITY $violation] in {Error {Critical Warning}}} {error "JOINT_ROUTE_VIOLATION:$violation"}
    }
    # Read the actual report, not an assumed current_design ROUTE_STATUS property.
    if {![regexp -nocase {# of unrouted nets[.\s:|]*([0-9]+)} $route_status -> unrouted] || $unrouted != 0} {
        error "JOINT_UNROUTED_OR_UNPARSED:see [file join $reports route-status.rpt]"
    }
    if {![regexp -nocase {# of nets with routing errors[.\s:|]*([0-9]+)} $route_status -> route_errors] || $route_errors != 0} {
        error "JOINT_ROUTING_ERRORS_OR_UNPARSED:see [file join $reports route-status.rpt]"
    }
    write_checkpoint [file join $out joint-routed.dcp]
    puts [ipcfg::write_evidence [file join $out joint-route.tcldict] [dict create status ROUTED_REVIEW_PENDING resources $resources reports $reports board_ready 0]]
    puts "JOINT_ROUTE_COMPLETE: no bitstream/deployment; final CDC/constraint review and board acceptance pending"
}
joint::verify_sources
joint::capture live_sources {joint::live_sources}
close_project
