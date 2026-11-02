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

proc joint::parse_device_resources {text} {
    set totals [dict create]
    foreach line [split $text \n] {
        if {[regexp {^\s*\|\s*Slice LUTs\*?\s*\|\s*([0-9,]+)\s*\|} $line -> value]} {
            dict set totals lut [string map {, {}} $value]
        } elseif {[regexp {^\s*\|\s*Slice Registers\s*\|\s*([0-9,]+)\s*\|} $line -> value]} {
            dict set totals ff [string map {, {}} $value]
        } elseif {[regexp {^\s*\|\s*Block RAM Tile\s*\|\s*([0-9,.]+)\s*\|} $line -> value]} {
            dict set totals bram36 [string map {, {}} $value]
        } elseif {[regexp {^\s*\|\s*DSPs\s*\|\s*([0-9,]+)\s*\|} $line -> value]} {
            dict set totals dsp [string map {, {}} $value]
        }
    }
    foreach key {lut ff bram36 dsp} {
        if {![dict exists $totals $key]} {error "JOINT_DEVICE_RESOURCE_ROW_MISSING:$key"}
    }
    return $totals
}

proc joint::write_report_text {path text} {
    set ch [open $path {WRONLY CREAT EXCL}]
    puts $ch $text
    close $ch
}

proc joint::resource_gate {reports stage} {
    variable cfg
    # Use the device-level report for capacity checks. The hierarchy report's
    # logical LUT estimate can exceed the mapped Slice LUT count before opt/place.
    set text [report_utilization -return_string]
    joint::write_report_text [file join $reports utilization.rpt] $text
    set hierarchy [report_utilization -hierarchical -return_string]
    joint::write_report_text [file join $reports utilization-hierarchical.rpt] $hierarchy
    set totals [joint::parse_device_resources $text]
    set lut [dict get $totals lut]
    set ff [dict get $totals ff]
    set bram36 [dict get $totals bram36]
    set dsp [dict get $totals dsp]
    set region_core [get_cells -hier -filter {REF_NAME =~ *rle_region_features}]
    if {[llength $region_core] != 1} {error "JOINT_REGION_CORE_MISSING_OR_DUPLICATED:$region_core"}
    set lut_limit [dict get $cfg max_lut]
    if {$stage eq "synth"} {
        # Vivado warns that final LUT usage is commonly lower after physical
        # optimization. Allow only a small recorded synthesis-estimate margin;
        # the routed device-level count remains strictly capped.
        set lut_limit [expr {$lut_limit + $lut_limit / 20}]
    }
    foreach {key value limit} [list max_lut $lut $lut_limit max_ff $ff [dict get $cfg max_ff] \
            max_bram36 $bram36 [dict get $cfg max_bram36] max_dsp $dsp [dict get $cfg max_dsp]] {
        if {$value > $limit} {error "JOINT_RESOURCE_LIMIT:$stage:$key:$value>$limit"}
    }
    return [dict merge $totals [dict create region_core $region_core lut_gate_limit $lut_limit
        lut_estimate_over_device [expr {$stage eq "synth" && $lut > [dict get $cfg max_lut]}]]]
}

proc joint::disable_duplicate_rgb2dvi_clock_xdc {} {
    set matches [get_files -all -quiet *rgb2dvi_clocks.xdc]
    if {[llength $matches] != 1} {
        error "JOINT_RGB2DVI_CLOCK_XDC_COUNT:[llength $matches]"
    }
    set xdc [lindex $matches 0]
    set_property USED_IN_SYNTHESIS false $xdc
    set_property USED_IN_IMPLEMENTATION false $xdc
    if {[get_property USED_IN_SYNTHESIS $xdc] || [get_property USED_IN_IMPLEMENTATION $xdc]} {
        error "JOINT_RGB2DVI_CLOCK_XDC_STILL_ENABLED:$xdc"
    }
    return SUCCESS:joint_duplicate_rgb2dvi_clock_xdc_disabled
}

