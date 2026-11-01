# PYNQ v3.1 rgb2dvi's generic constraint assumes SerialClk is generated from
# PixelClk inside that IP. This design sets kGenerateSerialClk=false: both are
# supplied by axi_dynclk. Follow the real MMCM serial output through the BUFG.
# The build excludes rgb2dvi's generic clock XDC because this design supplies
# SerialClk from axi_dynclk rather than generating it inside rgb2dvi.
set serial_source [get_pins -quiet display_i/hdmi_out/frontend/axi_dynclk/U0/Inst_mmcme2_drp/mmcm_adv_inst/CLKOUT0]
set serial_target [get_pins -quiet display_i/hdmi_out/frontend/rgb2dvi_0/U0/SerialClk]
create_generated_clock -name display_i/hdmi_out/frontend/rgb2dvi_0/U0/SerialClk \
    -source $serial_source -divide_by 1 $serial_target

# Mechanical buttons are asynchronous. The instantiated GPIO has four
# synchronization stages; only the path to its first stage is asynchronous.
set button_first [get_pins -hier -regexp {.*btns_gpio.*syncstages_ff_reg\[0\]\[[0-3]\]/D}]
set_false_path -from [get_ports {btns_4bits_tri_i[*]}] -to $button_first
# HPD here is the reference design's slow GPIO control output, with no external
# synchronous sampling clock. External TMDS output delays remain unspecified
# until receiver and board timing data are available.
set_false_path -to [get_ports {hdmi_out_hpd[0]}]
