# Isolated WO-0013 Phase A. Run from a fresh Vivado 2026.1 Tcl session.
# Caller may set wo13_output and ::ipcfg::native_log_path before sourcing.
set wo13_repo [file normalize [file join [file dirname [info script]] ../..]]
if {![info exists wo13_output]} {
    set wo13_output [file join $wo13_repo data results wo0013-build-20261014]
}
set wo13_output [file normalize $wo13_output]
file mkdir $wo13_output
file mkdir [file join $wo13_output reports]
file mkdir [file join $wo13_output export]
file mkdir [file join $wo13_output ipcfg_evidence]
if {![llength [info commands ipcfg::capture]]} {
    source -notrace [file join $wo13_repo .agents skills vivado-ip-configurator lib ipcfg.tcl]
}
if {![info exists ::ipcfg::native_log_path]} {
    error {Set ::ipcfg::native_log_path to this session's actual native log first.}
}

proc wo13_capture {label command expected} {
    global wo13_output
    set path [file join $wo13_output ipcfg_evidence "$label.[pid].[clock clicks].tcldict"]
    set receipt [ipcfg::capture $path $command $expected]
    puts $receipt
    if {[dict get $receipt status] ne "OK"} {error "WO13_STAGE_FAIL:$label:$path"}
}

if {[file exists [file join $wo13_output project wo0013_phase_a.xpr]]} {
    error {Existing project found. Select a fresh wo13_output; do not overwrite it.}
}
create_project wo0013_phase_a [file join $wo13_output project] -part xc7z020clg400-1
set_property board_part tul.com.tw:pynq-z2:part0:1.0 [current_project]
set_property target_language Verilog [current_project]
add_files -norecurse [file join $wo13_repo fpga experimental wo0013 axis_dma_rx_monitor.v]
add_files -norecurse [file join $wo13_repo fpga experimental wo0013 axis_dma_rx_monitor_bd.v]
create_bd_design wo0013_dma

wo13_capture ps_create [list ipcfg::create_cell xilinx.com:ip:processing_system7:5.5 ps7] {SUCCESS:created}
wo13_capture ps_preset [list ipcfg::run_automation ps7 xilinx.com:bd_rule:processing_system7 \
    [dict create CONFIG [dict create apply_board_preset 1 make_external {FIXED_IO, DDR}]]] {AUTOMATION:OK}
wo13_capture ps_ports [list ipcfg::require ps7 [dict create \
    CONFIG.PCW_USE_M_AXI_GP0 1 CONFIG.PCW_USE_S_AXI_HP0 1 \
    CONFIG.PCW_S_AXI_HP0_DATA_WIDTH 64 CONFIG.PCW_EN_CLK0_PORT 1 \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ 50] {Isolated MM2S POC: GP0 control, official PYNQ 64-bit HP0 DDR, 50MHz PL}] {REQ:pinned+applied}
wo13_capture dma_create [list ipcfg::create_cell_cfg xilinx.com:ip:axi_dma:7.1 axi_dma_0 [dict create \
    CONFIG.c_include_sg 0 CONFIG.c_include_mm2s 1 CONFIG.c_include_s2mm 0 \
    CONFIG.c_micro_dma 0 CONFIG.c_sg_length_width 17 CONFIG.c_addr_width 32 \
    CONFIG.c_m_axi_mm2s_data_width 64 CONFIG.c_m_axis_mm2s_tdata_width 32 \
    CONFIG.c_include_mm2s_dre 0 CONFIG.c_mm2s_burst_size 16]] {SUCCESS:created+configured}
wo13_capture fabric_create [list ipcfg::create_cell_cfg xilinx.com:ip:smartconnect:1.0 control_fabric \
    [dict create CONFIG.NUM_SI 1 CONFIG.NUM_MI 2 CONFIG.NUM_CLKS 1 CONFIG.HAS_ARESETN 1]] {SUCCESS:created+configured}
wo13_capture converter_create [list ipcfg::create_cell_cfg xilinx.com:ip:axi_protocol_converter:2.1 hp0_protocol \
    [dict create CONFIG.SI_PROTOCOL AXI4 CONFIG.MI_PROTOCOL AXI3 CONFIG.DATA_WIDTH 64 CONFIG.TRANSLATION_MODE 2]] {SUCCESS:created+configured}
wo13_capture reset_create [list ipcfg::create_cell_cfg xilinx.com:ip:proc_sys_reset:5.0 reset_50m \
    [dict create CONFIG.C_EXT_RESET_HIGH 0 CONFIG.C_AUX_RESET_HIGH 1]] {SUCCESS:created+configured}
wo13_capture high_create [list ipcfg::create_cell_cfg xilinx.com:ip:xlconstant:1.1 constant_high \
    [dict create CONFIG.CONST_WIDTH 1 CONFIG.CONST_VAL 1]] {SUCCESS:created+configured}
wo13_capture low_create [list ipcfg::create_cell_cfg xilinx.com:ip:xlconstant:1.1 constant_low \
    [dict create CONFIG.CONST_WIDTH 1 CONFIG.CONST_VAL 0]] {SUCCESS:created+configured}
create_bd_cell -type module -reference axis_dma_rx_monitor_bd rx_monitor

# Control and DDR paths. The HP0 protocol converter is explicit, not a direct AXI4->AXI3 wire.
connect_bd_intf_net [get_bd_intf_pins ps7/M_AXI_GP0] [get_bd_intf_pins control_fabric/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins control_fabric/M00_AXI] [get_bd_intf_pins axi_dma_0/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins control_fabric/M01_AXI] [get_bd_intf_pins rx_monitor/s_axi]
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_MM2S] [get_bd_intf_pins hp0_protocol/S_AXI]
connect_bd_intf_net [get_bd_intf_pins hp0_protocol/M_AXI] [get_bd_intf_pins ps7/S_AXI_HP0]
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXIS_MM2S] [get_bd_intf_pins rx_monitor/s_axis]

