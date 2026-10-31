`timescale 1ns/1ps
module tb_rle_region_features;
  reg clk=0; always #5 clk=~clk;
  reg resetn=0, start=0, run_valid=0, finish=0, result_ready=0;
  reg [31:0] frame_id=0;
  reg [9:0] run_y=0, run_x0=0, run_x1=0;
  wire run_ready, result_valid, done, overflow, busy;
  wire [31:0] result_area, result_sum_x, result_sum_y;
  wire [9:0] result_x0, result_y0, result_x1, result_y1;
  wire [31:0] result_frame_id;
  wire [3:0] record_count;

  rle_region_features #(.MAX_LABELS(8),.MAX_RUNS_PER_ROW(4)) dut (
    .clk(clk),.resetn(resetn),.start(start),.frame_id(frame_id),
    .run_valid(run_valid),.run_ready(run_ready),.run_y(run_y),.run_x0(run_x0),.run_x1(run_x1),
    .finish(finish),.result_valid(result_valid),.result_ready(result_ready),
    .result_area(result_area),.result_sum_x(result_sum_x),.result_sum_y(result_sum_y),
    .result_x0(result_x0),.result_y0(result_y0),.result_x1(result_x1),.result_y1(result_y1),
    .result_frame_id(result_frame_id),.done(done),.overflow(overflow),.record_count(record_count),.busy(busy)
  );

  task automatic begin_frame(input [31:0] fid);
    begin
      @(negedge clk); frame_id=fid; start=1;
      @(negedge clk); start=0;
    end
  endtask
  task automatic send_run(input integer y, input integer x0, input integer x1);
    integer waitcnt;
    begin
      @(negedge clk); run_y=y; run_x0=x0; run_x1=x1; run_valid=1;
      waitcnt=0;
      do begin
        @(posedge clk); waitcnt=waitcnt+1;
        if (waitcnt>100) $fatal(1,"run handshake timeout y=%0d x0=%0d x1=%0d state=%0d idx=%0d prev_count=%0d",y,x0,x1,dut.state,dut.compare_index,dut.prev_count);
      end while (!run_ready);
      @(negedge clk); run_valid=0;
    end
  endtask
  task automatic end_input;
    begin @(negedge clk); finish=1; @(negedge clk); finish=0; end
  endtask
  task automatic expect_region(input [31:0] a,sx,sy,input [9:0] x0,y0,x1,y1,input [31:0] fid);
    reg [31:0] hold_a,hold_sx,hold_sy,hold_fid;
    reg [9:0] hold_x0,hold_y0,hold_x1,hold_y1;
    integer waitcnt;
    begin
      waitcnt=0;
      while (!result_valid) begin
        @(posedge clk); waitcnt=waitcnt+1;
        if (waitcnt>5000) $fatal(1,"result timeout state=%0d labels=%0d emit=%0d area0=%0d root0=%0d",dut.state,dut.label_count,dut.emit_index,dut.area_acc[0],dut.parent[0]);
      end
      if ({result_area,result_sum_x,result_sum_y,result_x0,result_y0,result_x1,result_y1,result_frame_id} !==
          {a,sx,sy,x0,y0,x1,y1,fid})
        $fatal(1,"region mismatch got area=%0d sx=%0d sy=%0d bbox=(%0d,%0d)-(%0d,%0d) fid=%0d",
          result_area,result_sum_x,result_sum_y,result_x0,result_y0,result_x1,result_y1,result_frame_id);
      hold_a=result_area; hold_sx=result_sum_x; hold_sy=result_sum_y; hold_fid=result_frame_id;
      hold_x0=result_x0; hold_y0=result_y0; hold_x1=result_x1; hold_y1=result_y1;
      repeat (3) begin
        @(posedge clk);
        if (!result_valid || result_area!==hold_a || result_sum_x!==hold_sx || result_sum_y!==hold_sy || result_frame_id!==hold_fid ||
            result_x0!==hold_x0 || result_y0!==hold_y0 || result_x1!==hold_x1 || result_y1!==hold_y1)
          $fatal(1,"output changed while backpressured");
      end
      @(negedge clk); result_ready=1;
      @(negedge clk); result_ready=0;
    end
  endtask
  task automatic expect_done(input integer n, input bit ov);
    integer waitcnt;
    begin
      waitcnt=0;
      while (!done) begin
        @(negedge clk); waitcnt=waitcnt+1;
        if (waitcnt>5000) $fatal(1,"done timeout state=%0d labels=%0d emit=%0d",dut.state,dut.label_count,dut.emit_index);
      end
      if (overflow !== ov) $fatal(1,"overflow expected %0d got %0d",ov,overflow);
      if (record_count !== n) $fatal(1,"record_count expected %0d got %0d",n,record_count);
    end
  endtask
  integer k;
  integer yloop;
  initial begin
    repeat (4) @(negedge clk); resetn=1;

    // Empty frame.
    $display("CASE empty"); begin_frame(1); end_input(); expect_done(0,0);

    // Exact run moments: pixels x=3,4,5 on row 2.
    $display("CASE single"); begin_frame(2); send_run(2,3,5); end_input();
    expect_region(3,12,6,3,2,5,2,2); expect_done(1,0);

    // Diagonal joins followed by a run that bridges two prior components.
    $display("CASE bridge"); begin_frame(3);
    send_run(0,0,1); send_run(0,5,5);
    send_run(1,2,3); send_run(1,5,6);
    send_run(2,0,6); end_input();
    expect_region(14,43,18,0,0,6,2,3); expect_done(1,0);

    // A missing row keeps geometrically aligned runs disconnected.
    $display("CASE gap"); begin_frame(4); send_run(0,1,1); send_run(2,1,1); end_input();
    expect_region(1,1,0,1,0,1,0,4); expect_region(1,1,2,1,2,1,2,4); expect_done(2,0);

    // Twelve runs form four persistent roots: labels are not allocated per run.
    $display("CASE root reuse"); begin_frame(8);
    for (k=0;k<4;k=k+1) send_run(0,k*2,k*2);
    for (yloop=1;yloop<3;yloop=yloop+1)
      for (k=0;k<4;k=k+1) send_run(yloop,k*2,k*2);
    end_input();
    for (k=0;k<4;k=k+1) expect_region(3,k*6,3,k*2,0,k*2,2,8);
    expect_done(4,0);
    if (dut.label_count != 4) $fatal(1,"expected 4 reused component labels, got %0d",dut.label_count);

    // Per-row run bound overflow rejects the entire frame.
    $display("CASE row overflow"); begin_frame(5);
    for (k=0;k<4;k=k+1) send_run(0, k*2, k*2);
    send_run(0,8,8);
    if (!overflow || run_ready) $fatal(1,"row exhaustion must block run_ready");
    end_input(); expect_done(0,1);

    // Label capacity overflow across non-adjacent rows rejects the entire frame.
    $display("CASE label overflow"); begin_frame(6);
    for (k=0;k<8;k=k+1) send_run(k*2,0,0);
    send_run(16,0,0); wait(overflow);
    if (run_ready) $fatal(1,"label exhaustion must block run_ready");
    end_input(); expect_done(0,1);

    // Illegal interval rejects the entire frame.
    $display("CASE illegal"); begin_frame(7); send_run(0,4,3); end_input(); expect_done(0,1);

    // Adjacent same-row intervals are not maximal and invalidate the frame.
    $display("CASE nonmaximal"); begin_frame(11); send_run(0,0,1); send_run(0,2,2);
    if (!overflow || run_ready) $fatal(1,"overflow must block further run handshakes");
    end_input(); expect_done(0,1);

    // Enforce the actual 640x400 geometry, not merely the 10-bit encoding.
    $display("CASE x bound"); begin_frame(12); send_run(0,640,640);
    if (!overflow || run_ready) $fatal(1,"x bound overflow did not block run_ready");
    end_input(); expect_done(0,1);
    $display("CASE y bound"); begin_frame(13); send_run(400,0,0);
    if (!overflow || run_ready) $fatal(1,"y bound overflow did not block run_ready");
    end_input(); expect_done(0,1);

    // Reset aborts an unconsumed record and clears active/valid state.
    $display("CASE reset abort"); begin_frame(14); send_run(0,0,0); end_input();
    while (!result_valid) @(negedge clk);
    @(negedge clk); resetn=0;
    @(posedge clk); @(negedge clk);
    if (busy || result_valid || done) $fatal(1,"reset failed to abort active result");
    resetn=1;
    begin_frame(15); end_input(); expect_done(0,0);

    $display("RLE_FEATURE_PASS cases=12");
    $finish;
  end
  initial begin #200000; $fatal(1,"watchdog timeout"); end
  always @(posedge clk) if (resetn && overflow && result_valid)
    $fatal(1,"overflow frame exposed a partial result");
endmodule
