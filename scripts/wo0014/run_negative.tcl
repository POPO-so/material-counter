set bin [file dirname [info nameofexecutable]]
cd C:/fpga/material-counter/data/results/wo0014-offline-20261014/new-sim
set result [exec [file join $bin xsim.exe] wo14_small_reviewed -R -testplusarg INPUT=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/negative/bad_header.bin -testplusarg EXPECTED=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/directed_expected_th128.bin -testplusarg ACTUAL=C:/fpga/material-counter/data/results/wo0014-offline-20261014/bad_header-actual.bin -testplusarg NEGATIVE=1 -testplusarg EXPECTED_ERROR=1 -testplusarg BAD_RECORDS=20 -testplusarg DRAIN_RECORDS=0 -testplusarg RECOVERY_RECORDS=20 -testplusarg BASE_FRAME_ID=40980 -log C:/fpga/material-counter/data/results/wo0014-offline-20261014/bad_header.log]
puts "NEGATIVE_CASE bad_header"
puts $result
if {![string match {*RESULT PASS:*} $result] || [string match {*Fatal:*} $result]} {error {Negative testcase failed}}
set result [exec [file join $bin xsim.exe] wo14_small_reviewed -R -testplusarg INPUT=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/negative/bad_length.bin -testplusarg EXPECTED=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/directed_expected_th128.bin -testplusarg ACTUAL=C:/fpga/material-counter/data/results/wo0014-offline-20261014/bad_length-actual.bin -testplusarg NEGATIVE=1 -testplusarg EXPECTED_ERROR=2 -testplusarg BAD_RECORDS=20 -testplusarg DRAIN_RECORDS=0 -testplusarg RECOVERY_RECORDS=20 -testplusarg BASE_FRAME_ID=40980 -log C:/fpga/material-counter/data/results/wo0014-offline-20261014/bad_length.log]
puts "NEGATIVE_CASE bad_length"
puts $result
if {![string match {*RESULT PASS:*} $result] || [string match {*Fatal:*} $result]} {error {Negative testcase failed}}
set result [exec [file join $bin xsim.exe] wo14_small_reviewed -R -testplusarg INPUT=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/negative/bad_tkeep.bin -testplusarg EXPECTED=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/directed_expected_th128.bin -testplusarg ACTUAL=C:/fpga/material-counter/data/results/wo0014-offline-20261014/bad_tkeep-actual.bin -testplusarg NEGATIVE=1 -testplusarg EXPECTED_ERROR=3 -testplusarg BAD_RECORDS=20 -testplusarg DRAIN_RECORDS=0 -testplusarg RECOVERY_RECORDS=20 -testplusarg BASE_FRAME_ID=40980 -log C:/fpga/material-counter/data/results/wo0014-offline-20261014/bad_tkeep.log]
puts "NEGATIVE_CASE bad_tkeep"
puts $result
if {![string match {*RESULT PASS:*} $result] || [string match {*Fatal:*} $result]} {error {Negative testcase failed}}
set result [exec [file join $bin xsim.exe] wo14_small_reviewed -R -testplusarg INPUT=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/negative/early_tlast.bin -testplusarg EXPECTED=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/directed_expected_th128.bin -testplusarg ACTUAL=C:/fpga/material-counter/data/results/wo0014-offline-20261014/early_tlast-actual.bin -testplusarg NEGATIVE=1 -testplusarg EXPECTED_ERROR=4 -testplusarg BAD_RECORDS=5 -testplusarg DRAIN_RECORDS=0 -testplusarg RECOVERY_RECORDS=20 -testplusarg BASE_FRAME_ID=40980 -log C:/fpga/material-counter/data/results/wo0014-offline-20261014/early_tlast.log]
puts "NEGATIVE_CASE early_tlast"
puts $result
if {![string match {*RESULT PASS:*} $result] || [string match {*Fatal:*} $result]} {error {Negative testcase failed}}
set result [exec [file join $bin xsim.exe] wo14_small_reviewed -R -testplusarg INPUT=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/negative/missing_tlast.bin -testplusarg EXPECTED=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/directed_expected_th128.bin -testplusarg ACTUAL=C:/fpga/material-counter/data/results/wo0014-offline-20261014/missing_tlast-actual.bin -testplusarg NEGATIVE=1 -testplusarg EXPECTED_ERROR=4 -testplusarg BAD_RECORDS=20 -testplusarg DRAIN_RECORDS=20 -testplusarg RECOVERY_RECORDS=20 -testplusarg BASE_FRAME_ID=40980 -log C:/fpga/material-counter/data/results/wo0014-offline-20261014/missing_tlast.log]
puts "NEGATIVE_CASE missing_tlast"
puts $result
if {![string match {*RESULT PASS:*} $result] || [string match {*Fatal:*} $result]} {error {Negative testcase failed}}
set result [exec [file join $bin xsim.exe] wo14_small_reviewed -R -testplusarg INPUT=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/negative/bad_checksum.bin -testplusarg EXPECTED=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/directed_expected_th128.bin -testplusarg ACTUAL=C:/fpga/material-counter/data/results/wo0014-offline-20261014/bad_checksum-actual.bin -testplusarg NEGATIVE=1 -testplusarg EXPECTED_ERROR=5 -testplusarg BAD_RECORDS=20 -testplusarg DRAIN_RECORDS=0 -testplusarg RECOVERY_RECORDS=20 -testplusarg BASE_FRAME_ID=40980 -log C:/fpga/material-counter/data/results/wo0014-offline-20261014/bad_checksum.log]
puts "NEGATIVE_CASE bad_checksum"
puts $result
if {![string match {*RESULT PASS:*} $result] || [string match {*Fatal:*} $result]} {error {Negative testcase failed}}
set result [exec [file join $bin xsim.exe] wo14_small_reviewed -R -testplusarg INPUT=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/negative/extra_payload.bin -testplusarg EXPECTED=C:/fpga/material-counter/data/results/wo0014-offline-20261014/vectors/directed_expected_th128.bin -testplusarg ACTUAL=C:/fpga/material-counter/data/results/wo0014-offline-20261014/extra_payload-actual.bin -testplusarg NEGATIVE=1 -testplusarg EXPECTED_ERROR=5 -testplusarg BAD_RECORDS=21 -testplusarg DRAIN_RECORDS=0 -testplusarg RECOVERY_RECORDS=20 -testplusarg BASE_FRAME_ID=40980 -log C:/fpga/material-counter/data/results/wo0014-offline-20261014/extra_payload.log]
puts "NEGATIVE_CASE extra_payload"
puts $result
if {![string match {*RESULT PASS:*} $result] || [string match {*Fatal:*} $result]} {error {Negative testcase failed}}
