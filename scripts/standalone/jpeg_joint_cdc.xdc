# Joint JPEG/PL candidate CDC constraints.
# The fixed project snapshot already constrains both asynchronous switch inputs
# in offload-switch-cdc.xdc, including the SW1 pin exposed on GPIO bit 5.
# Keep this override limited to button debounce and overlay mailbox paths.
set button_meta_first [get_pins -hier -regexp {.*button_debounce.*button_meta_reg\[[0-3]\]/D}]
set_false_path -from [get_ports {btns_4bits_tri_i[*]}] -to $button_meta_first

# Toggle synchronizers terminate these control paths.
set_false_path -to [get_pins -hier -regexp {.*binary_overlay.*(publish_sync1|ack_sync1|cfg_ack_s1|cfg_req_s1)_reg/D}]

# Bundled payload registers remain stable until the request/ack handshake ends.
# Anchor endpoints to actual register cells; do not collect optimized logic
# cells whose names merely contain a *_reg* fragment.
set_max_delay -datapath_only 10 -from [get_cells -hier -regexp {.*binary_overlay.*cfg_mb_.*_reg(\[[0-9]+\])?$}] -to [get_cells -hier -regexp {.*binary_overlay.*(enable_p|id_p|count_p|boxes_p|line_p|box_valid_p|line_valid_p)_reg(\[[0-9]+\])?$}]
set_max_delay -datapath_only 10 -from [get_cells -hier -regexp {.*binary_overlay.*publish_(id|bank)_reg(\[[0-9]+\])?$}] -to [get_cells -hier -regexp {.*binary_overlay.*(displayed_frame_id|display_bank)_reg(\[[0-9]+\])?$}]
