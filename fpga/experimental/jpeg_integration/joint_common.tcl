# Shared helpers; this file alone neither opens a project nor starts a run.
namespace eval joint {variable cfg; variable evidence; variable sequence 0}

proc joint::one {objects label} {
    if {[llength $objects] != 1} {error "JOINT_EXPECTED_ONE:$label:$objects"}
    return [lindex $objects 0]
}

proc joint::read_dict {path} {
    set ch [open $path r]
    set value [read $ch]
    close $ch
    dict size $value
    return $value
}

proc joint::under {path root} {
    set path [string tolower [file normalize $path]]
    set root [string tolower [file normalize $root]]
    return [expr {[string first "${root}/" "${path}/"] == 0}]
}

proc joint::init {arguments stage} {
    variable cfg
    variable evidence
    if {[llength $arguments] % 2} {error "JOINT_ARGS: -spec <data.tcldict> -native-log <actual Vivado -log path> ?-cdc-review <receipt>?"}
    set options [dict create {*}$arguments]
    foreach key {-spec -native-log} {
        if {![dict exists $options $key]} {error "JOINT_REQUIRED_OPTION:$key"}
    }
    set cfg [joint::read_dict [dict get $options -spec]]
    if {[dict get $cfg schema] ne "jpeg_joint_spec_v1"} {error "JOINT_SPEC_SCHEMA"}
    set out [dict get $cfg out]
    set xpr [dict get $cfg xpr]
    if {![joint::under $xpr [file join $out snapshot]]} {error "JOINT_REFUSE_ORIGINAL_PROJECT:$xpr"}
    set evidence [file join $out evidence $stage]
    if {[file exists $evidence]} {error "JOINT_STAGE_ALREADY_ATTEMPTED:$evidence; use a fresh build snapshot"}
    file mkdir $evidence
    set_param general.maxThreads 2
    if {[get_param general.maxThreads] != 2} {error "JOINT_THREAD_LIMIT_READBACK_FAILED"}
    if {[llength [get_projects]]} {error "JOINT_FRESH_BATCH_PROCESS_REQUIRED"}
    open_project $xpr
    # The library creates a relative namespace before using ::ipcfg variables;
    # load it at global scope even though this initializer is in ::joint.
    uplevel #0 [list source -notrace [dict get $cfg ipcfg]]
    set ::ipcfg::native_log_path [file normalize [dict get $options -native-log]]
    if {![file isfile $::ipcfg::native_log_path]} {error "JOINT_ACTUAL_NATIVE_LOG_MISSING"}
    set ::env(IPCFG_PYTHON) [dict get $cfg python]
    set status [ipcfg::python_status]
    puts $status
    if {![string match {PYTHON:OK *} $status]} {error $status}
    puts [ipcfg::python_exec -c {import argparse,base64,hashlib,json,pathlib,re,sys,typing; print("PYTHON_IMPORTS:OK")}]
    joint::verify_sources
    joint::capture ip_repository [list joint::ensure_ip_repository [dict get $cfg hls_ip]] \
        {SUCCESS:ip_repository_ready}
    set bd [joint::one [get_files */display.bd] display.bd]
    open_bd_design $bd
    joint::live_sources
    if {$stage in {connect synth route}} {
        set result [ipcfg::python_script [dict get $cfg verifier] --verify-manifest [dict get $cfg manifest] --verify-gates]
        if {$result ne "JOINT_SOURCE_HASHES_OK\nJOINT_GREEN_GATES_OK"} {error "JOINT_GREEN_GATES_FAILED:$result"}
    }
    return $options
}

proc joint::ensure_ip_repository {repository} {
    set wanted [file normalize $repository]
    set existing [get_property ip_repo_paths [current_project]]
    foreach item $existing {
        if {[string equal -nocase [file normalize $item] $wanted]} {
            return SUCCESS:ip_repository_ready
        }
    }
    set_property ip_repo_paths [concat $existing [list $repository]] [current_project]
    update_ip_catalog
    return SUCCESS:ip_repository_ready
}

proc joint::live_sources {} {
    variable cfg
    variable evidence
    variable sequence
    incr sequence
    set listing [file join $evidence live-files-$sequence.txt]
    set ch [open $listing {WRONLY CREAT EXCL}]
    foreach file [get_files -all] {puts $ch [get_property NAME $file]}
    close $ch
    set repos [file join $evidence live-ip-repos-$sequence.txt]
    set ch [open $repos {WRONLY CREAT EXCL}]
    foreach repo [get_property ip_repo_paths [current_project]] {puts $ch $repo}
    close $ch
    set result [ipcfg::python_script [dict get $cfg verifier] --verify-manifest [dict get $cfg manifest] --live-files $listing --live-ip-repos $repos]
    if {$result ne "JOINT_SOURCE_HASHES_OK\nJOINT_LIVE_SOURCE_ASSOCIATIONS_OK"} {error "JOINT_LIVE_FILES_FAILED:$result"}
    if {[get_property TOP [get_filesets sources_1]] ne "display_wrapper"} {error "JOINT_TOP_CHANGED"}
    return $result
}

