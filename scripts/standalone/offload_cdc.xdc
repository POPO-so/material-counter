# Candidate overlay: only first-stage single-bit synchronizers are cut.
# Physical buttons first enter button_debounce; constrain only its four
# asynchronous meta flops. The second stage and later stable data remain timed.
set button_meta_first [get_pins -hier -regexp {.*button_debounce.*button_meta_reg\[[0-3]\]/D}]
set_false_path -from [get_ports {btns_4bits_tri_i[*]}] -to $button_meta_first
# Slide switches are asynchronous to FCLK0. Cut only the path to their first
# synchronizer stage; meta-to-sync and downstream paths remain timed.
set switch_meta_first [get_pins -hier -regexp {.*switch_synchronizer.*switch_meta_reg\[[0-1]\]/D}]
if {[llength $switch_meta_first] != 2} {error "SWITCH_SYNCHRONIZER_ENDPOINT_COUNT:[llength $switch_meta_first]"}
set_false_path -from [get_ports {sws_2bits_tri_i[*]}] -to $switch_meta_first
# Publish/config payloads are stable until the returning acknowledgement.
set_false_path -to [get_pins -hier -regexp {.*binary_overlay.*(publish_sync1|ack_sync1|cfg_ack_s1|cfg_req_s1)_reg/D}]
# Data must settle before the two-stage request reaches the pixel FSM.
set_max_delay -datapath_only 10 -from [get_cells -hier -regexp {.*binary_overlay.*cfg_mb_.*_reg.*}] -to [get_cells -hier -regexp {.*binary_overlay.*(enable_p|id_p|count_p|boxes_p|line_p|box_valid_p|line_valid_p)_reg.*}]
set_max_delay -datapath_only 10 -from [get_cells -hier -regexp {.*binary_overlay.*publish_(id|bank)_reg.*}] -to [get_cells -hier -regexp {.*binary_overlay.*(displayed_frame_id|display_bank)_reg.*}]
