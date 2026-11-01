set repo [file normalize [file join [file dirname [info script]] .. .. ..]]
if {[catch {
    source [file join $repo scripts standalone create_g2_integration.tcl]
    source [file join $repo scripts standalone build_g2_integration.tcl]
} err opts]} {
    puts stderr "G2_BUILD_ERROR: $err"
    if {[dict exists $opts -errorinfo]} { puts stderr [dict get $opts -errorinfo] }
    exit 1
}
exit 0
