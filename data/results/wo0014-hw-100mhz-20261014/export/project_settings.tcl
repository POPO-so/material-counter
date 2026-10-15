################################################################################
# project_settings.tcl - Captured Project Configuration
# Generated: Wed Oct 14 18:18:34 2026
# Project: wo0014_image_100m
# Device: xc7z020clg400-1
################################################################################

# Derive base_dir for referencing exported files (e.g., hook scripts)
set base_dir [file dirname [file dirname [file normalize [info script]]]]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PROJECT SETTINGS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
set_property PART xc7z020clg400-1 [current_project]
set_property BOARD_PART {tul.com.tw:pynq-z2:part0:1.0} [current_project]
set_property TARGET_LANGUAGE {Verilog} [current_project]
set_property IP_CACHE_PERMISSIONS {read write} [current_project]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FILESET SETTINGS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
set_property TOP {wo0014_image_wrapper} [get_filesets sources_1]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SYNTHESIS SETTINGS (non-default only)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
set_property AUTO_INCREMENTAL_CHECKPOINT false [get_runs synth_1]
set_property STEPS.SYNTH_DESIGN.ARGS.SHREG_MIN_SIZE {3} [get_runs synth_1]
set_property STEPS.SYNTH_DESIGN.ARGS.CONTROL_SET_OPT_THRESHOLD {auto} [get_runs synth_1]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# IMPLEMENTATION SETTINGS (non-default only)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# All implementation settings are at defaults �? nothing to restore

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TCL HOOK SCRIPTS (pre/post hooks)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# No hook scripts configured

puts "Project settings restored from saved configuration"
