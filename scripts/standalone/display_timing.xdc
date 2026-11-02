# axi_dynclk supplies PXL_CLK_O and PXL_CLK_5X_O from sibling MMCM outputs.
# Keep their propagated common master; do not model the 5x output as derived
# through the rgb2dvi PixelClk input.

# rgb2dvi_clocks.xdc derives SerialClk from PixelClk, but this design drives
# SerialClk from axi_dynclk's sibling 5x MMCM output. joint_build.tcl disables
# that one duplicate XDC at project level for synthesis and implementation;
# the propagated MMCM clock remains the timing source for the connected pin.
# Mechanical buttons are asynchronous. The instantiated GPIO has four
# synchronization stages; only the path to its first stage is asynchronous.
set button_first [get_pins -hier -regexp {.*btns_gpio.*syncstages_ff_reg\[0\]\[[0-3]\]/D}]
set_false_path -from [get_ports {btns_4bits_tri_i[*]}] -to $button_first
# HPD here is the reference design's slow GPIO control output, with no external
# synchronous sampling clock. External TMDS output delays remain unspecified
# until receiver and board timing data are available.
set_false_path -to [get_ports {hdmi_out_hpd[0]}]
