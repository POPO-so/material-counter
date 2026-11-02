`timescale 1ns/1ps
// C candidate: configuration switch and frame correspondence.
//
// Full 64x64 viewport, all-zero mask, no count-line scaling (identity), so a
// probe pixel is white iff the overlay draws there, black if in view and
// unmasked, and base gray if the overlay is disabled (in_view=0).
//
// Frames (frame_idx from the output vsync):
//   1 warm-up (display_valid turns on at the 2nd boundary)
//   2 cfg A: box 10..20/10..20, line 30
//   3 cfg B committed 5 pixel_clk before VSYNC: box 30..40, line 50
//   4 cfg C id=2 while displayed_frame_id=1: box suppressed, line drawn
//   5 cfg D count=0: no box, line drawn
//   6 cfg E illegal box (x0>x1): invalidated, line drawn
//   7 cfg F enable=0: everything suppressed (base gray)
//   8 cfg G committed mid-frame during frame 7: applies at this boundary
module tb_video_binary_overlay_cfgswitch;

  reg pixel_clk=0, mask_clk=0, cfg_clk=0;
  reg pixel_resetn=0, mask_resetn=0, cfg_resetn=0;
  always #5 pixel_clk=~pixel_clk;
  always #10 mask_clk=~mask_clk;
  always #10 cfg_clk=~cfg_clk;

  reg tap_valid=0, tap_sof=0, tap_eol=0, tap_bit=0; reg [31:0] tap_frame_id=0;
  reg mask_accept=0; reg [31:0] accepted_frame_id=0;
  reg cfg_commit=0, cfg_enable=0; reg [31:0] cfg_frame_id=0; reg [4:0] cfg_box_count=0;
  reg [16*40-1:0] cfg_boxes=0; reg [9:0] cfg_line_x=0; wire cfg_ready;
  reg [23:0] vid_data24=24'h404040; reg vid_active=0, vid_vsync=0, vid_hsync=0;
  wire [23:0] new_data, old_data;
  wire new_active, new_vsync, new_hsync, new_dv, old_active, old_vsync, old_hsync, old_dv;

  video_binary_overlay #(.WIDTH(64),.HEIGHT(64),.VIEW_X(0),.VIEW_Y(0)) new_dut (
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

  video_binary_overlay_frozen #(.WIDTH(64),.HEIGHT(64),.VIEW_X(0),.VIEW_Y(0)) old_dut (
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
  reg [9:0] dx0=0, dx1=0, dy0=0, dy1=0;
  always @(posedge pixel_clk) begin dx0<=xpos; dx1<=dx0; dy0<=ypos; dy1<=dy0; end

  reg mid_ok=0;
  event mid_commit;
  integer frame_idx=0, cmp_mismatches=0;
  reg vsync_prev=0;
  // per-scenario probe results
  integer a_box=0, a_line=0;
  integer b_left=0, b_right=0, b_line=0, b_oldbox_black=0, b_oldline_black=0;
  integer c_box_black=0, c_line=0;
  integer d_box_black=0, d_line=0;
  integer e_box_black=0, e_line=0;
  integer f_gray=0;
  integer g_box=0, g_line=0;
  integer corr_disp=0, corr_id=0, corr_bad=0;

  task apply_cfg(input en, input [31:0] id, input [4:0] cnt,
                 input [9:0] bx0, bx1, by0, by1, line);
    reg tgl0;
    begin
      cfg_enable<=en; cfg_frame_id<=id; cfg_box_count<=cnt; cfg_boxes<=0;
      cfg_boxes[0*40+ 0 +:10]<=bx0; cfg_boxes[0*40+10 +:10]<=bx1;
      cfg_boxes[0*40+20 +:10]<=by0; cfg_boxes[0*40+30 +:10]<=by1;
      cfg_line_x<=line;
      tgl0 = new_dut.cfg_toggle;      // sync on publish, not on the ack
      wait(cfg_ready==1'b1);          // previous config already acked
      @(posedge cfg_clk); cfg_commit<=1;
      wait(cfg_ready==1'b0);
      @(posedge cfg_clk); cfg_commit<=0;
      wait(new_dut.cfg_toggle !== tgl0);  // scaling state machine published
      repeat(2) @(posedge cfg_clk);
    end
  endtask

  integer drv_count=0;
  task drive_frame;
    integer px, py;
    begin
      drv_count = drv_count + 1;
      for (py=0; py<64; py=py+1) begin
        vid_vsync <= (py==0);
        for (px=0; px<64; px=px+1) begin
          if (drv_count==7 && py==5 && px==0) -> mid_commit;
          xpos<=px; ypos<=py; vid_active<=1; vid_hsync<=(px==0);
          @(posedge pixel_clk);
        end
        vid_active<=0; @(posedge pixel_clk);
      end
    end
  endtask

  initial begin : stim
    integer i;
    repeat(30) @(posedge pixel_clk); mask_resetn<=1; cfg_resetn<=1;
    repeat(8) @(posedge pixel_clk); pixel_resetn<=1; repeat(8) @(posedge pixel_clk);
    // mask: 64x64 all zero, id=1
    @(posedge mask_clk); tap_sof<=1; tap_valid<=1; tap_bit<=0; tap_frame_id<=32'd1;
    @(posedge mask_clk); tap_sof<=0;
    for (i=1;i<4096;i=i+1) begin tap_eol<=((i%64)==63); @(posedge mask_clk); end
    tap_valid<=0; tap_eol<=0;
    @(posedge mask_clk); mask_accept<=1; accepted_frame_id<=32'd1; @(posedge mask_clk); mask_accept<=0;

    apply_cfg(1, 32'd1, 5'd1, 10'd10,10'd20,10'd10,10'd20,10'd30);  // A
    drive_frame;                                                    // frame 1 warm-up
    drive_frame;                                                    // frame 2 A
    apply_cfg(1, 32'd1, 5'd1, 10'd30,10'd40,10'd10,10'd20,10'd50);  // B
    repeat(5) @(posedge pixel_clk);                                 // near VSYNC
    drive_frame;                                                    // frame 3 B
    apply_cfg(1, 32'd2, 5'd1, 10'd10,10'd20,10'd10,10'd20,10'd30);  // C id mismatch
    drive_frame;                                                    // frame 4 C
    apply_cfg(1, 32'd1, 5'd0, 10'd10,10'd20,10'd10,10'd20,10'd30);  // D count 0
    drive_frame;                                                    // frame 5 D
    apply_cfg(1, 32'd1, 5'd1, 10'd20,10'd10,10'd10,10'd20,10'd30);  // E illegal x0>x1
    drive_frame;                                                    // frame 6 E
    apply_cfg(0, 32'd1, 5'd1, 10'd10,10'd20,10'd10,10'd20,10'd30);  // F disabled
    drive_frame;                                                    // frame 7 F (mid_commit fires)
    wait(mid_ok);                                                   // G applied mid-frame 7
    drive_frame;                                                    // frame 8 G
    vid_active<=0;
    repeat(8) @(posedge pixel_clk);
    $display("CFGSW: a_box=%0d a_line=%0d b_left=%0d b_right=%0d b_line=%0d b_oldbox_black=%0d b_oldline_black=%0d",
             a_box,a_line,b_left,b_right,b_line,b_oldbox_black,b_oldline_black);
    $display("CFGSW: c_box_black=%0d c_line=%0d d_box_black=%0d d_line=%0d e_box_black=%0d e_line=%0d f_gray=%0d g_box=%0d g_line=%0d cmp_mm=%0d",
             c_box_black,c_line,d_box_black,d_line,e_box_black,e_line,f_gray,g_box,g_line,cmp_mismatches);
    $display("CFGSW correspondence: disp_ok=%0d id_en_ok=%0d bad=%0d", corr_disp, corr_id, corr_bad);
    if (a_box==0 || a_line==0) $fatal(1,"CFGSW_A_NOT_DRAWN");
    if (b_left==0 || b_right==0 || b_line==0 || b_oldbox_black==0 || b_oldline_black==0) $fatal(1,"CFGSW_B_NOT_REPLACED");
    if (c_box_black==0 || c_line==0) $fatal(1,"CFGSW_C_ID_MISMATCH_WRONG");
    if (d_box_black==0 || d_line==0) $fatal(1,"CFGSW_D_COUNT0_WRONG");
    if (e_box_black==0 || e_line==0) $fatal(1,"CFGSW_E_ILLEGAL_BOX_WRONG");
    if (f_gray==0) $fatal(1,"CFGSW_F_DISABLE_WRONG");
    if (g_box==0 || g_line==0) $fatal(1,"CFGSW_G_MIDFRAME_NOT_APPLIED");
    if (corr_bad!=0 || corr_disp==0 || corr_id==0) $fatal(1,"CFGSW_FRAME_CORRESPONDENCE_WRONG");
    if (cmp_mismatches!=0) $fatal(1,"CFGSW_OLD_NEW_MISMATCH");
    $display("C_CFGSWITCH_PASS");
    $finish;
  end

  initial begin : mid_helper
    @(mid_commit);
    fork
      begin
        apply_cfg(1, 32'd1, 5'd1, 10'd10,10'd20,10'd10,10'd20,10'd30); // G
        mid_ok = 1;
      end
    join_none
  end

  // d1 tap of the TB-driven scan; probes per scenario frame
  always @(posedge pixel_clk) begin
    vsync_prev<=old_vsync;
    if (old_vsync && !vsync_prev) frame_idx<=frame_idx+1;
    if (pixel_resetn) begin
      if (new_data!==old_data_d || new_active!==old_active_d || new_vsync!==old_vsync_d || new_hsync!==old_hsync_d)
        cmp_mismatches<=cmp_mismatches+1;
      if (frame_idx==3 && dy1==25 && dx1==30 && old_data==24'h000000) b_oldline_black<=b_oldline_black+1;
      if (old_active && old_dv && dy1==15) begin
        if (frame_idx>=7 && (dx1==10 || dx1==30))
          $display("DBG f=%0d x=%0d o=%h en=%b dv=%b", frame_idx, dx1, old_data, new_dut.enable_p, new_dut.display_valid);
        // config / frame_id / displayed-frame correspondence
        if (dx1==10) begin
          if (new_dut.displayed_frame_id==32'd1) corr_disp<=corr_disp+1;
          else corr_bad<=corr_bad+1;
          case (frame_idx)
            2,3,5,6,8: if (new_dut.id_p==32'd1 && new_dut.enable_p==1'b1) corr_id<=corr_id+1;
                       else corr_bad<=corr_bad+1;
            4:         if (new_dut.id_p==32'd2 && new_dut.enable_p==1'b1) corr_id<=corr_id+1;
                       else corr_bad<=corr_bad+1;
            7:         if (new_dut.id_p==32'd1 && new_dut.enable_p==1'b0) corr_id<=corr_id+1;
                       else corr_bad<=corr_bad+1;
            default: ;
          endcase
        end
        case (frame_idx)
          2: begin if (dx1==10 && old_data==24'hffffff) a_box<=a_box+1;
                   if (dx1==30 && old_data==24'hffffff) a_line<=a_line+1; end
          3: begin if (dx1==30 && old_data==24'hffffff) b_left<=b_left+1;
                   if (dx1==40 && old_data==24'hffffff) b_right<=b_right+1;
                   if (dx1==50 && old_data==24'hffffff) b_line<=b_line+1;
                   if (dx1==10 && old_data==24'h000000) b_oldbox_black<=b_oldbox_black+1; end
          4: begin if (dx1==10 && old_data==24'h000000) c_box_black<=c_box_black+1;
                   if (dx1==30 && old_data==24'hffffff) c_line<=c_line+1; end
          5: begin if (dx1==10 && old_data==24'h000000) d_box_black<=d_box_black+1;
                   if (dx1==30 && old_data==24'hffffff) d_line<=d_line+1; end
          6: begin if (dx1==10 && old_data==24'h000000) e_box_black<=e_box_black+1;
                   if (dx1==30 && old_data==24'hffffff) e_line<=e_line+1; end
          7: begin if ((dx1==10 || dx1==30) && old_data==24'h404040) f_gray<=f_gray+1; end
          8: begin if (dx1==10 && old_data==24'hffffff) g_box<=g_box+1;
                   if (dx1==30 && old_data==24'hffffff) g_line<=g_line+1; end
          default: ;
        endcase
      end
    end
  end

  initial begin #30000000; $fatal(1,"watchdog"); end
endmodule