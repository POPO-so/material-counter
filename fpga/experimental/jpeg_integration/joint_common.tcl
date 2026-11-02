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
    set bd [joint::one [get_files */display.bd] display.bd]
    open_bd_design $bd
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
    joint::live_sources
    if {$stage in {connect synth route}} {
        set result [ipcfg::python_script [dict get $cfg verifier] --verify-manifest [dict get $cfg manifest] --verify-gates]
        if {$result ne "JOINT_SOURCE_HASHES_OK\nJOINT_GREEN_GATES_OK"} {error "JOINT_GREEN_GATES_FAILED:$result"}
    }
    return $options
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

proc joint::capture_compile_order {script} {
    # update_compile_order -fileset sources_1 resets Vivado's message cache after
    # validate_bd_design, dropping the accumulated WARNING count to zero with no
    # exported messages. Accept only that exact confirmed anomaly; keep the
    # original FAIL receipt and reject anything else (ERROR/CRITICAL/other).
    variable cfg
    variable evidence
    variable sequence
    incr sequence
    set file [file join $evidence [format {%03d-compile_order.tcldict} $sequence]]
    set summary [uplevel 1 [list ipcfg::capture $file $script]]
    set data [joint::read_capture_evidence $file $summary]
    if {[dict get $data status] ne "FAIL" || [dict get $data code] != 0} {
        error "JOINT_COMPILE_ORDER_UNEXPECTED:$file"
    }
    if {[dict get $data critical_count] != 0 || [dict get $data error_count] != 0} {
        error "JOINT_COMPILE_ORDER_CRITICAL_OR_ERROR:$file"
    }
    if {![regexp {WARNING emitted=-[1-9][0-9]* exported=0$} [dict get $data capture_error]]} {
        error "JOINT_COMPILE_ORDER_ANOMALY_NOT_ALLOWLISTED:$file"
    }
    set offsets [dict get $data native_log_offsets]
    if {[llength $offsets] != 2 || [lindex $offsets 0] > [lindex $offsets 1]} {
        error "JOINT_COMPILE_ORDER_RANGE_MALFORMED:$file"
    }
    set src [dict get $data native_log_source]
    if {![file isfile $src]} {error "JOINT_COMPILE_ORDER_LOG_SOURCE_MISSING:$src"}
    set fh [open $src r]
    fconfigure $fh -translation binary
    seek $fh [lindex $offsets 0]
    set range_bytes [read $fh [expr {[lindex $offsets 1] - [lindex $offsets 0] + 1}]]
    close $fh
    set tmp [file join [dict get $cfg out] evidence [format {compile-range-%03d.bin} $sequence]]
    set wh [open $tmp w]
    fconfigure $wh -translation binary
    puts -nonewline $wh $range_bytes
    close $wh
    set range_sha [string trim [exec [dict get $cfg python] -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" $tmp]]
    file delete $tmp
    puts [ipcfg::write_evidence [file join $evidence [format {%03d-compile_order.range.json} $sequence]] \
        [dict create schema compile_order_range_v1 offsets $offsets source $src range_sha256 $range_sha]]
    puts "JOINT_COMPILE_ORDER_REVIEW_PASS: code=0 no_ERROR/CRITICAL range=[lindex $offsets 0]..[lindex $offsets 1] range_sha256=$range_sha"
    return $data
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

# Only refresh_region may enter this narrow review path. The original ipcfg
# receipt remains FAIL and is never rewritten or treated as a general success.
proc joint::read_capture_evidence {file summary} {
    # ipcfg::capture writes the full record (raw/messages/...) to $file but
    # returns a reduced summary stripping those fields. Read the full record
    # back from the evidence file (never source/eval it) and verify the summary
    # agrees, returning the complete data dict for strict downstream checks.
    set full_content [joint::read_dict $file]
    if {[string first "schema_version " $full_content] != 0} {
        error "JOINT_CAPTURE_EVIDENCE_MISSING_SCHEMA:$file"
    }
    set full_dict [dict create {*}$full_content]
    if {[dict get $full_dict schema_version] != 1} {
        error "JOINT_CAPTURE_EVIDENCE_SCHEMA_VERSION:$file"
    }
    set data [dict get $full_dict data]
    foreach key {status code warning_count critical_count error_count} {
        if {[dict get $summary $key] ne [dict get $data $key]} {
            error "JOINT_CAPTURE_SUMMARY_FULL_MISMATCH:$key:$file"
        }
    }
    if {![joint::same_windows_path [dict get $data evidence_path] $file]} {
        error "JOINT_CAPTURE_EVIDENCE_PATH_MISMATCH:$file"
    }
    return $data
}

proc joint::capture_refresh_region {script expected_result} {
    variable cfg
    variable evidence
    variable sequence
    incr sequence
    set file [file join $evidence [format {%03d-refresh_region.tcldict} $sequence]]
    set summary [uplevel 1 [list ipcfg::capture $file $script]]
    set receipt [joint::read_capture_evidence $file $summary]
    puts $receipt
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
        ![regexp {^CAPTURE_FAIL:native message export incomplete or counters changed: WARNING emitted=-[1-9][0-9]* exported=[0-9]+$} [dict get $receipt capture_error]]} {
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

proc joint::same_windows_path {left right} {
    return [string equal -nocase [file normalize $left] [file normalize $right]]
}

proc joint::refresh_module_reference_checked {reference expected_result} {
    set actual_result [update_module_reference $reference]
    if {![joint::same_windows_path $actual_result $expected_result]} {
        error "JOINT_MODULE_REFERENCE_RESULT_MISMATCH:$actual_result"
    }
    return SUCCESS:module_reference_updated
}

proc joint::module_reference_xci {reference} {
    # update_module_reference returns the module-reference leaf's .xci path,
    # whereas get_property NAME returns its instance name. Derive the expected
    # .xci from the same leaf's NAME plus the block-design ip/ directory so the
    # full-path check stays strict without re-running the mutating command.
    set name [get_property NAME $reference]
    set bd [joint::one [get_files */display.bd] display.bd]
    set xci [file normalize [file join [file dirname $bd] ip $name $name.xci]]
    if {![file isfile $xci]} {error "JOINT_MODULE_REFERENCE_XCI_MISSING:$xci"}
    return $xci
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
    foreach pin [list control/M12_ACLK $cell/[dict get $cfg clock_pin]] {
        if {[lsearch -exact [joint::pin_ends ps7/FCLK_CLK0] $pin] < 0} {error "JOINT_NOT_ON_FCLK0:$pin"}
    }
    foreach pin [list control/M12_ARESETN $cell/[dict get $cfg reset_pin]] {
        if {[lsearch -exact [joint::pin_ends reset_rle/peripheral_aresetn] $pin] < 0} {error "JOINT_RESET_MISMATCH:$pin"}
    }
    foreach key {input_data_pin output_data_pin} {
        set pin [joint::one [get_bd_pins $cell/[dict get $cfg $key]] $key]
        set width [expr {abs([get_property LEFT $pin] - [get_property RIGHT $pin]) + 1}]
        if {$width != 32} {error "JOINT_DATA_WIDTH_NOT_32:$key:$width"}
    }
    set matches 0
    dict for {segment mapping} [joint::addr_snapshot] {
        lassign $mapping base range
        if {$base == 0x43C80000} {
            if {$range != 0x10000 || ![string match *jpeg_axis_gray_0* $segment]} {error "JOINT_JPEG_ADDRESS_WRONG:$segment:$mapping"}
            incr matches
        }
    }
    if {$matches != 1} {error "JOINT_EXPECTED_ONE_JPEG_ADDRESS_SEGMENT"}
    return SUCCESS:joint_jpeg_readback
}
