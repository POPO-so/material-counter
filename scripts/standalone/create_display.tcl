# G1 independent HDMI OUT candidate, no RLE integration or automatic deployment.
# Source in a fresh Vivado 2026.1 session. Dependencies are pinned in video_sources.lock.json.
set repo [file normalize [file join [file dirname [info script]] ../..]]
foreach name {PYNQ_SOURCE VIDEO_IP_BUILT DISPLAY_OUT DISPLAY_NATIVE_LOG} {
    if {![info exists ::env($name)]} {error "Missing environment variable $name"}
}
set out [file normalize $::env(DISPLAY_OUT)]
set project_file [file join $out project display.xpr]
if {[file exists $project_file] && ![info exists ::env(DISPLAY_RESUME)]} {error "Use a fresh DISPLAY_OUT or explicit DISPLAY_RESUME"}
if {[llength [get_projects -quiet]]} {error "Use a fresh Vivado session"}
file mkdir $out
file mkdir [file join $out evidence]
set helper [file join $::env(USERPROFILE) .codex skills vivado-ip-configurator lib ipcfg.tcl]
source -notrace $helper
set ::ipcfg::native_log_path [file normalize $::env(DISPLAY_NATIVE_LOG)]
puts [ipcfg::python_status]
puts [ipcfg::python_script [file join $repo scripts standalone verify_sources.py] --source-root $::env(PYNQ_SOURCE) --brief]

proc capture {label cmd {expected {}}} {
    global out
    set receipt [ipcfg::capture [file join $out evidence ${label}.[clock clicks].tcldict] $cmd $expected]
    puts $receipt
    if {[dict get $receipt status] ne "OK"} {error "DISPLAY_STAGE_FAILED:$label"}
}
if {[file exists $project_file]} {open_project $project_file} else {
    create_project display [file join $out project] -part xc7z020clg400-1
}
set board_paths [get_param board.repoPaths]
if {[lsearch -exact $board_paths C:/FPGA/vivado_board_repos] < 0} {lappend board_paths C:/FPGA/vivado_board_repos}
set_param board.repoPaths $board_paths
set boards [get_board_parts -quiet *pynq-z2*]
if {[llength $boards] != 1} {error "PYNQ-Z2 board preset is ambiguous or missing: $boards"}
set_property board_part [lindex $boards 0] [current_project]
set_property ip_repo_paths [list [file join $::env(PYNQ_SOURCE) boards ip] \
    [file join $::env(VIDEO_IP_BUILT) color_convert solution1 impl ip] \
    [file join $::env(VIDEO_IP_BUILT) pixel_unpack solution1 impl ip]] [current_project]
update_ip_catalog
add_files [file join $repo fpga experimental standalone video_frame_monitor.v]
update_compile_order -fileset sources_1
set bd_files [get_files -quiet */display.bd]
if {[llength $bd_files]} {open_bd_design [lindex $bd_files 0]} else {create_bd_design display}
if {[llength [get_bd_cells -quiet ps7]]} {error "Partial design exists; resume in its saved session without recreating IP"}
capture ps_create {ipcfg::create_cell xilinx.com:ip:processing_system7 ps7} {SUCCESS:created}
capture ps_preset {ipcfg::run_automation ps7 xilinx.com:bd_rule:processing_system7 \
    [dict create CONFIG [dict create apply_board_preset 1 make_external {FIXED_IO, DDR}]]} {AUTOMATION:OK}
capture ps_config {ipcfg::require ps7 [dict create \
    CONFIG.PCW_USE_M_AXI_GP0 1 CONFIG.PCW_USE_S_AXI_HP0 1 CONFIG.PCW_S_AXI_HP0_DATA_WIDTH 64 \
    CONFIG.PCW_USE_S_AXI_HP1 1 CONFIG.PCW_S_AXI_HP1_DATA_WIDTH 32 \
    CONFIG.PCW_USE_S_AXI_HP2 1 CONFIG.PCW_S_AXI_HP2_DATA_WIDTH 32 \
    CONFIG.PCW_EN_CLK0_PORT 1 CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ 50 \
    CONFIG.PCW_EN_CLK1_PORT 1 CONFIG.PCW_FPGA1_PERIPHERAL_FREQMHZ 100 \
    CONFIG.PCW_EN_CLK2_PORT 1 CONFIG.PCW_FPGA2_PERIPHERAL_FREQMHZ 142.857 \
    CONFIG.PCW_USE_FABRIC_INTERRUPT 1 CONFIG.PCW_IRQ_F2P_INTR 1] \
    {G1 independent video reference; FCLK0/HP0/HP1 reserved for the later RLE integration}} {REQ:pinned+applied}

