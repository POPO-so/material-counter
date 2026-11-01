# Optional PS-PL binary overlay candidate. Sourced by create_g2_integration.tcl
# after the baseline design and RLE connections have been created.
if {![info exists g2_offload_candidate] || !$g2_offload_candidate} {
    error "OFFLOAD_CONNECT_REQUIRES_OFFLOAD_CANDIDATE"
}
set g2_sw0_candidate [expr {[info exists ::env(SW0_CANDIDATE)] && $::env(SW0_CANDIDATE) eq "1"}]
capture control_overlay_expand [list ipcfg::require control [dict create CONFIG.NUM_MI 12] \
    {Preserve M00-M10; append experimental overlay on M11}] {REQ:pinned+applied}
capture overlay_concat_expand [list ipcfg::require video_interrupts [dict create CONFIG.NUM_PORTS 4] \
    {Preserve existing interrupt inputs 0-2 and append overlay GPIO at input 3}] {REQ:pinned+applied}
create_bd_cell -type module -reference video_binary_overlay_bd binary_overlay
create_bd_cell -type module -reference button_debounce_irq button_debounce
set_property -dict [list CONFIG.KEY_COUNT 4 CONFIG.DEBOUNCE_CYCLES 1500000] [get_bd_cells button_debounce]
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 button_debounce_tieoff
set_property -dict [list CONFIG.CONST_WIDTH 4 CONFIG.CONST_VAL 0] [get_bd_cells button_debounce_tieoff]
set button_port [get_bd_ports btns_4bits_tri_i]
set button_gpio_pin [get_bd_pins btns_gpio/gpio_io_i]
set button_old_net [get_bd_nets -quiet -of_objects $button_gpio_pin]
if {[llength $button_old_net] != 1} {error "OFFLOAD_BUTTON_INPUT_NET_MISSING_OR_AMBIGUOUS:$button_old_net"}
disconnect_bd_net $button_old_net $button_port $button_gpio_pin
connect_bd_net $button_port [get_bd_pins button_debounce/button_in]
connect_bd_net [get_bd_pins button_debounce/stable_state] [get_bd_pins binary_overlay/button_state]
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins button_debounce/clk]
connect_bd_net [get_bd_pins reset_rle/peripheral_aresetn] [get_bd_pins button_debounce/resetn]
connect_bd_net [get_bd_pins button_debounce/press_event] [get_bd_pins binary_overlay/button_events]
connect_bd_net [get_bd_pins button_debounce/fault_sticky] [get_bd_pins binary_overlay/button_fault]
connect_bd_net [get_bd_pins binary_overlay/event_clear] [get_bd_pins button_debounce/event_clear]
connect_bd_net [get_bd_pins binary_overlay/fault_clear] [get_bd_pins button_debounce/fault_clear]
connect_bd_net [get_bd_pins button_debounce_tieoff/dout] [get_bd_pins button_debounce/fault_in]
if {$g2_sw0_candidate} {
    capture sw0_gpio_expand [list ipcfg::require btns_gpio [dict create \
        CONFIG.C_GPIO_WIDTH 5 CONFIG.C_INTERRUPT_PRESENT 1] \
        {Preserve BTN0-3 at bits 0-3 and append synchronized SW0 at bit 4}] {REQ:pinned+applied}
    create_bd_port -dir I sw0
    create_bd_cell -type module -reference sw0_synchronizer switch0_synchronizer
    create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:2.1 btns_gpio_concat
    set_property -dict [list CONFIG.NUM_PORTS 2 CONFIG.IN0_WIDTH 4 CONFIG.IN1_WIDTH 1] \
        [get_bd_cells btns_gpio_concat]
    connect_bd_net [get_bd_ports sw0] [get_bd_pins switch0_synchronizer/switch_async]
    connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins switch0_synchronizer/clk]
    connect_bd_net [get_bd_pins reset_rle/peripheral_aresetn] [get_bd_pins switch0_synchronizer/resetn]
    connect_bd_net [get_bd_pins button_debounce/stable_state] [get_bd_pins btns_gpio_concat/In0]
    connect_bd_net [get_bd_pins switch0_synchronizer/switch_sync] [get_bd_pins btns_gpio_concat/In1]
    connect_bd_net [get_bd_pins btns_gpio_concat/dout] $button_gpio_pin

    set sw0_xdc_file [file join $g2_out sw0-candidate.xdc]
    if {[file exists $sw0_xdc_file]} {error "SW0_XDC_ALREADY_EXISTS:$sw0_xdc_file"}
    set sw0_xdc_channel [open $sw0_xdc_file {WRONLY CREAT EXCL}]
    puts $sw0_xdc_channel {# PYNQ-Z2 SW0: official board mapping, candidate input only.}
    puts $sw0_xdc_channel {set_property -dict {PACKAGE_PIN M20 IOSTANDARD LVCMOS33} [get_ports {sw0}]}
    close $sw0_xdc_channel
    add_files -fileset constrs_1 $sw0_xdc_file
} else {
    capture overlay_gpio_switches [list ipcfg::require btns_gpio [dict create \
        CONFIG.C_GPIO_WIDTH 6 CONFIG.C_INTERRUPT_PRESENT 1] \
        {Keep BTN0-3 on bits 0-3; add SW0/SW1 on bits 4-5}] {REQ:pinned+applied}
    create_bd_port -dir I -from 1 -to 0 sws_2bits_tri_i
create_bd_cell -type module -reference slide_switch_synchronizer switch_synchronizer
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:2.1 btns_gpio_concat
set_property -dict [list CONFIG.NUM_PORTS 2 CONFIG.IN0_WIDTH 4 CONFIG.IN1_WIDTH 2] \
    [get_bd_cells btns_gpio_concat]
connect_bd_net [get_bd_ports sws_2bits_tri_i] [get_bd_pins switch_synchronizer/switch_async]
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins switch_synchronizer/clk]
connect_bd_net [get_bd_pins reset_rle/peripheral_aresetn] [get_bd_pins switch_synchronizer/resetn]
connect_bd_net [get_bd_pins button_debounce/stable_state] [get_bd_pins btns_gpio_concat/In0]
connect_bd_net [get_bd_pins switch_synchronizer/switch_sync] [get_bd_pins btns_gpio_concat/In1]
connect_bd_net [get_bd_pins btns_gpio_concat/dout] $button_gpio_pin
}
# 按键消抖路径仍待综合验证；GPIO IRQ 到 INTC In3 保持原连接。
# 当前没有外部故障源：fault_in 明确接常量 0，仅保留状态/W1C接口。
if {[get_property CONFIG.NUM_MI [get_bd_cells control]] != 12} {error "OFFLOAD_CONTROL_MI_READBACK_FAILED"}
foreach pin {control/M11_AXI control/M11_ACLK control/M11_ARESETN} {
    if {![llength [get_bd_pins -quiet $pin]] && ![llength [get_bd_intf_pins -quiet $pin]]} {error "OFFLOAD_CONTROL_PIN_MISSING:$pin"}
}
connect_bd_intf_net [get_bd_intf_pins control/M11_AXI] [get_bd_intf_pins binary_overlay/S_AXI]
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins control/M11_ACLK] [get_bd_pins binary_overlay/s_axi_aclk] [get_bd_pins binary_overlay/mask_clk]
connect_bd_net [get_bd_pins reset_rle/peripheral_aresetn] [get_bd_pins control/M11_ARESETN] [get_bd_pins binary_overlay/s_axi_aresetn] [get_bd_pins binary_overlay/mask_resetn]
connect_bd_net [get_bd_pins reset_pixel/peripheral_aresetn] [get_bd_pins binary_overlay/pixel_resetn]
connect_bd_net [get_bd_pins hdmi_out/frontend/axi_dynclk/PXL_CLK_O] [get_bd_pins binary_overlay/pixel_clk]

