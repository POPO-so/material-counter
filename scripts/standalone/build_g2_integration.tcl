# Build the dedicated G2 project created by create_g2_integration.tcl.
foreach name {G2_OUT G2_MONITOR_SIM_OUT} {
    if {![info exists ::env($name)]} {error "Missing environment variable $name"}
}
set g2_out [file normalize $::env(G2_OUT)]
set repo [file normalize [file join [file dirname [info script]] ../..]]
set results [file normalize [file join $repo data results]]
if {![string equal -nocase [file dirname $g2_out] $results] ||
    ![regexp {^standalone-[0-9]{8}(-[0-9]{6})?-g2-integration$} [file tail $g2_out]]} {
    error "G2_BUILD_OUTSIDE_DEDICATED_RESULTS_DIRECTORY:$g2_out"
}
set project_file [file join $g2_out project display.xpr]
set bd_file [file join $g2_out project display.srcs sources_1 bd display display.bd]
set readback_file [file join $g2_out evidence g2-architecture-readback.tcldict]
if {![file isfile $project_file] || ![file isfile $bd_file] || ![file isfile $readback_file]} {
    error "G2_BLOCK_DESIGN_OR_ARCHITECTURE_RECEIPT_MISSING:$g2_out"
}

if {![llength [get_projects -quiet]]} {open_project $project_file}
open_bd_design $bd_file
set offload_candidate [expr {[info exists ::env(OFFLOAD_CANDIDATE)] && $::env(OFFLOAD_CANDIDATE) eq "1"}]
set expected_mi [expr {$offload_candidate ? 12 : 11}]
if {[get_property CONFIG.NUM_MI [get_bd_cells control]] != $expected_mi} {error "G2_BUILD_CONTROL_MI_MISMATCH"}
foreach cell {axi_dma_0 image_pipeline hp0_protocol hp1_fabric reset_rle} {
    if {![llength [get_bd_cells -quiet $cell]]} {error "G2_BUILD_CELL_MISSING:$cell"}
}
if {$offload_candidate && (![llength [get_bd_cells -quiet binary_overlay]] || ![llength [get_bd_intf_pins -quiet control/M11_AXI]])} {
    error "G2_BUILD_OFFLOAD_CELLS_MISSING"
}
if {[llength [get_bd_intf_nets -quiet -of_objects [get_bd_intf_pins axi_dma_0/M_AXIS_MM2S]]] != 1 ||
    [llength [get_bd_intf_nets -quiet -of_objects [get_bd_intf_pins image_pipeline/m_axis]]] != 1} {
    error "G2_BUILD_STREAM_CONNECTION_MISSING"
}

set ::env(DISPLAY_OUT) $g2_out
set ::env(DISPLAY_TAG) g2-integrated
set ::env(MONITOR_SIM_OUT) [file normalize $::env(G2_MONITOR_SIM_OUT)]
source [file join $repo scripts standalone build_display.tcl]
foreach extension {bit hwh} {
    set artifact [file join $g2_out export g2-integrated.$extension]
    if {![file isfile $artifact]} {error "G2_BUILD_ARTIFACT_MISSING:$artifact"}
}
puts "G2_BUILD_COMPLETE:BIT_HWH_PRESENT;manifest_and_board_smoke_pending"
