`timescale 1ns/1ps
module tb_region_diff;
  reg clk=0; always #5 clk=~clk;
  reg resetn=0, start=0, run_valid=0, finish=0, result_ready=0;
  reg [31:0] frame_id=0;
  reg [9:0] run_y=0, run_x0=0, run_x1=0;
  wire rr, dr, rv, dv, rd, dd, ro, dover, rb, db;
  wire [31:0] ra, rsx, rsy, rf, da, dsx, dsy, df;
  wire [9:0] rx0, ry0, rx1, ry1, dx0, dy0, dx1, dy1;
  wire [3:0] rc, dc;
  wire candidate_flag;
  wire [2:0] candidate_reason;
  reg [23:0] cfg_max_area=0;
  reg [15:0] cfg_max_w=0, cfg_max_h=0;
  reg [7:0] cfg_aspect_x16=0;
  integer checks=0;

  rle_region_features_reference #(.MAX_LABELS(8),.MAX_RUNS_PER_ROW(4)) ref_dut (
    .clk(clk),.resetn(resetn),.start(start),.frame_id(frame_id),.run_valid(run_valid),.run_ready(rr),
    .run_y(run_y),.run_x0(run_x0),.run_x1(run_x1),.finish(finish),.result_valid(rv),.result_ready(result_ready),
    .result_area(ra),.result_sum_x(rsx),.result_sum_y(rsy),.result_x0(rx0),.result_y0(ry0),
    .result_x1(rx1),.result_y1(ry1),.result_frame_id(rf),.done(rd),.overflow(ro),.record_count(rc),.busy(rb));
  rle_region_features #(.MAX_LABELS(8),.MAX_RUNS_PER_ROW(4)) dut (
    .clk(clk),.resetn(resetn),.start(start),.frame_id(frame_id),.run_valid(run_valid),.run_ready(dr),
    .run_y(run_y),.run_x0(run_x0),.run_x1(run_x1),.finish(finish),.result_valid(dv),.result_ready(result_ready),
    .result_area(da),.result_sum_x(dsx),.result_sum_y(dsy),.result_x0(dx0),.result_y0(dy0),
    .result_x1(dx1),.result_y1(dy1),.result_frame_id(df),.done(dd),.overflow(dover),.record_count(dc),.busy(db),
    .candidate_flag(candidate_flag),.candidate_reason(candidate_reason),.cfg_max_area(cfg_max_area),
    .cfg_max_w(cfg_max_w),.cfg_max_h(cfg_max_h),.cfg_aspect_x16(cfg_aspect_x16));

  task automatic begin_frame(input [31:0] fid);
    begin @(negedge clk); frame_id=fid; start=1; @(negedge clk); start=0; end
  endtask
  task automatic send_run(input integer y,x0,x1);
    integer t;
    begin
      t=0;
      while (!(rr && dr)) begin @(negedge clk); t=t+1; if (t>5000) $fatal(1,"diff ready timeout"); end
      run_y=y; run_x0=x0; run_x1=x1; run_valid=1;
      @(posedge clk);
      if (!(rr && dr)) $fatal(1,"reference/DUT handshake skew");
      @(negedge clk); run_valid=0;
    end
  endtask
  task automatic end_input;
    begin @(negedge clk); finish=1; @(negedge clk); finish=0; end
  endtask
  task automatic compare_result(input integer stall_cycles,input [31:0] ea,esx,esy,
                                input [9:0] ex0,ey0,ex1,ey1,input [31:0] efid);
    reg [167:0] ref_hold, dut_hold;
    integer t;
    begin
      t=0;
      while (!(rv && dv)) begin @(negedge clk); t=t+1; if (t>10000) $fatal(1,"diff result timeout ref=%b dut=%b",rv,dv); end
      ref_hold={ra,rsx,rsy,rx0,ry0,rx1,ry1,rf};
      dut_hold={da,dsx,dsy,dx0,dy0,dx1,dy1,df};
      if (ref_hold !== dut_hold) $fatal(1,"reference mismatch %h != %h",dut_hold,ref_hold);
      if (ref_hold !== {ea,esx,esy,ex0,ey0,ex1,ey1,efid})
        $fatal(1,"reference scoreboard mismatch got area=%0d sx=%0d sy=%0d bbox=(%0d,%0d)-(%0d,%0d)",ra,rsx,rsy,rx0,ry0,rx1,ry1);
      if (candidate_flag !== 1'b0 || candidate_reason !== 3'b000)
        $fatal(1,"disabled candidate filters must produce zero reason");
      repeat (stall_cycles) begin
        @(posedge clk);
        if (!rv || !dv || {ra,rsx,rsy,rx0,ry0,rx1,ry1,rf} !== ref_hold ||
            {da,dsx,dsy,dx0,dy0,dx1,dy1,df} !== dut_hold || candidate_flag !== 1'b0 ||
            candidate_reason !== 3'b000) $fatal(1,"stall stability mismatch");
      end
      @(negedge clk); result_ready=1; @(negedge clk); result_ready=0; checks=checks+1;
    end
  endtask
  task automatic expect_done(input integer count,input bit ov);
    integer t; reg rseen,dseen;
    begin
      t=0; rseen=0; dseen=0;
      while (!(rseen && dseen)) begin
        @(negedge clk); if (rd) rseen=1; if (dd) dseen=1;
        t=t+1; if (t>12000) $fatal(1,"diff done timeout ref=%b dut=%b",rd,dd);
      end
      if (ro !== ov || dover !== ov || rc !== count || dc !== count || ro !== dover)
        $fatal(1,"diff completion mismatch count ref=%0d dut=%0d ov ref=%b dut=%b",rc,dc,ro,dover);
    end
  endtask

  initial begin
    repeat (4) @(negedge clk); resetn=1;
    $display("DIFF empty"); begin_frame(1); end_input(); expect_done(0,0);
    $display("DIFF merge-chain plus dynamic stall"); begin_frame(2);
    send_run(0,0,0); send_run(0,4,4); send_run(0,8,8);
    send_run(1,1,5); send_run(1,8,8); send_run(2,0,8); end_input();
    compare_result(1,18,71,24,0,0,8,2,2); expect_done(1,0);
    $display("DIFF empty multi-frame"); begin_frame(3); end_input(); expect_done(0,0);
    $display("DIFF row-gap multi-frame"); begin_frame(4); send_run(0,1,1); send_run(2,1,1); end_input();
    compare_result(0,1,1,0,1,0,1,0,4); compare_result(3,1,1,2,1,2,1,2,4); expect_done(2,0);
    $display("DIFF root reuse"); begin_frame(5);
    for (integer k=0;k<4;k=k+1) send_run(0,2*k,2*k);
    for (integer y=1;y<3;y=y+1) for (integer k=0;k<4;k=k+1) send_run(y,2*k,2*k);
    end_input();
    for (integer k=0;k<4;k=k+1) compare_result(k,3,6*k,3,2*k,0,2*k,2,5);
    expect_done(4,0);
    $display("REGION_DIFF_PASS compared=%0d",checks); $finish;
  end
  initial begin #500000; $fatal(1,"diff watchdog"); end
endmodule
