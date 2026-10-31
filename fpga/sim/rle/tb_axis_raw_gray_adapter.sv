`timescale 1ns/1ps
module tb_axis_raw_gray_adapter;
  localparam integer W=640,H=400,BEATS=W*H/4;
  reg clk=0; always #5 clk=~clk;
  reg rstn=0,valid=0,last=0,ready=0;
  reg [31:0] data=0; reg [3:0] keep=4'hf;
  wire sready,msof,meol,mvalid,done,error;
  wire [7:0] mdata; wire [3:0] code; wire [31:0] fid;
  integer accepted=0, emitted=0, cycle_count=0, i, phase;
  reg saw_error=0;
  reg [31:0] pattern;
  axis_raw_gray_adapter #(.WIDTH(W),.HEIGHT(H)) dut(
    .aclk(clk),.aresetn(rstn),.s_tdata(data),.s_tkeep(keep),.s_tlast(last),
    .s_tvalid(valid),.s_tready(sready),.m_tdata(mdata),.m_tuser(msof),
    .m_tlast(meol),.m_tvalid(mvalid),.m_tready(ready),.cfg_frame_id(32'h12345678),
    .packet_done(done),.packet_error(error),.error_code(code),.frame_id(fid));
  always @(negedge clk) if(rstn) ready=(cycle_count%13<8);
  always @(posedge clk) if(rstn) begin
    cycle_count=cycle_count+1;
    if(error)saw_error=1;
    if(cycle_count>3000000)$fatal(1,"watchdog phase=%0d",phase);
    if(mvalid&&ready&&phase==1) begin
      if(mdata!==((((emitted/4)*13+7+(emitted%4)))&255))$fatal(1,"pixel %0d got %h",emitted,mdata);
      if(msof!==(emitted==0))$fatal(1,"SOF at pixel %0d",emitted);
      if(meol!==((emitted%W)==W-1))$fatal(1,"EOL at pixel %0d",emitted);
      emitted=emitted+1;
    end
  end
  task send_beat(input [31:0] value,input [3:0] byte_keep,input bit eol,
                 input integer gap);
    begin
      repeat(gap) @(negedge clk);
      data=value;keep=byte_keep;last=eol;valid=1;
      do @(posedge clk); while(!sready);
      accepted=accepted+1;
      @(negedge clk);valid=0;last=0;keep=4'hf;
    end
  endtask
  task reset_dut;
    begin
      @(negedge clk);rstn=0;valid=0;ready=0;repeat(3)@(negedge clk);
      accepted=0;emitted=0;saw_error=0;rstn=1;repeat(2)@(negedge clk);
    end
  endtask
  initial begin
    phase=1;reset_dut();
    for(i=0;i<BEATS;i=i+1)begin
      pattern=(((i*13+10)&255)<<24)|(((i*13+9)&255)<<16)|
              (((i*13+8)&255)<<8)|((i*13+7)&255);
      send_beat(pattern,4'hf,i==BEATS-1,i%17==0?2:0);
    end
    wait(done);repeat(20)@(negedge clk);
    if(error||accepted!=BEATS||emitted!=W*H||fid!=32'h12345678)
      $fatal(1,"valid frame accepted=%0d emitted=%0d error=%b",accepted,emitted,error);
    phase=2;reset_dut();
    for(i=0;i<10;i=i+1)send_beat(32'h04030201,4'hf,i==9,0);
    if(!error||code!=4||accepted!=10)$fatal(1,"early TLAST rejection code=%0d beats=%0d",code,accepted);
    phase=3;reset_dut();
    for(i=0;i<BEATS+3;i=i+1)send_beat(32'h04030201,4'hf,i==BEATS+2,0);
    if(!saw_error||code!=4||accepted!=BEATS+3)$fatal(1,"late TLAST not drained code=%0d beats=%0d",code,accepted);
    phase=4;reset_dut();
    for(i=0;i<BEATS;i=i+1)send_beat(32'h04030201,i==5?4'h7:4'hf,i==BEATS-1,0);
    if(!saw_error||code!=3||accepted!=BEATS)$fatal(1,"bad TKEEP not drained code=%0d beats=%0d",code,accepted);
    $display("RAW_GRAY_ADAPTER_PASS pixels=%0d beats=%0d early_late_keep=PASS",W*H,BEATS);
    $finish;
  end
endmodule
