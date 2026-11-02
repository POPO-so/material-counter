`timescale 1ns/1ps
// C-candidate old/new comparison: the new overlay adds ONE pipeline stage to the
// pixel output path (box-hit vector + q2 alignment), so its active/vsync/hsync and
// the base datapath must match the old instance delayed by exactly one pixel_clk.
// Focused scope: base passthrough + timing alignment (no mask/config needed to
// prove the +1 pipeline and full alignment of RGB/DE/HSYNC/VSYNC).
module tb_video_binary_overlay_cmp;

  reg pixel_clk=0, mask_clk=0, cfg_clk=0;
  reg pixel_resetn=0, mask_resetn=0, cfg_resetn=0;
  always #5 pixel_clk=~pixel_clk;    // 100 MHz
  always #8.333 mask_clk=~mask_clk;  // 60 MHz (not timing-critical here)
  always #10 cfg_clk=~cfg_clk;       // 50 MHz

  reg [23:0] vid_data24=0;
  reg vid_active=0, vid_vsync=0, vid_hsync=0;

  wire [23:0] new_data, old_data;
  wire new_active, new_vsync, new_hsync, old_active, old_vsync, old_hsync;

  video_binary_overlay #(.WIDTH(640),.HEIGHT(400)) new_dut (
    .mask_clk(mask_clk), .mask_resetn(mask_resetn),
    .tap_valid(1'b0), .tap_sof(1'b0), .tap_eol(1'b0), .tap_bit(1'b0),
    .tap_frame_id(32'd0), .mask_accept(1'b0), .accepted_frame_id(32'd0),
    .pixel_clk(pixel_clk), .pixel_resetn(pixel_resetn),
    .vid_data24(vid_data24), .vid_active(vid_active), .vid_vsync(vid_vsync), .vid_hsync(vid_hsync),
    .cfg_clk(cfg_clk), .cfg_resetn(cfg_resetn), .cfg_commit(1'b0), .cfg_ready(),
    .cfg_enable(1'b0), .cfg_frame_id(32'd0), .cfg_box_count(5'd0),
    .cfg_boxes({16*40{1'b0}}), .cfg_line_x(10'd0),
    .out_data24(new_data), .out_active(new_active), .out_vsync(new_vsync), .out_hsync(new_hsync),
    .capture_count(), .drop_count(), .reject_count(), .captured_frame_id(),
    .displayed_frame_id(), .capture_busy(), .pending_valid(), .completed_valid_status(),
    .display_valid(), .mask_displayed_frame_id(), .mask_display_valid());

  video_binary_overlay_frozen #(.WIDTH(640),.HEIGHT(400)) old_dut (
    .mask_clk(mask_clk), .mask_resetn(mask_resetn),
    .tap_valid(1'b0), .tap_sof(1'b0), .tap_eol(1'b0), .tap_bit(1'b0),
    .tap_frame_id(32'd0), .mask_accept(1'b0), .accepted_frame_id(32'd0),
    .pixel_clk(pixel_clk), .pixel_resetn(pixel_resetn),
    .vid_data24(vid_data24), .vid_active(vid_active), .vid_vsync(vid_vsync), .vid_hsync(vid_hsync),
    .cfg_clk(cfg_clk), .cfg_resetn(cfg_resetn), .cfg_commit(1'b0), .cfg_ready(),
    .cfg_enable(1'b0), .cfg_frame_id(32'd0), .cfg_box_count(5'd0),
    .cfg_boxes({16*40{1'b0}}), .cfg_line_x(10'd0),
    .out_data24(old_data), .out_active(old_active), .out_vsync(old_vsync), .out_hsync(old_hsync),
    .capture_count(), .drop_count(), .reject_count(), .captured_frame_id(),
    .displayed_frame_id(), .capture_busy(), .pending_valid(), .completed_valid_status(),
    .display_valid(), .mask_displayed_frame_id(), .mask_display_valid());

  // reference: delayed copy of the OLD outputs (one pixel_clk)
  reg [23:0] old_data_d=0;
  reg old_active_d=0, old_vsync_d=0, old_hsync_d=0;
  always @(posedge pixel_clk) begin
    old_data_d  <= old_data;
    old_active_d<= old_active;
    old_vsync_d <= old_vsync;
    old_hsync_d <= old_hsync;
  end

  integer cycle=0, mismatches=0;
  initial begin
    // reset
    repeat(20) @(posedge pixel_clk);
    pixel_resetn<=1; mask_resetn<=1; cfg_resetn<=1;
    repeat(10) @(posedge pixel_clk);
    // drive a distinguishable pixel pattern + timing shoulders
    for (integer i=0;i<2000;i=i+1) begin
      vid_active  <= (i%100)<40;
      vid_hsync   <= (i%100)==95;
      vid_vsync   <= (i==1000 || i==1500);
      vid_data24  <= i[9:0]==10'd0 ? 24'h00ff00 : (i%2 ? 24'h123456 : 24'hffffff);
      @(posedge pixel_clk);
    end
    // allow last pipeline flush
    repeat(10) @(posedge pixel_clk);
    $display("CMP mismatches=%0d  (new must equal old delayed by exactly 1 pixel_clk)", mismatches);
    if (mismatches) $fatal(1,"OLD_NEW_MISMATCH");
    $display("OLD_NEW_DELAY_ALIGN_PASS");
    $finish;
  end

  // compare after reset, once old_data_d (old+1) is valid
  always @(posedge pixel_clk) begin
    if (pixel_resetn) begin
      if (new_active!==old_active_d || new_vsync!==old_vsync_d || new_hsync!==old_hsync_d || new_data!==old_data_d)
        mismatches<=mismatches+1;
    end
  end

  initial begin #2000000; $fatal(1,"watchdog"); end
endmodule