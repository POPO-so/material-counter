`timescale 1ns/1ps
module tb_axis_dma_gray_adapter;
  localparam W=5,H=3,N=W*H;
  reg clk=0; always #5 clk=~clk;
  reg rstn=0, sv=0, sl=0, mr=1;
  reg [31:0] sd=0; reg [3:0] sk=0;
  wire sr,mv,ml,done,err,checker_error; wire [7:0] md; wire [0:0] mu;
  wire [3:0] ec; wire [31:0] fid,op,os,oe,oc;
  reg [7:0] pix[0:N-1]; integer got=0, i, frame_no=0, errors=0;
  reg [31:0] checksum;
  reg [7:0] expected;
  reg stalled=0, done_seen=0; reg [7:0] hold_data; reg hold_last,hold_user;
  axis_dma_gray_image_pipeline #(.WIDTH(W),.HEIGHT(H),.THRESHOLD(128)) dut
   (.aclk(clk),.aresetn(rstn),.s_tdata(sd),.s_tkeep(sk),.s_tlast(sl),.s_tvalid(sv),.s_tready(sr),
    .m_tdata(md),.m_tuser(mu),.m_tlast(ml),.m_tvalid(mv),.m_tready(mr),
    .packet_done(done),.packet_error(err),.error_code(ec),.frame_id(fid),
    .checker_error(checker_error),.observed_pixels(op),.observed_sofs(os),.observed_eols(oe),.observed_checksum(oc));
  always @(negedge clk) if (rstn) mr <= (($time/10)%5 != 0);
  always @(posedge clk) begin
    if (!rstn) begin got<=0;stalled<=0;end
    else begin
      if (mv && !mr) begin
        if (stalled && (md!==hold_data || ml!==hold_last || mu!==hold_user)) $fatal(1,"output changed while stalled");
        stalled<=1;hold_data<=md;hold_last<=ml;hold_user<=mu;
      end else if(mv && mr) begin
        stalled<=0;
        if (md !== ((pix[got]>=128)?8'hff:8'h00)) $fatal(1,"pixel %0d mismatch got %h source %h",got,md,pix[got]);
        if (ml !== ((got%W)==W-1) || mu !== (got==0)) $fatal(1,"pixel markers mismatch %0d",got);
        got<=got+1;
      end
      if (err) errors<=errors+1;
    end
  end
  task sendword(input [31:0] d,input [3:0] k,input l,input integer bubble);
    begin
      if (bubble) begin @(negedge clk); sv=0; repeat(bubble) @(negedge clk); end
      @(negedge clk); sd=d;sk=k;sl=l;sv=1;
      do @(posedge clk); while(!sr);
      @(negedge clk);sv=0;sl=0;
    end
  endtask
  task sendframe(input integer fidin,input integer badmode);
    integer j, nbytes, validbytes; reg [31:0] word;
    begin
      checksum=0;
      for(j=0;j<N;j=j+1) begin
        case(j%4) 0:pix[j]=0; 1:pix[j]=127; 2:pix[j]=128; 3:pix[j]=255; endcase
        checksum=checksum+pix[j];
      end
      sendword((badmode==1)?32'h12345678:32'h47525938,15,0,0);
      sendword((badmode==2)?32'h00040005:((H<<16)|W),15,0,1);
      sendword((badmode==3)?N+1:N,15,0,0);
      sendword(fidin,15,0,0);
      nbytes=(badmode==4)?4:N;
      for(j=0;j<nbytes;j=j+4) begin
        word=0; validbytes=((nbytes-j)>=4)?4:(nbytes-j);
        word[7:0]=pix[j];if(validbytes>1)word[15:8]=pix[j+1];if(validbytes>2)word[23:16]=pix[j+2];if(validbytes>3)word[31:24]=pix[j+3];
        sendword(word,(validbytes==4)?15:((1<<validbytes)-1),(badmode==4 && j==0),j%3==0);
      end
      if(badmode==4) begin
        repeat(20) @(posedge clk); if(errors==0)$fatal(1,"early TLAST not rejected");
      end else begin
        sendword(32'h454e4438,15,0,0);sendword(fidin,15,0,1);
        sendword((badmode==5)?checksum+1:checksum,15,0,0);
        sendword(32'hc0dec018,15,1,0);
        repeat(40) @(posedge clk);
        if(badmode==0) begin
          if(got!=N)$fatal(1,"expected %0d pixels, got %0d",N,got);
          if(!done_seen)$fatal(1,"packet not accepted");
          if(badmode==0 && checker_error)$fatal(1,"checker error");
          if(badmode==5 && errors==0)$fatal(1,"checksum error not rejected");
        end
      end
    end
  endtask
  always @(posedge clk) if(rstn && done) done_seen<=1;
  initial begin
    repeat(3) @(negedge clk);rstn=1;
    sendframe(10,0); // output pressure and source bubbles
    repeat(5) @(posedge clk);
    if(checker_error)$fatal(1,"first frame checker error");
    @(negedge clk);got=0;done_seen=0;
    sendframe(10,0);repeat(5) @(posedge clk);
    if(got!=N || checker_error)$fatal(1,"consecutive frame failed");
    if(got!=N)$fatal(1,"backpressure drain failed");
    // Reset isolates error cases and proves parser recovery after each rejection.
    rstn=0;repeat(3) @(posedge clk);@(negedge clk);rstn=1;got=0;done_seen=0;errors=0;
    sendframe(11,1); if(errors==0)$fatal(1,"bad magic not rejected");
    rstn=0;repeat(3) @(posedge clk);@(negedge clk);rstn=1;errors=0;
    sendframe(12,3); if(errors==0)$fatal(1,"bad length not rejected");
    rstn=0;repeat(3) @(posedge clk);@(negedge clk);rstn=1;errors=0;
    sendword(32'h47525938,15,0,0);sendword((H<<16)|W,15,0,0);sendword(N,15,0,0);sendword(1,15,0,0);
    sendword(32'h04030201,4'b0101,0,0);repeat(10)@(posedge clk);if(errors==0)$fatal(1,"bad keep not rejected");
    rstn=0;repeat(3) @(posedge clk);@(negedge clk);rstn=1;errors=0;
    sendframe(13,4); if(errors==0)$fatal(1,"early TLAST not rejected");
    rstn=0;repeat(3) @(posedge clk);@(negedge clk);rstn=1;got=0;done_seen=0;errors=0;
    sendframe(14,5);if(errors==0)$fatal(1,"bad checksum not rejected");
    rstn=0;repeat(3) @(posedge clk);@(negedge clk);rstn=1;got=0;done_seen=0;errors=0;
    sendframe(15,0);if(got!=N)$fatal(1,"reset recovery failed");
    $display("GRAY8_ADAPTER_PASS pixels=%0d",N);$finish;
  end
  initial begin #1000000;$fatal(1,"simulation timeout");end
endmodule







