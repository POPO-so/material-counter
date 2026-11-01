# Replay the already elaborated HLS cosim snapshot with its existing VIP test.
# Run from hls/sim/verilog. The runner prefixes a process-local Tcl path variable.
if {[catch {
if {![info exists ::jpeg_axis_vcd_path] || $::jpeg_axis_vcd_path eq ""} {
    error "jpeg_axis_vcd_path must name an output VCD in the external HLS workdir"
}
open_vcd $::jpeg_axis_vcd_path
set dut /apatb_jpeg_axis_gray_top/AESL_inst_jpeg_axis_gray
set ports [get_objects -filter {type == in_port || type == out_port || type == inout_port || type == port} ${dut}/*]
if {[llength $ports] == 0} { error "No DUT ports available for waveform capture" }
log_vcd $ports
# Finite simulation bound; the generated self-checking VIP test finishes sooner.
run 200 ms
flush_vcd
close_vcd
} message]} {
    puts stderr "TRACE_FAIL: $message"
    exit 1
}
quit
