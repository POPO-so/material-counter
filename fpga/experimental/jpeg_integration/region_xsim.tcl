# Run from C:/w/mc-jpeg-regions-20261101; all simulator outputs stay there.
set src $::env(REGION_SRC_DIR)
exec xvlog -sv [file join $src rle_region_features.v] [file join $src region_reference.v] \
    [file join $src tb_rle_region_features.sv] [file join $src tb_region_diff.sv] \
    [file join $src tb_region_limits.sv] [file join $src tb_region_candidate_filters.sv] \
    [file join $src rle_feature_tap.v] > region_compile.log
exec xelab tb_rle_region_features -s region_final_cases > region_elab_cases.log
exec xsim region_final_cases -runall > region_cases.log
exec xelab tb_region_diff -s region_final_diff > region_elab_diff.log
exec xsim region_final_diff -runall > region_diff.log
exec xelab tb_region_limits -s region_final_limits > region_elab_limits.log
exec xsim region_final_limits -runall > region_limits.log
exec xelab tb_region_candidate_filters -s region_final_candidates > region_elab_candidates.log
exec xsim region_final_candidates -runall > region_candidates.log
exec xelab rle_feature_tap -s region_tap_link > region_elab_tap.log
puts "REGION_TAP_LINK_PASS"
foreach {log marker} {
    region_cases.log {RLE_FEATURE_PASS cases=12}
    region_diff.log {REGION_DIFF_PASS}
    region_limits.log {REGION_LIMITS_PASS}
    region_candidates.log {REGION_CANDIDATE_PASS cases=7}
} {
    set fh [open $log r]
    set transcript [read $fh]
    close $fh
    if {[string first $marker $transcript] < 0 || [regexp {Fatal:|ERROR:|watchdog timeout} $transcript]} {
        error "REGION_XSIM_FAIL: inspect $log"
    }
    puts "REGION_XSIM_PASS: $log $marker"
}