proc joint::verify_sources {} {
    variable cfg
    set result [ipcfg::python_script [dict get $cfg verifier] --verify-manifest [dict get $cfg manifest]]
    if {$result ne "JOINT_SOURCE_HASHES_OK"} {error "JOINT_HASH_CHECK_FAILED:$result"}
    puts $result
}

proc joint::capture {label script {expect {}}} {
    variable evidence
    variable sequence
    incr sequence
    set file [file join $evidence [format {%03d-%s.tcldict} $sequence $label]]
    set receipt [uplevel 1 [list ipcfg::capture $file $script $expect]]
    puts $receipt
    if {[dict get $receipt status] ne "OK"} {error "JOINT_CAPTURE_FAIL:$file"}
    return $receipt
}

# Capture the cumulative counts and native-log offset before save_bd_design.
# reset_msg_count does not clear get_msg_config's cumulative severity counts in
# Vivado 2026.1, so the save capture handles a counter reset using the same
# narrow native-log review used for Vivado runs.
proc joint::record_save_counter_baseline {} {
    variable evidence
    if {![info exists ::ipcfg::native_log_path] ||
        ![file isfile $::ipcfg::native_log_path]} {
        error "JOINT_SAVE_COUNTER_NATIVE_LOG_MISSING"
    }
    set counts [ipcfg::_message_counts]
    file stat $::ipcfg::native_log_path native_log_stat
    set receipt_path [file join $evidence save_bd-counter-baseline.tcldict]
    puts [ipcfg::write_evidence $receipt_path [dict create \
        reason {record pre-save cumulative counts and audit save output by native log range} \
        counts_before $counts \
        native_log $::ipcfg::native_log_path native_log_offset $native_log_stat(size) \
        counter_reset_command {not used; get_msg_config cumulative counts are not reset by reset_msg_count}]]
    return "JOINT_SAVE_COUNTER_BASELINE_RECORDED:native_log_offset=$native_log_stat(size)"
}

# ipcfg::capture returns a compact summary and intentionally omits raw/messages.
# The immutable .tcldict envelope retains the complete data needed for review.
proc joint::capture_payload {path summary} {
    set envelope [joint::read_dict $path]
    if {![dict exists $envelope schema_version] || [dict get $envelope schema_version] != 1 ||
        ![dict exists $envelope data]} {
        error "JOINT_CAPTURE_RECEIPT_SCHEMA_INVALID:$path"
    }
    set data [dict get $envelope data]
    if {[catch {dict size $data}]} {error "JOINT_CAPTURE_RECEIPT_DATA_INVALID:$path"}
    foreach key {status code evidence_path native_log_source native_log_offsets native_counts_before native_counts_after} {
        if {![dict exists $summary $key] || ![dict exists $data $key] ||
            [dict get $summary $key] ne [dict get $data $key]} {
            error "JOINT_CAPTURE_RECEIPT_SUMMARY_MISMATCH:$key:$path"
        }
    }
    if {[dict exists $summary capture_error] &&
        (![dict exists $data capture_error] || [dict get $summary capture_error] ne [dict get $data capture_error])} {
        error "JOINT_CAPTURE_RECEIPT_SUMMARY_MISMATCH:capture_error:$path"
    }
    return [dict merge $summary $data]
}

proc joint::read_binary_range {path start end} {
    set channel [open $path rb]
    try {
        fconfigure $channel -translation binary
        seek $channel $start
        return [read $channel [expr {$end - $start}]]
    } finally {close $channel}
}