# Import only two unmodified official procedure definitions. Do not execute its root design.
set fd [open [file join $::env(PYNQ_SOURCE) boards Pynq-Z2 base base.tcl]]
set base [read $fd]; close $fd
foreach proc_name {create_hier_cell_frontend_1 create_hier_cell_hdmi_out} {
    set start [string first "proc $proc_name " $base]
    set end [string first "\nproc " $base [expr {$start + 5}]]
    if {$start < 0 || $end < 0} {error "Pinned reference procedure missing: $proc_name"}
    eval [string range $base $start [expr {$end - 1}]]
}
capture official_hdmi {create_hier_cell_hdmi_out / hdmi_out}
capture vdma {ipcfg::create_cell_cfg xilinx.com:ip:axi_vdma axi_vdma [dict create \
    CONFIG.c_include_s2mm 0 CONFIG.c_include_mm2s 1 CONFIG.c_num_fstores 4 \
    CONFIG.c_m_axi_mm2s_data_width 32 CONFIG.c_m_axis_mm2s_tdata_width 32 \
    CONFIG.c_mm2s_genlock_mode 1 CONFIG.c_mm2s_linebuffer_depth 512 \
    CONFIG.c_mm2s_max_burst_length 32]} {SUCCESS:created+configured}
capture control {ipcfg::create_cell_cfg xilinx.com:ip:axi_interconnect control [dict create CONFIG.NUM_SI 1 CONFIG.NUM_MI 9]} {SUCCESS:created+configured}
capture memory {ipcfg::create_cell_cfg xilinx.com:ip:axi_interconnect memory [dict create CONFIG.NUM_SI 1 CONFIG.NUM_MI 1]} {SUCCESS:created+configured}
capture buttons {ipcfg::create_cell_cfg xilinx.com:ip:axi_gpio btns_gpio [dict create CONFIG.C_GPIO_WIDTH 4 CONFIG.C_ALL_INPUTS 1]} {SUCCESS:created+configured}
capture intc {ipcfg::create_cell_cfg xilinx.com:ip:axi_intc system_interrupts [dict create CONFIG.C_HAS_FAST 0 CONFIG.C_IRQ_CONNECTION 1]} {SUCCESS:created+configured}
capture concat {ipcfg::create_cell_cfg xilinx.com:ip:xlconcat video_interrupts [dict create CONFIG.NUM_PORTS 3]} {SUCCESS:created+configured}
capture constant {ipcfg::create_cell_cfg xilinx.com:ip:xlconstant one [dict create CONFIG.CONST_VAL 1 CONFIG.CONST_WIDTH 1]} {SUCCESS:created+configured}
foreach suffix {control stream pixel} {
    capture reset_$suffix [list ipcfg::create_cell_cfg xilinx.com:ip:proc_sys_reset reset_$suffix [dict create CONFIG.C_EXT_RESET_HIGH 0]] {SUCCESS:created+configured}
    connect_bd_net [get_bd_pins ps7/FCLK_RESET0_N] [get_bd_pins reset_$suffix/ext_reset_in]
}
# Module-reference inference resets Vivado's message counters. Preserve its native
# log and inspect inferred interfaces directly rather than treating that reset as
# a trustworthy IP-configurator severity delta.
create_bd_cell -type module -reference video_frame_monitor frame_monitor
if {[get_property CONFIG.PROTOCOL [get_bd_intf_pins frame_monitor/S_AXI]] ne "AXI4LITE" ||
    [get_property CONFIG.DATA_WIDTH [get_bd_intf_pins frame_monitor/S_AXI]] != 32} {error "MONITOR_AXI_INFERENCE_FAILED"}
