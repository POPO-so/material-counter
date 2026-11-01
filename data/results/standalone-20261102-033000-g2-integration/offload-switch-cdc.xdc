# Cut asynchronous board switch inputs; keep meta-to-sync paths timed.
set_false_path -from [get_ports {sws_2bits_tri_i[1]}]
set_false_path -from [get_ports {sws_2bits_tri_i[0]}]
