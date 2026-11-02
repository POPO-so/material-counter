`timescale 1ns/1ps

// Standalone regression for atomic pixel-domain overlay configuration updates.
// Compile this test with the fixed standalone/video_binary_overlay.v baseline.
module tb_overlay_atomic_update;
  localparam integer WIDTH = 20;
  localparam integer HEIGHT = 20;
  localparam integer VIEW_WIDTH = 16;
  localparam integer VIEW_HEIGHT = 16;
  localparam integer VIEW_X = 0;
  localparam integer VIEW_Y = 0;

  reg mask_clk = 0;
  reg pixel_clk = 0;
  reg cfg_clk = 0;
  always #3 mask_clk = ~mask_clk;
  always #5 pixel_clk = ~pixel_clk;
  always #7 cfg_clk = ~cfg_clk;

  reg mask_resetn = 0;
  reg pixel_resetn = 0;
  reg cfg_resetn = 0;
  reg tap_valid = 0, tap_sof = 0, tap_eol = 0, tap_bit = 0;
  reg [31:0] tap_frame_id = 0;
  reg mask_accept = 0;
  reg [31:0] accepted_frame_id = 0;
  reg [23:0] vid_data24 = 24'hffffff;
  reg vid_active = 0, vid_vsync = 0, vid_hsync = 0;
  reg cfg_commit = 0, cfg_enable = 0;
  reg [31:0] cfg_frame_id = 0;
  reg [4:0] cfg_box_count = 0;
  reg [39:0] cfg_boxes = 0;
  reg [9:0] cfg_line_x = 10'h3ff;
  wire cfg_ready;
  wire [23:0] out_data24;
  wire out_active, out_vsync, out_hsync;
  wire [31:0] capture_count, drop_count, reject_count;
  wire [31:0] captured_frame_id, displayed_frame_id;
  wire capture_busy, pending_valid, completed_valid_status;
  wire display_valid, mask_display_valid;
  wire [31:0] mask_displayed_frame_id;

  video_binary_overlay #(
    .WIDTH(WIDTH), .HEIGHT(HEIGHT), .VIEW_X(VIEW_X), .VIEW_Y(VIEW_Y),
    .VIEW_WIDTH(VIEW_WIDTH), .VIEW_HEIGHT(VIEW_HEIGHT), .MAX_BOXES(1), .COUNT_ENABLE(0)
  ) dut (
    .mask_clk(mask_clk), .mask_resetn(mask_resetn),
    .tap_valid(tap_valid), .tap_sof(tap_sof), .tap_eol(tap_eol),
    .tap_bit(tap_bit), .tap_frame_id(tap_frame_id),
    .mask_accept(mask_accept), .accepted_frame_id(accepted_frame_id),
    .pixel_clk(pixel_clk), .pixel_resetn(pixel_resetn),
    .vid_data24(vid_data24), .vid_active(vid_active),
    .vid_vsync(vid_vsync), .vid_hsync(vid_hsync),
    .cfg_clk(cfg_clk), .cfg_resetn(cfg_resetn), .cfg_commit(cfg_commit),
    .cfg_ready(cfg_ready), .cfg_enable(cfg_enable),
    .cfg_frame_id(cfg_frame_id), .cfg_box_count(cfg_box_count),
    .cfg_total_count(32'd0), .cfg_boxes(cfg_boxes), .cfg_line_x(cfg_line_x),
    .out_data24(out_data24), .out_active(out_active),
    .out_vsync(out_vsync), .out_hsync(out_hsync),
    .capture_count(capture_count), .drop_count(drop_count),
    .reject_count(reject_count), .captured_frame_id(captured_frame_id),
    .displayed_frame_id(displayed_frame_id), .capture_busy(capture_busy),
    .pending_valid(pending_valid), .completed_valid_status(completed_valid_status),
    .display_valid(display_valid), .mask_displayed_frame_id(mask_displayed_frame_id),
    .mask_display_valid(mask_display_valid)
  );

  integer q1_valid = 0, q1_frame = 0, q1_x = 0, q1_y = 0;
  integer q2_valid = 0, q2_frame = 0, q2_x = 0, q2_y = 0;
  integer q1_active = 0, q1_vsync = 0, q1_hsync = 0;
  integer q2_active = 0, q2_vsync = 0, q2_hsync = 0;
  integer sample_frame = 0, sample_x = 0, sample_y = 0;
  integer checks_frame1_b = 0, checks_frame1_a = 0;
  integer checks_frame2_b = 0, checks_frame2_a = 0;

  // pixel_tick only drives the raster; the pixel clock keeps running during
  // cfg_clk waits too. The scoreboard therefore advances on every pixel edge.
  task automatic pixel_tick(input integer active, input integer vsync,
                            input integer frame_no, input integer px, input integer py);
    begin
      @(negedge pixel_clk);
      vid_active = active;
      vid_vsync = vsync;
      vid_hsync = 0;
      sample_frame = frame_no;
      sample_x = px;
      sample_y = py;
      @(posedge pixel_clk);
      #1;
    end
  endtask

  function automatic [23:0] expected_pixel(input integer frame_no,
                                             input integer px, input integer py);
    begin
      // Outside the camera viewport the core preserves the white base video;
      // inside it an empty binary mask is black unless a box edge is active.
      expected_pixel = 24'hffffff;
      if (px >= VIEW_X && px < VIEW_X+VIEW_WIDTH &&
          py >= VIEW_Y && py < VIEW_Y+VIEW_HEIGHT)
        expected_pixel = 24'h000000;
      if (frame_no == 1 && px == 2 && py == 14)
        expected_pixel = 24'hffffff;
      else if (frame_no == 2 && px == 2 && py == 2)
        expected_pixel = 24'hffffff;
    end
  endfunction

  // The output register follows the two registered compare/reduction stages.
  // Include idle pixel clocks during configuration handshakes in this oracle.
  always @(posedge pixel_clk) begin
    #1;
    if (q2_valid && out_data24 !== expected_pixel(q2_frame, q2_x, q2_y)) begin
      $display("FAIL pixel frame=%0d x=%0d y=%0d got=%06h expected=%06h",
               q2_frame, q2_x, q2_y, out_data24,
               expected_pixel(q2_frame, q2_x, q2_y));
      $fatal(1, "overlay configuration changed within a frame or missed VSYNC update");
    end
    if (q2_valid && (out_active !== q2_active[0] || out_vsync !== q2_vsync[0] ||
                     out_hsync !== q2_hsync[0]))
      $fatal(1, "video timing metadata is not aligned with the two-cycle pixel pipeline");
    if (q2_valid) begin
      if (q2_frame == 1 && q2_x == 2 && q2_y == 2) checks_frame1_b = checks_frame1_b + 1;
      if (q2_frame == 1 && q2_x == 2 && q2_y == 14) checks_frame1_a = checks_frame1_a + 1;
      if (q2_frame == 2 && q2_x == 2 && q2_y == 2) checks_frame2_b = checks_frame2_b + 1;
      if (q2_frame == 2 && q2_x == 2 && q2_y == 14) checks_frame2_a = checks_frame2_a + 1;
    end
    q2_valid = q1_valid;
    q2_frame = q1_frame;
    q2_x = q1_x;
    q2_y = q1_y;
    q2_active = q1_active;
    q2_vsync = q1_vsync;
    q2_hsync = q1_hsync;
    q1_valid = vid_active;
    q1_frame = sample_frame;
    q1_x = sample_x;
    q1_y = sample_y;
    q1_active = vid_active;
    q1_vsync = vid_vsync;
    q1_hsync = vid_hsync;
  end

  task automatic commit_boxes(input [39:0] boxes);
    integer timeout;
    begin
      timeout = 0;
      while (!cfg_ready && timeout < 200) begin
        @(posedge cfg_clk); timeout = timeout + 1;
      end
      if (!cfg_ready) $fatal(1, "configuration mailbox did not become ready");
      @(negedge cfg_clk);
      cfg_boxes = boxes;
      cfg_frame_id = 32'd100;
      cfg_box_count = 1;
      cfg_enable = 1;
      cfg_commit = 1;
      @(negedge cfg_clk);
      cfg_commit = 0;
    end
  endtask

  task automatic emit_line(input integer frame_no, input integer row);
    integer col;
    begin
      for (col = 0; col < WIDTH; col = col + 1)
        pixel_tick(1, 0, frame_no, col, row);
      pixel_tick(0, 0, frame_no, 0, 0);
    end
  endtask

  task automatic frame_boundary(input integer next_frame);
    begin
      pixel_tick(0, 1, next_frame, 0, 0);
      pixel_tick(0, 0, next_frame, 0, 0);
    end
  endtask

  integer row, col, timeout;
  initial begin
    repeat (5) @(posedge pixel_clk);
    mask_resetn = 1;
    pixel_resetn = 1;
    cfg_resetn = 1;
    repeat (5) @(posedge pixel_clk);

    // Capture an all-zero mask through the public stream, accept it, and let
    // the normal publish/VSYNC handshake make display_valid true.
    for (row = 0; row < HEIGHT; row = row + 1) begin
      for (col = 0; col < WIDTH; col = col + 1) begin
        @(negedge mask_clk);
        tap_valid = 1;
        tap_sof = (row == 0 && col == 0);
        tap_eol = (col == WIDTH-1);
        tap_bit = 0;
        tap_frame_id = 32'd100;
        @(posedge mask_clk);
      end
    end
    @(negedge mask_clk); tap_valid = 0; tap_sof = 0; tap_eol = 0;
    wait (completed_valid_status && captured_frame_id == 32'd100);
    @(negedge mask_clk);
    accepted_frame_id = 32'd100;
    mask_accept = 1;
    @(negedge mask_clk); mask_accept = 0;
    wait (pending_valid);

    // Configuration A: a single-pixel border sample on a later row.
    commit_boxes({10'd18, 10'd18, 10'd3, 10'd3});
    // Allow the 32-cycle BCD conversion and coordinate staging to finish,
    // then let the pixel-domain mailbox latch A at a frame boundary.
    repeat (80) pixel_tick(0, 0, 0, 0, 0);
    frame_boundary(1);
    timeout = 0;
    while (!cfg_ready && timeout < 20) begin
      @(posedge cfg_clk); timeout = timeout + 1;
    end
    if (!cfg_ready) $fatal(1, "configuration A mailbox did not complete");
    timeout = 0;
    while (!display_valid && timeout < 20) begin
      pixel_tick(0, 0, 1, 0, 0); timeout = timeout + 1;
    end
    timeout = 0;
    while ((!mask_display_valid || pending_valid) && timeout < 20) begin
      @(posedge mask_clk); timeout = timeout + 1;
    end
    if (!display_valid || !mask_display_valid || pending_valid || displayed_frame_id != 32'd100) begin
      $display("FAIL publish state: display=%0b mask_display=%0b displayed_id=%0d captured_id=%0d pending=%0b reject=%0d drop=%0d",
               display_valid, mask_display_valid, displayed_frame_id, captured_frame_id,
               pending_valid, reject_count, drop_count);
      $fatal(1, "mask capture/accept/publish did not establish display_valid");
    end

    // Start frame 1, then commit B before its early target row. B must remain
    // invisible for this frame; A must remain visible on its later row.
    emit_line(1, 0);
    commit_boxes({10'd3, 10'd3, 10'd3, 10'd3});
    for (row = 1; row < HEIGHT; row = row + 1) emit_line(1, row);
    repeat (3) pixel_tick(0, 0, 1, 0, 0);
    if (checks_frame1_b != 1 || checks_frame1_a != 1)
      $fatal(1, "frame 1 target pixels not observed exactly once: B=%0d A=%0d",
             checks_frame1_b, checks_frame1_a);

    // The next VSYNC is the only permitted point where B becomes active.
    frame_boundary(2);
    for (row = 0; row < HEIGHT; row = row + 1) emit_line(2, row);
    repeat (3) pixel_tick(0, 0, 2, 0, 0);
    if (checks_frame2_b != 1 || checks_frame2_a != 1)
      $fatal(1, "frame 2 target pixels not observed exactly once: B=%0d A=%0d",
             checks_frame2_b, checks_frame2_a);
    if (reject_count != 0 || drop_count != 0)
      $fatal(1, "mask handshake unexpectedly rejected/dropped: reject=%0d drop=%0d",
             reject_count, drop_count);
    $display("TEST_PASS overlay atomic update: frame1 B=black A=white; frame2 B=white A=black");
    $finish;
  end

  initial begin
    #200000;
    $fatal(1, "testbench timeout");
  end
endmodule
