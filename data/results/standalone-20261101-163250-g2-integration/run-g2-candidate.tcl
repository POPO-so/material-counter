if {[catch {
    source {Q:/scripts/standalone/create_g2_integration.tcl}
    source {Q:/scripts/standalone/build_g2_integration.tcl}
} err opts]} {
    puts stderr "G2_BUILD_ERROR: $err"
    if {[dict exists $opts -errorinfo]} { puts stderr [dict get $opts -errorinfo] }
    exit 1
}
exit 0