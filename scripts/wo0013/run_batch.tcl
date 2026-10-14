# vivado -mode batch -source run_batch.tcl -log <out>/vivado.log
#        -journal <out>/vivado.jou -tclargs <fresh-out> <board-repo>
if {$argc != 2} {error {Expected fresh output directory and board repository}}
set wo13_output [file normalize [lindex $argv 0]]
set wo13_board_repo [file normalize [lindex $argv 1]]
set wo13_script_dir [file dirname [info script]]
set wo13_repo [file normalize [file join $wo13_script_dir ../..]]
file mkdir $wo13_output
set_param board.repoPaths [list $wo13_board_repo]
source -notrace [file join $wo13_repo .agents skills vivado-ip-configurator lib ipcfg.tcl]
set ::ipcfg::native_log_path [file join $wo13_output vivado.log]
puts "WO13_STAGE preflight version=[version -short] board_repo=$wo13_board_repo"
set py_status [ipcfg::python_status]
puts $py_status
if {![string match {PYTHON:OK *} $py_status]} {error $py_status}
puts [ipcfg::python_exec -c {import argparse, base64, hashlib, json, pathlib, re, sys, typing; print("PYTHON_IMPORTS:OK")}]
if {![llength [get_board_parts tul.com.tw:pynq-z2:part0:1.0]]} {error {PYNQ-Z2 board files unavailable}}
puts {WO13_STAGE create_bd}
source -notrace [file join $wo13_script_dir create_dma_replay_bd.tcl]
puts {WO13_STAGE synth_impl_bit_export}
source -notrace [file join $wo13_script_dir build_dma_replay.tcl]
puts {WO13_BATCH_COMPLETE board_tested=false}
close_project
exit 0
