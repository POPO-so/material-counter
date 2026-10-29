`timescale 1ns/1ps

module tb_video_monitor_netlist;
  localparam integer WIDTH = 1280;
  localparam integer HEIGHT = 720;
  localparam integer MARKER_X = 640;

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

  display_frame_monitor_0 dut (
    .pixel_clk(pixel_clk), .pixel_resetn(pixel_resetn),
    .vid_data(vid_data), .vid_active(vid_active), .vid_vsync(vid_vsync),
    .s_axi_aclk(axi_clk), .s_axi_aresetn(axi_resetn),
    .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
    .s_axi_wdata(wdata), .s_axi_wstrb(wstrb), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
    .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
    .s_axi_araddr(araddr), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
    .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rvalid(rvalid), .s_axi_rready(rready)
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

  reg [31:0] rd; reg [1:0] rr;
  initial begin
    repeat(20) @(posedge pixel_clk); pixel_resetn=1;
    repeat(20) @(posedge axi_clk); axi_resetn=1;
    repeat(20) @(posedge pixel_clk);
    set_vsync(1);set_vsync(0);
    send_small_frame(128'h00112233445566778899aabbccddeeff,64'd1,7);
    snapshot();axi_read(8'h10,rd,rr);
    if(rd !== 1) $fatal(1,"NETLIST rejected legal Gray8 frame unique=%0d",rd);
    send_small_frame(128'h00112233445566778899aabbccddeeff,64'd1,7);
    snapshot();axi_read(8'h10,rd,rr);
    if(rd !== 1) $fatal(1,"NETLIST repeat falsely counted");
    axi_read(8'h20,rd,rr);if(rd !== 1) $fatal(1,"NETLIST repeat counter mismatch");
    send_small_frame(128'h00112233445566778899aabbccddeeff,64'd2,7);
    snapshot();axi_read(8'h10,rd,rr);
    if(rd !== 2) $fatal(1,"NETLIST new identity not counted");
    $display("VIDEO_MONITOR_NETLIST_PASS");$finish;
  end
  initial begin #100000000; $fatal(1,"NETLIST watchdog"); end
endmodule