# Long Vivado runs can reset cumulative WARNING counters when child runs are
# opened. Audit only the exact native-log byte span recorded by ipcfg; the
# immutable original capture remains FAIL and any CRITICAL WARNING is retained
# as an explicit Manager review requirement.
proc joint::audit_run_capture {receipt} {
    foreach key {status code raw expected capture_error warning_count critical_count error_count messages native_log native_log_source native_log_offsets native_counts_before native_counts_after} {
        if {![dict exists $receipt $key]} {error "JOINT_RUN_CAPTURE_EVIDENCE_MISSING:$key"}
    }
    if {[dict get $receipt status] ne "FAIL" || [dict get $receipt code] != 0} {
        error "JOINT_RUN_CAPTURE_NOT_COMPLETED_WITH_TCL_CODE_ZERO"
    }
    if {[dict get $receipt raw] ne [dict get $receipt expected]} {
        error "JOINT_RUN_CAPTURE_EXPECTED_RESULT_MISMATCH"
    }
    if {![regexp {^CAPTURE_FAIL:native message export incomplete or counters changed: WARNING emitted=(-?[0-9]+) exported=([0-9]+)$} [dict get $receipt capture_error] -> emitted exported]} {
        error "JOINT_RUN_CAPTURE_COUNTER_RESET_NOT_ALLOWLISTED"
    }
    set offsets [dict get $receipt native_log_offsets]
    if {[llength $offsets] != 2 ||
        ![string is integer -strict [lindex $offsets 0]] ||
        ![string is integer -strict [lindex $offsets 1]] ||
        [lindex $offsets 0] < 0 || [lindex $offsets 1] < [lindex $offsets 0]} {
        error "JOINT_RUN_CAPTURE_RANGE_MALFORMED"
    }
    lassign $offsets start end
    set source [file normalize [dict get $receipt native_log_source]]
    set capture [file normalize [dict get $receipt native_log]]
    if {![file isfile $source] || ![file isfile $capture]} {
        error "JOINT_RUN_CAPTURE_LOG_MISSING"
    }
    if {[info exists ::ipcfg::native_log_path] &&
        $source ne [file normalize $::ipcfg::native_log_path]} {
        error "JOINT_RUN_CAPTURE_NATIVE_LOG_MISMATCH"
    }
    file stat $source source_stat
    if {$end > $source_stat(size)} {error "JOINT_RUN_CAPTURE_RANGE_OUTSIDE_LOG"}
    set source_bytes [joint::read_binary_range $source $start $end]
    set capture_channel [open $capture rb]
    try {
        fconfigure $capture_channel -translation binary
        set capture_bytes [read $capture_channel]
    } finally {close $capture_channel}
    if {$capture_bytes ne $source_bytes} {error "JOINT_RUN_CAPTURE_SLICE_MISMATCH"}
    if {$end > $start &&
        (($start > 0 && [string index [joint::read_binary_range $source [expr {$start - 1}] $start] 0] ne "\n") ||
         [string index $source_bytes end] ne "\n")} {
        error "JOINT_RUN_CAPTURE_RANGE_SPLITS_LINE"
    }
    if {[catch {encoding convertfrom utf-8 $source_bytes} decoded]} {
        error "JOINT_RUN_CAPTURE_RANGE_NOT_UTF8"
    }
    set observed {}
    set counts [dict create WARNING 0 {CRITICAL WARNING} 0 ERROR 0]
    foreach line [split $decoded \n] {
        set line [string trimright $line \r]
        if {[regexp {^\s*(CRITICAL WARNING|WARNING|ERROR):} $line -> severity]} {
            lappend observed $line
            dict incr counts $severity
        }
    }
    if {$observed ne [dict get $receipt messages]} {
        error "JOINT_RUN_CAPTURE_MESSAGES_MISMATCH"
    }
    set before [dict get $receipt native_counts_before]
    set after [dict get $receipt native_counts_after]
    foreach severity {WARNING {CRITICAL WARNING} ERROR} {
        if {![dict exists $before $severity] || ![dict exists $after $severity] ||
            ![string is integer -strict [dict get $before $severity]] ||
            ![string is integer -strict [dict get $after $severity]]} {
            error "JOINT_RUN_CAPTURE_COUNTERS_MALFORMED:$severity"
        }
    }
    if {$emitted != [expr {[dict get $after WARNING] - [dict get $before WARNING]}] ||
        $exported != [dict get $receipt warning_count] ||
        $exported != [dict get $counts WARNING] ||
        [dict get $receipt critical_count] != [dict get $counts {CRITICAL WARNING}] ||
        [dict get $receipt error_count] != [dict get $counts ERROR] ||
        [expr {[dict get $after {CRITICAL WARNING}] - [dict get $before {CRITICAL WARNING}]}] != [dict get $counts {CRITICAL WARNING}] ||
        [expr {[dict get $after ERROR] - [dict get $before ERROR]}] != [dict get $counts ERROR]} {
        error "JOINT_RUN_CAPTURE_SEVERITY_COUNTS_MISMATCH"
    }
    if {[dict get $counts ERROR] != 0} {error "JOINT_RUN_CAPTURE_CONTAINS_ERROR"}
    set status [expr {[dict get $counts {CRITICAL WARNING}] ?
        "VERIFIED_FOR_REPORTING_REVIEW_REQUIRED" : "VERIFIED_FOR_REPORTING"}]
    return [dict create status $status warning_count [dict get $counts WARNING] \
        critical_count [dict get $counts {CRITICAL WARNING}] error_count 0 \
        expected [dict get $receipt expected] native_log_source $source \
        native_log_offsets $offsets]
}