connect_bd_net [get_bd_pins one/dout] [get_bd_pins reset_control/dcm_locked] [get_bd_pins reset_stream/dcm_locked]
connect_bd_net [get_bd_pins hdmi_out/frontend/axi_dynclk/LOCKED_O] [get_bd_pins reset_pixel/dcm_locked]
connect_bd_net [get_bd_pins ps7/FCLK_CLK1] [get_bd_pins reset_control/slowest_sync_clk] \
    [get_bd_pins ps7/M_AXI_GP0_ACLK] [get_bd_pins control/ACLK] [get_bd_pins control/S00_ACLK] \
    [get_bd_pins axi_vdma/s_axi_lite_aclk] [get_bd_pins hdmi_out/clk_100M] \
    [get_bd_pins btns_gpio/s_axi_aclk] [get_bd_pins system_interrupts/s_axi_aclk] [get_bd_pins frame_monitor/s_axi_aclk]
connect_bd_net [get_bd_pins ps7/FCLK_CLK2] [get_bd_pins reset_stream/slowest_sync_clk] \
    [get_bd_pins ps7/S_AXI_HP2_ACLK] [get_bd_pins memory/ACLK] [get_bd_pins memory/S00_ACLK] [get_bd_pins memory/M00_ACLK] \
    [get_bd_pins axi_vdma/m_axi_mm2s_aclk] [get_bd_pins axi_vdma/m_axis_mm2s_aclk] [get_bd_pins hdmi_out/clk_142M]
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins ps7/S_AXI_HP0_ACLK] [get_bd_pins ps7/S_AXI_HP1_ACLK]
connect_bd_net [get_bd_pins hdmi_out/frontend/axi_dynclk/PXL_CLK_O] [get_bd_pins reset_pixel/slowest_sync_clk] [get_bd_pins frame_monitor/pixel_clk]
connect_bd_net [get_bd_pins reset_pixel/peripheral_aresetn] [get_bd_pins frame_monitor/pixel_resetn]
connect_bd_net [get_bd_pins reset_pixel/peripheral_reset] [get_bd_pins hdmi_out/frontend/v_axi4s_vid_out_0/vid_io_out_reset]
connect_bd_net [get_bd_pins reset_stream/peripheral_aresetn] [get_bd_pins hdmi_out/frontend/v_axi4s_vid_out_0/aresetn]
connect_bd_net [get_bd_pins reset_control/interconnect_aresetn] [get_bd_pins control/ARESETN]
connect_bd_net [get_bd_pins reset_control/peripheral_aresetn] [get_bd_pins control/S00_ARESETN] \
    [get_bd_pins axi_vdma/axi_resetn] [get_bd_pins hdmi_out/periph_resetn_clk100M] \
    [get_bd_pins btns_gpio/s_axi_aresetn] [get_bd_pins system_interrupts/s_axi_aresetn] [get_bd_pins frame_monitor/s_axi_aresetn]
