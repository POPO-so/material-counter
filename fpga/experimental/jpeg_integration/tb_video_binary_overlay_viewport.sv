`timescale 1ns/1ps
// C candidate: viewport sub-rectangle check. WIDTH=HEIGHT=64 but the view is
// (8,8)..(39,39), so every pixel outside the view must pass the unique base RGB
// through unchanged. Inside the view, an all-zero mask makes pixels black; with
// no boxes and no count line there is no overlay. This proves both the window
// boundary and that the unique base encoding is only used in uncovered regions.
module tb_video_binary_overlay_viewport;

  reg pixel_clk=0, mask_clk=0, cfg_clk=0;
  reg pixel_resetn=0, mask_resetn=0, cfg_resetn=0;
  always #5 pixel_clk=~pixel_clk;
  always #20 mask_clk=~mask_clk;
  always #10 cfg_clk=~cfg_clk;

  reg tap_valid=0, tap_sof=0, tap_eol=0, tap_bit=0; reg [31:0] tap_frame_id=0;
  reg mask_accept=0; reg [31:0] accepted_frame_id=0;
  reg cfg_commit=0, cfg_enable=0; reg [31:0] cfg_frame_id=0; reg [4:0] cfg_box_count=0;
  reg [16*40-1:0] cfg_boxes=0; reg [9:0] cfg_line_x=0; wire cfg_ready;
  reg [23:0] vid_data24=24'h404040; reg vid_active=0, vid_vsync=0, vid_hsync=0;
  wire [23:0] new_data, old_data; wire new_active, new_vsync, new_hsync, new_dv, old_active, old_vsync, old_hsync, old_dv;

  video_binary_overlay #(.WIDTH(64),.HEIGHT(64),.VIEW_X(8),.VIEW_Y(8),.VIEW_WIDTH(32),.VIEW_HEIGHT(32)) new_dut (
    .mask_clk(mask_clk),.mask_resetn(mask_resetn),
    .tap_valid(tap_valid),.tap_sof(tap_sof),.tap_eol(tap_eol),.tap_bit(tap_bit),
    .tap_frame_id(tap_frame_id),.mask_accept(mask_accept),.accepted_frame_id(accepted_frame_id),
    .pixel_clk(pixel_clk),.pixel_resetn(pixel_resetn),
    .vid_data24(vid_data24),.vid_active(vid_active),.vid_vsync(vid_vsync),.vid_hsync(vid_hsync),
    .cfg_clk(cfg_clk),.cfg_resetn(cfg_resetn),.cfg_commit(cfg_commit),.cfg_ready(cfg_ready),
    .cfg_enable(cfg_enable),.cfg_frame_id(cfg_frame_id),.cfg_box_count(cfg_box_count),
    .cfg_boxes(cfg_boxes),.cfg_line_x(cfg_line_x),
    .out_data24(new_data),.out_active(new_active),.out_vsync(new_vsync),.out_hsync(new_hsync),
    .capture_count(),.drop_count(),.reject_count(),.captured_frame_id(),
    .displayed_frame_id(),.capture_busy(),.pending_valid(),.completed_valid_status(),
    .display_valid(new_dv),.mask_displayed_frame_id(),.mask_display_valid());

  video_binary_overlay_frozen #(.WIDTH(64),.HEIGHT(64),.VIEW_X(8),.VIEW_Y(8),.VIEW_WIDTH(32),.VIEW_HEIGHT(32)) old_dut (
    .mask_clk(mask_clk),.mask_resetn(mask_resetn),
    .tap_valid(tap_valid),.tap_sof(tap_sof),.tap_eol(tap_eol),.tap_bit(tap_bit),
    .tap_frame_id(tap_frame_id),.mask_accept(mask_accept),.accepted_frame_id(accepted_frame_id),
    .pixel_clk(pixel_clk),.pixel_resetn(pixel_resetn),
    .vid_data24(vid_data24),.vid_active(vid_active),.vid_vsync(vid_vsync),.vid_hsync(vid_hsync),
    .cfg_clk(cfg_clk),.cfg_resetn(cfg_resetn),.cfg_commit(cfg_commit),.cfg_ready(),
    .cfg_enable(cfg_enable),.cfg_frame_id(cfg_frame_id),.cfg_box_count(cfg_box_count),
    .cfg_boxes(cfg_boxes),.cfg_line_x(cfg_line_x),
    .out_data24(old_data),.out_active(old_active),.out_vsync(old_vsync),.out_hsync(old_hsync),
    .capture_count(),.drop_count(),.reject_count(),.captured_frame_id(),
    .displayed_frame_id(),.capture_busy(),.pending_valid(),.completed_valid_status(),
    .display_valid(old_dv),.mask_displayed_frame_id(),.mask_display_valid());

  reg [23:0] old_data_d=0; reg old_active_d=0, old_vsync_d=0, old_hsync_d=0;
  always @(posedge pixel_clk) begin old_data_d<=old_data; old_active_d<=old_active; old_vsync_d<=old_vsync; old_hsync_d<=old_hsync; end

  integer in_black=0, out_gray=0, edge_in=0, edge_out=0, bad_pixel=0, cmp_mismatches=0;

  integer xpos, ypos;
  reg [9:0] dx0=0, dx1=0, dx2=0, dx3=0, dy0=0, dy1=0, dy2=0, dy3=0;
  always @(posedge pixel_clk) begin
    dx0<=xpos; dx1<=dx0; dx2<=dx1; dx3<=dx2;
    dy0<=ypos; dy1<=dy0; dy2<=dy1; dy3<=dy2;
  end

  initial begin : stim
    integer i, py, px;
    repeat(30) @(posedge pixel_clk); mask_resetn<=1; cfg_resetn<=1; repeat(8) @(posedge pixel_clk); pixel_resetn<=1; repeat(8) @(posedge pixel_clk);
    // cfg: enabled, no boxes (count=0), count line disabled (line_x >= WIDTH)
    cfg_enable<=1; cfg_frame_id<=32'd1; cfg_box_count<=5'd0;
    cfg_boxes<=0; cfg_line_x<=10'd64;
    @(posedge cfg_clk); cfg_commit<=1; wait(cfg_ready==1'b0); @(posedge cfg_clk); cfg_commit<=0;
    // publish one all-zero 64x64 mask
    @(posedge mask_clk); tap_sof<=1; tap_valid<=1; tap_bit<=0; tap_frame_id<=32'd1;
    @(posedge mask_clk); tap_sof<=0;
    for (i=1;i<4096;i=i+1) begin tap_eol<=((i%64)==63); @(posedge mask_clk); end
    tap_valid<=0; tap_eol<=0;
    @(posedge mask_clk); mask_accept<=1; accepted_frame_id<=32'd1; @(posedge mask_clk); mask_accept<=0;
    // two frames
    for (py=0; py<64; py=py+1) begin
      vid_vsync<= (py==0);
      for (px=0; px<64; px=px+1) begin xpos<=px; ypos<=py; vid_active<=1; vid_hsync<=(px==0); @(posedge pixel_clk); end
      vid_active<=0; @(posedge pixel_clk);
    end
    for (py=0; py<64; py=py+1) begin
      vid_vsync<= (py==0);
      for (px=0; px<64; px=px+1) begin xpos<=px; ypos<=py; vid_active<=1; vid_hsync<=(px==0); @(posedge pixel_clk); end
      vid_active<=0; @(posedge pixel_clk);
    end
    vid_active<=0;
    repeat(8) @(posedge pixel_clk);
    $display("VIEWPORT: in_black=%0d out_gray=%0d edge_in=%0d edge_out=%0d bad=%0d cmp_mm=%0d",
             in_black, out_gray, edge_in, edge_out, bad_pixel, cmp_mismatches);
    if (in_black==0 || out_gray==0 || edge_in==0 || edge_out==0) $fatal(1,"VIEWPORT_COVERAGE_NOT_MET");
    if (bad_pixel!=0) $fatal(1,"VIEWPORT_UNEXPECTED_PIXEL");
    if (cmp_mismatches!=0) $fatal(1,"VIEWPORT_OLD_NEW_MISMATCH");
    $display("C_VIEWPORT_PASS");
    $finish;
  end

  // d1 tap of the TB-driven source scan: independent coordinate model,
  // calibrated so rendered pixels reproduce the published geometry exactly.
  always @(posedge pixel_clk) begin
    if (pixel_resetn) begin
      if (new_data!==old_data_d || new_active!==old_active_d || new_vsync!==old_vsync_d || new_hsync!==old_hsync_d)
        cmp_mismatches<=cmp_mismatches+1;
      if (old_active && old_dv) begin
        // view = x in 8..39, y in 8..39
        if (dx1>=8 && dx1<=39 && dy1>=8 && dy1<=39) begin
          if (old_data==24'h000000) in_black<=in_black+1; else bad_pixel<=bad_pixel+1;
        end else begin
          if (old_data==24'h404040) out_gray<=out_gray+1; else bad_pixel<=bad_pixel+1;
        end
        if (dx1==8  && dy1==20 && old_data==24'h000000) edge_in<=edge_in+1;   // first view col
        if (dx1==39 && dy1==20 && old_data==24'h000000) edge_in<=edge_in+1;   // last view col
        if (dx1==7  && dy1==20 && old_data==24'h404040) edge_out<=edge_out+1; // just left of view
        if (dx1==40 && dy1==20 && old_data==24'h404040) edge_out<=edge_out+1; // just right of view
        if (dx1==20 && dy1==7  && old_data==24'h404040) edge_out<=edge_out+1; // just above view
        if (dx1==20 && dy1==40 && old_data==24'h404040) edge_out<=edge_out+1; // just below view
      end
    end
  end

  initial begin #30000000; $fatal(1,"watchdog"); end
endmodule