# Fresh isolated PYNQ-Z2 experiment. Never sources or opens the WO-0013 project.
set wo14_repo [file normalize [file join [file dirname [info script]] ../..]]
if {![info exists wo14_output] || ![info exists wo14_clock_mhz]} {error "Set output and 50/100 MHz first"}
if {$wo14_clock_mhz ni {50 100}} {error "Only separate 50 and 100 MHz experiments are authorized"}
set wo14_output [file normalize $wo14_output]
set wo14_project_name wo0014_image_${wo14_clock_mhz}m
foreach dir {reports export ipcfg_evidence rtl} {file mkdir [file join $wo14_output $dir]}
if {[file exists [file join $wo14_output project ${wo14_project_name}.xpr]]} {error "Use a fresh output; project already exists"}
if {[llength [get_projects -quiet]]} {error "Use a fresh dedicated Vivado session"}
if {![llength [info commands ipcfg::capture]]} {
    source -notrace [file join $wo14_repo .agents skills vivado-ip-configurator lib ipcfg.tcl]
}
if {![info exists ::ipcfg::native_log_path]} {error "Bind the actual session native log"}
proc wo14_capture {label command expected} {
    global wo14_output
    set path [file join $wo14_output ipcfg_evidence "$label.[pid].[clock clicks].tcldict"]
    set receipt [ipcfg::capture $path $command $expected]
    puts $receipt
    if {[dict get $receipt status] ne "OK"} {error "WO14_STAGE_FAIL:$label:$path"}
}
create_project $wo14_project_name [file join $wo14_output project] -part xc7z020clg400-1
set_property board_part tul.com.tw:pynq-z2:part0:1.0 [current_project]
set_property target_language Verilog [current_project]
foreach source {
    fpga/rtl/axis_rgb2gray.v fpga/rtl/axis_gray2binary.v
    fpga/experimental/wo0014/axis_dma_rgb_adapter.v
    fpga/experimental/wo0014/axis_binary_output_checker.v
    fpga/experimental/wo0014/axis_dma_image_pipeline.v
    fpga/experimental/wo0014/axis_dma_image_pipeline_bd.v
} {
    set staged [file join $wo14_output rtl [file tail $source]]
    file copy [file join $wo14_repo $source] $staged
    add_files -norecurse $staged
}
create_bd_design wo0014_image
wo14_capture ps_create [list ipcfg::create_cell xilinx.com:ip:processing_system7 ps7] {SUCCESS:created}
wo14_capture ps_preset [list ipcfg::run_automation ps7 xilinx.com:bd_rule:processing_system7 \
    [dict create CONFIG [dict create apply_board_preset 1 make_external {FIXED_IO, DDR}]]] {AUTOMATION:OK}
wo14_capture ps_ports [list ipcfg::require ps7 [dict create \
    CONFIG.PCW_USE_M_AXI_GP0 1 CONFIG.PCW_USE_S_AXI_HP0 1 \
    CONFIG.PCW_S_AXI_HP0_DATA_WIDTH 64 CONFIG.PCW_EN_CLK0_PORT 1 \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ $wo14_clock_mhz] {PYNQ-Z2 preset, GP0 control, HP0 64-bit DDR, requested PL clock}] {REQ:pinned+applied}
wo14_capture dma_create [list ipcfg::create_cell_cfg xilinx.com:ip:axi_dma axi_dma_0 [dict create \
    CONFIG.c_include_sg 0 CONFIG.c_include_mm2s 1 CONFIG.c_include_s2mm 0 \
    CONFIG.c_micro_dma 0 CONFIG.c_sg_length_width 18 CONFIG.c_addr_width 32 \
    CONFIG.c_m_axi_mm2s_data_width 64 CONFIG.c_m_axis_mm2s_tdata_width 32 \
    CONFIG.c_include_mm2s_dre 0 CONFIG.c_mm2s_burst_size 16]] {SUCCESS:created+configured}
wo14_capture fabric_create [list ipcfg::create_cell_cfg xilinx.com:ip:smartconnect control_fabric \
    [dict create CONFIG.NUM_SI 1 CONFIG.NUM_MI 2 CONFIG.NUM_CLKS 1 CONFIG.HAS_ARESETN 1]] {SUCCESS:created+configured}
wo14_capture converter_create [list ipcfg::create_cell_cfg xilinx.com:ip:axi_protocol_converter hp0_protocol \
    [dict create CONFIG.SI_PROTOCOL AXI4 CONFIG.MI_PROTOCOL AXI3 CONFIG.DATA_WIDTH 64 CONFIG.TRANSLATION_MODE 2]] {SUCCESS:created+configured}
wo14_capture reset_create [list ipcfg::create_cell_cfg xilinx.com:ip:proc_sys_reset reset_pl \
    [dict create CONFIG.C_EXT_RESET_HIGH 0 CONFIG.C_AUX_RESET_HIGH 1]] {SUCCESS:created+configured}
wo14_capture high_create [list ipcfg::create_cell_cfg xilinx.com:ip:xlconstant constant_high \
    [dict create CONFIG.CONST_WIDTH 1 CONFIG.CONST_VAL 1]] {SUCCESS:created+configured}
