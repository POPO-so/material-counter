`timescale 1ns/1ps
module tb_region_candidate_filters;
  reg clk=0; always #5 clk=~clk;
  reg resetn=0, start=0, run_valid=0, finish=0, result_ready=0;
  reg [31:0] frame_id=0;
  reg [9:0] run_y=0, run_x0=0, run_x1=0;
  reg [23:0] cfg_max_area=0;
  reg [15:0] cfg_max_w=0, cfg_max_h=0;
  reg [7:0] cfg_aspect_x16=0;
  wire run_ready, result_valid, done, overflow, busy, candidate_flag;
  wire [2:0] candidate_reason;
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
    .result_frame_id(result_frame_id),.done(done),.overflow(overflow),.record_count(record_count),.busy(busy),
    .candidate_flag(candidate_flag),.candidate_reason(candidate_reason),.cfg_max_area(cfg_max_area),
    .cfg_max_w(cfg_max_w),.cfg_max_h(cfg_max_h),.cfg_aspect_x16(cfg_aspect_x16)
  );

  task automatic begin_frame(input [31:0] fid, input [23:0] area_limit,
                             input [15:0] width_limit, height_limit, input [7:0] aspect_limit);
    begin
      @(negedge clk);
      frame_id=fid; cfg_max_area=area_limit; cfg_max_w=width_limit;
      cfg_max_h=height_limit; cfg_aspect_x16=aspect_limit; start=1;
      @(negedge clk); start=0;
    end
  endtask

  task automatic send_run(input integer y, x0, x1);
    integer t;
    begin
      @(negedge clk); run_y=y; run_x0=x0; run_x1=x1; run_valid=1; t=0;
      while (!run_ready) begin
        @(negedge clk); t=t+1;
        if (t>1000) $fatal(1,"candidate run-ready timeout");
      end
      @(posedge clk); @(negedge clk); run_valid=0;
    end
  endtask

  task automatic end_input;
    begin @(negedge clk); finish=1; @(negedge clk); finish=0; end
  endtask

  task automatic expect_result(input [31:0] area, input [9:0] x0,y0,x1,y1,
                               input [2:0] expected_reason, input integer test_case);
    reg [167:0] held_data;
    reg [2:0] held_reason;
    reg held_flag;
    integer t;
    begin
      t=0;
      while (!result_valid) begin
        @(negedge clk); t=t+1;
        if (t>10000) $fatal(1,"candidate result timeout case=%0d",test_case);
      end
      if ({result_area,result_x0,result_y0,result_x1,result_y1} !== {area,x0,y0,x1,y1} ||
          candidate_reason !== expected_reason || candidate_flag !== (|expected_reason))
        $fatal(1,"candidate mismatch case=%0d area=%0d bbox=(%0d,%0d)-(%0d,%0d) reason=%b flag=%b expected=%b",
          test_case,result_area,result_x0,result_y0,result_x1,result_y1,candidate_reason,candidate_flag,expected_reason);
      held_data={result_area,result_sum_x,result_sum_y,result_x0,result_y0,result_x1,result_y1,result_frame_id};
      held_reason=candidate_reason; held_flag=candidate_flag;
      repeat (4) begin
        @(posedge clk);
        if (!result_valid || {result_area,result_sum_x,result_sum_y,result_x0,result_y0,result_x1,result_y1,result_frame_id} !== held_data ||
            candidate_reason !== held_reason || candidate_flag !== held_flag)
          $fatal(1,"candidate output changed during backpressure case=%0d",test_case);
      end
      @(negedge clk); result_ready=1; @(negedge clk); result_ready=0;
    end
  endtask

  task automatic expect_done(input integer test_case);
    integer t;
    begin
      t=0;
      while (!done) begin
        @(negedge clk); t=t+1;
        if (t>1000) $fatal(1,"candidate done timeout case=%0d",test_case);
      end
      if (overflow || record_count!=1) $fatal(1,"candidate completion mismatch case=%0d",test_case);
    end
  endtask

  initial begin
    repeat (4) @(negedge clk); resetn=1;

    // Area comparison is strict and disabled at zero.
    begin_frame(1,2,0,0,0); send_run(0,0,2); end_input();
    expect_result(3,0,0,2,0,3'b001,1); expect_done(1);

    begin_frame(2,3,0,0,0); send_run(0,0,2); end_input();
    expect_result(3,0,0,2,0,3'b000,2); expect_done(2);

    // Width and height must both be enabled and both dimensions must exceed limits.
    begin_frame(3,0,2,1,0); send_run(0,0,2); send_run(1,0,2); end_input();
    expect_result(6,0,0,2,1,3'b010,3); expect_done(3);

    begin_frame(4,0,2,0,0); send_run(0,0,2); send_run(1,0,2); end_input();
    expect_result(6,0,0,2,1,3'b000,4); expect_done(4);

    // Aspect test is strict: 3/1 exceeds 2/1, while 2/1 equals 2/1.
    begin_frame(5,0,0,0,32); send_run(0,0,2); end_input();
    expect_result(3,0,0,2,0,3'b100,5); expect_done(5);

    begin_frame(6,0,0,0,32); send_run(0,0,1); end_input();
    expect_result(2,0,0,1,0,3'b000,6); expect_done(6);

    // Multiple reasons may be asserted together; equality suppresses bit 2.
    begin_frame(7,5,2,1,24); send_run(0,0,2); send_run(1,0,2); end_input();
    expect_result(6,0,0,2,1,3'b011,7); expect_done(7);

    $display("REGION_CANDIDATE_PASS cases=7"); $finish;
  end
  initial begin #1000000; $fatal(1,"candidate test watchdog"); end
endmodule
