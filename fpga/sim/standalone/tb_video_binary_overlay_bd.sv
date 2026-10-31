`timescale 1ns/1ps
module tb_video_binary_overlay_bd;
  reg clk=0,pclk=0;
  always #10 clk=~clk;
  always #7 pclk=~pclk;
  reg resetn=0,presetn=0;
  reg vsync=0;
  reg [31:0] awaddr=0,wdata=0,araddr=0;
  reg awvalid=0,wvalid=0,arvalid=0,bready=0,rready=0;
  reg [3:0] wstrb=4'hf;
  wire awready,wready,arready,bvalid,rvalid;
  wire [1:0] bresp,rresp;
  wire [31:0] rdata;
  wire [23:0] out_data24;
  wire out_active,out_vsync,out_hsync;
  wire [31:0] capture_count,drop_count,reject_count,captured_frame_id,mask_displayed_frame_id;
  wire capture_busy,pending_valid,completed_valid_status,mask_display_valid;
  video_binary_overlay_bd dut(
    .s_axi_aclk(clk),.s_axi_aresetn(resetn),.s_axi_awaddr(awaddr),.s_axi_awvalid(awvalid),.s_axi_awready(awready),
    .s_axi_wdata(wdata),.s_axi_wstrb(wstrb),.s_axi_wvalid(wvalid),.s_axi_wready(wready),
    .s_axi_bresp(bresp),.s_axi_bvalid(bvalid),.s_axi_bready(bready),.s_axi_araddr(araddr),.s_axi_arvalid(arvalid),
    .s_axi_arready(arready),.s_axi_rdata(rdata),.s_axi_rresp(rresp),.s_axi_rvalid(rvalid),.s_axi_rready(rready),
    .mask_clk(clk),.mask_resetn(resetn),.tap_valid(1'b0),.tap_sof(1'b0),.tap_eol(1'b0),.tap_data(8'b0),.tap_frame_id(32'b0),
    .pixel_clk(pclk),.pixel_resetn(presetn),.vid_data24(24'h123456),.vid_active(1'b0),.vid_vsync(vsync),.vid_hsync(1'b0),
    .out_data24(out_data24),.out_active(out_active),.out_vsync(out_vsync),.out_hsync(out_hsync),
    .capture_count(capture_count),.drop_count(drop_count),.reject_count(reject_count),.capture_busy(capture_busy),
    .pending_valid(pending_valid),.completed_valid_status(completed_valid_status),.captured_frame_id(captured_frame_id),
    .mask_displayed_frame_id(mask_displayed_frame_id),.mask_display_valid(mask_display_valid));
  task automatic read_reg(input [31:0] addr,input [31:0] expected,input [1:0] response);
    begin
      @(negedge clk);araddr=addr;arvalid=1;
      @(negedge clk);arvalid=0;
      wait(rvalid);
      if(rdata!==expected||rresp!==response)$fatal(1,"read %08x got %08x/%b expected %08x/%b",addr,rdata,rresp,expected,response);
      @(negedge clk);rready=1;
      @(negedge clk);rready=0;
    end
  endtask
  task automatic write_reg(input [31:0] addr,input [31:0] value,input [3:0] strobe,input [1:0] response,input bit separate);
    begin
      @(negedge clk);awaddr=addr;wdata=value;wstrb=strobe;
      if(separate)begin awvalid=1;wvalid=0;@(negedge clk);awvalid=0;wvalid=1;@(negedge clk);wvalid=0;end
      else begin awvalid=1;wvalid=1;@(negedge clk);awvalid=0;wvalid=0;end
      wait(bvalid);
      if(bresp!==response)$fatal(1,"write %08x response %b expected %b",addr,bresp,response);
      @(negedge clk);bready=1;
      @(negedge clk);bready=0;
    end
  endtask
  initial begin
    repeat(4)@(negedge clk);resetn=1;presetn=1;
    read_reg(0,32'h50424f31,0);
    read_reg(32'h43c50000,32'h50424f31,0);
    read_reg(4,32'h01900280,0);
    write_reg(32'h30,1,4'hf,0,1);
    write_reg(32'h43c50034,32'h1234,4'hf,0,0);
    read_reg(32'h34,32'h1234,0);
    write_reg(32'h43c60034,32'h5678,4'hf,2,0);
    read_reg(32'h43c60034,0,2);
    write_reg(32'h3c,640,4'hf,2,0);
    write_reg(32'h3c,20,4'h7,2,0);
    write_reg(32'h40,1,4'hf,0,0);
    repeat(5)@(posedge pclk);
    @(negedge pclk);vsync=1;
    @(negedge pclk);vsync=0;
    repeat(10)@(posedge clk);
    read_reg(8,32'h1,0);
    read_reg(32'h80,0,2);
    $display("BINARY_OVERLAY_BD_PASS AXIL_reads_writes=12 local_and_absolute=PASS");$finish;
  end
  initial begin #200000;$fatal(1,"watchdog");end
endmodule