proc joint::capture_run {label script expected_result} {
    variable evidence
    variable sequence
    variable cfg
    incr sequence
    set index [format {%03d} $sequence]
    set file [file join $evidence ${index}-${label}.tcldict]
    set summary [uplevel 1 [list ipcfg::capture $file $script $expected_result]]
    puts $summary
    set receipt [joint::capture_payload $file $summary]
    if {[dict get $receipt status] eq "OK"} {return $receipt}
    # ipcfg::capture records its expected marker after counter validation; a
    # documented counter reset can fail that check before the marker is stored.
    if {![dict exists $receipt expected]} {dict set receipt expected $expected_result}
    set audit [joint::audit_run_capture $receipt]
    set messages_file [file join $evidence ${index}-${label}-messages.txt]
    set channel [open $messages_file {WRONLY CREAT EXCL}]
    try {
        fconfigure $channel -encoding utf-8 -translation lf
        foreach message [dict get $receipt messages] {puts $channel $message}
    } finally {close $channel}
    set review_file [file join $evidence ${index}-${label}.supplement.json]
    set before [dict get $receipt native_counts_before]
    set after [dict get $receipt native_counts_after]
    set offsets [dict get $receipt native_log_offsets]
    set review_args [list --capture-receipt $file --scope run \
        --status [dict get $receipt status] --code [dict get $receipt code] \
        --capture-error [dict get $receipt capture_error] --raw-result [dict get $receipt raw] \
        --expected-result $expected_result \
        --warning-before [dict get $before WARNING] --warning-after [dict get $after WARNING] \
        --critical-before [dict get $before {CRITICAL WARNING}] --critical-after [dict get $after {CRITICAL WARNING}] \
        --error-before [dict get $before ERROR] --error-after [dict get $after ERROR] \
        --warning-count [dict get $receipt warning_count] \
        --critical-count [dict get $receipt critical_count] --error-count [dict get $receipt error_count] \
        --native-log [dict get $receipt native_log_source] --start [lindex $offsets 0] \
        --end [lindex $offsets 1] --messages-file $messages_file --out $review_file]
    set review [ipcfg::python_script [dict get $cfg capture_review] {*}$review_args]
    if {[dict get $audit status] eq "VERIFIED_FOR_REPORTING_REVIEW_REQUIRED"} {
        if {![string match {JOINT_CAPTURE_REVIEW_REQUIRED *} $review]} {
            error "JOINT_RUN_SUPPLEMENTAL_REVIEW_MISMATCH:$review_file:$review"
        }
    } elseif {![string match {JOINT_CAPTURE_REVIEW_PASS *} $review]} {
        error "JOINT_RUN_SUPPLEMENTAL_REVIEW_FAIL:$review_file:$review"
    }
    dict set receipt reporting_status [dict get $audit status]
    dict set receipt supplemental_review $review_file
    puts $review
    return $receipt
}

proc joint::capture_save_design {} {
    set receipt [joint::capture_run save_bd {
        save_bd_design
        update_compile_order -fileset sources_1
        set ::joint::save_result {SUCCESS:joint_bd_saved}
    } {SUCCESS:joint_bd_saved}]
    if {[dict exists $receipt reporting_status]} {
        set reporting_status [dict get $receipt reporting_status]
        if {$reporting_status eq "VERIFIED_FOR_REPORTING"} {return $receipt}
        if {$reporting_status eq "VERIFIED_FOR_REPORTING_REVIEW_REQUIRED"} {
            set review [expr {[dict exists $receipt supplemental_review] ?
                [dict get $receipt supplemental_review] : "missing"}]
            error "JOINT_SAVE_CRITICAL_WARNING_REVIEW_REQUIRED:$review"
        }
    } elseif {[dict exists $receipt status] && [dict get $receipt status] eq "OK"} {
        return $receipt
    }
    error "JOINT_SAVE_CAPTURE_NOT_VERIFIED"
}

proc joint::parse_hierarchical_resources {text top} {
    foreach line [split $text \n] {
        if {![regexp {^\s*\|([^|]+)\|\s*\(top\)\s*\|\s*([0-9,]+)\s*\|\s*([0-9,]+)\s*\|\s*([0-9,]+)\s*\|\s*([0-9,]+)\s*\|\s*([0-9,]+)\s*\|\s*([0-9,]+)\s*\|\s*([0-9,]+)\s*\|\s*([0-9,]+)\s*\|} $line -> instance lut logic lutram srl ff bram36 bram18 dsp]} {
            continue
        }
        if {[string trim $instance] ne $top} {continue}
        foreach name {lut logic lutram srl ff bram36 bram18 dsp} {
            set $name [string map {, {}} [set $name]]
        }
        return [dict create lut $lut logic_lut $logic lutram $lutram srl $srl \
            ff $ff bram36 $bram36 bram18 $bram18 dsp $dsp]
    }
    error "JOINT_HIERARCHICAL_RESOURCE_ROW_MISSING:$top"
}

