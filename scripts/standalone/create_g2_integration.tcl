# Independent G2 RLE + HDMI block-design entrypoint.
# G1 create_display.tcl remains the source of the standalone display baseline.

set g2_script_dir [file dirname [file normalize [info script]]]
set g2_repo [file normalize [file join $g2_script_dir ../..]]
set g2_results [file normalize [file join $g2_repo data results]]
if {[info exists ::env(G2_OUT)]} {
    set g2_out [file normalize $::env(G2_OUT)]
} else {
    set g2_out [file join $g2_results standalone-20261030-g2-integration]
}
set g2_tail [file tail $g2_out]
if {![string equal -nocase [file dirname $g2_out] $g2_results] ||
    ![regexp {^standalone-[0-9]{8}(-[0-9]{6})?-g2-integration$} $g2_tail]} {
    error "G2_OUTPUT_MUST_BE_A_DEDICATED_G2_RESULTS_DIRECTORY:$g2_out"
}

set g2_project_file [file join $g2_out project display.xpr]
if {[file exists $g2_project_file]} {
    error "G2_PROJECT_EXISTS_USE_A_FRESH_TIMESTAMPED_G2_OUT:$g2_project_file"
}
foreach artifact [list [file join $g2_out export g2-integrated.bit] \
                       [file join $g2_out export g2-integrated.hwh]] {
    if {[file exists $artifact]} {error "G2_ARTIFACT_EXISTS_WILL_NOT_OVERWRITE:$artifact"}
}

proc g2_read_lower {path} {
    set channel [open $path r]
    set value [read $channel]
    close $channel
    return [string tolower $value]
}

proc g2_require_source_text {path needles} {
    if {![file isfile $path]} {error "G2_SOURCE_MISSING:$path"}
    set text [g2_read_lower $path]
    foreach needle $needles {
        if {[string first [string tolower $needle] $text] < 0} {
            error "G2_SOURCE_CONTRACT_CHANGED:$path:$needle"
        }
    }
}

set g2_display_tcl [file join $g2_repo scripts standalone create_display.tcl]
set g2_build_display_tcl [file join $g2_repo scripts standalone build_display.tcl]
set g2_gray8_tcl [file join $g2_repo scripts gray8 create_bd.tcl]
g2_require_source_text $g2_display_tcl {
    "CONFIG.NUM_MI 9" "PCW_FPGA0_PERIPHERAL_FREQMHZ 50"
    "PCW_FPGA1_PERIPHERAL_FREQMHZ 100" "PCW_FPGA2_PERIPHERAL_FREQMHZ 142.857"
    "ps7/s_axi_hp2" "axi_vdma/m_axi_mm2s" "0x43c40000"
    "axi_vdma/s_axi_lite" "hdmi_out/s01_axilite" "hdmi_out/s03_axilite"
    "hdmi_out/s04_axilite" "hdmi_out/s02_axilite" "hdmi_out/s00_axilite"
    "btns_gpio/s_axi" "system_interrupts/s_axi" "frame_monitor/s_axi"
    "0x43000000" "0x43c10000" "0x43c30000" "0x43c60000" "0x43c70000"
    "0x41210000" "0x41200000" "0x40000000"
}
g2_require_source_text $g2_build_display_tcl {"launch_runs synth_1" "launch_runs impl_1" "display_wrapper.bit"}
g2_require_source_text $g2_gray8_tcl {
    "0x40400000" "0x43c20000" "axi_dma_0/m_axi_mm2s" "ps7/s_axi_hp0"
    "axi_dma_0/m_axi_s2mm" "ps7/s_axi_hp1" "pcw_fpga0_peripheral_freqmhz 50"
}

set g2_rtl_sources {
    fpga/experimental/gray8/axis_dma_gray_adapter.v
    fpga/rtl/axis_morphology3x3.v
    fpga/experimental/rle/axis_rle_encoder.v
    fpga/experimental/rle/axis_dma_rle_pipeline_bd.v
}
foreach relative $g2_rtl_sources {
    set source [file join $g2_repo $relative]
    if {![file isfile $source]} {error "G2_RTL_SOURCE_MISSING:$relative"}
}

