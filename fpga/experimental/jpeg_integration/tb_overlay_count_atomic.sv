`timescale 1ns/1ps

module tb_overlay_count_atomic;
  localparam integer WIDTH=128, HEIGHT=64;
  reg mask_clk=0, pixel_clk=0, cfg_clk=0;
  always #3 mask_clk=~mask_clk;
  always #5 pixel_clk=~pixel_clk;
  always #7 cfg_clk=~cfg_clk;

  reg mask_resetn=0, pixel_resetn=0, cfg_resetn=0;
  reg tap_valid=0, tap_sof=0, tap_eol=0, tap_bit=0;
  reg [31:0] tap_frame_id=0;
  reg mask_accept=0;
  reg [31:0] accepted_frame_id=0;
  reg [23:0] vid_data24=24'hffffff;
  reg vid_active=0, vid_vsync=0, vid_hsync=0;
  reg cfg_commit=0, cfg_enable=0;
  reg [31:0] cfg_frame_id=0, cfg_total_count=0;
  reg [4:0] cfg_box_count=0;
  reg [39:0] cfg_boxes=0;
  reg [9:0] cfg_line_x=WIDTH;
  wire cfg_ready;
  wire [23:0] out_data24;
  wire out_active, out_vsync, out_hsync;
  wire [31:0] capture_count, drop_count, reject_count;
  wire [31:0] captured_frame_id, displayed_frame_id;
  wire capture_busy, pending_valid, completed_valid_status;
  wire display_valid, mask_display_valid;
  wire [31:0] mask_displayed_frame_id;
  integer q1_valid=0, q1_x=0, q1_y=0;
  integer q2_valid=0, q2_x=0, q2_y=0;

  video_binary_overlay #(
    .WIDTH(WIDTH), .HEIGHT(HEIGHT), .VIEW_X(0), .VIEW_Y(0),
    .VIEW_WIDTH(64), .VIEW_HEIGHT(32), .MAX_BOXES(1),
    .COUNT_ENABLE(1), .COUNT_X(40), .COUNT_Y(40)
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
    .cfg_total_count(cfg_total_count), .cfg_boxes(cfg_boxes),
    .cfg_line_x(cfg_line_x), .out_data24(out_data24),
    .out_active(out_active), .out_vsync(out_vsync), .out_hsync(out_hsync),
    .capture_count(capture_count), .drop_count(drop_count),
    .reject_count(reject_count), .captured_frame_id(captured_frame_id),
    .displayed_frame_id(displayed_frame_id), .capture_busy(capture_busy),
    .pending_valid(pending_valid), .completed_valid_status(completed_valid_status),
    .display_valid(display_valid), .mask_displayed_frame_id(mask_displayed_frame_id),
    .mask_display_valid(mask_display_valid)
  );

  task automatic frame_boundary;
    begin
      @(negedge pixel_clk); vid_active=0; vid_vsync=1;
      @(posedge pixel_clk); #1;
      @(negedge pixel_clk); vid_vsync=0;
      @(posedge pixel_clk); #1;
      q1_valid=0; q2_valid=0;
    end
  endtask

  task automatic pixel(input integer active, input integer px, input integer py);
    begin
      @(negedge pixel_clk);
      vid_active=active; vid_vsync=0;
      @(posedge pixel_clk); #1;
      if(q2_valid && q2_y==40 && q2_x==115 && out_data24!==24'hffffff)
        $fatal(1,"count 7 top segment missing: got %06h",out_data24);
      if(q2_valid && q2_y==40 && q2_x==112 && out_data24!==24'h000000)
        $fatal(1,"count panel did not cover the upper-left blank: got %06h",out_data24);
      if(q2_valid && q2_y==41 && q2_x==118 && out_data24!==24'hffffff)
        $fatal(1,"count 7 upper-right segment missing: got %06h",out_data24);
      if(q2_valid && q2_y==41 && q2_x==112 && out_data24!==24'h000000)
        $fatal(1,"count 7 unexpectedly lit upper-left segment: got %06h dut_x=%0d dut_y=%0d bcd=%010h digit=%0d gx=%0d gy=%0d seg=%07b panel_q=%0b glyph_q=%0b total=%0d bcd_src=%0d bcd_value=%010h payload=%010h dest=%010h req=%0b ack=%0b",
               out_data24,dut.x,dut.y,dut.count_bcd_active_p,dut.count_digit_index,
               dut.count_glyph_x,dut.count_glyph_y,dut.count_segments,
               dut.count_panel_s2,dut.count_glyph_s2,cfg_total_count,dut.cfg_bcd_source,
               dut.cfg_bcd_value,dut.cfg_cdc_src_data[39:0],dut.cfg_cdc_dest_data[39:0],
               dut.cfg_cdc_dest_req,dut.cfg_cdc_dest_ack);
      q2_valid=q1_valid; q2_x=q1_x; q2_y=q1_y;
      q1_valid=active; q1_x=px; q1_y=py;
    end
  endtask

  integer timeout, row, col;
  initial begin
    repeat(5) @(posedge pixel_clk);
    mask_resetn=1; pixel_resetn=1; cfg_resetn=1;
    repeat(5) @(posedge cfg_clk);
    timeout=0;
    while(!cfg_ready && timeout<100) begin @(posedge cfg_clk); timeout=timeout+1; end
    if(!cfg_ready) $fatal(1,"initial config handshake did not become ready");
    @(negedge cfg_clk);
    cfg_enable=1; cfg_frame_id=0; cfg_total_count=7; cfg_box_count=0;
    cfg_commit=1;
    @(negedge cfg_clk); cfg_commit=0;
    timeout=0;
    while(!cfg_ready && timeout<200) begin
      repeat(8) @(posedge cfg_clk);
      frame_boundary();
      timeout=timeout+1;
    end
    if(!cfg_ready) $fatal(1,"count mailbox did not complete at a VSYNC boundary");
    if(dut.cfg_bcd_value!==40'h0000000007 || dut.cfg_cdc_src_data[39:0]!==40'h0000000007 ||
       dut.cfg_cdc_dest_data[39:0]!==40'h0000000007 || dut.count_bcd_active_p!==40'h0000000007)
      $fatal(1,"count BCD did not cross source/mailbox/pixel domains: source=%010h payload=%010h dest=%010h pixel=%010h",
             dut.cfg_bcd_value,dut.cfg_cdc_src_data[39:0],dut.cfg_cdc_dest_data[39:0],dut.count_bcd_active_p);

    for(row=0;row<40;row=row+1) begin
      for(col=0;col<WIDTH;col=col+1) pixel(1,col,row);
      pixel(0,0,row);
    end
    for(col=0;col<WIDTH;col=col+1) pixel(1,col,40);
    pixel(0,0,40);
    for(col=0;col<WIDTH;col=col+1) pixel(1,col,41);
    $display("TEST_PASS count glyph: BCD 7, black panel, same pixel-domain frame latch");
    $finish;
  end

  initial begin
    #2000000;
    $fatal(1,"testbench timeout");
  end
endmodule
