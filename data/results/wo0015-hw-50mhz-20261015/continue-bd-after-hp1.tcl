# Resume the same project after correcting invalid Interconnect pseudo-parameters.
# Previous failed capture and attempt counters remain intact.
puts [ipcfg::write_evidence [file join $wo15_output reports hp1-rejected-requirements.tcldict] [ipcfg::requirements_data hp1_fabric]]
wo15_capture hp1_correct_ledger [list ipcfg::forget_requirements hp1_fabric] {REQ:cleared}
set hp1_fabric_requirements [dict create CONFIG.NUM_SI 1 CONFIG.NUM_MI 1]
wo15_capture hp1_fabric_parameters [list ipcfg::require hp1_fabric $hp1_fabric_requirements \
    {One AXI Interconnect SI from S2MM and one MI to the PS HP1 port; endpoint connectivity derives AXI4-to-AXI3 conversion}] {REQ:pinned+applied}

wo15_capture reset_create [list ipcfg::create_cell_cfg xilinx.com:ip:proc_sys_reset reset_pl \
    [dict create CONFIG.C_EXT_RESET_HIGH 0 CONFIG.C_AUX_RESET_HIGH 1]] {SUCCESS:created+configured}
wo15_capture high_create [list ipcfg::create_cell_cfg xilinx.com:ip:xlconstant constant_high \
    [dict create CONFIG.CONST_WIDTH 1 CONFIG.CONST_VAL 1]] {SUCCESS:created+configured}
wo15_capture low_create [list ipcfg::create_cell_cfg xilinx.com:ip:xlconstant constant_low \
    [dict create CONFIG.CONST_WIDTH 1 CONFIG.CONST_VAL 0]] {SUCCESS:created+configured}

create_bd_cell -type module -reference axis_dma_s2mm_pipeline_bd image_pipeline
wo15_capture image_parameters [list set_property -dict [list \
    CONFIG.WIDTH 320 CONFIG.HEIGHT 240 CONFIG.THRESHOLD 128] [get_bd_cells image_pipeline]]

# Control path and original HP0 MM2S read path.
connect_bd_intf_net [get_bd_intf_pins ps7/M_AXI_GP0] [get_bd_intf_pins control_fabric/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins control_fabric/M00_AXI] [get_bd_intf_pins axi_dma_0/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins control_fabric/M01_AXI] [get_bd_intf_pins image_pipeline/s_axi]
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_MM2S] [get_bd_intf_pins hp0_protocol/S_AXI]
connect_bd_intf_net [get_bd_intf_pins hp0_protocol/M_AXI] [get_bd_intf_pins ps7/S_AXI_HP0]

# S2MM write path. The AXI Interconnect SI follows the DMA AXI4 interface;
# its MI follows the PS HP1 AXI3 interface. Do not replace this with SmartConnect.
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_S2MM] [get_bd_intf_pins hp1_fabric/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins hp1_fabric/M00_AXI] [get_bd_intf_pins ps7/S_AXI_HP1]

connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXIS_MM2S] [get_bd_intf_pins image_pipeline/s_axis]
connect_bd_intf_net [get_bd_intf_pins image_pipeline/m_axis] [get_bd_intf_pins axi_dma_0/S_AXIS_S2MM]

# One synchronous 50 MHz domain. AXI Interconnect has common, SI, and MI
# clock/reset pins; each reset is driven from the same proc_sys_reset output.
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] \
    [get_bd_pins ps7/M_AXI_GP0_ACLK] \
    [get_bd_pins ps7/S_AXI_HP0_ACLK] [get_bd_pins ps7/S_AXI_HP1_ACLK] \
    [get_bd_pins control_fabric/aclk] [get_bd_pins hp0_protocol/aclk] \
    [get_bd_pins hp1_fabric/ACLK] [get_bd_pins hp1_fabric/S00_ACLK] [get_bd_pins hp1_fabric/M00_ACLK] \
    [get_bd_pins axi_dma_0/s_axi_lite_aclk] \
    [get_bd_pins axi_dma_0/m_axi_mm2s_aclk] [get_bd_pins axi_dma_0/m_axi_s2mm_aclk] \
    [get_bd_pins image_pipeline/aclk] [get_bd_pins reset_pl/slowest_sync_clk]