# Only clock INPUTS appear after the FCLK source. DMA interrupt output is intentionally unused (polling).
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] \
    [get_bd_pins ps7/M_AXI_GP0_ACLK] [get_bd_pins ps7/S_AXI_HP0_ACLK] \
    [get_bd_pins control_fabric/aclk] [get_bd_pins hp0_protocol/aclk] \
    [get_bd_pins axi_dma_0/s_axi_lite_aclk] [get_bd_pins axi_dma_0/m_axi_mm2s_aclk] \
    [get_bd_pins rx_monitor/aclk] [get_bd_pins reset_50m/slowest_sync_clk]
connect_bd_net [get_bd_pins ps7/FCLK_RESET0_N] [get_bd_pins reset_50m/ext_reset_in]
connect_bd_net [get_bd_pins constant_high/dout] [get_bd_pins reset_50m/dcm_locked]
# aux_reset_in is explicitly active high above; tie low to keep it deasserted.
connect_bd_net [get_bd_pins constant_low/dout] [get_bd_pins reset_50m/aux_reset_in] \
    [get_bd_pins reset_50m/mb_debug_sys_rst] [get_bd_pins ps7/S_AXI_HP0_RDISSUECAP1_EN] \
    [get_bd_pins ps7/S_AXI_HP0_WRISSUECAP1_EN]
connect_bd_net [get_bd_pins reset_50m/interconnect_aresetn] [get_bd_pins control_fabric/aresetn]
connect_bd_net [get_bd_pins reset_50m/peripheral_aresetn] \
    [get_bd_pins axi_dma_0/axi_resetn] [get_bd_pins hp0_protocol/aresetn] [get_bd_pins rx_monitor/aresetn]

assign_bd_address -offset 0x40400000 -range 64K -target_address_space [get_bd_addr_spaces ps7/Data] \
    [get_bd_addr_segs axi_dma_0/S_AXI_LITE/Reg]
set monitor_segments [get_bd_addr_segs rx_monitor/s_axi/*]
if {[llength $monitor_segments] != 1} {error "Expected one monitor address segment: $monitor_segments"}
assign_bd_address -offset 0x43C10000 -range 64K -target_address_space [get_bd_addr_spaces ps7/Data] $monitor_segments
assign_bd_address -offset 0 -range 512M -target_address_space [get_bd_addr_spaces axi_dma_0/Data_MM2S] \
    [get_bd_addr_segs ps7/S_AXI_HP0/HP0_DDR_LOWOCM]
save_bd_design
wo13_capture bd_validate [list validate_bd_design] {}
save_bd_design
puts [ipcfg::audit_requirements]
set bd_file [get_files */wo0013_dma.bd]
generate_target all $bd_file
set wrapper [make_wrapper -files $bd_file -top]
add_files -norecurse $wrapper
set_property top wo0013_dma_wrapper [current_fileset]
update_compile_order -fileset sources_1
write_bd_tcl -force [file join $wo13_output export wo0013_dma_recreate.tcl]
puts "WO0013_BD_READY project=[get_property DIRECTORY [current_project]] wrapper=$wrapper"
