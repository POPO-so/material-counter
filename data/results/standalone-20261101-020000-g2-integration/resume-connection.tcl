# One-time bounded recovery for the recorded HSync interface mismatch.
set repo {Q:/}
set g2_out {Q:/data/results/standalone-20261101-020000-g2-integration}
set raw_snapshot [file join $g2_out rtl axis_raw_gray_adapter.v]
if {![file exists $raw_snapshot]} {file copy [file join $repo fpga experimental gray8 axis_raw_gray_adapter.v] $raw_snapshot}
if {![llength [get_files -quiet $raw_snapshot]]} {add_files -norecurse $raw_snapshot}
update_compile_order -fileset sources_1

set video_out_cell hdmi_out/frontend/v_axi4s_vid_out_0
set swap_cell hdmi_out/frontend/color_swap_0
set old_interface [get_bd_intf_nets -quiet -of_objects [get_bd_intf_pins ${swap_cell}/pixel_input]]
if {[llength $old_interface]} {disconnect_bd_intf_net $old_interface [get_bd_intf_pins ${swap_cell}/pixel_input]}
# The earlier partial retries left only the local HSync sink detached.
connect_bd_net [get_bd_pins hdmi_out/frontend/hsync_in] [get_bd_pins hdmi_out/frontend/color_swap_0/hsync_in]
set hdst [get_bd_pins hdmi_out/frontend/color_swap_0/hsync_in]
# Interface expansion may restore observer nets; explicitly release each sink.
foreach sink {frame_monitor/vid_data frame_monitor/vid_active frame_monitor/vid_vsync} {
    set sink_pin [get_bd_pins $sink]
    set prior_net [get_bd_nets -quiet -of_objects $sink_pin]
    if {[llength $prior_net]} {disconnect_bd_net $prior_net $sink_pin}
}
connect_bd_net [get_bd_pins binary_overlay/out_data24] [get_bd_pins frame_monitor/vid_data]
connect_bd_net [get_bd_pins binary_overlay/out_active] [get_bd_pins frame_monitor/vid_active]
connect_bd_net [get_bd_pins binary_overlay/out_vsync] [get_bd_pins frame_monitor/vid_vsync]

foreach {off seg} {0x43C50000 binary_overlay/S_AXI/*} {
    set matches [get_bd_addr_segs -quiet $seg]
    if {[llength $matches]!=1} {error "OFFLOAD_ADDRESS_SEGMENT_MISSING:$seg:$matches"}
    assign_bd_address -offset $off -range 0x10000 -target_address_space [get_bd_addr_spaces ps7/Data] $matches
}
connect_bd_net [get_bd_pins btns_gpio/ip2intc_irpt] [get_bd_pins video_interrupts/In3]

proc offload_require_shared_net {source sink} {
    set todo [get_bd_nets -quiet -boundary_type both -of_objects [get_bd_pins $source]]
    set target [get_bd_nets -quiet -boundary_type both -of_objects [get_bd_pins $sink]]
    if {![llength $todo] || ![llength $target]} {error "OFFLOAD_UNCONNECTED_PIN:$source:$sink"}
    set visited [dict create]
    while {[llength $todo]} {
        set net [lindex $todo 0];set todo [lrange $todo 1 end]
        if {[dict exists $visited $net]} {continue}
        dict set visited $net 1
        if {[lsearch -exact $target $net]>=0} {return [dict keys $visited]}
        foreach pin [get_bd_pins -quiet -of_objects $net] {
            foreach adjacent [get_bd_nets -quiet -boundary_type both -of_objects $pin] {
                if {![dict exists $visited $adjacent]} {lappend todo $adjacent}
            }
        }
    }
    error "OFFLOAD_NET_PATH_MISMATCH:$source:$sink"
}
set video_readback {}
foreach {source sink} {
    binary_overlay/out_data24 hdmi_out/frontend/color_swap_0/pixel_in
    binary_overlay/out_active hdmi_out/frontend/color_swap_0/vde_in
    binary_overlay/out_vsync hdmi_out/frontend/color_swap_0/vsync_in
    binary_overlay/out_hsync hdmi_out/frontend/color_swap_0/hsync_in
    binary_overlay/out_data24 frame_monitor/vid_data
    binary_overlay/out_active frame_monitor/vid_active
    binary_overlay/out_vsync frame_monitor/vid_vsync
} {
    if {[string match *hsync_in $sink]} {set sink $hdst}
    dict set video_readback $sink [offload_require_shared_net $source $sink]
}
foreach {source sink} {
    image_pipeline/preview_valid binary_overlay/tap_valid
    image_pipeline/preview_sof binary_overlay/tap_sof
    image_pipeline/preview_eol binary_overlay/tap_eol
    image_pipeline/preview_bit binary_overlay/tap_data
    image_pipeline/preview_frame_id binary_overlay/tap_frame_id
} {offload_require_shared_net $source $sink}
if {[get_property CONFIG.NUM_MI [get_bd_cells control]] != 12 ||
    [get_property CONFIG.NUM_PORTS [get_bd_cells video_interrupts]] != 4 ||
    [get_property CONFIG.C_INTERRUPT_PRESENT [get_bd_cells btns_gpio]] != 1} {
    error "OFFLOAD_READBACK_COUNT_OR_GPIO_MISMATCH"
}
set receipt [dict create candidate ps-pl-offload control_mi_count [get_property CONFIG.NUM_MI [get_bd_cells control]] \
    overlay_master M11 AXIL_base 0x43C50000 AXIL_range 0x10000 \
    interrupt_ports [get_property CONFIG.NUM_PORTS [get_bd_cells video_interrupts]] GPIO_interrupt [get_property CONFIG.C_INTERRUPT_PRESENT [get_bd_cells btns_gpio]] \
    clock_domain {s_axi_aclk mask_clk cfg_clk=FCLK0_50MHz} pixel_clock hdmi_pixel_clock \
    reset_domains {AXI+mask=reset_rle_peripheral_aresetn pixel=reset_pixel_peripheral_aresetn} \
    CDC {publish_toggle_sync ack_toggle_sync cfg_mailbox_stable no_multibit_direct_status_sync} \
    video_path $video_readback]
set evidence_path [file join $g2_out evidence offload-bd-readback.tcldict]
if {[file exists $evidence_path]} {error "OFFLOAD_READBACK_EVIDENCE_EXISTS:$evidence_path"}
set evidence [open $evidence_path {WRONLY CREAT EXCL}]
puts $evidence $receipt
close $evidence
assign_bd_address -offset 0 -range 0x20000000 -target_address_space [get_bd_addr_spaces axi_dma_0/Data_MM2S] [get_bd_addr_segs ps7/S_AXI_HP0/HP0_DDR_LOWOCM]
assign_bd_address -offset 0 -range 0x20000000 -target_address_space [get_bd_addr_spaces axi_dma_0/Data_S2MM] [get_bd_addr_segs ps7/S_AXI_HP1/HP1_DDR_LOWOCM]
validate_bd_design
save_bd_design
set bd_file [get_files */display.bd]
generate_target all $bd_file
update_compile_order -fileset sources_1
set receipt [dict create design display bd $bd_file control_mi_count 12 recovery HSync-explicit-interface raw_source_added true]
set fd [open [file join $g2_out evidence g2-architecture-readback.tcldict] {WRONLY CREAT EXCL}]
puts $fd $receipt
close $fd
puts OFFLOAD_RECOVERY_BD_VALIDATED
