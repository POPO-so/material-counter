# Route evidence recovery after launch_runs reset the in-process message counter.
# The route itself has already completed; this script preserves the original
# FAIL capture receipt, audits its exact native-log range, re-runs the formal
# post-route gates on the routed impl_1 run, and writes joint-routed.dcp and
# joint-route.tcldict. It does not route again and adds no waiver.
source -notrace [file join [file dirname [file normalize [info script]]] joint_common.tcl]
joint::init $argv route_continue2
set cfg $joint::cfg
set out [dict get $cfg out]
set options [dict create {*}$argv]
foreach key {-route-review -route-capture} {
    if {![dict exists $options $key]} {error "ROUTE_CONTINUE_REQUIRED_OPTION:$key"}
}
set route_review [dict get $options -route-review]
set route_capture [dict get $options -route-capture]
set manifest [dict get $cfg manifest]

set gate [ipcfg::python_script [dict get $cfg verifier] --verify-manifest $manifest --route-review $route_review]
if {$gate ne "JOINT_SOURCE_HASHES_OK\nJOINT_GREEN_GATES_OK\nJOINT_ROUTE_REVIEW_OK"} {
    error "JOINT_ROUTE_REVIEW_FAILED:$gate"
}
if {![file isfile [file join $out joint-synth.tcldict]]} {error "JOINT_SYNTH_RECEIPT_MISSING"}

# Preserve and independently audit the original route FAIL receipt.
set full [dict create {*}[joint::read_dict $route_capture]]
set data [dict get $full data]
if {[dict get $data status] ne "FAIL" || [dict get $data code] != 0} {
    error "ROUTE_CAPTURE_NOT_FAIL:$route_capture"
}
if {![regexp {^CAPTURE_FAIL:native message export incomplete or counters changed: WARNING emitted=-[1-9][0-9]* exported=[0-9]+$} [dict get $data capture_error]]} {
    error "ROUTE_CAPTURE_ERROR_NOT_ALLOWLISTED:$route_capture"
}
foreach key {native_log_source native_log_offsets native_counts_before native_counts_after messages} {
    if {![dict exists $data $key]} {error "ROUTE_CAPTURE_EVIDENCE_MISSING:$key:$route_capture"}
}
set native_log [file normalize [dict get $data native_log_source]]
set offsets [dict get $data native_log_offsets]
if {[llength $offsets] != 2} {error "ROUTE_CAPTURE_RANGE_MALFORMED:$route_capture"}
set before [dict get [dict get $data native_counts_before] WARNING]
set after [dict get [dict get $data native_counts_after] WARNING]
set messages_file [file join $joint::evidence route-capture.messages.txt]
set mch [open $messages_file w]
fconfigure $mch -encoding utf-8 -translation lf
set warning_messages 0
foreach message [dict get $data messages] {
    if {![string match {WARNING:*} $message]} {
        close $mch
        error "ROUTE_CAPTURE_UNEXPECTED_EXPORTED_SEVERITY:$route_capture"
    }
    puts $mch $message
    incr warning_messages
}
close $mch
if {!$warning_messages} {error "ROUTE_CAPTURE_NO_EXPORTED_WARNINGS:$route_capture"}
set review_file [file join $joint::evidence route-capture.supplement.json]
set review [ipcfg::python_script [file join [file dirname [file normalize [info script]]] joint_route_capture_review.py] \
    --capture-receipt $route_capture --status [dict get $data status] --code [dict get $data code] \
    --capture-error [dict get $data capture_error] --warning-before $before --warning-after $after \
    --native-log $native_log --start [lindex $offsets 0] --end [lindex $offsets 1] \
    --messages-file $messages_file --out $review_file]
if {![string match {JOINT_CAPTURE_REVIEW_PASS *} $review]} {
    error "JOINT_ROUTE_SUPPLEMENTAL_REVIEW_FAIL:$review_file:$review"
}
puts $review

