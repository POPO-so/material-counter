# Same monitor CDC constraints as pinned scripts/standalone/build_display.tcl.
set_false_path -to [get_pins -hier -regexp {.*frame_monitor.*(request_sync1|acknowledge_sync1|axi_reset_sync1)_reg/D}]
set_max_delay -datapath_only 10 -from [get_cells -hier -regexp {.*frame_monitor.*mailbox_.*_reg(\[[0-9]+\])?$}] -to [get_cells -hier -regexp {.*frame_monitor.*snap_.*_reg(\[[0-9]+\])?$}]
