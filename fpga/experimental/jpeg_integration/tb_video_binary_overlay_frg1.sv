`timescale 1ns/1ps
// C candidate: real FRG1 parameters + non-zero row/column mask.
//
// WIDTH=640 HEIGHT=400 VIEW_X=384 VIEW_Y=146 VIEW_WIDTH=512 VIEW_HEIGHT=320
// satisfies HEIGHT*4==VIEW_HEIGHT*5, so the g_row_base_5_4 branch is the one
// elaborated (the TB asserts the same condition the RTL generate uses).
//
// The mask is a checkerboard bit(x,y) = (x+y)&1 written in raster order. With
// no boxes and no count line, the output inside the view must be white iff the
// source mask bit at (source_x,source_y) is 1. source_x=lx+(lx>>2),
// source_y=ly+(ly>>2) with lx=x-384, ly=y-146. Any row-base stepping error,
// phase-wrap error, or first/last-row error changes the parity and shows up.
// The whole frame is driven twice so the next-frame reset is exercised too.
//
// Reachability note: VIEW_X+VIEW_WIDTH=896 > WIDTH=640, so the physically
// reachable window is x in [384,639], y in [146,399] (256x254). This is a
// property of the frozen baseline, not a candidate change.
module tb_video_binary_overlay_frg1;

  localparam integer W=640, H=400, VX=384, VY=146, VW=512, VH=320;
  localparam [23:0] WHITE=24'hffffff, BLACK=24'h000000, BASE=24'h404040;

  reg pixel_clk=0, mask_clk=0, cfg_clk=0;
  reg pixel_resetn=0, mask_resetn=0, cfg_resetn=0;
  always #5  pixel_clk=~pixel_clk;   // 100 MHz
  always #10 mask_clk=~mask_clk;     // 50 MHz
  always #10 cfg_clk=~cfg_clk;

  reg tap_valid=0, tap_sof=0, tap_eol=0, tap_bit=0; reg [31:0] tap_frame_id=0;
  reg mask_accept=0; reg [31:0] accepted_frame_id=0;
  reg cfg_commit=0, cfg_enable=0; reg [31:0] cfg_frame_id=0; reg [4:0] cfg_box_count=0;
  reg [16*40-1:0] cfg_boxes=0; reg [9:0] cfg_line_x=0; wire cfg_ready;
  reg [23:0] vid_data24=BASE; reg vid_active=0, vid_vsync=0, vid_hsync=0;
  wire [23:0] new_data, old_data;
  wire new_active, new_vsync, new_hsync, new_dv, old_active, old_vsync, old_hsync, old_dv;

  video_binary_overlay #(.WIDTH(W),.HEIGHT(H),.VIEW_X(VX),.VIEW_Y(VY),.VIEW_WIDTH(VW),.VIEW_HEIGHT(VH)) new_dut (
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

  video_binary_overlay_frozen #(.WIDTH(W),.HEIGHT(H),.VIEW_X(VX),.VIEW_Y(VY),.VIEW_WIDTH(VW),.VIEW_HEIGHT(VH)) old_dut (
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

  integer xpos, ypos;
  reg [9:0] dx0=0, dx1=0, dx2=0; reg [9:0] dy0=0, dy1=0, dy2=0;
  always @(posedge pixel_clk) begin
    dx0<=xpos; dx1<=dx0; dx2<=dx1;
    dy0<=ypos; dy1<=dy0; dy2<=dy1;
  end

  integer in_checked=0, out_checked=0, mismatch_in=0, bad_out=0, cmp_mismatches=0;
  integer row_first=0, row_last=0, row_phase3=0, row_wrap=0;
  integer f1_checked=0, f2_checked=0, f3_checked=0, frame_idx=0;
  reg vsync_prev=0;

  initial begin : stim
    integer i, py, px, beat;
    if (!(H*4==VH*5)) $fatal(1,"ROW_BASE_BRANCH_NOT_ACTIVE");
    $display("ROW_BASE_BRANCH_ACTIVE HEIGHT*4=%0d VIEW_HEIGHT*5=%0d", H*4, VH*5);
    $display("SOURCE_X_BRANCH_ACTIVE WIDTH*4=%0d VIEW_WIDTH*5=%0d", W*4, VW*5);
    repeat(30) @(posedge pixel_clk); mask_resetn<=1; cfg_resetn<=1;
    repeat(8) @(posedge pixel_clk); pixel_resetn<=1; repeat(8) @(posedge pixel_clk);
    // cfg: enabled, no boxes, count line disabled (line_x >= WIDTH)
    cfg_enable<=1; cfg_frame_id<=32'd1; cfg_box_count<=5'd0; cfg_boxes<=0; cfg_line_x<=10'd640;
    @(posedge cfg_clk); cfg_commit<=1; wait(cfg_ready==1'b0); @(posedge cfg_clk); cfg_commit<=0;
    // mask: full 640x400 checkerboard, raster order
    beat=0;
    @(posedge mask_clk); tap_sof<=1; tap_valid<=1; tap_bit<=((0+0)&1); tap_frame_id<=32'd1;
    @(posedge mask_clk); tap_sof<=0;
    for (beat=1; beat<W*H; beat=beat+1) begin
      tap_bit  <= (((beat%W) + (beat/W)) & 1);
      tap_eol  <= ((beat%W)==W-1);
      @(posedge mask_clk);
    end
    tap_valid<=0; tap_eol<=0;
    @(posedge mask_clk); mask_accept<=1; accepted_frame_id<=32'd1; @(posedge mask_clk); mask_accept<=0;
    // three full frames: the mask handshake completes at the second frame
    // boundary, so frames 2 and 3 are both overlay-active and prove the
    // next-frame row-base reset.
    for (py=0; py<H; py=py+1) begin
      vid_vsync<= (py==0);
      for (px=0; px<W; px=px+1) begin xpos<=px; ypos<=py; vid_active<=1; vid_hsync<=(px==0); @(posedge pixel_clk); end
      vid_active<=0; @(posedge pixel_clk);
    end
    for (py=0; py<H; py=py+1) begin
      vid_vsync<= (py==0);
      for (px=0; px<W; px=px+1) begin xpos<=px; ypos<=py; vid_active<=1; vid_hsync<=(px==0); @(posedge pixel_clk); end
      vid_active<=0; @(posedge pixel_clk);
    end
    for (py=0; py<H; py=py+1) begin
      vid_vsync<= (py==0);
      for (px=0; px<W; px=px+1) begin xpos<=px; ypos<=py; vid_active<=1; vid_hsync<=(px==0); @(posedge pixel_clk); end
      vid_active<=0; @(posedge pixel_clk);
    end
    vid_active<=0;
    repeat(8) @(posedge pixel_clk);
    $display("FRG1: in_checked=%0d out_checked=%0d mismatch_in=%0d bad_out=%0d cmp_mm=%0d",
             in_checked, out_checked, mismatch_in, bad_out, cmp_mismatches);
    $display("FRG1 coverage: row_first=%0d row_last=%0d row_phase3=%0d row_wrap=%0d f1=%0d f2=%0d f3=%0d",
             row_first, row_last, row_phase3, row_wrap, f1_checked, f2_checked, f3_checked);
    if (mismatch_in!=0 || bad_out!=0 || cmp_mismatches!=0) $fatal(1,"FRG1_DATA_MISMATCH");
    if (in_checked==0 || out_checked==0) $fatal(1,"FRG1_NO_PIXELS");
    if (row_first==0 || row_last==0 || row_phase3==0 || row_wrap==0) $fatal(1,"FRG1_ROW_COVERAGE_NOT_MET");
    if (f2_checked==0 || f3_checked==0) $fatal(1,"FRG1_FRAME_COVERAGE_NOT_MET");
    $display("C_FRG1_PASS");
    $finish;
  end

  // d1 tap of the TB-driven source scan (independent coordinate model).
  always @(posedge pixel_clk) begin
    vsync_prev<=old_vsync;
    if (old_vsync && !vsync_prev) frame_idx<=frame_idx+1;
    if (pixel_resetn) begin
      if (new_data!==old_data_d || new_active!==old_active_d || new_vsync!==old_vsync_d || new_hsync!==old_hsync_d)
        cmp_mismatches<=cmp_mismatches+1;
      if (old_active && old_dv && dy1>=1) begin
        if (dx1>=VX && dx1<VX+VW && dy1>=VY && dy1<VY+VH) begin
          if ( (((dx1-VX)+((dx1-VX)>>2)) + ((dy1-VY)+((dy1-VY)>>2))) & 1 )
            begin if (old_data!==WHITE) mismatch_in<=mismatch_in+1; end
          else
            begin if (old_data!==BLACK) mismatch_in<=mismatch_in+1; end
          in_checked<=in_checked+1;
          if (frame_idx==1) f1_checked<=f1_checked+1;
          else if (frame_idx==2) f2_checked<=f2_checked+1;
          else if (frame_idx==3) f3_checked<=f3_checked+1;
          if (dy1==VY)        row_first<=row_first+1;
          if (dy1==H-1)       row_last<=row_last+1;
          if (dy1==VY+3)      row_phase3<=row_phase3+1;  // local_y=3, phase 3
          if (dy1==VY+4)      row_wrap<=row_wrap+1;      // local_y=4, wrap adds 2 rows
        end else begin
          if (old_data!==BASE) bad_out<=bad_out+1;
          out_checked<=out_checked+1;
        end
      end
    end
  end

  initial begin #120000000; $fatal(1,"watchdog"); end
endmodule