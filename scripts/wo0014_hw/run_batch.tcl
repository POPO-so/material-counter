# vivado -mode batch -source run_batch.tcl -log <out>/vivado.log
#        -journal <out>/vivado.jou -tclargs <fresh-out> <board-repo> <50|100>
if {$argc != 3} {error "Expected fresh output, board repository, 50 or 100 MHz"}
set wo14_output [file normalize [lindex $argv 0]]
set wo14_board_repo [file normalize [lindex $argv 1]]
set wo14_clock_mhz [lindex $argv 2]
file mkdir $wo14_output
set_param board.repoPaths [list $wo14_board_repo]
set wo14_script_dir [file dirname [info script]]
set wo14_repo [file normalize [file join $wo14_script_dir ../..]]
source -notrace [file join $wo14_repo .agents skills vivado-ip-configurator lib ipcfg.tcl]
set ::ipcfg::native_log_path [file join $wo14_output vivado.log]
if {![llength [get_board_parts tul.com.tw:pynq-z2:part0:1.0]]} {error "PYNQ-Z2 board preset unavailable"}
source -notrace [file join $wo14_script_dir create_image_bd.tcl]
source -notrace [file join $wo14_script_dir build_image.tcl]
close_project
exit 0
