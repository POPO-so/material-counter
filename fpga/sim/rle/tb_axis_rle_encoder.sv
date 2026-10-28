`timescale 1ns/1ps
module tb_axis_rle_encoder;
  reg clk=0; always #10 clk=~clk;
  reg rst=0,start=0,valid=0,ready=0,packet_done=0,packet_error=0;
  reg [7:0] pixel=0;
  reg sof=0,eol=0;
  wire inready,ov,ol,busy,done;
  wire [31:0] od,bytes;
  wire [3:0] keep;
  integer cycles=0,n=0,frames=0,j;
  reg [31:0] words[0:150];
  reg stalled=0; reg [31:0] held; reg held_last;
`ifdef POST_SYNTH
  axis_rle_encoder dut(
`else
  axis_rle_encoder #(.WIDTH(5),.HEIGHT(3),.MAX_RUNS(2)) dut(
`endif
    .aclk(clk),.aresetn(rst),.start(start),.cfg_frame_id(32'd7),
    .cfg_params_version(32'd11),.cfg_timestamp(64'd123),
    .cfg_roi_x0(16'd0),.cfg_roi_y0(16'd0),.cfg_roi_x1(16'd5),.cfg_roi_y1(16'd3),
    .cfg_threshold(8'd128),.cfg_offset(32'd0),.cfg_morph(2'd0),
    .s_tdata(pixel),.s_tuser(sof),.s_tlast(eol),.s_tvalid(valid),.s_tready(inready),
    .packet_done(packet_done),.packet_error(packet_error),.input_error_code(4'd5),
    .m_tdata(od),.m_tkeep(keep),.m_tlast(ol),.m_tvalid(ov),.m_tready(ready),
    .busy(busy),.done(done),.actual_bytes(bytes));
  always @(posedge clk) if(rst) begin
    cycles=cycles+1;
    if(cycles>20000) $fatal(1,"RLE watchdog");
    if(stalled && (!ov || od!==held || ol!==held_last)) $fatal(1,"unstable AXIS");
    stalled=ov&&!ready; held=od;held_last=ol;
    if(ov&&ready) begin
      if(keep!==15) $fatal(1,"keep");
      words[n]=od;n=n+1;
      if(ol) frames=frames+1;
    end
  end
  always @(negedge clk) ready=(cycles%7!=1 && cycles%7!=2);
  task begin_frame;
    begin
      wait(!busy); @(negedge clk);n=0;start=1;packet_done=0;packet_error=0;
      @(negedge clk);start=0;
    end
  endtask
  task put;
    input integer k; input integer foreground;
    begin
      @(negedge clk);valid=1;pixel=foreground?255:0;sof=(k==0);eol=(k%5==4);
      do @(posedge clk); while(!inready);
      @(negedge clk);valid=0;
    end
  endtask
  task finish_frame;
    begin
      @(negedge clk);packet_done=1;@(negedge clk);packet_done=0;
      wait(done);@(negedge clk);
      if(words[0]!==32'h31454c52 || words[n-16]!==32'h31444e45 || words[n-1]!==32'hc0dec0de)
        $fatal(1,"frame markers");
      if(words[n-9]!==n*4 || bytes!==n*4) $fatal(1,"actual bytes");
      begin: checksum_test
        reg[31:0] sum; integer q;
        sum=0;for(q=0;q<n-16;q=q+1)sum=sum+words[q];
        if(words[n-8]!==sum)$fatal(1,"checksum");
      end
    end
  endtask
  initial begin
    repeat(4)@(negedge clk);rst=1;
    begin_frame();for(j=0;j<15;j=j+1)put(j,0);finish_frame();
    if(n!=36 || words[n-13]!=0 || words[n-10]!=0)$fatal(1,"empty frame");
    begin_frame();for(j=0;j<15;j=j+1)put(j,(j<5 || j==9));finish_frame();
    if(words[16]!==4 || words[17]!==((1<<20)|(4<<10)|4))$fatal(1,"edge runs");
    if(words[n-13]!=6 || words[n-11]!=2 || words[n-10]!=0)$fatal(1,"stats");
    if(words[18]!==32'h00010005 || words[19]!==32'h00010000 ||
       words[20]!==32'h00010001 || words[21]!==32'h00020001)
        $fatal(1,"packed row/column projections lose odd addresses");
    begin_frame();for(j=0;j<15;j=j+1)put(j,(j%2==0));finish_frame();
    if(words[n-13]!=8 || words[n-12]!=8 || words[n-11]!=2 || words[n-10]!=1)$fatal(1,"overflow");
    begin_frame();put(0,1);@(negedge clk);packet_error=1;@(negedge clk);packet_error=0;
    wait(done);@(negedge clk);
    if(words[n-10]!=2)$fatal(1,"input error not returned");
    $display("RLE_ENCODER_PASS frames=%0d",frames);$finish;
  end
endmodule
