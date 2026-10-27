`timescale 1ns/1ps
module tb_axis_dma_gray_wrapper;
 reg clk=0; always #5 clk=~clk;
 reg rstn=0;
 reg [31:0] awaddr=0,wdata=0,araddr=0; reg awvalid=0,wvalid=0,arvalid=0,rready=0,bready=0; reg [3:0] wstrb=15;
 wire awready,wready,arready; wire [1:0] bresp,rresp; wire bvalid,rvalid; wire [31:0] rdata;
 reg [31:0] sd=0;reg [3:0] sk=15;reg sl=0,sv=0;wire sr;
 wire [31:0] md;wire [3:0] mk;wire ml,mv;reg mr=1;
 integer beats=0, i;
 axis_dma_gray_s2mm_pipeline_bd dut(
 .aclk(clk),.aresetn(rstn),.s_axi_awaddr(awaddr),.s_axi_awvalid(awvalid),.s_axi_awready(awready),
 .s_axi_wdata(wdata),.s_axi_wstrb(wstrb),.s_axi_wvalid(wvalid),.s_axi_wready(wready),.s_axi_bresp(bresp),.s_axi_bvalid(bvalid),.s_axi_bready(bready),
 .s_axi_araddr(araddr),.s_axi_arvalid(arvalid),.s_axi_arready(arready),.s_axi_rdata(rdata),.s_axi_rresp(rresp),.s_axi_rvalid(rvalid),.s_axi_rready(rready),
 .s_axis_tdata(sd),.s_axis_tkeep(sk),.s_axis_tlast(sl),.s_axis_tvalid(sv),.s_axis_tready(sr),
 .m_axis_tdata(md),.m_axis_tkeep(mk),.m_axis_tlast(ml),.m_axis_tvalid(mv),.m_axis_tready(mr));
 always @(posedge clk) if(rstn && mv && mr) begin
   if(md!==32'hffffffff || mk!==4'hf) $fatal(1,"wrapper output pixel packing mismatch at beat %0d: %h",beats,md);
   if(ml !== (beats==19199)) $fatal(1,"wrapper TLAST mismatch at beat %0d",beats);
   beats<=beats+1;
 end
 task put(input [31:0] d,input [3:0] k,input l);
 begin
   @(negedge clk);sd=d;sk=k;sl=l;sv=1;
   do @(posedge clk); while(!sr);
   @(negedge clk);sv=0;sl=0;
 end endtask
 task axi_arm;
 begin
   @(negedge clk);awaddr=32'h43c2005c;wdata=1;awvalid=1;wvalid=1;bready=1;
   do @(posedge clk); while(!(awready&&wready));
   @(negedge clk);awvalid=0;wvalid=0;
   do @(posedge clk); while(!bvalid);
   if(bresp!=0)$fatal(1,"wrapper ARM write rejected");
   @(negedge clk);bready=0;
 end endtask
 initial begin
   repeat(4)@(negedge clk);rstn=1;axi_arm();
   put(32'h47525938,15,0);put(32'h00f00140,15,0);put(32'd76800,15,0);put(32'd7,15,0);
   for(i=0;i<19200;i=i+1) put(32'h80808080,15,0);
   put(32'h454e4438,15,0);put(32'd7,15,0);put(32'h00960000,15,0);put(32'hc0dec018,15,1);
   wait(beats==19200);repeat(10)@(posedge clk);
   if(dut.packet_error_seen || dut.pipe_checker_error || dut.packer_fault) $fatal(1,"wrapper reported error");
   $display("GRAY8_WRAPPER_320X240_PASS beats=%0d",beats);$finish;
 end
 initial begin #30000000;$fatal(1,"wrapper simulation timeout");end
endmodule
