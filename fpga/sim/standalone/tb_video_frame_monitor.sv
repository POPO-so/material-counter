`timescale 1ns/1ps

module tb_video_frame_monitor;
  localparam integer WIDTH = 256;
  localparam integer HEIGHT = 4;
  localparam integer MARKER_X = 8;

  reg pixel_clk = 0;
  reg axi_clk = 0;
  always #7 pixel_clk = ~pixel_clk;
  always #5 axi_clk = ~axi_clk;

  reg pixel_resetn = 0;
  reg axi_resetn = 0;
  reg [23:0] vid_data = 0;
  reg vid_active = 0;
  reg vid_vsync = 0;
  reg [7:0] awaddr = 0;
  reg [3:0] wstrb = 0;
  reg awvalid = 0, wvalid = 0, bready = 0;
  reg [31:0] wdata = 0;
  wire awready, wready, bvalid;
  wire [1:0] bresp;
  reg [7:0] araddr = 0;
  reg arvalid = 0, rready = 0;
  wire arready, rvalid;
  wire [31:0] rdata;
  wire [1:0] rresp;

  video_frame_monitor #(.WIDTH(WIDTH), .HEIGHT(HEIGHT), .MARKER_X(MARKER_X)) dut (
    .pixel_clk(pixel_clk), .pixel_resetn(pixel_resetn),
    .vid_data(vid_data), .vid_active(vid_active), .vid_vsync(vid_vsync),
    .s_axi_aclk(axi_clk), .s_axi_aresetn(axi_resetn),
    .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
    .s_axi_wdata(wdata), .s_axi_wstrb(wstrb), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
    .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
    .s_axi_araddr(araddr), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
    .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rvalid(rvalid), .s_axi_rready(rready)
  );

  wire full_awready, full_wready, full_bvalid, full_arready, full_rvalid;
  wire [1:0] full_bresp, full_rresp;
  wire [31:0] full_rdata;
  video_frame_monitor #(.WIDTH(1280), .HEIGHT(720), .MARKER_X(640)) full_dut (
    .pixel_clk(pixel_clk), .pixel_resetn(pixel_resetn),
    .vid_data(vid_data), .vid_active(vid_active), .vid_vsync(vid_vsync),
    .s_axi_aclk(axi_clk), .s_axi_aresetn(axi_resetn),
    .s_axi_awaddr(8'b0), .s_axi_awvalid(1'b0), .s_axi_awready(full_awready),
    .s_axi_wdata(32'b0), .s_axi_wstrb(4'b0), .s_axi_wvalid(1'b0), .s_axi_wready(full_wready),
    .s_axi_bresp(full_bresp), .s_axi_bvalid(full_bvalid), .s_axi_bready(1'b0),
    .s_axi_araddr(8'b0), .s_axi_arvalid(1'b0), .s_axi_arready(full_arready),
    .s_axi_rdata(full_rdata), .s_axi_rresp(full_rresp), .s_axi_rvalid(full_rvalid), .s_axi_rready(1'b0)
  );

  function automatic [15:0] crc16_ccitt_false(input [207:0] bits_in);
    integer i;
    reg [15:0] c;
    begin
      c = 16'hffff;
      for (i = 207; i >= 0; i = i - 1) begin
        if (c[15] ^ bits_in[i]) c = {c[14:0],1'b0} ^ 16'h1021;
        else c = {c[14:0],1'b0};
      end
      crc16_ccitt_false = c;
    end
  endfunction

  function automatic [223:0] make_marker(input [127:0] session_id, input [63:0] frame_id);
    reg [207:0] body;
    begin
      body = {16'h4d43, session_id, frame_id};
      make_marker = {body, crc16_ccitt_false(body)};
    end
  endfunction

  task automatic pixel(input reg active, input reg [23:0] rgb);
    begin
      @(negedge pixel_clk); vid_active = active; vid_data = rgb;
      @(posedge pixel_clk);
    end
  endtask

  task automatic set_vsync(input reg value);
    begin @(negedge pixel_clk); vid_active = 0; vid_vsync = value; @(posedge pixel_clk); end
  endtask

  task automatic send_small_frame(input [127:0] session_id, input [63:0] frame_id,
                                  input integer fault_mode);
    reg [223:0] mtop, mbot;
    reg [23:0] p;
    integer y, x, bit_index;
    begin
      mtop = make_marker(session_id, frame_id);
      mbot = mtop;
      if (fault_mode == 1) mbot[10] = ~mbot[10]; // bottom identity/CRC mismatch
      if (fault_mode == 2) mtop[0] = ~mtop[0];  // CRC error
      if (fault_mode == 9) mbot = make_marker(session_id, frame_id + 1'b1);
      for (y = 0; y < HEIGHT; y = y + 1) begin
        for (x = 0; x < WIDTH
             - ((fault_mode == 4) && (y == HEIGHT-1) ? 1 : 0)
             - ((fault_mode == 6) && (y == 0) ? 1 : 0)
             + ((fault_mode == 6) && (y == 1) ? 1 : 0); x = x + 1) begin
          p = (fault_mode == 7) ? 24'h808080 : 24'h000000;
          if (fault_mode == 7 && y == 1 && x < 4) p = 24'h000000;
          if (fault_mode == 7 && y == 1 && x >= 4 && x < 8) p = 24'hffffff;
          if ((y == 0 || y == HEIGHT-1) && x >= MARKER_X && x < MARKER_X+224) begin
            bit_index = 223 - (x - MARKER_X);
            if (y == 0) p = mtop[bit_index] ? 24'hffffff : 24'h000000;
            else p = mbot[bit_index] ? 24'hffffff : 24'h000000;
          end
          if (fault_mode == 8 && y == 0 && x >= MARKER_X && x < MARKER_X+224 &&
              !mtop[bit_index]) p = 24'h808080;
          if (fault_mode == 3 && y == 1 && x == WIDTH-1) begin
            pixel(1, 24'h123456); // non-gray invalid pixel
          end else pixel(1, p);
        end
        pixel(0,24'h000000); // active-low horizontal blank separates rows
      end
      if (fault_mode == 5) pixel(1,24'h000000); // one pixel beyond frame geometry
      set_vsync(1);
      set_vsync(0);
    end
  endtask

  task automatic axi_write(input [7:0] addr, input [31:0] data,
                           input [3:0] strb, input integer aw_first, input integer hold_b);
    reg aw_done, w_done;
    begin
      aw_done = 0; w_done = 0;
      if (aw_first != 0) begin
        @(negedge axi_clk); awaddr = addr; awvalid = 1;
        do @(posedge axi_clk); while (!awready);
        @(negedge axi_clk); awvalid = 0;
        wdata = data; wstrb = strb; wvalid = 1;
        do @(posedge axi_clk); while (!wready);
        @(negedge axi_clk); wvalid = 0;
      end else begin
        @(negedge axi_clk); wdata = data; wstrb = strb; wvalid = 1;
        do @(posedge axi_clk); while (!wready);
        @(negedge axi_clk); wvalid = 0;
        awaddr = addr; awvalid = 1;
        do @(posedge axi_clk); while (!awready);
        @(negedge axi_clk); awvalid = 0;
      end
      if (hold_b != 0) begin
        repeat (4) begin
          @(posedge axi_clk);
          if (!bvalid) $fatal(1,"BVALID dropped under backpressure");
        end
      end
      while (!bvalid) @(negedge axi_clk);
      bready = 1;
      @(posedge axi_clk);
      if (bresp != 0) $fatal(1,"AXI write response error addr=%02x resp=%0d",addr,bresp);
      @(negedge axi_clk); bready = 0;
    end
  endtask

  task automatic axi_read(input [7:0] addr, output [31:0] data_out, output [1:0] resp_out);
    begin
      @(negedge axi_clk); araddr = addr; arvalid = 1;
      do @(posedge axi_clk); while (!arready);
      @(negedge axi_clk); arvalid = 0;
      while (!rvalid) @(negedge axi_clk);
      data_out = rdata; resp_out = rresp;
      rready = 1;
      @(posedge axi_clk);
      @(negedge axi_clk); rready = 0;
    end
  endtask

  task automatic axi_read_stalled(input [7:0] addr, output [31:0] data_out);
    integer k;
    reg [31:0] held_data;
    begin
      @(negedge axi_clk); araddr = addr; arvalid = 1;
      do @(posedge axi_clk); while (!arready);
      @(negedge axi_clk); arvalid = 0;
      while (!rvalid) @(negedge axi_clk);
      held_data = rdata;
      repeat (4) begin
        @(posedge axi_clk);
        if (!rvalid || rdata !== held_data) $fatal(1,"R channel changed under backpressure");
      end
      data_out = rdata;
      @(negedge axi_clk); rready = 1;
      @(posedge axi_clk);
      @(negedge axi_clk); rready = 0;
    end
  endtask

  task automatic axi_busy_trigger_test;
    begin
      // Complete one request, then submit another before its pixel-domain ack.
      @(negedge axi_clk);
      awaddr = 8'h04; awvalid = 1;
      wdata = 32'h1; wstrb = 4'h1; wvalid = 1; bready = 1;
      @(posedge axi_clk);
      @(negedge axi_clk); awvalid = 0; wvalid = 0;
      @(posedge axi_clk); // accept first B response
      @(negedge axi_clk);
      awaddr = 8'h04; awvalid = 1;
      wdata = 32'h1; wstrb = 4'h1; wvalid = 1;
      @(posedge axi_clk);
      @(negedge axi_clk); awvalid = 0; wvalid = 0; bready = 0;
      while (!bvalid) @(negedge axi_clk);
      if (bresp != 2'b10) $fatal(1,"snapshot request while busy was not SLVERR");
      @(negedge axi_clk); bready = 1;
      @(posedge axi_clk);
      @(negedge axi_clk); bready = 0;
    end
  endtask

  task automatic snapshot;
    begin
      axi_write(8'h04, 32'h1, 4'h1, 1, 1);
      repeat (30) @(posedge axi_clk);
    end
  endtask

  task automatic check_diagnostic(input [31:0] expected_flags,
                                  input [223:0] expected_top,
                                  input [223:0] expected_bottom);
    reg [223:0] read_top, read_bottom;
    reg [31:0] word_value;
    reg [1:0] response;
    integer word_index;
    begin
      snapshot();
      axi_read(8'h50, word_value, response);
      if (response != 0 || word_value !== expected_flags)
        $fatal(1, "diagnostic flags mismatch got=%08x expected=%08x", word_value, expected_flags);
      for (word_index = 0; word_index < 7; word_index = word_index + 1) begin
        axi_read(8'h60 + word_index*4, word_value, response);
        if (response != 0) $fatal(1, "top marker AXI read failed");
        read_top[word_index*32 +: 32] = word_value;
        axi_read(8'h80 + word_index*4, word_value, response);
        if (response != 0) $fatal(1, "bottom marker AXI read failed");
        read_bottom[word_index*32 +: 32] = word_value;
      end
      if (read_top !== expected_top)
        $fatal(1, "top marker readback mismatch got=%056x expected=%056x", read_top, expected_top);
      if (read_bottom !== expected_bottom)
        $fatal(1, "bottom marker readback mismatch got=%056x expected=%056x", read_bottom, expected_bottom);
    end
  endtask

  reg [31:0] rd;
  reg [1:0] rr;
  reg [223:0] full_marker;
  reg [223:0] diag_top, diag_bottom;
  integer x, y, bit_index;
  reg [23:0] full_pixel;
  initial begin
    repeat (6) @(posedge pixel_clk);
    pixel_resetn = 1;
    repeat (6) @(posedge axi_clk);
    axi_resetn = 1;
    repeat (12) @(posedge pixel_clk);

    // First rising vsync initializes frame accounting.
    set_vsync(1); set_vsync(0);
    send_small_frame(128'h112233445566778899aabbccddeeff00,64'd9,0);
    send_small_frame(128'h112233445566778899aabbccddeeff00,64'd9,0); // repeated identity
    send_small_frame(128'h0123456789abcdef0123456789abcdef,64'd9,7); // legal grayscale background and 0/128/255 blocks
    if (dut.unique_count != 2) $fatal(1,"legal equal-channel grayscale frame was rejected");
    send_small_frame(128'hffeeddccbbaa99887766554433221100,64'd9,0); // new session, same frame ID
    send_small_frame(128'hffeeddccbbaa99887766554433221100,64'd9,2); // invalid copy of last valid identity
    send_small_frame(128'hffeeddccbbaa99887766554433221100,64'd9,0); // must remain repeat after invalid frame
    if (dut.unique_count != 3 || dut.repeat_count != 2)
      $fatal(1,"identity after invalid frame was not counted as repeat");
    send_small_frame(128'hffeeddccbbaa99887766554433221100,64'd10,0); // monotonic frame ID
    send_small_frame(128'hffeeddccbbaa99887766554433221100,64'd9,0); // same-session rollback must not be unique
    if (dut.unique_count != 4 || dut.invalid_count != 2)
      $fatal(1,"same-session frame-ID rollback was not rejected");
    send_small_frame(128'hffeeddccbbaa99887766554433221100,64'd10,1); // top/bottom mismatch
    send_small_frame(128'hffeeddccbbaa99887766554433221100,64'd11,2); // CRC error
    send_small_frame(128'hffeeddccbbaa99887766554433221100,64'd12,3); // non-gray pixel
    send_small_frame(128'hffeeddccbbaa99887766554433221100,64'd13,4); // truncated line/frame
    send_small_frame(128'hffeeddccbbaa99887766554433221100,64'd14,5); // extra active pixel
    send_small_frame(128'hffeeddccbbaa99887766554433221100,64'd15,6); // wrong row widths, same total pixels
    send_small_frame(128'hffeeddccbbaa99887766554433221100,64'd10,0); // partial frames must not erase last valid identity
    if (dut.unique_count != 4 || dut.repeat_count != 3)
      $fatal(1,"identity after partial frames was not counted as repeat");
    axi_write(8'h04,32'h1,4'h0,1,0); // byte-0 strobe clear: no snapshot request
    axi_read(8'h08,rd,rr); if (rr != 0 || rd[1:0] != 2'b00) $fatal(1,"masked trigger changed status");
    axi_write(8'h04,32'h1,4'h1,0,1); // W before AW, response backpressure
    repeat (30) @(posedge axi_clk);
    axi_read(8'h08,rd,rr); if (rr != 0 || rd[1:0] != 2'b10) $fatal(1,"snapshot not valid");
    axi_read(8'h10,rd,rr); if (rd != 4) $fatal(1,"unique count mismatch after invalid/rollback/grayscale vectors: %0d",rd);
    axi_read(8'h20,rd,rr); if (rd != 3) $fatal(1,"repeat count mismatch: %0d",rd);
    axi_read(8'h28,rd,rr); if (rd != 8) $fatal(1,"invalid count mismatch: %0d",rd);
    axi_read(8'h30,rd,rr); if (rd != 3) $fatal(1,"partial count mismatch: %0d",rd);
    axi_read(8'h18,rd,rr); if (rd != 15) $fatal(1,"scan count mismatch: %0d",rd);
    axi_read(8'h0c,rd,rr); if (rd != 1) $fatal(1,"snapshot sequence mismatch");
    axi_read(8'h38,rd,rr); if (rd != 32'h33221100) $fatal(1,"last session low word mismatch");
    axi_read(8'h48,rd,rr); if (rd != 10) $fatal(1,"last frame mismatch");

    // Read registers remain a stable snapshot while live counters continue.
    send_small_frame(128'hffeeddccbbaa99887766554433221100,64'd17,0);
    axi_read(8'h10,rd,rr); if (rd != 4) $fatal(1,"snapshot changed without request");
    axi_busy_trigger_test();
    repeat (30) @(posedge axi_clk);
    axi_read(8'h10,rd,rr); if (rd != 5) $fatal(1,"busy test snapshot did not complete");
    axi_read(8'h0c,rd,rr); if (rd != 2) $fatal(1,"busy test snapshot sequence mismatch");
    axi_write(8'h04,32'h1,4'h1,1,0); // AW before W
    repeat (30) @(posedge axi_clk);
    axi_read(8'h10,rd,rr); if (rd != 5) $fatal(1,"third snapshot did not update");
    axi_read(8'h0c,rd,rr); if (rd != 3) $fatal(1,"third snapshot sequence mismatch");
    axi_read_stalled(8'h00,rd); if (rd != 32'h4d435631) $fatal(1,"signature read mismatch");

    // Full-resolution complete frame, including both 224-bit marker rows.
    full_marker = make_marker(128'h0123456789abcdef0123456789abcdef,64'h0000000100000007);
    for (y=0; y<720; y=y+1) begin
      for (x=0; x<1280; x=x+1) begin
        full_pixel=24'h000000;
        if ((y==0 || y==719) && x>=640 && x<864) begin
          bit_index=223-(x-640);
          full_pixel=full_marker[bit_index] ? 24'hffffff : 24'h000000;
        end
        pixel(1,full_pixel);
      end
      pixel(0,24'h000000);
    end
    set_vsync(1); set_vsync(0);
    repeat (10) @(posedge pixel_clk);
    if (full_dut.unique_count != 1) $fatal(1,"full-resolution frame was not accepted");

    // Pixel-only reset invalidates the AXI snapshot until a fresh mailbox request.
    @(negedge pixel_clk); pixel_resetn = 0;
    repeat (6) @(posedge axi_clk);
    @(negedge pixel_clk); pixel_resetn = 1;
    repeat (20) @(posedge axi_clk);
    if (dut.unique_count != 0 || dut.snapshot_valid != 0 || dut.cdc_ready != 1)
      $fatal(1,"pixel reset did not clear counters and invalidate snapshot");
    axi_write(8'h04,32'h1,4'h1,1,0);
    repeat (20) @(posedge axi_clk);
    axi_read(8'h08,rd,rr);
    if (rr != 0 || rd[1:0] != 2'b10) $fatal(1,"snapshot did not recover after pixel reset");
    @(negedge axi_clk); axi_resetn = 0;
    repeat (6) @(posedge axi_clk);
    @(negedge axi_clk); axi_resetn = 1;
    repeat (20) @(posedge axi_clk);
    if (dut.snapshot_valid != 0 || dut.cdc_ready != 1)
      $fatal(1,"AXI reset did not invalidate snapshot through CDC warmup");
    axi_read(8'h50,rd,rr); if (rr != 0 || rd != 0) $fatal(1,"diagnostic flags were nonzero before first snapshot");
    for (x=0; x<7; x=x+1) begin
      axi_read(8'h60+x*4,rd,rr); if (rr != 0 || rd != 0) $fatal(1,"top marker was nonzero before first snapshot");
      axi_read(8'h80+x*4,rd,rr); if (rr != 0 || rd != 0) $fatal(1,"bottom marker was nonzero before first snapshot");
    end
    axi_write(8'h04,32'h1,4'h1,0,0);
    repeat (20) @(posedge axi_clk);
    axi_read(8'h08,rd,rr);
    if (rr != 0 || rd[1:0] != 2'b10) $fatal(1,"snapshot did not recover after AXI reset");

    // The first VSYNC only arms monitoring; subsequent completed scans lock diagnostics.
    set_vsync(1); set_vsync(0);
    check_diagnostic(32'h0,224'b0,224'b0);
    diag_top = make_marker(128'h123456789abcdef00123456789abcdef,64'd21);
    send_small_frame(128'h123456789abcdef00123456789abcdef,64'd21,0);
    check_diagnostic(32'h0,diag_top,diag_top);
    send_small_frame(128'h123456789abcdef00123456789abcdef,64'd22,3); // colored pixel
    check_diagnostic(32'h01,make_marker(128'h123456789abcdef00123456789abcdef,64'd22),
                     make_marker(128'h123456789abcdef00123456789abcdef,64'd22));
    diag_top = make_marker(128'h123456789abcdef00123456789abcdef,64'd23);
    diag_top[0] = ~diag_top[0];
    diag_bottom = make_marker(128'h123456789abcdef00123456789abcdef,64'd23);
    send_small_frame(128'h123456789abcdef00123456789abcdef,64'd23,2); // bad top CRC
    check_diagnostic(32'h0c,diag_top,diag_bottom);
    diag_top = make_marker(128'h123456789abcdef00123456789abcdef,64'd24);
    send_small_frame(128'h123456789abcdef00123456789abcdef,64'd24,8); // gray 128 marker pixels
    check_diagnostic(32'h02,diag_top,diag_top);
    diag_top = make_marker(128'h123456789abcdef00123456789abcdef,64'd25);
    diag_bottom = make_marker(128'h123456789abcdef00123456789abcdef,64'd26);
    send_small_frame(128'h123456789abcdef00123456789abcdef,64'd25,9); // distinct, valid bottom marker
    check_diagnostic(32'h04,diag_top,diag_bottom);
    diag_top = make_marker(128'h123456789abcdef00123456789abcdef,64'd28);
    send_small_frame(128'h123456789abcdef00123456789abcdef,64'd28,4); // incomplete geometry, complete markers
    check_diagnostic(32'h0,diag_top,diag_top);
    diag_bottom = make_marker(128'h123456789abcdef00123456789abcdef,64'd27);
    diag_bottom[10] = ~diag_bottom[10];
    send_small_frame(128'h123456789abcdef00123456789abcdef,64'd27,1); // bottom CRC and pair mismatch
    check_diagnostic(32'h14,make_marker(128'h123456789abcdef00123456789abcdef,64'd27),diag_bottom);
    $display("VIDEO_MONITOR_PASS");
    $finish;
  end
  initial begin
    #20000000;
    $fatal(1,"simulation timeout (possible AXI handshake deadlock)");
  end
endmodule