# Check all 11 control windows as 64-KiB intervals before Vivado mutates a project.
set g2_control_map {
    {VDMA 0x43000000} {dynamic-clock 0x43C10000} {VTC 0x43C30000}
    {color-convert 0x43C60000} {pixel-unpack 0x43C70000}
    {HDMI-HPD 0x41210000} {buttons 0x41200000} {video-INTC 0x40000000}
    {frame-monitor 0x43C40000} {RLE-DMA 0x40400000} {RLE-pipeline 0x43C20000}
}
if {[llength $g2_control_map] != 11} {error "G2_CONTROL_MAP_COUNT_MISMATCH"}
for {set i 0} {$i < [llength $g2_control_map]} {incr i} {
    lassign [lindex $g2_control_map $i] name address
    scan $address %x start
    set stop [expr {$start + 0x10000}]
    for {set j [expr {$i + 1}]} {$j < [llength $g2_control_map]} {incr j} {
        lassign [lindex $g2_control_map $j] other other_address
        scan $other_address %x other_start
        set other_stop [expr {$other_start + 0x10000}]
        if {$start < $other_stop && $other_start < $stop} {
            error "G2_AXIL_ADDRESS_OVERLAP:$name:$address:$other:$other_address"
        }
    }
}

puts "G2_PREFLIGHT_OUT=$g2_out"
puts "G2_PREFLIGHT_CONTROL_MI=M00-M08 preserved; M09=RLE DMA; M10=RLE pipeline"
puts "G2_PREFLIGHT_CLOCKS=GP0/S00 100MHz; RLE MI+HP0+HP1 FCLK0 50MHz; VDMA/HP2 FCLK2 142.857MHz"
puts "G2_PREFLIGHT_ADDRESSES=11 non-overlapping 64KiB control windows"
puts "G2_PREFLIGHT_G1_ENTRY=$g2_display_tcl"

