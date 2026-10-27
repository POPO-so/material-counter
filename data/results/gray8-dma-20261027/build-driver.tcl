set gray8_repo {C:/Users/14173/Documents/material-counter}
set gray8_output [file join $gray8_repo data results gray8-dma-20261027]
source {C:/Users/14173/.codex/skills/vivado-ip-configurator/lib/ipcfg.tcl}
set ::ipcfg::native_log_path [file join $gray8_output build.log]
puts [ipcfg::python_status]
puts [ipcfg::python_exec -c {import argparse,hashlib,json,pathlib,sys,xml.etree.ElementTree; print("GRAY8_BUILD_PYTHON_OK")}]
set gray8_xsim_gate PASS
source [file join $gray8_repo scripts gray8 create_bd.tcl]
source [file join $gray8_repo scripts gray8 build.tcl]
exit