# Preview tap is the accepted-pixel stream from the shared raw adapter. It is
# observation-only and never participates in the AXIS ready path.
foreach {src dst} {
    image_pipeline/preview_valid binary_overlay/tap_valid
    image_pipeline/preview_sof binary_overlay/tap_sof
    image_pipeline/preview_eol binary_overlay/tap_eol
    image_pipeline/preview_bit binary_overlay/tap_data
    image_pipeline/preview_frame_id binary_overlay/tap_frame_id
} {
    if {![llength [get_bd_pins -quiet $src]] || ![llength [get_bd_pins -quiet $dst]]} {error "OFFLOAD_TAP_PIN_MISSING:$src:$dst"}
    connect_bd_net [get_bd_pins $src] [get_bd_pins $dst]
}

# Feed every pixel signal through the one-cycle overlay delay. Discover the
# hsync pins from the live BD by function and require exactly one match.
set video_out_cell hdmi_out/frontend/v_axi4s_vid_out_0
set swap_cell hdmi_out/frontend/color_swap_0
# All four signals are explicit below. Detach the bundled video interface so
# HSync cannot bypass the overlay while data/active/VSYNC are delayed.
set old_interface [get_bd_intf_nets -quiet -of_objects [get_bd_intf_pins ${swap_cell}/pixel_input]]
if {[llength $old_interface]} {
    disconnect_bd_intf_net $old_interface [get_bd_intf_pins ${swap_cell}/pixel_input]
}
foreach {src_pin swap_pin overlay_pin monitor_pin} {
    vid_data pixel_in vid_data24 vid_data
    vid_active_video vde_in vid_active vid_active
    vid_vsync vsync_in vid_vsync vid_vsync
} {
    set source [get_bd_pins -quiet ${video_out_cell}/$src_pin]
    set old [get_bd_pins -quiet ${swap_cell}/$swap_pin]
    set observer [get_bd_pins -quiet frame_monitor/$monitor_pin]
    if {![llength $source] || ![llength $old] || ![llength $observer]} {error "OFFLOAD_VIDEO_PIN_MISSING:$src_pin:$swap_pin:$monitor_pin"}
    set nets [get_bd_nets -quiet -of_objects $old]
    if {[llength $nets] != 1} {error "OFFLOAD_EXPECTED_ONE_OLD_VIDEO_NET:$swap_pin:$nets"}
    disconnect_bd_net $nets $old $observer
    connect_bd_net $source [get_bd_pins binary_overlay/$overlay_pin]
}
set hsrc_candidates {}
foreach p [get_bd_pins -of_objects [get_bd_cells $video_out_cell]] {
    if {[string match -nocase *hsync* [get_property NAME $p]] && [get_property DIR $p] eq "O"} {lappend hsrc_candidates $p}
}
set hdst_candidates {}
foreach p [get_bd_pins -of_objects [get_bd_cells $swap_cell]] {
    if {[string match -nocase *hsync* [get_property NAME $p]] && [get_property DIR $p] eq "I"} {lappend hdst_candidates $p}
}
if {[llength $hsrc_candidates]!=1 || [llength $hdst_candidates]!=1} {error "OFFLOAD_HSYNC_PIN_DISCOVERY_AMBIGUOUS:src=$hsrc_candidates dst=$hdst_candidates"}
set hdst [lindex $hdst_candidates 0]
set oldnets [get_bd_nets -quiet -of_objects $hdst]
if {[llength $oldnets]>1} {error "OFFLOAD_HSYNC_OLD_NET_AMBIGUOUS:$hdst:$oldnets"}
if {[llength $oldnets]} {disconnect_bd_net $oldnets $hdst}
connect_bd_net [lindex $hsrc_candidates 0] [get_bd_pins binary_overlay/vid_hsync]
connect_bd_net [get_bd_pins binary_overlay/out_data24] [get_bd_pins ${swap_cell}/pixel_in]
connect_bd_net [get_bd_pins binary_overlay/out_active] [get_bd_pins ${swap_cell}/vde_in]
connect_bd_net [get_bd_pins binary_overlay/out_vsync] [get_bd_pins ${swap_cell}/vsync_in]
connect_bd_net [get_bd_pins binary_overlay/out_hsync] $hdst
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
    [get_property CONFIG.C_INTERRUPT_PRESENT [get_bd_cells btns_gpio]] != 1 ||
    [get_property CONFIG.C_GPIO_WIDTH [get_bd_cells btns_gpio]] != [expr {$g2_sw0_candidate ? 5 : 6}]} {
    error "OFFLOAD_READBACK_COUNT_OR_GPIO_MISMATCH"
}
if {$g2_sw0_candidate} {
    if {[llength [get_bd_ports -quiet sw0]] != 1 ||
        [llength [get_bd_pins -quiet switch0_synchronizer/switch_sync]] != 1} {
        error "SW0_EXTERNAL_PORT_OR_SYNCHRONIZER_MISSING"
    }
} elseif {[llength [get_bd_ports -quiet sws_2bits_tri_i]] != 1 ||
          [llength [get_bd_pins -quiet switch_synchronizer/switch_sync]] != 1} {
    error "SWITCH_INPUT_PORT_OR_SYNCHRONIZER_MISSING"
}
if {$g2_sw0_candidate} {
    set gpio_input_bits {BTN0=0 BTN1=1 BTN2=2 BTN3=3 SW0=4}
    set switch_pins {SW0=M20}
} else {
    set gpio_input_bits {BTN0=0 BTN1=1 BTN2=2 BTN3=3 SW0=4 SW1=5}
    set switch_pins {SW0=M20 SW1=M19}
}
set receipt [dict create candidate ps-pl-offload control_mi_count [get_property CONFIG.NUM_MI [get_bd_cells control]] \
    overlay_master M11 AXIL_base 0x43C50000 AXIL_range 0x10000 \
    switch0_candidate $g2_sw0_candidate GPIO_width [get_property CONFIG.C_GPIO_WIDTH [get_bd_cells btns_gpio]] \
    GPIO_input_bits $gpio_input_bits switch_pins $switch_pins switch_polarity {up=1 down=0} \
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