# Re-use the completed routed run; do not launch another route.
set run [get_runs impl_1]
if {[get_property PROGRESS $run] ne "100%" || ![string match {*route_design Complete*} [get_property STATUS $run]]} {
    error "JOINT_ROUTE_NOT_COMPLETE:[get_property STATUS $run]:[get_property PROGRESS $run]"
}
open_run impl_1
foreach type {max min} {
    set path [joint::one [get_timing_paths -delay_type $type -max_paths 1] timing_$type]
    if {[get_property SLACK $path] < 0} {error "JOINT_TIMING_FAIL:$type:[get_property SLACK $path]"}
}

set route_text [report_route_status -return_string]
set reports [file join $out reports route]
file mkdir $reports
set rch [open [file join $reports route-status.rpt] w]
puts $rch $route_text
close $rch
if {[regexp -nocase {# of unrouted nets[.\s:|]*([0-9]+)} $route_text -> unrouted]} {
    if {$unrouted != 0} {error "JOINT_UNROUTED_NETS:$unrouted"}
} else {
    if {![regexp -nocase {# of routable nets[.\s:|]*([0-9]+)} $route_text -> routable] ||
        ![regexp -nocase {# of fully routed nets[.\s:|]*([0-9]+)} $route_text -> fully]} {
        error "JOINT_ROUTE_STATUS_UNPARSED"
    }
    if {$fully != $routable} {error "JOINT_UNROUTED_NETS:$fully!=$routable"}
}
if {![regexp -nocase {# of nets with routing errors[.\s:|]*([0-9]+)} $route_text -> route_errors] || $route_errors != 0} {
    error "JOINT_ROUTING_ERRORS_OR_UNPARSED:$route_text"
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
set resources [joint::resource_gate $reports]

joint::capture route_evidence {
    report_timing_summary -delay_type min_max -report_unconstrained -file [file join $reports timing.rpt]
    report_cdc -details -file [file join $reports cdc.rpt]
    report_drc -file [file join $reports drc.rpt]
    report_methodology -file [file join $reports methodology.rpt]
    report_exceptions -file [file join $reports exceptions.rpt]
    report_bus_skew -file [file join $reports bus-skew.rpt]
    write_checkpoint -force [file join $out joint-routed.dcp]
}
# report_drc/report_methodology above have populated the violation collections;
# querying them before those reports returns "Result '' not found".
foreach violation [concat [get_drc_violations] [get_methodology_violations]] {
    if {[get_property SEVERITY $violation] in {Error {Critical Warning}}} {
        error "JOINT_ROUTE_VIOLATION:$violation"
    }
}
set report_sha [dict create]
foreach name {utilization.rpt timing.rpt cdc.rpt drc.rpt methodology.rpt exceptions.rpt bus-skew.rpt route-status.rpt} {
    dict set report_sha $name [joint::file_sha256 [file join $reports $name]]
}
set xdc [file join [dict get $cfg source_root] constraints candidate_offload_cdc.xdc]
set bind [dict create status ROUTED_REVIEW_PENDING resources $resources reports $reports \
    manifest_sha256 [joint::file_sha256 $manifest] \
    synth_receipt [file join $out joint-synth.tcldict] \
    synth_receipt_sha256 [joint::file_sha256 [file join $out joint-synth.tcldict]] \
    candidate_mailbox_xdc $xdc \
    candidate_mailbox_xdc_sha256 [joint::file_sha256 $xdc] \
    route_capture_receipt $route_capture \
    route_capture_receipt_sha256 [joint::file_sha256 $route_capture] \
    route_capture_supplement $review_file \
    route_capture_supplement_sha256 [joint::file_sha256 $review_file] \
    route_review $route_review \
    route_review_sha256 [joint::file_sha256 $route_review] \
    report_sha256 $report_sha board_ready 0]
puts [ipcfg::write_evidence [file join $out joint-route.tcldict] $bind]
puts "JOINT_ROUTE_COMPLETE: formal route evidence recovered; no bitstream/deployment; board acceptance pending"
joint::verify_sources
close_project