proc joint::parse_route_status {text} {
    if {![regexp -nocase {# of routable nets[.\s:|]*([0-9]+)} $text -> routable]} {
        error JOINT_ROUTE_STATUS_ROUTABLE_NET_COUNT_MISSING
    }
    if {![regexp -nocase {# of fully routed nets[.\s:|]*([0-9]+)} $text -> fully_routed]} {
        error JOINT_ROUTE_STATUS_FULLY_ROUTED_NET_COUNT_MISSING
    }
    if {![regexp -nocase {# of nets with routing errors[.\s:|]*([0-9]+)} $text -> route_errors]} {
        error JOINT_ROUTE_STATUS_ROUTING_ERROR_COUNT_MISSING
    }
    foreach name {routable fully_routed route_errors} {
        set $name [string map {, {}} [set $name]]
    }
    if {$routable != $fully_routed} {
        error "JOINT_ROUTE_STATUS_UNROUTED_NETS:$fully_routed/$routable"
    }
    if {$route_errors != 0} {
        error "JOINT_ROUTE_STATUS_ROUTING_ERRORS:$route_errors"
    }
    return [dict create routable_nets $routable fully_routed_nets $fully_routed \
        routing_errors $route_errors]
}

# Only refresh_region may enter this narrow review path. The original ipcfg
# receipt remains FAIL and is never rewritten or treated as a general success.
proc joint::capture_refresh_region {script expected_result} {
    variable cfg
    variable evidence
    variable sequence
    incr sequence
    set file [file join $evidence [format {%03d-refresh_region.tcldict} $sequence]]
    set summary [uplevel 1 [list ipcfg::capture $file $script]]
    puts $summary
    set receipt [joint::capture_payload $file $summary]
    if {[dict get $receipt status] eq "OK"} {
        if {![dict exists $receipt raw] || ![joint::same_windows_path [dict get $receipt raw] $expected_result]} {
            error "JOINT_REFRESH_EXPECTED_RESULT_MISMATCH:$file"
        }
        return $receipt
    }
    if {[dict get $receipt status] ne "FAIL" || [dict get $receipt code] != 0} {
        error "JOINT_REFRESH_CAPTURE_FAIL:$file"
    }
    if {![dict exists $receipt capture_error] ||
        ![regexp {^CAPTURE_FAIL:native message export incomplete or counters changed: WARNING emitted=-?[0-9]+ exported=[0-9]+$} [dict get $receipt capture_error]]} {
        error "JOINT_REFRESH_CAPTURE_ERROR_NOT_ALLOWLISTED:$file"
    }
    foreach key {native_log_source native_log_offsets native_counts_before native_counts_after raw messages} {
        if {![dict exists $receipt $key]} {error "JOINT_REFRESH_CAPTURE_EVIDENCE_MISSING:$key:$file"}
    }
    set native_log [file normalize [dict get $receipt native_log_source]]
    if {$native_log ne [file normalize $::ipcfg::native_log_path]} {
        error "JOINT_REFRESH_CAPTURE_NATIVE_LOG_MISMATCH:$file"
    }
    set offsets [dict get $receipt native_log_offsets]
    if {[llength $offsets] != 2} {error "JOINT_REFRESH_CAPTURE_RANGE_MALFORMED:$file"}
    set before [dict get [dict get $receipt native_counts_before] WARNING]
    set after [dict get [dict get $receipt native_counts_after] WARNING]
    set review_file [file join $evidence [format {%03d-refresh_region.supplement.json} $sequence]]
    set review_args [list --capture-receipt $file --status [dict get $receipt status] \
        --code [dict get $receipt code] --capture-error [dict get $receipt capture_error] \
        --raw-result [dict get $receipt raw] --expected-result $expected_result \
        --warning-before $before --warning-after $after --native-log $native_log \
        --start [lindex $offsets 0] --end [lindex $offsets 1] --out $review_file]
    set warning_messages 0
    foreach message [dict get $receipt messages] {
        if {![string match {WARNING:*} $message]} {
            error "JOINT_REFRESH_CAPTURE_UNEXPECTED_EXPORTED_SEVERITY:$file"
        }
        lappend review_args --warning-message $message
        incr warning_messages
    }
    if {!$warning_messages} {error "JOINT_REFRESH_CAPTURE_NO_EXPORTED_WARNINGS:$file"}
    set review [ipcfg::python_script [dict get $cfg capture_review] {*}$review_args]
    if {![string match {JOINT_CAPTURE_REVIEW_PASS *} $review]} {
        error "JOINT_REFRESH_SUPPLEMENTAL_REVIEW_FAIL:$review_file:$review"
    }
    puts $review
    return $receipt
}

# The top-level synth run exports child-run messages to the native log while
# Vivado may reset its cumulative message counters. Review only that exact
# synth capture interval; the original FAIL receipt remains immutable.
proc joint::capture_synth {script} {
    variable cfg
    variable evidence
    variable sequence
    incr sequence
    set index [format {%03d} $sequence]
    set file [file join $evidence ${index}-synth.tcldict]
    set summary [uplevel 1 [list ipcfg::capture $file $script]]
    puts $summary
    set receipt [joint::capture_payload $file $summary]
    if {[dict get $receipt status] eq "OK"} {return $receipt}
    if {[dict get $receipt status] ne "FAIL" || [dict get $receipt code] != 0} {
        error "JOINT_SYNTH_CAPTURE_FAIL:$file"
    }
    if {![dict exists $receipt capture_error] ||
        ![regexp {^CAPTURE_FAIL:native message export incomplete or counters changed: WARNING emitted=-?[0-9]+ exported=[0-9]+$} [dict get $receipt capture_error]]} {
        error "JOINT_SYNTH_CAPTURE_ERROR_NOT_ALLOWLISTED:$file"
    }
    foreach key {native_log_source native_log_offsets native_counts_before native_counts_after raw messages warning_count critical_count error_count} {
        if {![dict exists $receipt $key]} {error "JOINT_SYNTH_CAPTURE_EVIDENCE_MISSING:$key:$file"}
    }
    set native_log [file normalize [dict get $receipt native_log_source]]
    if {$native_log ne [file normalize $::ipcfg::native_log_path]} {
        error "JOINT_SYNTH_CAPTURE_NATIVE_LOG_MISMATCH:$file"
    }
    set offsets [dict get $receipt native_log_offsets]
    if {[llength $offsets] != 2 ||
        ![string is integer -strict [lindex $offsets 0]] ||
        ![string is integer -strict [lindex $offsets 1]] ||
        [lindex $offsets 0] < 0 || [lindex $offsets 1] <= [lindex $offsets 0]} {
        error "JOINT_SYNTH_CAPTURE_RANGE_MALFORMED:$file"
    }
    set counts_before [dict get $receipt native_counts_before]
    set counts_after [dict get $receipt native_counts_after]
    foreach severity {WARNING {CRITICAL WARNING} ERROR} {
        if {![dict exists $counts_before $severity] || ![dict exists $counts_after $severity] ||
            ![string is integer -strict [dict get $counts_before $severity]] ||
            ![string is integer -strict [dict get $counts_after $severity]]} {
            error "JOINT_SYNTH_CAPTURE_COUNTERS_MALFORMED:$severity:$file"
        }
    }
    set messages_file [file join $evidence ${index}-synth-messages.txt]
    set channel [open $messages_file {WRONLY CREAT EXCL}]
    try {
        fconfigure $channel -encoding utf-8 -translation lf
        foreach message [dict get $receipt messages] {puts $channel $message}
    } finally {close $channel}
    set review_file [file join $evidence ${index}-synth.supplement.json]
    set review_args [list --capture-receipt $file --scope synth \
        --status [dict get $receipt status] --code [dict get $receipt code] \
        --capture-error [dict get $receipt capture_error] --raw-result [dict get $receipt raw] \
        --warning-before [dict get $counts_before WARNING] \
        --warning-after [dict get $counts_after WARNING] \
        --critical-before [dict get $counts_before {CRITICAL WARNING}] \
        --critical-after [dict get $counts_after {CRITICAL WARNING}] \
        --error-before [dict get $counts_before ERROR] \
        --error-after [dict get $counts_after ERROR] \
        --warning-count [dict get $receipt warning_count] \
        --critical-count [dict get $receipt critical_count] \
        --error-count [dict get $receipt error_count] \
        --native-log $native_log --start [lindex $offsets 0] --end [lindex $offsets 1] \
        --messages-file $messages_file --out $review_file]
    set review [ipcfg::python_script [dict get $cfg capture_review] {*}$review_args]
    if {![string match {JOINT_CAPTURE_REVIEW_PASS *} $review]} {
        error "JOINT_SYNTH_SUPPLEMENTAL_REVIEW_FAIL:$review_file:$review"
    }
    puts $review
    return "JOINT_SYNTH_CAPTURE_REVIEW_PASS:$review_file"
}

proc joint::same_windows_path {left right} {
    return [string equal -nocase [file normalize $left] [file normalize $right]]
}

proc joint::module_reference_xci_path {cell} {
    variable cfg
    if {$cell ni {display_image_pipeline_0 display_binary_overlay_0}} {
        error "JOINT_MODULE_REFERENCE_CELL_NOT_ALLOWLISTED:$cell"
    }
    set project_dir [file dirname [dict get $cfg xpr]]
    return [file normalize [file join $project_dir display.srcs sources_1 bd display ip $cell ${cell}.xci]]
}

proc joint::refresh_module_reference_checked {reference expected_result} {
    set actual_result [update_module_reference $reference]
    if {![joint::same_windows_path $actual_result $expected_result]} {
        error "JOINT_MODULE_REFERENCE_RESULT_MISMATCH:$actual_result"
    }
    return SUCCESS:module_reference_updated
}

proc joint::require_cfg {cell wanted reason} {
    set result [ipcfg::require $cell $wanted $reason]
    if {![string match {REQ:pinned+applied*} $result]} {error "JOINT_REQUIRE_FAIL:$cell:$result"}
    set bad [ipcfg::verify_stuck $cell $wanted]
    if {[llength $bad]} {error "JOINT_PARAMETER_READBACK_FAIL:$cell:$bad"}
    return $result
}

proc joint::number {text} {
    if {[regexp -nocase {^([0-9]+)([KMG])$} $text -> n unit]} {
        set power [dict get {K 1 M 2 G 3} [string toupper $unit]]
        return [expr {$n * (1024 ** $power)}]
    }
    return [expr {wide($text)}]
}

proc joint::addr_snapshot {} {
    set result [dict create]
    foreach segment [get_bd_addr_segs -of_objects [get_bd_addr_spaces ps7/Data]] {
        dict set result $segment [list [joint::number [get_property OFFSET $segment]] [joint::number [get_property RANGE $segment]]]
    }
    return $result
}

proc joint::intf_ends {pin} {
    set obj [joint::one [get_bd_intf_pins $pin] $pin]
    set net [joint::one [get_bd_intf_nets -of_objects $obj] "$pin net"]
    set endpoints {}
    foreach endpoint [get_bd_intf_pins -of_objects $net] {lappend endpoints [string trimleft $endpoint /]}
    return [lsort $endpoints]
}

proc joint::pin_ends {pin} {
    set obj [joint::one [get_bd_pins $pin] $pin]
    set net [joint::one [get_bd_nets -of_objects $obj] "$pin net"]
    set endpoints {}
    foreach endpoint [get_bd_pins -of_objects $net] {lappend endpoints [string trimleft $endpoint /]}
    return [lsort $endpoints]
}

proc joint::pin_width {pin} {
    set obj [joint::one [get_bd_pins $pin] $pin]
    set left [get_property LEFT $obj]
    set right [get_property RIGHT $obj]
    if {$left eq {} && $right eq {}} {return 1}
    if {![string is integer -strict $left] || ![string is integer -strict $right]} {
        error "JOINT_PIN_RANGE_INVALID:$pin:$left:$right"
    }
    return [expr {abs($left - $right) + 1}]
}

proc joint::check_axis_pin {source sink width label} {
    # Vivado represents AXI-Stream wiring as an interface net; it does not
    # create scalar BD nets for the mapped TDATA/TKEEP/TLAST pins.
    foreach pin [list $source $sink] {
        set actual_width [joint::pin_width $pin]
        if {$actual_width != $width} {error "JOINT_AXIS_PIN_WIDTH_WRONG:$label:$pin:$actual_width"}
    }
}

proc joint::preserved_snapshot {} {
    set result [dict create interface {} scalar {} clock_properties {} addresses [joint::addr_snapshot]]
    set interfaces {ps7/M_AXI_GP0 ps7/S_AXI_HP0 ps7/S_AXI_HP1 ps7/S_AXI_HP2 axi_vdma/M_AXIS_MM2S image_pipeline/m_axis}
    for {set i 0} {$i < 12} {incr i} {lappend interfaces [format {control/M%02d_AXI} $i]}
    foreach pin $interfaces {dict set result interface $pin [joint::intf_ends $pin]}
    foreach pin {
        ps7/FCLK_CLK0 ps7/FCLK_CLK1 ps7/FCLK_CLK2 reset_rle/peripheral_aresetn
        hdmi_out/PXL_CLK_O binary_overlay/pixel_resetn
        binary_overlay/out_data24 binary_overlay/out_active binary_overlay/out_vsync binary_overlay/out_hsync
    } {dict set result scalar $pin [joint::pin_ends $pin]}
    foreach key {PCW_FPGA0_PERIPHERAL_FREQMHZ PCW_FPGA1_PERIPHERAL_FREQMHZ PCW_FPGA2_PERIPHERAL_FREQMHZ} {
        dict set result clock_properties $key [get_property CONFIG.$key [get_bd_cells ps7]]
    }
    return $result
}

proc joint::check_preserved {before} {
    set after [joint::preserved_snapshot]
    foreach field {interface clock_properties} {
        dict for {key wanted} [dict get $before $field] {
            if {[dict get $after $field $key] ne $wanted} {error "JOINT_BASELINE_CHANGED:$field:$key"}
        }
    }
    dict for {pin endpoints} [dict get $before scalar] {
        foreach endpoint $endpoints {
            if {[lsearch -exact [dict get $after scalar $pin] $endpoint] < 0} {error "JOINT_BASELINE_SCALAR_DETACHED:$pin:$endpoint"}
        }
    }
    dict for {segment mapping} [dict get $before addresses] {
        if {![dict exists $after addresses $segment] || [dict get $after addresses $segment] ne $mapping} {
            error "JOINT_BASELINE_ADDRESS_CHANGED:$segment"
        }
    }
    return $after
}

proc joint::check_jpeg {} {
    variable cfg
    set cell jpeg_axis_gray_0
    foreach {component key wanted} {control CONFIG.NUM_MI 13 axi_dma_0 CONFIG.c_sg_length_width 19 axi_dma_0 CONFIG.c_include_mm2s_dre 0 axi_dma_0 CONFIG.c_include_s2mm_dre 0 axi_dma_0 CONFIG.c_m_axis_mm2s_tdata_width 32} {
        if {[get_property $key [get_bd_cells $component]] ne $wanted} {error "JOINT_CONFIG_CHANGED:$component:$key"}
    }
    foreach {source sink} [list axi_dma_0/M_AXIS_MM2S $cell/[dict get $cfg input_intf] $cell/[dict get $cfg output_intf] image_pipeline/s_axis control/M12_AXI $cell/[dict get $cfg control_intf]] {
        if {[lsearch -exact [joint::intf_ends $source] $sink] < 0} {error "JOINT_INTF_NOT_CONNECTED:$source:$sink"}
    }
    foreach pin [list control/M12_ACLK $cell/[dict get $cfg clock_pin] image_pipeline/aclk] {
        if {[lsearch -exact [joint::pin_ends ps7/FCLK_CLK0] $pin] < 0} {error "JOINT_NOT_ON_FCLK0:$pin"}
    }
    foreach pin [list control/M12_ARESETN $cell/[dict get $cfg reset_pin]] {
        if {[lsearch -exact [joint::pin_ends reset_rle/peripheral_aresetn] $pin] < 0} {error "JOINT_RESET_MISMATCH:$pin"}
    }
    foreach intf [list [dict get $cfg input_intf] [dict get $cfg output_intf]] {
        if {$intf eq [dict get $cfg input_intf]} {
            set peer axi_dma_0/M_AXIS_MM2S
            set peer_pin_prefix axi_dma_0/M_AXIS_MM2S_
        } else {
            set peer image_pipeline/s_axis
            set peer_pin_prefix image_pipeline/s_axis_
        }
        foreach {signal width} {TDATA 32 TKEEP 4 TLAST 1 TVALID 1 TREADY 1} {
            set jpeg_pin $cell/${intf}_${signal}
            if {$peer eq "image_pipeline/s_axis"} {
                set physical_signal [string tolower $signal]
            } else {
                set physical_signal $signal
            }
            set peer_pin ${peer_pin_prefix}${physical_signal}
            joint::check_axis_pin $jpeg_pin $peer_pin $width "${intf}_${signal}"
        }
    }
    set target_segment [joint::one \
        [get_bd_addr_segs $cell/[dict get $cfg control_intf]/*] jpeg_target_segment]
    set mapped_segments [get_bd_addr_segs -of_objects $target_segment]
    set mapped_segment [joint::one $mapped_segments jpeg_mapped_segment]
    set addresses [joint::addr_snapshot]
    if {![dict exists $addresses $mapped_segment]} {
        error "JOINT_JPEG_MAPPED_SEGMENT_MISSING:$mapped_segment"
    }
    set mapping [dict get $addresses $mapped_segment]
    lassign $mapping mapped_base mapped_range
    if {$mapped_base != 0x43C80000 || $mapped_range != 0x10000} {
        error "JOINT_JPEG_ADDRESS_WRONG:$mapped_segment:$mapping"
    }
    set matches 0
    dict for {segment mapping} $addresses {
        lassign $mapping base range
        if {$base == 0x43C80000} {
            if {$range != 0x10000 || $segment ne $mapped_segment} {
                error "JOINT_JPEG_ADDRESS_WRONG:$segment:$mapping"
            }
            incr matches
        }
    }
    if {$matches != 1} {error "JOINT_EXPECTED_ONE_JPEG_ADDRESS_SEGMENT"}
    foreach {segment mapping} $addresses {
        lassign $mapping base range
        if {$range <= 0} {error "JOINT_ADDRESS_RANGE_NOT_POSITIVE:$segment:$mapping"}
        set end [expr {$base + $range}]
        dict for {other other_mapping} $addresses {
            if {[string compare $segment $other] >= 0} {continue}
            lassign $other_mapping other_base other_range
            if {$other_range <= 0} {error "JOINT_ADDRESS_RANGE_NOT_POSITIVE:$other:$other_mapping"}
            set other_end [expr {$other_base + $other_range}]
            if {$base < $other_end && $other_base < $end} {
                error "JOINT_ADDRESS_SEGMENTS_OVERLAP:$segment:$other"
            }
        }
    }
    return SUCCESS:joint_jpeg_readback
}