set g2_dry_run 0
if {[info exists ::env(G2_DRY_RUN)] && $::env(G2_DRY_RUN) eq "1"} {set g2_dry_run 1}
if {$g2_dry_run} {
    puts "G2_STATIC_PREFLIGHT_PASS"
} else {
    foreach name {PYNQ_SOURCE VIDEO_IP_BUILT} {
        if {![info exists ::env($name)] || ![file isdirectory $::env($name)]} {
            error "G2_REQUIRED_DIRECTORY_MISSING:$name"
        }
    }
    if {[llength [get_projects -quiet]]} {error "G2_REQUIRES_A_FRESH_VIVADO_SESSION"}
    if {![file isfile [file join $g2_out decision-record.md]]} {
        error "G2_MANAGER_DECISION_RECORD_REQUIRED:$g2_out"
    }
    file mkdir [file join $g2_out rtl]
    file mkdir [file join $g2_out evidence]
    foreach relative $g2_rtl_sources {
        set source [file join $g2_repo $relative]
        set snapshot [file join $g2_out rtl [file tail $source]]
        if {[file exists $snapshot]} {error "G2_RTL_SNAPSHOT_EXISTS:$snapshot"}
    }

    set ::env(DISPLAY_OUT) $g2_out
    set ::env(DISPLAY_NATIVE_LOG) [file join $g2_out vivado-native.log]
    set ::env(DISPLAY_TAG) g2-integrated
    # ipcfg::capture snapshots this file before the first BD mutation.
    set native_log_channel [open $::env(DISPLAY_NATIVE_LOG) {WRONLY CREAT EXCL}]
    close $native_log_channel
    source $g2_display_tcl

    current_bd_design display
    set bd_file [get_files -quiet */display.bd]
    if {[llength $bd_file] != 1} {error "G2_EXPECTED_ONE_DISPLAY_BD:$bd_file"}
    foreach relative $g2_rtl_sources {
        set source [file join $g2_repo $relative]
        set snapshot [file join $g2_out rtl [file tail $source]]
        file copy $source $snapshot
        add_files -norecurse $snapshot
    }
    update_compile_order -fileset sources_1

    capture control_expand [list ipcfg::require control [dict create CONFIG.NUM_MI 11] \
        {Preserve display M00-M08 and append RLE DMA/pipeline M09-M10}] {REQ:pinned+applied}
    foreach {name vlnv cfg expected} {
        axi_dma_0 xilinx.com:ip:axi_dma \
            {CONFIG.c_include_sg 0 CONFIG.c_include_mm2s 1 CONFIG.c_include_s2mm 1 CONFIG.c_micro_dma 0 CONFIG.c_sg_length_width 18 CONFIG.c_addr_width 32 CONFIG.c_m_axi_mm2s_data_width 64 CONFIG.c_m_axis_mm2s_tdata_width 32 CONFIG.c_include_mm2s_dre 0 CONFIG.c_mm2s_burst_size 16 CONFIG.c_m_axi_s2mm_data_width 32 CONFIG.c_s_axis_s2mm_tdata_width 32 CONFIG.c_include_s2mm_dre 0 CONFIG.c_s2mm_burst_size 16} \
            {SUCCESS:created+configured} \
        hp0_protocol xilinx.com:ip:axi_protocol_converter \
            {CONFIG.SI_PROTOCOL AXI4 CONFIG.MI_PROTOCOL AXI3 CONFIG.DATA_WIDTH 64 CONFIG.TRANSLATION_MODE 2} \
            {SUCCESS:created+configured} \
        hp1_fabric xilinx.com:ip:axi_interconnect \
            {CONFIG.NUM_SI 1 CONFIG.NUM_MI 1} {SUCCESS:created+configured} \
        reset_rle xilinx.com:ip:proc_sys_reset \
            {CONFIG.C_EXT_RESET_HIGH 0 CONFIG.C_AUX_RESET_HIGH 1} {SUCCESS:created+configured} \
        zero xilinx.com:ip:xlconstant \
            {CONFIG.CONST_WIDTH 1 CONFIG.CONST_VAL 0} {SUCCESS:created+configured}
    } {
        capture ${name}_create [list ipcfg::create_cell_cfg $vlnv $name [dict create {*}$cfg]] $expected
    }

    create_bd_cell -type module -reference axis_dma_rle_pipeline_bd image_pipeline
    set_property -dict [list CONFIG.WIDTH 640 CONFIG.HEIGHT 400 CONFIG.MAX_RUNS 16384] [get_bd_cells image_pipeline]
    foreach {pin wanted} {CONFIG.WIDTH 640 CONFIG.HEIGHT 400 CONFIG.MAX_RUNS 16384} {
        if {[get_property $pin [get_bd_cells image_pipeline]] != $wanted} {
            error "G2_RLE_PIPELINE_PARAMETER_MISMATCH:$pin"
        }
    }

    connect_bd_intf_net [get_bd_intf_pins control/M09_AXI] [get_bd_intf_pins axi_dma_0/S_AXI_LITE]
    connect_bd_intf_net [get_bd_intf_pins control/M10_AXI] [get_bd_intf_pins image_pipeline/s_axi]
    connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_MM2S] [get_bd_intf_pins hp0_protocol/S_AXI]
    connect_bd_intf_net [get_bd_intf_pins hp0_protocol/M_AXI] [get_bd_intf_pins ps7/S_AXI_HP0]
    connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_S2MM] [get_bd_intf_pins hp1_fabric/S00_AXI]
    connect_bd_intf_net [get_bd_intf_pins hp1_fabric/M00_AXI] [get_bd_intf_pins ps7/S_AXI_HP1]
    connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXIS_MM2S] [get_bd_intf_pins image_pipeline/s_axis]
    connect_bd_intf_net [get_bd_intf_pins image_pipeline/m_axis] [get_bd_intf_pins axi_dma_0/S_AXIS_S2MM]

    connect_bd_net [get_bd_pins ps7/FCLK_RESET0_N] [get_bd_pins reset_rle/ext_reset_in]
    connect_bd_net [get_bd_pins one/dout] [get_bd_pins reset_rle/dcm_locked]
    connect_bd_net [get_bd_pins zero/dout] [get_bd_pins reset_rle/aux_reset_in] [get_bd_pins reset_rle/mb_debug_sys_rst]
    connect_bd_net [get_bd_pins ps7/FCLK_CLK0] \
        [get_bd_pins reset_rle/slowest_sync_clk] \
        [get_bd_pins control/M09_ACLK] [get_bd_pins control/M10_ACLK] \
        [get_bd_pins axi_dma_0/s_axi_lite_aclk] \
        [get_bd_pins axi_dma_0/m_axi_mm2s_aclk] [get_bd_pins axi_dma_0/m_axi_s2mm_aclk] \
        [get_bd_pins hp0_protocol/aclk] \
        [get_bd_pins hp1_fabric/ACLK] [get_bd_pins hp1_fabric/S00_ACLK] [get_bd_pins hp1_fabric/M00_ACLK] \
        [get_bd_pins image_pipeline/aclk]
    connect_bd_net [get_bd_pins reset_rle/peripheral_aresetn] \
        [get_bd_pins control/M09_ARESETN] [get_bd_pins control/M10_ARESETN] \
        [get_bd_pins axi_dma_0/axi_resetn] [get_bd_pins hp0_protocol/aresetn] \
        [get_bd_pins hp1_fabric/ARESETN] [get_bd_pins hp1_fabric/S00_ARESETN] [get_bd_pins hp1_fabric/M00_ARESETN] \
        [get_bd_pins image_pipeline/aresetn]

    foreach {offset segment} {
        0x40400000 axi_dma_0/S_AXI_LITE/Reg
        0x43C20000 image_pipeline/s_axi/*
    } {
        set segments [get_bd_addr_segs $segment]
        if {[llength $segments] != 1} {error "G2_EXPECTED_ONE_AXI_LITE_SEGMENT:$segment:$segments"}
        assign_bd_address -offset $offset -range 0x10000 -target_address_space [get_bd_addr_spaces ps7/Data] $segments
    }
    assign_bd_address -offset 0 -range 0x20000000 -target_address_space [get_bd_addr_spaces axi_dma_0/Data_MM2S] \
        [get_bd_addr_segs ps7/S_AXI_HP0/HP0_DDR_LOWOCM]
    assign_bd_address -offset 0 -range 0x20000000 -target_address_space [get_bd_addr_spaces axi_dma_0/Data_S2MM] \
        [get_bd_addr_segs ps7/S_AXI_HP1/HP1_DDR_LOWOCM]

    if {[get_property CONFIG.NUM_MI [get_bd_cells control]] != 11} {error "G2_CONTROL_MI_READBACK_FAILED"}
    foreach pin {control/M09_AXI control/M10_AXI} {
        if {![llength [get_bd_intf_pins -quiet $pin]]} {error "G2_CONTROL_MI_MISSING:$pin"}
    }
    foreach pin {control/M09_ARESETN control/M10_ARESETN} {
        if {![llength [get_bd_pins -quiet $pin]]} {error "G2_CONTROL_RESET_MISSING:$pin"}
    }
    foreach pin {axi_dma_0/mm2s_introut axi_dma_0/s2mm_introut} {
        if {[llength [get_bd_nets -quiet -of_objects [get_bd_pins -quiet $pin]]]} {
            error "G2_RLE_IRQ_MUST_REMAIN_UNCONNECTED:$pin"
        }
    }

    validate_bd_design
    save_bd_design
    generate_target all $bd_file
    update_compile_order -fileset sources_1
    set receipt [dict create design display bd $bd_file control_mi_count 11 \
        preserved_mi M00-M08 rle_dma_mi M09 rle_pipeline_mi M10 \
        clocks [dict create GP0_FCLK1_MHz 100 RLE_FCLK0_MHz 50 display_stream_FCLK2_MHz 142.857] \
        hp [dict create RLE_MM2S HP0_64bit RLE_S2MM HP1_32bit HDMI_read HP2_32bit] \
        axil $g2_control_map rle_irq connected-no \
        display_frame_buffers {PYNQ four-slot scheduler; CPU relinquishes slot to VDMA until retired}]
    set evidence [open [file join $g2_out evidence g2-architecture-readback.tcldict] {WRONLY CREAT EXCL}]
    puts $evidence $receipt
    close $evidence
    puts "G2_BLOCK_DESIGN_CREATED:implementation_and_hardware_gates_pending"
}