connect_bd_net [get_bd_pins ps7/FCLK_RESET0_N] [get_bd_pins reset_pl/ext_reset_in]
connect_bd_net [get_bd_pins constant_high/dout] [get_bd_pins reset_pl/dcm_locked]
connect_bd_net [get_bd_pins constant_low/dout] [get_bd_pins reset_pl/aux_reset_in] \
    [get_bd_pins reset_pl/mb_debug_sys_rst] \
    [get_bd_pins ps7/S_AXI_HP0_RDISSUECAP1_EN] [get_bd_pins ps7/S_AXI_HP0_WRISSUECAP1_EN] \
    [get_bd_pins ps7/S_AXI_HP1_RDISSUECAP1_EN] [get_bd_pins ps7/S_AXI_HP1_WRISSUECAP1_EN]
connect_bd_net [get_bd_pins reset_pl/interconnect_aresetn] [get_bd_pins control_fabric/aresetn]
connect_bd_net [get_bd_pins reset_pl/peripheral_aresetn] \
    [get_bd_pins axi_dma_0/axi_resetn] [get_bd_pins hp0_protocol/aresetn] \
    [get_bd_pins hp1_fabric/ARESETN] [get_bd_pins hp1_fabric/S00_ARESETN] [get_bd_pins hp1_fabric/M00_ARESETN] \
    [get_bd_pins image_pipeline/aresetn]

assign_bd_address -offset 0x40400000 -range 64K -target_address_space [get_bd_addr_spaces ps7/Data] \
    [get_bd_addr_segs axi_dma_0/S_AXI_LITE/Reg]
