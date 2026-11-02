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
    # The vendor rgb2dvi clock XDC assumes SerialClk is generated inside the IP.
    # This design supplies it from axi_dynclk, so the vendor definition emits
    # Timing 38-249 (no logical path from PixelClk). Disable only that
    # in-context file; keep the candidate display_timing.xdc active and read
    # both properties back after BD regeneration.
    joint::capture candidate_clock_constraint {
        set vendor [get_files -all -filter {NAME =~ *rgb2dvi_clocks.xdc}]
        if {[llength $vendor] != 1} {error "RGB2DVI_CLOCKS_XDC_COUNT:[llength $vendor]"}
        set_property IS_ENABLED false [lindex $vendor 0]
        if {[get_property IS_ENABLED [lindex $vendor 0]] != 0} {error "RGB2DVI_CLOCKS_XDC_STILL_ENABLED"}
        set timing [get_files -all -filter {NAME =~ *scripts/standalone/display_timing.xdc}]
        if {[llength $timing] != 1} {error "DISPLAY_TIMING_XDC_COUNT:[llength $timing]"}
        # Keep this file on the post-synthesis path: applied during synthesis it
        # evaluates while the BD is still a black box, fails to resolve the
        # SerialClk pin, and the deferred clock is absent from the synth DCP.
        if {[get_property IS_ENABLED [lindex $timing 0]] != 1 ||
            [get_property USED_IN_IMPLEMENTATION [lindex $timing 0]] != 1 ||
            [get_property USED_IN_SYNTHESIS [lindex $timing 0]] != 0} {
            error "DISPLAY_TIMING_XDC_NOT_POST_SYNTH_ACTIVE"
        }
        puts "CANDIDATE_CLOCK_CONSTRAINT_OK vendor_disabled=[lindex $vendor 0] timing_post_synth=[lindex $timing 0]"
    }
    set synth_receipt [joint::capture_synth synth {
        launch_runs synth_1 -jobs 1
        wait_on_run synth_1
        set run [get_runs synth_1]
        if {[get_property PROGRESS $run] ne "100%" || ![string match {*synth_design Complete*} [get_property STATUS $run]]} {
            error "JOINT_SYNTH_FAILED:[get_property STATUS $run]"
        }
        open_run synth_1
    }]
    joint::capture clock_readback {
        set serial [get_clocks -quiet *rgb2dvi_0/U0/SerialClk]
        if {[llength $serial] != 1} {error "SERIALCLK_CLOCK_COUNT:[llength $serial]"}
        set clk [lindex $serial 0]
        if {[get_property PERIOD $clk] != 2.000} {error "SERIALCLK_PERIOD:[get_property PERIOD $clk]"}
        if {[get_property MASTER_CLOCK $clk] ne "I"} {error "SERIALCLK_MASTER:[get_property MASTER_CLOCK $clk]"}
        if {[get_property SOURCE $clk] ne "display_i/hdmi_out/frontend/axi_dynclk/U0/Inst_mmcme2_drp/mmcm_adv_inst/CLKOUT0"} {
            error "SERIALCLK_SOURCE:[get_property SOURCE $clk]"
        }
        puts "SERIALCLK_READBACK period=2.000 master=I source=[get_property SOURCE $clk]"
    }
    joint::capture mailbox_readback {
        set dst [get_cells -hier -regexp {.*binary_overlay.*/(enable_p|id_p|count_p|boxes_p|box_valid_p|line_p|line_valid_p)_reg(\[[0-9]+\])?$}]
        if {[llength $dst] != 705} {error "MAILBOX_DST_COUNT:[llength $dst]"}
        foreach {group expected} {enable_p 1 id_p 32 count_p 5 boxes_p 640 box_valid_p 16 line_p 10 line_valid_p 1} {
            set cells [get_cells -hier -regexp ".*binary_overlay.*/${group}_reg(\[[0-9]+\])?$"]
            if {[llength $cells] != $expected} {error "MAILBOX_GROUP:$group:[llength $cells]!=$expected"}
        }
        set new [get_cells -hier -regexp {.*binary_overlay.*/(box_valid_p|line_valid_p)_reg(\[[0-9]+\])?$}]
        if {[llength $new] != 17} {error "MAILBOX_NEW_COUNT:[llength $new]"}
        set publish_src [get_cells -hier -regexp {.*binary_overlay.*publish_(id|bank)_reg.*$}]
        set publish_dst [get_cells -hier -regexp {.*binary_overlay.*/(displayed_frame_id|display_bank)_reg(\[[0-9]+\])?$}]
        if {[llength $publish_src] != 33 || [llength $publish_dst] != 33} {
            error "PUBLISH_ENDPOINT_COUNT:src=[llength $publish_src]:dst=[llength $publish_dst]"
        }
        set min_slack 999.0
        foreach c $new {
            set d [get_pins -of_objects $c -filter {NAME =~ *D}]
            if {[llength $d] != 1} {error "MAILBOX_NEW_D_PIN:[get_property NAME $c]"}
            set p [lindex [get_timing_paths -to [lindex $d 0] -max_paths 1 -quiet] 0]
            if {![llength $p]} {error "MAILBOX_NEW_NO_PATH:[get_property NAME $c]"}
            if {![string match {MaxDelay Path 10.000ns*} [get_property EXCEPTION $p]]} {
                error "MAILBOX_NEW_EXCEPTION:[get_property NAME $c]:[get_property EXCEPTION $p]"
            }
            set s [get_property SLACK $p]
            if {$s < $min_slack} {set min_slack $s}
        }
        puts "MAILBOX_READBACK dst=705 id_p=32 new=17 min_slack=$min_slack"
    }
    joint::capture synth_log_clean {
        if {![dict exists $synth_receipt native_log]} {error "SYNTH_NATIVE_LOG_MISSING"}
        set slice [dict get $synth_receipt native_log]
        if {![file isfile $slice]} {error "SYNTH_NATIVE_SLICE_MISSING:$slice"}
        set ch [open $slice r]; set text [read $ch]; close $ch
        if {[string first "38-249" $text] >= 0} {error "SERIALCLK_38_249_STILL_PRESENT"}
        if {[regexp -nocase -line {^\s*CRITICAL[ _]WARNING\s*:} $text]} {error "SYNTH_CRITICAL_WARNING_PRESENT"}
    }
    set resources [joint::resource_gate $reports]
    joint::capture reports {
        report_cdc -details -file [file join $reports cdc.rpt]
        report_drc -file [file join $reports drc.rpt]
        report_methodology -file [file join $reports methodology.rpt]
        report_clock_interaction -file [file join $reports clock-interaction.rpt]
        report_timing_summary -report_unconstrained -file [file join $reports timing.rpt]
        report_exceptions -file [file join $reports exceptions.rpt]
        report_clocks -file [file join $reports clocks.rpt]
        write_checkpoint [file join $out joint-synth.dcp]
    }
    set report_sha [dict create]
    foreach name {utilization.rpt cdc.rpt clock-interaction.rpt timing.rpt drc.rpt methodology.rpt exceptions.rpt clocks.rpt} {
        dict set report_sha $name [joint::file_sha256 [file join $reports $name]]
    }
    set mailbox_xdc [file join [dict get $cfg source_root] constraints candidate_offload_cdc.xdc]
    set capture_receipt [dict get $synth_receipt evidence_path]
    set capture_supplement ""
    set capture_supplement_sha ""
    if {[dict exists $synth_receipt supplement_review]} {
        set capture_supplement [dict get $synth_receipt supplement_review]
        set capture_supplement_sha [joint::file_sha256 $capture_supplement]
    }
    set synth_bind [dict create status SYNTHESIZED_CDC_REVIEW_PENDING \
        resources $resources reports $reports \
        candidate_mailbox_xdc $mailbox_xdc \
        candidate_mailbox_xdc_sha256 [joint::file_sha256 $mailbox_xdc] \
        capture_receipt $capture_receipt \
        capture_supplement $capture_supplement \
        capture_supplement_sha256 $capture_supplement_sha \
        serialclk [dict create period_ns 2.000 master I source display_i/hdmi_out/frontend/axi_dynclk/U0/Inst_mmcme2_drp/mmcm_adv_inst/CLKOUT0] \
        mailbox [dict create dst 705 id_p 32 box_valid_p 16 line_valid_p 1 new_total 17] \
        report_sha256 $report_sha]
    puts [ipcfg::write_evidence [file join $out joint-synth.tcldict] $synth_bind]
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
