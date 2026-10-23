# The hierarchical Interconnect boundaries have no CONFIG properties. Inspect its actual generated protocol converter.
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