set image_segments [get_bd_addr_segs image_pipeline/s_axi/*]
if {[llength $image_segments] != 1} {error "Expected one image-pipeline AXI-Lite segment"}
assign_bd_address -offset 0x43C20000 -range 64K -target_address_space [get_bd_addr_spaces ps7/Data] $image_segments
assign_bd_address -offset 0 -range 512M -target_address_space [get_bd_addr_spaces axi_dma_0/Data_MM2S] \
    [get_bd_addr_segs ps7/S_AXI_HP0/HP0_DDR_LOWOCM]
assign_bd_address -offset 0 -range 512M -target_address_space [get_bd_addr_spaces axi_dma_0/Data_S2MM] \
    [get_bd_addr_segs ps7/S_AXI_HP1/HP1_DDR_LOWOCM]

save_bd_design
wo15_capture bd_validate [list validate_bd_design]
save_bd_design

set audit [ipcfg::audit_requirements]
puts $audit
if {![string match {REQ:OK *} $audit]} {error "WO15_REQUIREMENT_AUDIT_FAILED:$audit"}

set interface_readback [dict create]
foreach {name width protocol} {
    axi_dma_0/M_AXI_MM2S 64 AXI4
    hp0_protocol/S_AXI 64 AXI4
    hp0_protocol/M_AXI 64 AXI3
    ps7/S_AXI_HP0 64 AXI3
    axi_dma_0/M_AXI_S2MM 32 AXI4
    hp1_fabric/S00_AXI 32 AXI4
    hp1_fabric/M00_AXI 32 AXI3
    ps7/S_AXI_HP1 32 AXI3
} {
    dict set interface_readback $name [wo15_check_axi_interface $name $width $protocol]
}
dict set interface_readback image_pipeline/m_axis [wo15_check_axis_interface image_pipeline/m_axis 4]
dict set interface_readback axi_dma_0/S_AXIS_S2MM [wo15_check_axis_interface axi_dma_0/S_AXIS_S2MM 4]

set readback [dict create clock_mhz 50 packet_bytes 76800 packet_beats 19200 \
    hp0_memory_width_bits 64 hp1_memory_width_bits 32 dma_params [dict create]]
foreach key {c_include_sg c_include_mm2s c_include_s2mm c_micro_dma c_sg_length_width c_addr_width \
             c_m_axi_mm2s_data_width c_m_axis_mm2s_tdata_width c_include_mm2s_dre c_mm2s_burst_size \
             c_m_axi_s2mm_data_width c_s_axis_s2mm_tdata_width c_include_s2mm_dre c_s2mm_burst_size} {
    dict set readback dma_params $key [get_property CONFIG.$key [get_bd_cells axi_dma_0]]
}
foreach key {PCW_USE_S_AXI_HP0 PCW_S_AXI_HP0_DATA_WIDTH PCW_USE_S_AXI_HP1 PCW_S_AXI_HP1_DATA_WIDTH PCW_FPGA0_PERIPHERAL_FREQMHZ} {
    dict set readback ps7_params $key [get_property CONFIG.$key [get_bd_cells ps7]]
}
dict set readback interfaces $interface_readback
if {[dict get $readback dma_params c_include_s2mm] != 1 ||
    [dict get $readback dma_params c_sg_length_width] != 18 ||
    [dict get $readback dma_params c_m_axi_s2mm_data_width] != 32 ||
    [dict get $readback dma_params c_s_axis_s2mm_tdata_width] != 32 ||
    [dict get $readback dma_params c_m_axi_mm2s_data_width] != 64 ||
    [dict get $readback dma_params c_m_axis_mm2s_tdata_width] != 32} {
    error "WO15_DMA_READBACK_MISMATCH"
}
puts [ipcfg::write_evidence [file join $wo15_output reports parameter-readback.tcldict] $readback]

set wo15_coverage_ledger {
    {MM2S channel enabled} CONFIG.c_include_mm2s applied
    {S2MM channel enabled} CONFIG.c_include_s2mm applied
    {simple direct-register mode, SG disabled} CONFIG.c_include_sg applied
    {Micro DMA disabled} CONFIG.c_micro_dma applied
    {18-bit MM2S/S2MM buffer length register} CONFIG.c_sg_length_width applied
    {32-bit DMA address width} CONFIG.c_addr_width applied
    {MM2S memory width 64 bits and stream width 32 bits} CONFIG.c_m_axi_mm2s_data_width applied
    {S2MM memory width 32 bits and stream width 32 bits} CONFIG.c_m_axi_s2mm_data_width applied
    {S2MM DRE disabled} CONFIG.c_include_s2mm_dre applied
    {PS HP0 remains 64-bit for MM2S} CONFIG.PCW_S_AXI_HP0_DATA_WIDTH applied
    {PS HP1 is enabled at 32-bit for S2MM} CONFIG.PCW_S_AXI_HP1_DATA_WIDTH applied
    {HP1 AXI4 SI to AXI3 MI through 1x1 AXI Interconnect} hp1_fabric/S00_AXI->M00_AXI applied
    {FCLK_CLK0 is 50 MHz} CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ applied
}
set coverage [ipcfg::coverage_report $wo15_coverage_ledger]
puts $coverage
if {![string match {COVERAGE:FULL*} $coverage]} {error "WO15_REQUIREMENT_COVERAGE_FAILED:$coverage"}
set coverage_path [file join $wo15_output reports ip-requirement-coverage.txt]
set coverage_channel [open $coverage_path {WRONLY CREAT EXCL}]
puts $coverage_channel $coverage
close $coverage_channel

set wo15_bd_file [get_files -quiet */${wo15_bd_name}.bd]
if {[llength $wo15_bd_file] != 1} {error "Expected exactly one WO15 Block Design"}
generate_target all $wo15_bd_file
set wrapper [make_wrapper -files $wo15_bd_file -top]
add_files -norecurse $wrapper
set_property top ${wo15_bd_name}_wrapper [current_fileset]
update_compile_order -fileset sources_1
set recreate_path [file join $wo15_output export wo15_recreate.tcl]
write_bd_tcl -force $recreate_path
puts "WO15_BD_READY clock=50 project=$wo15_project_file bd=$wo15_bd_name wrapper=$wrapper build_started=false"
