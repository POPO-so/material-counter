# Candidate overlay: only first-stage single-bit synchronizers are cut.
# Publish/config payloads are stable until the returning acknowledgement.
set_false_path -to [get_pins -hier -regexp {.*binary_overlay.*(publish_sync1|ack_sync1|cfg_ack_s1|cfg_req_s1)_reg/D}]
# Data must settle before the two-stage request reaches the pixel FSM.
set_max_delay -datapath_only 10 -from [get_cells -hier -regexp {.*binary_overlay.*cfg_mb_.*_reg.*}] -to [get_cells -hier -regexp {.*binary_overlay.*(enable_p|id_p|count_p|boxes_p|line_p)_reg.*}]
set_max_delay -datapath_only 10 -from [get_cells -hier -regexp {.*binary_overlay.*publish_(id|bank)_reg.*}] -to [get_cells -hier -regexp {.*binary_overlay.*(displayed_frame_id|display_bank)_reg.*}]
