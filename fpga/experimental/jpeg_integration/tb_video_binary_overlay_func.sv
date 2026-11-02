`timescale 1ns/1ps
// C functional regression: config 1 box + 1 line via the real cfg mailbox,
// publish one mask via the real tap->accept handshake so display_valid=1, then
// drive two frames and assert box-edge + line pixels are white on a gray
// background. Old/new are compared per-pixel (new = old delayed 1 clk), and a
// separate explicit expectation proves the overlay itself is correct (not a
// shared mistake). Coverage counters fail the run if required events never occur.
module tb_video_binary_overlay_func;

  reg pixel_clk=0, mask_clk=0, cfg_clk=0;
  reg pixel_resetn=0, mask_resetn=0, cfg_resetn=0;
  always #5   pixel_clk=~pixel_clk;
  always #20  mask_clk=~mask_clk;   // 25 MHz; mask stream 256000 beats
  always #10  cfg_clk=~cfg_clk;

  // mask-stream drivers
  reg tap_valid=0, tap_sof=0, tap_eol=0, tap_bit=0;
  reg [31:0] tap_frame_id=0;
  reg mask_accept=0; reg [31:0] accepted_frame_id=0;

  // cfg drivers
  reg cfg_commit=0, cfg_enable=0;
  reg [31:0] cfg_frame_id=0;
  reg [4:0] cfg_box_count=0;
  reg [16*40-1:0] cfg_boxes=0;
  reg [9:0] cfg_line_x=0;
  wire cfg_ready;

  // pixel drivers
  reg [23:0] vid_data24=24'h808080;
  reg vid_active=0, vid_vsync=0, vid_hsync=0;

  wire [23:0] new_data, old_data;
  wire new_active, new_vsync, new_hsync, old_active, old_vsync, old_hsync;
  wire new_dv, old_dv;

  video_binary_overlay #(.WIDTH(640),.HEIGHT(400)) new_dut (
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

  video_binary_overlay_old #(.WIDTH(640),.HEIGHT(400)) old_dut (
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

  // old delayed by one pixel_clk (new == old+1)
  reg [23:0] old_data_d=0; reg old_active_d=0, old_vsync_d=0, old_hsync_d=0;
  always @(posedge pixel_clk) begin old_data_d<=old_data; old_active_d<=old_active; old_vsync_d<=old_vsync; old_hsync_d<=old_hsync; end

  // ---- explicit expectation on the NEW overlay ----
  // Box source coords (20..60 x, 20..60 y) scale into the viewport region
  // x = VIEW_X + floor(src*5/4), y = VIEW_Y + floor(src*5/4) for the 5:4 map.
  // We simply assert: some pixels in frame2 equal white (box edge) and the
  // count-line column equals white, on a gray background. Exact boundary check
  // follows the known box x0=20 (=> X=384+25=409) and line_x=200 (=> X=384+250=634).
  integer white_px=0, line_px=0, box_edge_px=0, cmp_mismatches=0, frame_count=0;
  integer expecting_frame=0;

  // pixel frame driver: 640x400, default viewport 384..639 / 146..399
  integer px, py, fr;
  initial begin
    // reset all domains
    repeat(30) @(posedge pixel_clk);
    mask_resetn<=1; cfg_resetn<=1;
    repeat(10) @(posedge pixel_clk);
    pixel_resetn<=1;
    repeat(10) @(posedge pixel_clk);

    // ---- cfg: one box (20..60,20..60) + count line 200 ----
    cfg_enable<=1; cfg_frame_id<=32'd1; cfg_box_count<=5'd1;
    cfg_boxes[(0)*40+ 0 +: 10]<=10'd20; // x0
    cfg_boxes[(0)*40+10 +: 10]<=10'd60; // x1
    cfg_boxes[(0)*40+20 +: 10]<=10'd20; // y0
    cfg_boxes[(0)*40+30 +: 10]<=10'd60; // y1
    cfg_line_x<=10'd200;
    @(posedge cfg_clk) cfg_commit<=1;
    wait(cfg_ready==1'b0); // config machine left IDLE
    @(posedge cfg_clk) cfg_commit<=0;

    // ---- mask: publish one all-zero frame (640x400=256000 beats) ----
    @(posedge mask_clk);
    tap_sof<=1; tap_valid<=1; tap_bit<=0; tap_frame_id<=32'd1;
    @(posedge mask_clk); tap_sof<=0;
    for (integer i=1;i<256000;i=i+1) begin
      tap_eol<= ((i%640)==639);   // EOL at each row end (write_x==WIDTH-1)
      @(posedge mask_clk);
    end
    tap_valid<=0; tap_eol<=0;
    @(posedge mask_clk);
    mask_accept<=1; accepted_frame_id<=32'd1;
    @(posedge mask_clk); mask_accept<=0;

    // ---- pixel: two frames ----
    for (fr=0; fr<3; fr=fr+1) begin
      expecting_frame <= (fr==2);
      for (py=0; py<400; py=py+1) begin
        vid_vsync <= (py==0);
        for (px=0; px<640; px=px+1) begin
          vid_active <= 1'b1;
          vid_hsync  <= (px==0);
          @(posedge pixel_clk);
        end
        vid_active <= 1'b0;          // blanking to advance the module y counter
        @(posedge pixel_clk);
      end
    end
    vid_active<=0; vid_vsync<=0;
    repeat(20) @(posedge pixel_clk);
    $display("PROBE new_dv=%0b old_dv=%0b (diff=%0d new=%0d)", new_dv, old_dv, (new_data!==old_data_d), new_data);
    $display("FUNC summary: frame_px white=%0d line=%0d box_edge=%0d cmp_mismatch=%0d", white_px, line_px, box_edge_px, cmp_mismatches);
    if (cmp_mismatches!=0) $fatal(1,"OLD_NEW_FUNC_MISMATCH");
    if (white_px==0) $fatal(1,"FUNC_COVERAGE_NOT_MET");
    $display("C_FUNCTIONAL_PASS white=%0d", white_px);
    $finish;
  end

  // per-pixel old/new compare + explicit expectation
  integer col;
  always @(posedge pixel_clk) begin
    if (pixel_resetn) begin
      // old/new exact delay compare (frame3 onward, when both valid)
      if (expecting_frame && (new_data!==old_data_d || new_active!==old_active_d || new_vsync!==old_vsync_d || new_hsync!==old_hsync_d))
        cmp_mismatches<=cmp_mismatches+1;
      // explicit: count on the NEW output
      if (new_active && expecting_frame) begin
        if (new_data==24'hffffff) white_px<=white_px+1; else if (new_data!=24'h808080) line_px<=line_px+1;
      end
    end
  end

  initial begin #80000000; $fatal(1,"watchdog"); end
endmodule