if {$stage eq "synth"} {
    joint::capture child_thread_limits {joint::thread_hooks synth} {SUCCESS:joint_thread_hooks}
    joint::capture generate_bd {generate_target all [joint::one [get_files */display.bd] display.bd]}
    joint::capture duplicate_rgb2dvi_clock_xdc {joint::disable_duplicate_rgb2dvi_clock_xdc} \
        {SUCCESS:joint_duplicate_rgb2dvi_clock_xdc_disabled}
    set synth_capture [joint::capture_run synth_run {
        launch_runs synth_1 -jobs 1
        wait_on_run synth_1
        set run [get_runs synth_1]
        if {[get_property PROGRESS $run] ne "100%" || ![string match {*synth_design Complete*} [get_property STATUS $run]]} {
            error "JOINT_SYNTH_FAILED:[get_property STATUS $run]"
        }
        open_run synth_1
        set run_capture_marker JOINT_SYNTH_RUN_COMPLETE
    } JOINT_SYNTH_RUN_COMPLETE]
    set resources [joint::resource_gate $reports synth]
    joint::capture reports {
        report_cdc -details -file [file join $reports cdc.rpt]
        report_drc -file [file join $reports drc.rpt]
        report_methodology -file [file join $reports methodology.rpt]
        report_clock_interaction -file [file join $reports clock-interaction.rpt]
        report_timing_summary -report_unconstrained -file [file join $reports timing.rpt]
        write_checkpoint [file join $out joint-synth.dcp]
    }
    set synth_status SYNTHESIZED_CDC_REVIEW_PENDING
    if {[dict exists $synth_capture reporting_status] &&
        [dict get $synth_capture reporting_status] eq "VERIFIED_FOR_REPORTING_REVIEW_REQUIRED"} {
        set synth_status SYNTHESIZED_REPORTS_READY_CRITICAL_WARNING_REVIEW_REQUIRED
    }
    puts [ipcfg::write_evidence [file join $out joint-synth.tcldict] [dict create \
        status $synth_status run_capture [dict remove $synth_capture raw options output messages capture_options] \
        resources $resources reports $reports board_ready 0]]
    puts "JOINT_SYNTH_COMPLETE: route requires explicit Manager CDC review and HLS cosim PASS receipt"
} else {
    if {![file isfile [file join $out joint-synth.tcldict]] || ![dict exists $options -cdc-review]} {error "JOINT_SYNTH_AND_CDC_REVIEW_REQUIRED"}
    joint::capture route_gate {
        set result [ipcfg::python_script [dict get $cfg verifier] --verify-manifest [dict get $cfg manifest] \
            --route-review [dict get $options -cdc-review]]
        if {$result ne "JOINT_SOURCE_HASHES_OK\nJOINT_GREEN_GATES_OK\nJOINT_ROUTE_REVIEW_OK"} {error "JOINT_ROUTE_REVIEW_FAILED:$result"}
    }
    joint::capture child_thread_limits {joint::thread_hooks route} {SUCCESS:joint_thread_hooks}
    joint::capture duplicate_rgb2dvi_clock_xdc {joint::disable_duplicate_rgb2dvi_clock_xdc} \
        {SUCCESS:joint_duplicate_rgb2dvi_clock_xdc_disabled}
    set route_capture [joint::capture_run route_run {
        launch_runs impl_1 -to_step route_design -jobs 1
        wait_on_run impl_1
        set run [get_runs impl_1]
        if {[get_property PROGRESS $run] ne "100%" || ![string match {*route_design Complete*} [get_property STATUS $run]]} {
            error "JOINT_ROUTE_FAILED:[get_property STATUS $run]"
        }
        open_run impl_1
        set run_capture_marker JOINT_ROUTE_RUN_COMPLETE
    } JOINT_ROUTE_RUN_COMPLETE]
    set resources [joint::resource_gate $reports route]
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
    set timing_slacks [dict create]
    foreach type {max min} {
        set path [joint::one [get_timing_paths -delay_type $type -max_paths 1] timing_$type]
        dict set timing_slacks $type [get_property SLACK $path]
        if {[dict get $timing_slacks $type] < 0} {
            error "JOINT_TIMING_FAIL:$type:[dict get $timing_slacks $type]"
        }
    }
    foreach violation [concat [get_drc_violations] [get_methodology_violations]] {
        if {[get_property SEVERITY $violation] in {Error {Critical Warning}}} {
            error "JOINT_ROUTE_VIOLATION:$violation"
        }
    }
    # Vivado reports routable and fully routed totals, not a separate
    # '# of unrouted nets' line.
    set route_counts [joint::parse_route_status $route_status]
    write_checkpoint [file join $out joint-routed.dcp]
    puts [ipcfg::write_evidence [file join $out joint-route.tcldict] [dict create \
        status ROUTED_REVIEW_PENDING route_run_capture [dict remove $route_capture raw options output messages capture_options] \
        resources $resources reports $reports route_counts $route_counts \
        wns [dict get $timing_slacks max] whs [dict get $timing_slacks min] board_ready 0]]
    puts "JOINT_ROUTE_COMPLETE: no bitstream/deployment; final CDC/constraint review and board acceptance pending"
}
joint::verify_sources
joint::capture live_sources {joint::live_sources}
close_project
