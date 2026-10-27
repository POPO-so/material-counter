# Create a fresh WO-0015 project and generate BD/module-wrapper targets only.
# This file intentionally does not launch synthesis, implementation, or bitgen.

set wo15_script_dir [file dirname [file normalize [info script]]]
if {![info exists wo15_repo]} {
    set wo15_repo [file normalize [file join $wo15_script_dir ../..]]
}
set wo15_repo [file normalize $wo15_repo]
set wo15_expected_output [file normalize [file join $wo15_repo data results wo0015-hw-50mhz-20261015]]
if {![info exists wo15_output]} {set wo15_output $wo15_expected_output}
set wo15_output [file normalize $wo15_output]
if {$wo15_output ne $wo15_expected_output} {
    error "WO15_OUTPUT_MUST_BE:$wo15_expected_output"
}
if {![info exists wo15_clock_mhz]} {set wo15_clock_mhz 50}
if {$wo15_clock_mhz != 50} {error "WO15_FIXED_CLOCK_MHZ:50"}
set wo15_project_name wo15_50m
set wo15_bd_name wo15
set wo15_project_dir [file join $wo15_output project]
set wo15_project_file [file join $wo15_project_dir ${wo15_project_name}.xpr]

if {[llength [get_projects -quiet]]} {
    error "Use a fresh Vivado session with no open project"
}
if {[file exists $wo15_project_file]} {
    error "WO15 project already exists; use a fresh output directory: $wo15_project_file"
}
foreach relative_dir {project reports export ipcfg_evidence rtl} {
    set path [file join $wo15_output $relative_dir]
    if {[file exists $path] && [llength [glob -nocomplain -directory $path *]]} {
        error "WO15 output subdirectory is not empty: $path"
    }
    file mkdir $path
}

# The Manager may preload the helper and bind its exact native Vivado log path.
if {![llength [info commands ipcfg::capture]]} {
    if {[info exists wo15_ipcfg_path]} {
        set wo15_helper $wo15_ipcfg_path
    } elseif {[info exists ::env(WO15_IPCFG_PATH)]} {
        set wo15_helper $::env(WO15_IPCFG_PATH)
    } else {
        set wo15_helper {C:/fpga/material-counter/.agents/skills/vivado-ip-configurator/lib/ipcfg.tcl}
    }
    if {![file isfile $wo15_helper]} {error "IPCFG_HELPER_MISSING:$wo15_helper"}
    source -notrace [file normalize $wo15_helper]
}
if {![info exists ::ipcfg::native_log_path] || $::ipcfg::native_log_path eq ""} {
    error "Bind the active Vivado session's exact native log path before sourcing this script"
}

proc wo15_capture {label command {expected {}}} {
    global wo15_output
    set path [file join $wo15_output ipcfg_evidence "$label.[pid].[clock clicks].tcldict"]
    set receipt [ipcfg::capture $path $command $expected]
    puts $receipt
    if {[dict get $receipt status] ne "OK"} {
        error "WO15_STAGE_FAIL:$label:$path"
    }
    return $receipt
}

proc wo15_check_axi_interface {name width protocol} {
    set pins [get_bd_intf_pins -quiet $name]
    if {[llength $pins] != 1} {error "WO15_INTERFACE_MISSING:$name"}
    set actual_width [string trim [get_property CONFIG.DATA_WIDTH $pins]]
    set actual_protocol [string trim [get_property CONFIG.PROTOCOL $pins]]
    if {$actual_width ne "$width" || $actual_protocol ne $protocol} {
        error "WO15_INTERFACE_MISMATCH:$name width=$actual_width/$width protocol=$actual_protocol/$protocol"
    }
    return [dict create data_width $actual_width protocol $actual_protocol]
}

proc wo15_check_axis_interface {name bytes} {
    set pins [get_bd_intf_pins -quiet $name]
    if {[llength $pins] != 1} {error "WO15_INTERFACE_MISSING:$name"}
    set readback [dict create]
    foreach {property wanted} [list \
        CONFIG.TDATA_NUM_BYTES $bytes \
        CONFIG.HAS_TKEEP 1 \
        CONFIG.HAS_TLAST 1 \
        CONFIG.HAS_TREADY 1] {
        set actual [string trim [get_property $property $pins]]
        if {$actual ne "$wanted"} {error "WO15_AXIS_INTERFACE_MISMATCH:$name $property=$actual expected=$wanted"}
        dict set readback $property $actual
    }
    return $readback
}

# Use an existing board repository when already configured. Append the supplied
# path only when the PYNQ-Z2 preset is not currently visible.
set wo15_board_paths_before [get_param board.repoPaths]
set wo15_board_paths_changed 0
if {![llength [get_board_parts tul.com.tw:pynq-z2:part0:1.0]]} {
    if {![info exists wo15_board_repo] || $wo15_board_repo eq ""} {
        error "PYNQ-Z2 board preset unavailable; set wo15_board_repo before sourcing"
    }
    set board_paths [get_param board.repoPaths]
    set wo15_board_repo [file normalize $wo15_board_repo]
    if {[lsearch -exact $board_paths $wo15_board_repo] < 0} {
        lappend board_paths $wo15_board_repo
        set_param board.repoPaths $board_paths
        set wo15_board_paths_changed 1
    }
}
if {![llength [get_board_parts tul.com.tw:pynq-z2:part0:1.0]]} {
    error "PYNQ-Z2 board preset still unavailable after repository lookup"
}

