# G0 dependency rebuild only. Run with Vitis 2026.1 vitis-run --mode hls --tcl.
# PYNQ_SOURCE must be the clean checkout pinned by video_sources.lock.json.
# VIDEO_IP_OUT must be a fresh directory; no runtime compilation is permitted.
foreach name {PYNQ_SOURCE VIDEO_IP_OUT VIDEO_PYTHON} {
    if {![info exists ::env($name)]} {error "Missing environment variable $name"}
}
set source_root [file normalize $::env(PYNQ_SOURCE)]
set output_root [file normalize $::env(VIDEO_IP_OUT)]
# With both trees under the same source-workspace parent, this installation's
# HLS 2026.1 persisted a design path relative to that parent, then omitted it
# from csim.mk. Keep outputs separate; retain the failed logs for diagnosis.
if {[string first "[file dirname $source_root]/" "$output_root/"] == 0} {
    error "VIDEO_IP_OUT must be outside the PYNQ checkout's parent directory"
}
set repo [file normalize [file join [file dirname [info script]] ../..]]
puts [exec $::env(VIDEO_PYTHON) -B [file join $repo scripts standalone verify_sources.py] --source-root $source_root --brief]
if {[file exists $output_root]} {error "Output already exists: $output_root"}
file mkdir $output_root
foreach name {color_convert pixel_unpack} {
    set source_dir [file join $source_root boards ip hls $name]
    open_project [file join $output_root $name]
    set_top $name
    add_files [file join $source_dir $name.cpp]
    add_files -tb [file join $source_dir ${name}_test.cpp]
    open_solution solution1 -flow_target vivado
    set_part xc7z020clg400-1
    create_clock -period 7
    csim_design
    csynth_design
    export_design -format ip_catalog
    close_project
}
puts "VIDEO_DEPENDENCIES_BUILT:hardware_acceptance_pending"
exit
