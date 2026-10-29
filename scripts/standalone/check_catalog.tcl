# Read the G0 catalog without creating or configuring any hardware IP.
foreach name {PYNQ_SOURCE VIDEO_IP_BUILT G0_CATALOG_OUT} {
    if {![info exists ::env($name)]} {error "Missing environment variable $name"}
}
if {[llength [get_projects -quiet]]} {error "Use a fresh Vivado session"}
set output [file normalize $::env(G0_CATALOG_OUT)]
if {[file exists $output]} {error "Evidence already exists: $output"}
create_project -in_memory -part xc7z020clg400-1
set repos [list [file join $::env(PYNQ_SOURCE) boards ip] \
    [file join $::env(VIDEO_IP_BUILT) color_convert solution1 impl ip] \
    [file join $::env(VIDEO_IP_BUILT) pixel_unpack solution1 impl ip]]
foreach path $repos {if {![file isdirectory $path]} {error "Missing IP repository: $path"}}
set_property ip_repo_paths $repos [current_project]
update_ip_catalog
set fd [open $output {WRONLY CREAT EXCL}]
puts $fd [list vivado_version [version -short] repositories $repos]
set missing {}
foreach vlnv {
    digilentinc.com:ip:axi_dynclk:1.0 digilentinc.com:ip:rgb2dvi:1.2
    xilinx.com:user:color_swap:1.1 xilinx.com:hls:color_convert:1.0
    xilinx.com:hls:pixel_unpack:1.0 xilinx.com:ip:axi_vdma:6.3
    xilinx.com:ip:v_axi4s_vid_out:4.0 xilinx.com:ip:v_tc:6.2
    xilinx.com:ip:axi_intc:4.1 xilinx.com:ip:axi_gpio:2.0
} {
    set found [get_ipdefs -all -quiet $vlnv]
    puts $fd [list vlnv $vlnv definitions $found]
    if {[llength $found] != 1} {lappend missing $vlnv}
}
puts $fd [list missing $missing]
close $fd
if {[llength $missing]} {error "G0_MISSING_IP:$missing"}
puts "G0_CATALOG_COMPLETE:hardware_acceptance_pending"
exit