try {
    create_project $wo15_project_name $wo15_project_dir -part xc7z020clg400-1
    set_property board_part tul.com.tw:pynq-z2:part0:1.0 [current_project]
    set_property target_language Verilog [current_project]
} finally {
    if {$wo15_board_paths_changed} {set_param board.repoPaths $wo15_board_paths_before}
}

# These are the Gray8 WO-0014 processing RTL sources plus the WO-0015 packer
# and wrapper. The old WO-0014 BD wrapper and RGB2Gray stage are excluded.
set wo15_sources {
    fpga/rtl/axis_gray2binary.v
    fpga/experimental/wo0014/axis_dma_gray_adapter.v
    fpga/experimental/wo0014/axis_binary_output_checker.v
    fpga/experimental/wo0014/axis_dma_image_pipeline.v
    fpga/experimental/wo0015/axis_s2mm_frame_packer.v
    fpga/experimental/wo0015/axis_dma_s2mm_pipeline_bd.v
}
foreach source $wo15_sources {
    set original [file join $wo15_repo $source]
    if {![file isfile $original]} {error "WO15_SOURCE_MISSING:$source"}
    set staged [file join $wo15_output rtl [file tail $source]]
    if {[file exists $staged]} {error "WO15_SOURCE_SNAPSHOT_EXISTS:$staged"}
    file copy $original $staged
    add_files -norecurse $staged
}

create_bd_design $wo15_bd_name
wo15_capture ps_create [list ipcfg::create_cell xilinx.com:ip:processing_system7 ps7] {SUCCESS:created}
wo15_capture ps_preset [list ipcfg::run_automation ps7 xilinx.com:bd_rule:processing_system7 \
    [dict create CONFIG [dict create apply_board_preset 1 make_external {FIXED_IO, DDR}]]] {AUTOMATION:OK}

set ps_requirements [dict create \
    CONFIG.PCW_USE_M_AXI_GP0 1 \
    CONFIG.PCW_USE_S_AXI_HP0 1 CONFIG.PCW_S_AXI_HP0_DATA_WIDTH 64 \
    CONFIG.PCW_USE_S_AXI_HP1 1 CONFIG.PCW_S_AXI_HP1_DATA_WIDTH 32 \
    CONFIG.PCW_EN_CLK0_PORT 1 CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ 50]
wo15_capture ps_ports [list ipcfg::require ps7 $ps_requirements \
    {WO-0015 keeps MM2S on 64-bit HP0 and enables the 32-bit HP1 S2MM path at 50 MHz} {} \
    {WO-0015 approved S2MM memory width 32 and PS HP1 width 32; MM2S HP0 remains 64-bit}] {REQ:pinned+applied}

wo15_capture dma_create [list ipcfg::create_cell xilinx.com:ip:axi_dma axi_dma_0] {SUCCESS:created}
set dma_requirements [dict create \
    CONFIG.c_include_sg 0 CONFIG.c_include_mm2s 1 CONFIG.c_include_s2mm 1 \
    CONFIG.c_micro_dma 0 CONFIG.c_sg_length_width 18 CONFIG.c_addr_width 32 \
    CONFIG.c_m_axi_mm2s_data_width 64 CONFIG.c_m_axis_mm2s_tdata_width 32 \
    CONFIG.c_include_mm2s_dre 0 CONFIG.c_mm2s_burst_size 16 \
    CONFIG.c_m_axi_s2mm_data_width 32 CONFIG.c_s_axis_s2mm_tdata_width 32 \
    CONFIG.c_include_s2mm_dre 0 CONFIG.c_s2mm_burst_size 16]
wo15_capture dma_parameters [list ipcfg::require axi_dma_0 $dma_requirements \
    {WO-0015 direct-register DMA: MM2S 64/32, S2MM 32/32, 18-bit length, 32-bit address, no SG/Micro/DRE}] {REQ:pinned+applied}

wo15_capture control_fabric_create [list ipcfg::create_cell_cfg xilinx.com:ip:smartconnect control_fabric \
    [dict create CONFIG.NUM_SI 1 CONFIG.NUM_MI 2 CONFIG.NUM_CLKS 1 CONFIG.HAS_ARESETN 1]] {SUCCESS:created+configured}
wo15_capture hp0_converter_create [list ipcfg::create_cell_cfg xilinx.com:ip:axi_protocol_converter hp0_protocol \
    [dict create CONFIG.SI_PROTOCOL AXI4 CONFIG.MI_PROTOCOL AXI3 CONFIG.DATA_WIDTH 64 CONFIG.TRANSLATION_MODE 2]] {SUCCESS:created+configured}

wo15_capture hp1_fabric_create [list ipcfg::create_cell xilinx.com:ip:axi_interconnect hp1_fabric] {SUCCESS:created}
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
    hp1_fabric/s00_couplers/auto_pc/S_AXI 32 AXI4
    hp1_fabric/s00_couplers/auto_pc/M_AXI 32 AXI3
    ps7/S_AXI_HP1 32 AXI3
} {
    dict set interface_readback $name [wo15_check_axi_interface $name $width $protocol]
}
dict set interface_readback image_pipeline/m_axis [wo15_check_axis_interface image_pipeline/m_axis 4]
dict set interface_readback axi_dma_0/S_AXIS_S2MM [wo15_check_axis_interface axi_dma_0/S_AXIS_S2MM 4]

set readback [dict create clock_mhz 50 \
    mm2s_payload_bytes 76800 mm2s_packet_bytes 76832 mm2s_packet_beats 19208 \
    s2mm_binary_bytes 76800 s2mm_binary_beats 19200 \
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
