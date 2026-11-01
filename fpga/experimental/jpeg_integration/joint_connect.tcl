# Explicit Vivado batch entrypoint. Operates only on joint_prepare.py's clone.
source -notrace [file join [file dirname [file normalize [info script]]] joint_common.tcl]
joint::init $argv connect
set cfg $joint::cfg
set out [dict get $cfg out]
set cell jpeg_axis_gray_0
if {[llength [get_bd_cells -filter {NAME == jpeg_axis_gray_0}]]} {error "JOINT_JPEG_ALREADY_PRESENT"}
if {[get_property CONFIG.NUM_MI [get_bd_cells control]] != 12 ||
    [get_property CONFIG.c_sg_length_width [get_bd_cells axi_dma_0]] != 18} {error "JOINT_WRONG_BASELINE_PARAMETERS"}
set expected_stream [lsort {axi_dma_0/M_AXIS_MM2S image_pipeline/s_axis}]
if {[joint::intf_ends axi_dma_0/M_AXIS_MM2S] ne $expected_stream} {error "JOINT_BASELINE_MM2S_CHANGED"}
set before [joint::preserved_snapshot]
puts [ipcfg::write_evidence [file join $joint::evidence baseline.tcldict] $before]

# Region source bytes were replaced only inside this clone by the preparer;
# every original source remains an immutable manifest input.
# Vivado 2026.1 requires the module-reference IP object, not its BD cell.
set region_reference [joint::one [get_ips -all *image_pipeline*] image_pipeline_module_reference]
set expected_region_result [file normalize [get_property NAME $region_reference]]
joint::capture_refresh_region [list update_module_reference $region_reference] $expected_region_result

# The binary_overlay module reference also contains the selected region RTL.
# Keep it on the ordinary capture path: the image_pipeline-only counter-reset
# review above does not authorize adapting another module reference operation.
set overlay_reference [joint::one [get_ips -all *binary_overlay*] binary_overlay_module_reference]
set expected_overlay_result [file normalize [get_property NAME $overlay_reference]]
joint::capture overlay_region_refresh [list joint::refresh_module_reference_checked \
    $overlay_reference $expected_overlay_result] {SUCCESS:module_reference_updated}
joint::capture ip_repository {
    set existing [get_property ip_repo_paths [current_project]]
    set_property ip_repo_paths [concat $existing [list [dict get $cfg hls_ip]]] [current_project]
    update_ip_catalog
}
joint::capture create_jpeg [list ipcfg::create_cell [dict get $cfg vlnv] $cell] {SUCCESS:created}

# These names come from component.xml. Fail if catalog elaboration differs.
foreach {key mode protocol} {input_intf Slave axis output_intf Master axis control_intf Slave aximm} {
    set pin [joint::one [get_bd_intf_pins $cell/[dict get $cfg $key]] $key]
    if {![string equal -nocase [get_property MODE $pin] $mode] ||
        ![string match "xilinx.com:interface:${protocol}:*" [get_property VLNV $pin]]} {
        error "JOINT_HLS_INTERFACE_CHANGED:$key"
    }
}
foreach key {clock_pin reset_pin input_data_pin output_data_pin} {
    joint::one [get_bd_pins $cell/[dict get $cfg $key]] $key
}
joint::capture control_append [list joint::require_cfg control [dict create CONFIG.NUM_MI 13] \
    {Keep M00-M11; append M12 for JPEG AXI-Lite control}] {REQ:pinned+applied}
joint::capture dma_length [list joint::require_cfg axi_dma_0 [dict create CONFIG.c_sg_length_width 19] \
    {Simple DMA accepts JPEG byte counts up to 512000; preserve DRE and widths}] {REQ:pinned+applied}

joint::capture connect_streams {
    set old [joint::one [get_bd_intf_nets -of_objects [get_bd_intf_pins image_pipeline/s_axis]] old_image_stream]
    disconnect_bd_intf_net $old [get_bd_intf_pins image_pipeline/s_axis]
    connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXIS_MM2S] [get_bd_intf_pins $cell/[dict get $cfg input_intf]]
    connect_bd_intf_net [get_bd_intf_pins $cell/[dict get $cfg output_intf]] [get_bd_intf_pins image_pipeline/s_axis]
    connect_bd_intf_net [get_bd_intf_pins control/M12_AXI] [get_bd_intf_pins $cell/[dict get $cfg control_intf]]
    connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins control/M12_ACLK] [get_bd_pins $cell/[dict get $cfg clock_pin]]
    connect_bd_net [get_bd_pins reset_rle/peripheral_aresetn] [get_bd_pins control/M12_ARESETN] [get_bd_pins $cell/[dict get $cfg reset_pin]]
}
joint::capture assign_control {
    set target [joint::one [get_bd_addr_segs $cell/[dict get $cfg control_intf]/*] jpeg_slave_segment]
    assign_bd_address -offset 0x43C80000 -range 0x10000 -target_address_space [get_bd_addr_spaces ps7/Data] $target
}
joint::capture constraints {
    foreach path [list [file join [dict get $cfg source_root] scripts standalone display_timing.xdc] \
        [file join [dict get $cfg source_root] scripts standalone offload_cdc.xdc] \
        [file join [dict get $cfg source_root] constraints joint_monitor_cdc.xdc]] {
        set matches [get_files -filter "NAME == \"$path\""]
        if {![llength $matches]} {add_files -fileset constrs_1 $path}
        set file [joint::one [get_files -filter "NAME == \"$path\""] $path]
        set_property USED_IN_SYNTHESIS false $file
        set_property PROCESSING_ORDER LATE $file
    }
}
joint::capture bd_validation {validate_bd_design}
joint::capture architecture_readback {joint::check_jpeg} {SUCCESS:joint_jpeg_readback}
set after [joint::check_preserved $before]
joint::capture save_bd {save_bd_design; update_compile_order -fileset sources_1}
joint::verify_sources
joint::capture live_sources {joint::live_sources}
puts [ipcfg::write_evidence [file join $out joint-connected.tcldict] [dict create \
    status CONNECTED_NOT_BUILT source_manifest [dict get $cfg manifest] before $before after $after \
    hls_vlnv [dict get $cfg vlnv] JPEG_control_base 0x43C80000 JPEG_control_range 0x10000 \
    JPEG_control_master M12 JPEG_clock FCLK0_50MHz JPEG_reset reset_rle_peripheral_aresetn \
    DMA_length_width 19 DMA_DRE 0 DMA_address_alignment {MM2S 8-byte; S2MM 4-byte; final TKEEP permits partial last word} \
    mask_accept {PS: completed JPEG status zero and status_ap_vld and RLE/FRG valid; no done bypass wired} \
    board_ready 0]]
puts "JOINT_CONNECT_COMPLETE: clone only; synthesis, CDC review, route and board acceptance pending"
close_project
