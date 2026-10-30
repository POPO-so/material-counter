open_project C:/Users/14173/Documents/material-counter/data/results/standalone-20261029-g0/hls-color_convert
set_top color_convert
add_files C:/Users/14173/Documents/material-counter/.runtime/pynq-v3.1/boards/ip/hls/color_convert/color_convert.cpp
add_files -tb C:/Users/14173/Documents/material-counter/.runtime/pynq-v3.1/boards/ip/hls/color_convert/color_convert_test.cpp
open_solution solution1 -flow_target vivado
set_part xc7z020clg400-1
create_clock -period 7
csim_design
csynth_design
export_design -format ip_catalog
exit
