source C:/fpga/wt/claude-jpeg-20261102/fpga/experimental/jpeg_integration/joint_common.tcl

proc write_evidence {file content} {
    set fh [open $file w]
    puts -nonewline $fh $content
    close $fh
}
proc s_summary {status code} {
    return [dict create status $status code $code warning_count 2 critical_count 0 error_count 0]
}

set tmpdir [file normalize C:/fpga/wt/_scratch_verify/captest]
file mkdir $tmpdir

# (1) OK path: full record carries raw + messages; summary strips them.
set f [file join $tmpdir 001.tcldict]
write_evidence $f "schema_version 1 data {status OK code 0 warning_count 2 critical_count 0 error_count 0 evidence_path $f raw C:/x/ip/abc.xci messages {{W: foo} {W: bar}}}"
set d [joint::read_capture_evidence $f [s_summary OK 0]]
if {[dict get $d raw] ne "C:/x/ip/abc.xci"} { error "OK raw mismatch" }
if {[llength [dict get $d messages]] != 2} { error "OK messages mismatch" }
puts "PASS ok"

# (2) FAIL path: raw still restored for the allowlisted supplemental review.
set f2 [file join $tmpdir 002.tcldict]
write_evidence $f2 "schema_version 1 data {status FAIL code 0 warning_count 4 critical_count 0 error_count 0 evidence_path $f2 raw C:/x/ip/abc.xci messages {{W: foo}} capture_error {WARNING emitted=-5 exported=2}}"
set d2 [joint::read_capture_evidence $f2 [dict create status FAIL code 0 warning_count 4 critical_count 0 error_count 0]]
if {[dict get $d2 raw] ne "C:/x/ip/abc.xci"} { error "FAIL raw mismatch" }
puts "PASS fail"

# (3) missing evidence file -> rejected at read.
if {![catch {joint::read_capture_evidence [file join $tmpdir 999.tcldict] [s_summary OK 0]} e3]} { error "missing not rejected" }
puts "PASS missing: $e3"

# (4) summary/full status mismatch -> rejected.
set f4 [file join $tmpdir 003.tcldict]
write_evidence $f4 "schema_version 1 data {status OK code 0 warning_count 2 critical_count 0 error_count 0 evidence_path $f4 raw C:/x/ip/abc.xci messages {{W: foo}}}"
if {![catch {joint::read_capture_evidence $f4 [s_summary FAIL 0]} e4]} { error "mismatch not rejected" }
if {![string match {*MISMATCH*} $e4]} { error "unexpected mismatch error: $e4" }
puts "PASS mismatch: $e4"

puts "CAPTURE_REGRESSION_ALL_PASS"