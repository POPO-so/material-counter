`timescale 1ns/1ps
module tb_axis_rle_pipeline;
  reg clk=0;always #10 clk=~clk;
  reg rst=0;
  reg [31:0] awaddr=0,wd=0,araddr=0,sd=0;
  reg awv=0,wv=0,br=1,arv=0,rr=1,sv=0,sl=0,mr=0;
  wire awr,wr,bv,arr,rv,sr,mv,ml;
  wire [1:0] bp,rp;wire [31:0] rd,md;wire [3:0] mk;
  integer n=0,cycles=0,input_count,expected_count,i,output_file;
  integer morph=0,offset=0,corrupt=0,roi_left=0,roi_top=0,roi_right=640,roi_bottom=400;
  reg [31:0] observed,snapshot_seq,cycle_lo,cycle_hi;
  reg [31:0] input_words[0:64007];reg [31:0] expected[0:20000];
  reg [31:0] tslo,tshi,expected_word;
  reg stalled=0;reg [31:0] held;reg heldlast;
  reg output_done=0,input_done=0;
  reg [4095:0] input_path,expected_path,output_path;
  axis_dma_rle_pipeline_bd dut(
    .aclk(clk),.aresetn(rst),.s_axi_awaddr(awaddr),.s_axi_awvalid(awv),.s_axi_awready(awr),
    .s_axi_wdata(wd),.s_axi_wstrb(4'hf),.s_axi_wvalid(wv),.s_axi_wready(wr),
    .s_axi_bresp(bp),.s_axi_bvalid(bv),.s_axi_bready(br),
    .s_axi_araddr(araddr),.s_axi_arvalid(arv),.s_axi_arready(arr),
    .s_axi_rdata(rd),.s_axi_rresp(rp),.s_axi_rvalid(rv),.s_axi_rready(rr),
    .s_axis_tdata(sd),.s_axis_tkeep(4'hf),.s_axis_tlast(sl),.s_axis_tvalid(sv),.s_axis_tready(sr),
    .m_axis_tdata(md),.m_axis_tkeep(mk),.m_axis_tlast(ml),.m_axis_tvalid(mv),.m_axis_tready(mr));
  task write_reg;
    input [31:0] addr,data;
    begin
      @(negedge clk);awaddr=addr;wd=data;awv=1;wv=1;
      fork
        begin do @(posedge clk);while(!awr);@(negedge clk);awv=0;end
        begin do @(posedge clk);while(!wr);@(negedge clk);wv=0;end
      join
      wait(bv);if(bp!=0)$fatal(1,"AXI write rejected %h",addr);
      @(negedge clk);
    end
  endtask
  task read_reg;
    input [31:0] addr; input [1:0] response;
    begin
      @(negedge clk);araddr=addr;arv=1;
      do @(posedge clk);while(!arr);
      @(negedge clk);arv=0;
      wait(rv);if(rp!==response)$fatal(1,"AXI read response %h got %h expected %h",addr,rp,response);
      observed=rd;@(negedge clk);
    end
  endtask
  always @(negedge clk) mr=(cycles%11<8);
  always @(posedge clk)if(rst)begin
    cycles=cycles+1;if(cycles>2000000)$fatal(1,"pipeline watchdog");
    if(stalled && (!mv || md!==held || ml!==heldlast))$fatal(1,"pipeline AXIS unstable");
    stalled=mv&&!mr;held=md;heldlast=ml;
    if(mv&&mr)begin
      if(mk!=15)$fatal(1,"pipeline KEEP");
      if(n>=expected_count)$fatal(1,"too many words");
      if(n==7)tslo=md;if(n==8)tshi=md;
      expected_word=expected[n];
      if(n==expected_count-8)expected_word=expected_word+tslo+tshi;
      if(n!=7 && n!=8 && md!==expected_word)
        $fatal(1,"word mismatch n=%0d got=%h expected=%h",n,md,expected_word);
      $fdisplay(output_file,"%08x",md);n=n+1;
      if(ml)begin
        if(n!=expected_count)$fatal(1,"premature TLAST");
        $fclose(output_file);output_done=1;
      end
    end
  end
  initial begin
    if(!$value$plusargs("INPUT=%s",input_path) || !$value$plusargs("EXPECTED=%s",expected_path) ||
       !$value$plusargs("OUTPUT=%s",output_path) || !$value$plusargs("WORDS=%d",expected_count))
        $fatal(1,"vector arguments required");
    i=$value$plusargs("MORPH=%d",morph);i=$value$plusargs("OFFSET=%d",offset);
    i=$value$plusargs("CORRUPT=%d",corrupt);
    i=$value$plusargs("X0=%d",roi_left);i=$value$plusargs("Y0=%d",roi_top);
    i=$value$plusargs("X1=%d",roi_right);i=$value$plusargs("Y1=%d",roi_bottom);
    $readmemh(input_path,input_words);$readmemh(expected_path,expected,0,expected_count-1);
    output_file=$fopen(output_path,"w");
    repeat(5)@(negedge clk);rst=1;
    // 0x00 is write-only; probing it must not be mistaken for a valid read.
    read_reg(0,2);
    write_reg('h3c,1);read_reg('h3c,0);
    if(observed!=1)$fatal(1,"snapshot sequence after reset");
    write_reg('h10,17);write_reg('h14,3);
    write_reg('h18,(roi_top<<16)|roi_left);write_reg('h1c,(roi_bottom<<16)|roi_right);
    write_reg('h20,128);write_reg('h24,offset);write_reg('h28,morph);write_reg(0,1);
    // Monitoring snapshots remain legal while a frame is active.
    write_reg('h3c,1);read_reg('h3c,0);
    if(observed!=2)$fatal(1,"snapshot while busy");
    for(i=0;i<64008;i=i+1)begin
      @(negedge clk);sv=1;sd=input_words[i];sl=(i==64007);
      do @(posedge clk);while(!sr);
      @(negedge clk);sv=0;
      if(i%7==0)@(negedge clk);
    end
    input_done=1;wait(output_done);
    write_reg('h3c,1);read_reg('h3c,0);snapshot_seq=observed;
    if(snapshot_seq!=3)$fatal(1,"snapshot sequence completion");
    read_reg('h50,0);if(observed!=64008)$fatal(1,"input beats %0d",observed);
    read_reg('h54,0);if(observed!=0)$fatal(1,"input upper bits");
    read_reg('h58,0);if(observed!=expected_count)$fatal(1,"output beats %0d expected %0d",observed,expected_count);
    read_reg('h5c,0);if(observed!=0)$fatal(1,"output upper bits");
    read_reg('h48,0);if(observed==0)$fatal(1,"active cycles missing");
    read_reg('h68,0);if(observed==0)$fatal(1,"backpressure not counted");
    read_reg('h40,0);cycle_lo=observed;read_reg('h44,0);cycle_hi=observed;
    repeat(40)@(negedge clk);
    read_reg('h40,0);if(observed!=cycle_lo)$fatal(1,"snapshot lower word unstable");
    read_reg('h44,0);if(observed!=cycle_hi)$fatal(1,"snapshot upper word unstable");
    write_reg('h3c,1);read_reg('h3c,0);if(observed!=snapshot_seq+1)$fatal(1,"snapshot did not advance");
    read_reg('h40,0);if(observed<=cycle_lo)$fatal(1,"running clock counter did not advance");
    $display("RLE_COUNTER_SNAPSHOT_PASS input=64008 output=%0d",expected_count);
    $display("RLE_PIPELINE_640X400_PASS words=%0d cycles=%0d",n,cycles);$finish;
  end
endmodule