connect_bd_net [get_bd_pins reset_stream/interconnect_aresetn] [get_bd_pins memory/ARESETN]
connect_bd_net [get_bd_pins reset_stream/peripheral_aresetn] [get_bd_pins memory/S00_ARESETN] [get_bd_pins memory/M00_ARESETN] [get_bd_pins hdmi_out/periph_resetn_clk142M]
connect_bd_intf_net [get_bd_intf_pins ps7/M_AXI_GP0] [get_bd_intf_pins control/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_vdma/M_AXI_MM2S] [get_bd_intf_pins memory/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins memory/M00_AXI] [get_bd_intf_pins ps7/S_AXI_HP2]
connect_bd_intf_net [get_bd_intf_pins axi_vdma/M_AXIS_MM2S] [get_bd_intf_pins hdmi_out/in_stream]
set targets {axi_vdma/S_AXI_LITE hdmi_out/S01_AXILite hdmi_out/S03_AXILite hdmi_out/S04_AXILite hdmi_out/S02_AXILite hdmi_out/S00_AXILite btns_gpio/S_AXI system_interrupts/s_axi frame_monitor/S_AXI}
for {set i 0} {$i < [llength $targets]} {incr i} {
    set port [format M%02d $i]
    connect_bd_intf_net [get_bd_intf_pins control/${port}_AXI] [get_bd_intf_pins [lindex $targets $i]]
    set stream_control [expr {$i == 1 || $i == 2}]
    set clk [expr {$stream_control ? "ps7/FCLK_CLK2" : "ps7/FCLK_CLK1"}]
    set reset [expr {$stream_control ? "reset_stream/peripheral_aresetn" : "reset_control/peripheral_aresetn"}]
    connect_bd_net [get_bd_pins $clk] [get_bd_pins control/${port}_ACLK]
    connect_bd_net [get_bd_pins $reset] [get_bd_pins control/${port}_ARESETN]
}
foreach {source dest} {
    axi_vdma/mm2s_introut video_interrupts/In0
    hdmi_out/vtc_out_irq video_interrupts/In1
    hdmi_out/hdmi_out_hpd_irq video_interrupts/In2
    video_interrupts/dout system_interrupts/intr
    system_interrupts/irq ps7/IRQ_F2P
} {connect_bd_net [get_bd_pins $source] [get_bd_pins $dest]}
# Tapping individual signals overrides their interface connection in Vivado.
# Explicitly retain the original TMDS-path receiver as well as the observer.
foreach {source original observer} {
    vid_data pixel_in vid_data
    vid_active_video vde_in vid_active
    vid_vsync vsync_in vid_vsync
} {
    connect_bd_net [get_bd_pins hdmi_out/frontend/v_axi4s_vid_out_0/$source] \
        [get_bd_pins hdmi_out/frontend/color_swap_0/$original] [get_bd_pins frame_monitor/$observer]
}
make_bd_intf_pins_external [get_bd_intf_pins hdmi_out/TMDS_out]
make_bd_pins_external [get_bd_pins hdmi_out/hdmi_out_hpd]
make_bd_pins_external [get_bd_pins btns_gpio/gpio_io_i]
set_property name hdmi_out [get_bd_intf_ports TMDS_out_0]
set_property name hdmi_out_hpd [get_bd_ports hdmi_out_hpd_0]
set_property name btns_4bits_tri_i [get_bd_ports gpio_io_i_0]
foreach {offset seg} {
    0x43000000 axi_vdma/S_AXI_LITE/Reg
    0x43c10000 hdmi_out/frontend/axi_dynclk/s00_axi/reg0
    0x43c30000 hdmi_out/frontend/vtc_out/ctrl/Reg
    0x43c60000 hdmi_out/color_convert/s_axi_control/Reg
    0x43c70000 hdmi_out/pixel_unpack/s_axi_control/Reg
    0x41210000 hdmi_out/frontend/hdmi_out_hpd_video/S_AXI/Reg
    0x41200000 btns_gpio/S_AXI/Reg
    0x40000000 system_interrupts/S_AXI/Reg
    0x43c40000 frame_monitor/S_AXI/reg0
} {assign_bd_address -offset $offset -range 0x10000 -target_address_space [get_bd_addr_spaces ps7/Data] [get_bd_addr_segs $seg]}
assign_bd_address -offset 0 -range 0x20000000 -target_address_space [get_bd_addr_spaces axi_vdma/Data_MM2S] [get_bd_addr_segs ps7/S_AXI_HP2/HP2_DDR_LOWOCM]
capture validate {validate_bd_design}
save_bd_design
generate_target all [get_files display.bd]
add_files [make_wrapper -files [get_files display.bd] -top]
set_property top display_wrapper [current_fileset]
set fd [open [file join $::env(PYNQ_SOURCE) boards Pynq-Z2 base vivado constraints base.xdc]]
set xdc [read $fd]; close $fd
set selected [open [file join $out display-pins.xdc] w]
set sw0_candidate [expr {[info exists ::env(SW0_CANDIDATE)] && $::env(SW0_CANDIDATE) eq "1"}]
set include_switches [expr {[info exists ::env(OFFLOAD_CANDIDATE)] && $::env(OFFLOAD_CANDIDATE) eq "1" && !$sw0_candidate}]
foreach line [split $xdc "\n"] {
    if {[regexp {get_ports.*(hdmi_out|btns_4bits_tri_i)} $line] ||
        ($include_switches && [regexp {get_ports.*sws_2bits_tri_i} $line])} {puts $selected $line}
}
close $selected
add_files -fileset constrs_1 [file join $out display-pins.xdc]
update_compile_order -fileset sources_1
puts "DISPLAY_BD_CREATED:build_and_hardware_gates_pending"