wo14_capture low_create [list ipcfg::create_cell_cfg xilinx.com:ip:xlconstant constant_low \
    [dict create CONFIG.CONST_WIDTH 1 CONFIG.CONST_VAL 0]] {SUCCESS:created+configured}
create_bd_cell -type module -reference axis_dma_image_pipeline_bd image_pipeline
set_property -dict [list CONFIG.WIDTH 320 CONFIG.HEIGHT 240 CONFIG.THRESHOLD 128] [get_bd_cells image_pipeline]
connect_bd_intf_net [get_bd_intf_pins ps7/M_AXI_GP0] [get_bd_intf_pins control_fabric/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins control_fabric/M00_AXI] [get_bd_intf_pins axi_dma_0/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins control_fabric/M01_AXI] [get_bd_intf_pins image_pipeline/s_axi]
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_MM2S] [get_bd_intf_pins hp0_protocol/S_AXI]
connect_bd_intf_net [get_bd_intf_pins hp0_protocol/M_AXI] [get_bd_intf_pins ps7/S_AXI_HP0]
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXIS_MM2S] [get_bd_intf_pins image_pipeline/s_axis]
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] \
    [get_bd_pins ps7/M_AXI_GP0_ACLK] [get_bd_pins ps7/S_AXI_HP0_ACLK] \
    [get_bd_pins control_fabric/aclk] [get_bd_pins hp0_protocol/aclk] \
    [get_bd_pins axi_dma_0/s_axi_lite_aclk] [get_bd_pins axi_dma_0/m_axi_mm2s_aclk] \
    [get_bd_pins image_pipeline/aclk] [get_bd_pins reset_pl/slowest_sync_clk]
connect_bd_net [get_bd_pins ps7/FCLK_RESET0_N] [get_bd_pins reset_pl/ext_reset_in]
connect_bd_net [get_bd_pins constant_high/dout] [get_bd_pins reset_pl/dcm_locked]
connect_bd_net [get_bd_pins constant_low/dout] [get_bd_pins reset_pl/aux_reset_in] \
    [get_bd_pins reset_pl/mb_debug_sys_rst] [get_bd_pins ps7/S_AXI_HP0_RDISSUECAP1_EN] \
    [get_bd_pins ps7/S_AXI_HP0_WRISSUECAP1_EN]
connect_bd_net [get_bd_pins reset_pl/interconnect_aresetn] [get_bd_pins control_fabric/aresetn]
connect_bd_net [get_bd_pins reset_pl/peripheral_aresetn] \
    [get_bd_pins axi_dma_0/axi_resetn] [get_bd_pins hp0_protocol/aresetn] [get_bd_pins image_pipeline/aresetn]
assign_bd_address -offset 0x40400000 -range 64K -target_address_space [get_bd_addr_spaces ps7/Data] \
    [get_bd_addr_segs axi_dma_0/S_AXI_LITE/Reg]
set image_segments [get_bd_addr_segs image_pipeline/s_axi/*]
if {[llength $image_segments] != 1} {error "Expected one pipeline address segment"}
assign_bd_address -offset 0x43C20000 -range 64K -target_address_space [get_bd_addr_spaces ps7/Data] $image_segments
assign_bd_address -offset 0 -range 512M -target_address_space [get_bd_addr_spaces axi_dma_0/Data_MM2S] \
    [get_bd_addr_segs ps7/S_AXI_HP0/HP0_DDR_LOWOCM]
save_bd_design
wo14_capture bd_validate [list validate_bd_design] {}
save_bd_design
puts [ipcfg::audit_requirements]
set readback [dict create clock_mhz $wo14_clock_mhz packet_bytes 230432 max_dma_bytes 262143]
foreach key {c_include_sg c_include_mm2s c_include_s2mm c_micro_dma c_sg_length_width c_addr_width c_m_axi_mm2s_data_width c_m_axis_mm2s_tdata_width c_include_mm2s_dre c_mm2s_burst_size} {
    dict set readback $key [get_property CONFIG.$key [get_bd_cells axi_dma_0]]
}
if {[dict get $readback c_sg_length_width] != 18} {error "DMA length width readback mismatch"}
dict set readback ps_clock [get_property CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ [get_bd_cells ps7]]
if {[dict get $readback ps_clock] != $wo14_clock_mhz} {error "PS clock readback mismatch"}
puts [ipcfg::write_evidence [file join $wo14_output reports parameter-readback.tcldict] $readback]
set bd_file [get_files */wo0014_image.bd]
generate_target all $bd_file
set wrapper [make_wrapper -files $bd_file -top]
add_files -norecurse $wrapper
set_property top wo0014_image_wrapper [current_fileset]
update_compile_order -fileset sources_1
write_bd_tcl -force [file join $wo14_output export wo0014_image_recreate.tcl]
puts "WO0014_BD_READY clock=$wo14_clock_mhz project=[get_property DIRECTORY [current_project]] wrapper=$wrapper"
