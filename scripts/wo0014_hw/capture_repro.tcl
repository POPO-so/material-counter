# Read project settings and retain the IP requirement coverage; no design edits.
set wo14_repro_repo [file normalize [file join [file dirname [info script]] ../..]]
source -notrace [file join $wo14_repro_repo .agents skills vivado-revision-control helper-procedures helper_scripts.tcl]
# This builder creates a standard non-DFX Zynq design. Do not probe a routed
# leaf-cell netlist with HD.RECONFIGURABLE, which is valid only on hierarchies.
set wo14_pr [get_property PR_FLOW [current_project]]
if {$wo14_pr in {true 1}} {error "Unexpected DFX project in this standard experiment"}
set wo14_flow [dict create flow_type Standard project_name [get_property NAME [current_project]] \
    device [get_property PART [current_project]] pr_flow_property $wo14_pr]
set wo14_sources [analyze_source_locations]
capture_project_settings [file join $wo14_output export project_settings.tcl]
set wo14_ledger [list \
    {MM2S enabled} CONFIG.c_include_mm2s applied \
    {S2MM disabled} CONFIG.c_include_s2mm applied \
    {Simple mode SG disabled} CONFIG.c_include_sg applied \
    {Micro DMA disabled} CONFIG.c_micro_dma applied \
    {18 bit length} CONFIG.c_sg_length_width applied \
    {32 bit stream} CONFIG.c_m_axis_mm2s_tdata_width applied \
    {64 bit MM2S memory port} CONFIG.c_m_axi_mm2s_data_width applied \
    {32 bit address} CONFIG.c_addr_width applied \
    {DRE disabled} CONFIG.c_include_mm2s_dre applied \
    {GP0 control} CONFIG.PCW_USE_M_AXI_GP0 applied \
    {HP0 DDR} CONFIG.PCW_USE_S_AXI_HP0 applied \
    {64 bit HP0} CONFIG.PCW_S_AXI_HP0_DATA_WIDTH applied \
    {Independent requested PL clock} CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ applied \
    {Explicit AXI4 to AXI3 conversion} CONFIG.MI_PROTOCOL applied \
    {Aux reset active high tied low} CONFIG.C_AUX_RESET_HIGH applied]
set wo14_coverage [ipcfg::coverage_report $wo14_ledger]
set wo14_coverage_handle [open [file join $wo14_output reports ip-requirement-coverage.txt] w]
puts $wo14_coverage_handle $wo14_coverage
close $wo14_coverage_handle
puts [ipcfg::write_evidence [file join $wo14_output reports project-context.[pid].[clock clicks].tcldict] \
    [dict create flow $wo14_flow sources $wo14_sources coverage $wo14_coverage]]
puts "WO14_REPRO_SETTINGS_READY coverage=FULL"
