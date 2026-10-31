`timescale 1ns/1ps
module tb_region_limits;
  reg clk=0; always #5 clk=~clk;
  reg resetn=0, start=0, run_valid=0, finish=0, result_ready=0;
  reg [31:0] frame_id=0;
  reg [9:0] run_y=0, run_x0=0, run_x1=0;
  wire rr,dr,rv,dv,rd,dd,ro,dover;
  wire [31:0] ra,rsx,rsy,rf,da,dsx,dsy,df;
  wire [9:0] rx0,ry0,rx1,ry1,dx0,dy0,dx1,dy1;
  wire [9:0] rc,dc;
  integer n, row_i, col_i, base_x, exp_x, exp_y;

  rle_region_features_reference #(.MAX_LABELS(512),.MAX_RUNS_PER_ROW(320)) ref_dut (
    .clk(clk),.resetn(resetn),.start(start),.frame_id(frame_id),.run_valid(run_valid),.run_ready(rr),
    .run_y(run_y),.run_x0(run_x0),.run_x1(run_x1),.finish(finish),.result_valid(rv),.result_ready(result_ready),
    .result_area(ra),.result_sum_x(rsx),.result_sum_y(rsy),.result_x0(rx0),.result_y0(ry0),
    .result_x1(rx1),.result_y1(ry1),.result_frame_id(rf),.done(rd),.overflow(ro),.record_count(rc),.busy());
  rle_region_features #(.MAX_LABELS(512),.MAX_RUNS_PER_ROW(320)) dut (
    .clk(clk),.resetn(resetn),.start(start),.frame_id(frame_id),.run_valid(run_valid),.run_ready(dr),
    .run_y(run_y),.run_x0(run_x0),.run_x1(run_x1),.finish(finish),.result_valid(dv),.result_ready(result_ready),
    .result_area(da),.result_sum_x(dsx),.result_sum_y(dsy),.result_x0(dx0),.result_y0(dy0),
    .result_x1(dx1),.result_y1(dy1),.result_frame_id(df),.done(dd),.overflow(dover),.record_count(dc),.busy());

  task automatic begin_frame(input [31:0] fid);
    begin @(negedge clk); frame_id=fid; start=1; @(negedge clk); start=0; end
  endtask
  task automatic send_run(input integer y,x);
    integer t;
    begin
      t=0; while (!(rr && dr)) begin @(negedge clk); t=t+1; if (t>10000) $fatal(1,"limit ready timeout"); end
      run_y=y; run_x0=x; run_x1=x; run_valid=1; @(posedge clk);
      if (!(rr && dr)) $fatal(1,"limit handshake skew");
      @(negedge clk); run_valid=0;
    end
  endtask
  task automatic end_input;
    begin @(negedge clk); finish=1; @(negedge clk); finish=0; end
  endtask
  task automatic expect_item(input integer index, input integer x, y, input [31:0] fid);
    integer t;
    begin
      t=0; while (!(rv && dv)) begin @(negedge clk); t=t+1; if (t>50000) $fatal(1,"limit result timeout index=%0d",index); end
      if ({ra,rsx,rsy,rx0,ry0,rx1,ry1,rf} !== {32'd1,32'(x),32'(y),10'(x),10'(y),10'(x),10'(y),fid} ||
          {da,dsx,dsy,dx0,dy0,dx1,dy1,df} !== {ra,rsx,rsy,rx0,ry0,rx1,ry1,rf})
        $fatal(1,"limit result mismatch index=%0d expected x=%0d y=%0d got ref=(%0d,%0d,%0d) dut=(%0d,%0d,%0d)",
          index,x,y,ra,rsx,rsy,da,dsx,dsy);
      // Exercise bounded, varying downstream stalls across the large result stream.
      repeat (index%4) begin
        @(posedge clk);
        if (!rv || !dv || {ra,rsx,rsy,rx0,ry0,rx1,ry1,rf} !== {32'd1,32'(x),32'(y),10'(x),10'(y),10'(x),10'(y),fid})
          $fatal(1,"limit backpressure instability index=%0d",index);
      end
      @(negedge clk); result_ready=1; @(negedge clk); result_ready=0;
    end
  endtask
  task automatic expect_done(input integer count,input bit ov);
    integer t; reg rs,ds;
    begin
      t=0; rs=0; ds=0;
      while (!(rs && ds)) begin @(negedge clk); if (rd) rs=1; if (dd) ds=1; t=t+1; if (t>60000) $fatal(1,"limit done timeout"); end
      if (ro!==ov || dover!==ov || rc!==count || dc!==count) $fatal(1,"limit completion mismatch");
    end
  endtask

  task automatic expect_two_row(input integer index);
    integer t, x;
    reg [167:0] expected;
    begin
      x=2*index;
      expected={32'd2,32'(2*x),32'd1,10'(x),10'd0,10'(x),10'd1,32'd23};
      t=0;
      while (!(rv && dv)) begin @(negedge clk); t=t+1; if(t>50000) $fatal(1,"row320 read timeout index=%0d",index); end
      if ({ra,rsx,rsy,rx0,ry0,rx1,ry1,rf} !== expected ||
          {da,dsx,dsy,dx0,dy0,dx1,dy1,df} !== expected) $fatal(1,"row320 synchronous read mismatch index=%0d",index);
      repeat(index%3) begin
        @(posedge clk);
        if (!rv || !dv || {da,dsx,dsy,dx0,dy0,dx1,dy1,df} !== expected)
          $fatal(1,"row320 stall mismatch index=%0d",index);
      end
      @(negedge clk); result_ready=1; @(negedge clk); result_ready=0;
    end
  endtask

  initial begin
    repeat (4) @(negedge clk); resetn=1;
    $display("LIMIT row320 exact capacity"); begin_frame(20);
    for (n=0;n<320;n=n+1) send_run(0,2*n);
    if (ro || dover) $fatal(1,"row320 exact fill overflowed"); end_input();
    for (n=0;n<320;n=n+1) expect_item(n,2*n,0,20);
    expect_done(320,0);

    $display("LIMIT row320 two banks synchronous scan"); begin_frame(23);
    for (n=0;n<320;n=n+1) send_run(0,2*n);
    for (n=0;n<320;n=n+1) send_run(1,2*n);
    end_input();
    for (n=0;n<320;n=n+1) expect_two_row(n);
    expect_done(320,0);

    $display("LIMIT label512 exact capacity"); begin_frame(21); n=0;
    for (row_i=0;row_i<171;row_i=row_i+1) begin
      exp_y=2*row_i; base_x=(row_i%2==0)?0:620;
      for (col_i=0;col_i<((row_i==170)?2:3);col_i=col_i+1) begin
        exp_x=base_x+4*col_i; send_run(exp_y,exp_x); n=n+1;
      end
    end
    if (n!=512 || ro || dover) $fatal(1,"512 boundary setup failed n=%0d",n);
    end_input(); n=0;
    for (row_i=0;row_i<171;row_i=row_i+1) begin
      exp_y=2*row_i; base_x=(row_i%2==0)?0:620;
      for (col_i=0;col_i<((row_i==170)?2:3);col_i=col_i+1) begin
        exp_x=base_x+4*col_i; expect_item(n,exp_x,exp_y,21); n=n+1;
      end
    end
    expect_done(512,0);

    $display("LIMIT label513 overflow"); begin_frame(22);
    for (row_i=0;row_i<171;row_i=row_i+1) begin
      exp_y=2*row_i; base_x=(row_i%2==0)?0:620;
      for (col_i=0;col_i<3;col_i=col_i+1) send_run(exp_y,base_x+4*col_i);
    end
    while (!(ro && dover)) @(negedge clk);
    end_input(); expect_done(0,1);
    $display("REGION_LIMITS_PASS row_runs=320 labels=512 overflow=513"); $finish;
  end
  initial begin #10000000; $fatal(1,"limits watchdog"); end
endmodule
