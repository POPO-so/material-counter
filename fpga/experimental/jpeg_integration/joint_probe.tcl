# Safe native-batch audit of an already prepared, disposable clone. No IP
# settings, connections, synthesis or implementation are changed/launched.
source -notrace [file join [file dirname [file normalize [info script]]] joint_common.tcl]
joint::init $argv probe
joint::capture baseline_probe {
    set snapshot [joint::preserved_snapshot]
    puts [ipcfg::write_evidence [file join $joint::evidence architecture.tcldict] $snapshot]
    puts "TOP=[get_property TOP [get_filesets sources_1]]"
    puts "IP_REPO_PATHS=[get_property ip_repo_paths [current_project]]"
    set mm2s_net [joint::one [get_bd_intf_nets -of_objects [get_bd_intf_pins axi_dma_0/M_AXIS_MM2S]] mm2s_net]
    puts "MM2S_RAW_ENDPOINTS=[get_bd_intf_pins -of_objects $mm2s_net]"
    puts "MM2S_CANONICAL_ENDPOINTS=[joint::intf_ends axi_dma_0/M_AXIS_MM2S]"
    if {[joint::intf_ends axi_dma_0/M_AXIS_MM2S] ne [lsort {axi_dma_0/M_AXIS_MM2S image_pipeline/s_axis}]} {
        error "JOINT_BASELINE_MM2S_CHANGED"
    }
    if {[get_property CONFIG.NUM_MI [get_bd_cells control]] != 12} {error "JOINT_PROBE_EXPECTED_BASELINE_MI12"}
    if {[get_property CONFIG.c_sg_length_width [get_bd_cells axi_dma_0]] != 18} {error "JOINT_PROBE_EXPECTED_BASELINE_DMA18"}
    set probe_status SUCCESS:joint_baseline_probe
} {SUCCESS:joint_baseline_probe}
joint::capture native_command_help {help report_route_status; help get_files}
joint::verify_sources
puts "JOINT_PROBE_PASS: clone associations and baseline readbacks only; no HLS/region build or route"
close_project
