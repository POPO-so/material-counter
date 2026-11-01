# Build the dedicated G2 project created by create_g2_integration.tcl.
foreach name {G2_OUT G2_MONITOR_SIM_OUT} {
    if {![info exists ::env($name)]} {error "Missing environment variable $name"}
}
foreach name {PYNQ_SOURCE VIDEO_IP_BUILT} {
    if {![info exists ::env($name)] || ![file isdirectory $::env($name)]} {
        error "G2_REQUIRED_DIRECTORY_MISSING:$name"
    }
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
# Re-register external IP repositories when this build is launched in a fresh
# Vivado process. The project file may retain paths relative to its old drive.
set_property ip_repo_paths [list \
    [file normalize [file join $::env(PYNQ_SOURCE) boards ip]] \
    [file normalize [file join $::env(VIDEO_IP_BUILT) color_convert solution1 impl ip]] \
    [file normalize [file join $::env(VIDEO_IP_BUILT) pixel_unpack solution1 impl ip]]] [current_project]
update_ip_catalog
open_bd_design $bd_file
set offload_candidate [expr {[info exists ::env(OFFLOAD_CANDIDATE)] && $::env(OFFLOAD_CANDIDATE) eq "1"}]
set sw0_candidate [expr {[info exists ::env(SW0_CANDIDATE)] && $::env(SW0_CANDIDATE) eq "1"}]
if {$sw0_candidate && !$offload_candidate} {error "G2_SW0_CANDIDATE_REQUIRES_OFFLOAD_CANDIDATE"}
set expected_mi [expr {$offload_candidate ? 12 : 11}]
if {[get_property CONFIG.NUM_MI [get_bd_cells control]] != $expected_mi} {error "G2_BUILD_CONTROL_MI_MISMATCH"}
foreach cell {axi_dma_0 image_pipeline hp0_protocol hp1_fabric reset_rle} {
    if {![llength [get_bd_cells -quiet $cell]]} {error "G2_BUILD_CELL_MISSING:$cell"}
}
if {$offload_candidate && (![llength [get_bd_cells -quiet binary_overlay]] || ![llength [get_bd_intf_pins -quiet control/M11_AXI]])} {
    error "G2_BUILD_OFFLOAD_CELLS_MISSING"
}
if {$sw0_candidate && ([get_property CONFIG.C_GPIO_WIDTH [get_bd_cells btns_gpio]] != 5 ||
    [llength [get_bd_ports -quiet sw0]] != 1 ||
    [llength [get_bd_pins -quiet switch0_synchronizer/switch_sync]] != 1)} {
    error "G2_BUILD_SW0_BD_READBACK_MISMATCH"
}
if {$offload_candidate && !$sw0_candidate && ([get_property CONFIG.C_GPIO_WIDTH [get_bd_cells btns_gpio]] != 6 ||
    [llength [get_bd_ports -quiet sws_2bits_tri_i]] != 1 ||
    [llength [get_bd_pins -quiet switch_synchronizer/switch_sync]] != 1)} {
    error "G2_BUILD_SWITCH_GPIO_READBACK_MISMATCH"
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
if {$sw0_candidate} {
    set hwh_file [file join $g2_out export g2-integrated.hwh]
    set hwh_channel [open $hwh_file r]
    set hwh_text [read $hwh_channel]
    close $hwh_channel
    set readback_file [file join $g2_out evidence offload-bd-readback.tcldict]
    set readback_channel [open $readback_file r]
    set readback_text [read $readback_channel]
    close $readback_channel
    if {[string first {NAME="C_GPIO_WIDTH" VALUE="5"} $hwh_text] < 0 ||
        [string first {NAME="sw0"} $hwh_text] < 0 ||
        [string first {SW0=4} $readback_text] < 0} {
        error "G2_SW0_HWH_READBACK_MISMATCH"
    }
} elseif {$offload_candidate} {
    set hwh_file [file join $g2_out export g2-integrated.hwh]
    set channel [open $hwh_file r]
    set hwh_text [read $channel]
    close $channel
    set readback_file [file join $g2_out evidence offload-bd-readback.tcldict]
    set channel [open $readback_file r]
    set readback_text [read $channel]
    close $channel
    foreach expected [list {NAME="C_GPIO_WIDTH" VALUE="6"} {NAME="sws_2bits_tri_i"}] {
        if {[string first $expected $hwh_text] < 0} {error "G2_SWITCH_HWH_READBACK_MISMATCH:$expected"}
    }
    foreach expected [list {GPIO_width 6} SW0=4 SW1=5] {
        if {[string first $expected $readback_text] < 0} {error "G2_SWITCH_BD_READBACK_MISMATCH:$expected"}
    }
}
puts "G2_BUILD_COMPLETE:BIT_HWH_PRESENT;manifest_and_board_smoke_pending"
