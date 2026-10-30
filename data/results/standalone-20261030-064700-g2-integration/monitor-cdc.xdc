# Request/acknowledge synchronizers terminate these CDC paths.
set_false_path -to [get_pins -hier -regexp {.*frame_monitor.*(request_sync1|acknowledge_sync1|axi_reset_sync1)_reg/D}]
# Mailbox is held until the next handshake; data settles before ack is used.
set_max_delay -datapath_only 10 -from [get_cells -hier -regexp {.*frame_monitor.*mailbox_.*_reg.*}] -to [get_cells -hier -regexp {.*frame_monitor.*snap_.*_reg.*}]
