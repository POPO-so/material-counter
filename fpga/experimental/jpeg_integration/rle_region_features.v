`timescale 1ns/1ps

// Standalone bounded 8-connected run-component feature core.
// Statistics and both row banks use synchronous-read inferred memories.
module rle_region_features #(
    parameter integer MAX_LABELS = 512,
    parameter integer MAX_RUNS_PER_ROW = 320,
    parameter integer FRAME_ID_W = 32,
    parameter integer LABEL_W = (MAX_LABELS <= 2) ? 1 : $clog2(MAX_LABELS),
    parameter integer COUNT_W = (MAX_LABELS < 2) ? 1 : $clog2(MAX_LABELS+1),
    parameter integer ROW_COUNT_W = (MAX_RUNS_PER_ROW < 2) ? 1 : $clog2(MAX_RUNS_PER_ROW+1)
)(
    input wire clk, input wire resetn,
    input wire start, input wire [FRAME_ID_W-1:0] frame_id,
    input wire run_valid, output wire run_ready,
    input wire [9:0] run_y, input wire [9:0] run_x0, input wire [9:0] run_x1,
    input wire finish,
    output reg result_valid, input wire result_ready,
    output reg [31:0] result_area, result_sum_x, result_sum_y,
    output reg [9:0] result_x0, result_y0, result_x1, result_y1,
    output reg [FRAME_ID_W-1:0] result_frame_id,
    output reg done, output reg overflow,
    output reg [COUNT_W-1:0] record_count,
    output reg busy,
    output reg candidate_flag,
    output reg [2:0] candidate_reason,
    input wire [23:0] cfg_max_area,
    input wire [15:0] cfg_max_w,
    input wire [15:0] cfg_max_h,
    input wire [7:0] cfg_aspect_x16
);
    localparam integer ROW_ADDR_W = (2*MAX_RUNS_PER_ROW <= 2) ? 1 : $clog2(2*MAX_RUNS_PER_ROW);
    localparam [3:0] S_IDLE=0, S_ACCEPT=1, S_ROW_REQ=2, S_ROW_WAIT=3,
                     S_FIND=4, S_MERGE_REQ=5, S_MERGE_WAIT=6, S_MERGE_WRITE=7,
                     S_APPLY_REQ=8, S_APPLY_WAIT=9, S_APPLY_WRITE=10,
                     S_EMIT_REQ=11, S_EMIT_WAIT=12, S_EMIT_HOLD=13, S_DONE=14;
    reg [3:0] state;

    // Parent remains a small asynchronous LUTRAM. The wide feature store is
    // one 136-bit synchronous dual-port memory: A is read/write, B is read-only.
    reg [LABEL_W-1:0] parent [0:MAX_LABELS-1];
    wire [135:0] stats_a_q, stats_b_q;
    wire [LABEL_W-1:0] stats_a_addr, stats_b_addr;
    wire [135:0] stats_a_din;
    wire stats_a_we, stats_a_re, stats_b_re;

    // Flat two-bank row RAM; {bank,index} is the synchronous read address.
    reg [19+LABEL_W:0] row_mem [0:2*MAX_RUNS_PER_ROW-1];
    reg [19+LABEL_W:0] row_q;
    wire [ROW_ADDR_W-1:0] row_rd_addr, row_wr_addr;
    wire [19+LABEL_W:0] row_din;
    wire row_we;

    reg row_bank, prev_bank;
    reg [ROW_COUNT_W-1:0] row_count, prev_count, row_run_count;
    reg row_seen;
    reg [9:0] last_y, last_x1;
    reg [9:0] cur_y, cur_x0, cur_x1;
    reg [31:0] cur_area, cur_sx, cur_sy;
    reg [ROW_COUNT_W-1:0] compare_index;
    reg [LABEL_W-1:0] walk_label, merge_root, low_root, high_root;
    reg merge_valid;
    reg [COUNT_W-1:0] label_count, emit_index, component_count;
    reg [FRAME_ID_W-1:0] frame_id_reg;
    reg finish_pending;
    reg [10:0] row_delta;

    wire [10:0] emit_bbox_w = {1'b0,stats_a_q[19:10]} - {1'b0,stats_a_q[39:30]} + 11'd1;
    wire [10:0] emit_bbox_h = {1'b0,stats_a_q[9:0]} - {1'b0,stats_a_q[29:20]} + 11'd1;
    wire [18:0] emit_aspect_lhs = {4'd0,emit_bbox_w,4'd0};
    wire [18:0] emit_aspect_rhs = {8'd0,emit_bbox_h} * {11'd0,cfg_aspect_x16};
    wire [2:0] emit_candidate_reason = {
        (cfg_aspect_x16 != 0) && (emit_aspect_lhs > emit_aspect_rhs),
        (cfg_max_w != 0) && (cfg_max_h != 0) && (emit_bbox_w > cfg_max_w) && (emit_bbox_h > cfg_max_h),
        (cfg_max_area != 0) && (stats_a_q[135:104] > cfg_max_area)
    };

    assign run_ready = (state == S_ACCEPT) && !overflow;
    assign stats_a_addr = (state==S_MERGE_REQ || state==S_MERGE_WAIT || state==S_MERGE_WRITE) ? low_root :
                          (state==S_APPLY_REQ || state==S_APPLY_WAIT || state==S_APPLY_WRITE) ?
                              (merge_valid ? merge_root : label_count[LABEL_W-1:0]) :
                          emit_index[LABEL_W-1:0];
    assign stats_b_addr = (state==S_MERGE_REQ || state==S_MERGE_WAIT || state==S_MERGE_WRITE) ? high_root : 0;
    assign row_rd_addr = prev_bank ? (MAX_RUNS_PER_ROW + compare_index) : compare_index;
    assign row_wr_addr = row_bank ? (MAX_RUNS_PER_ROW + row_count) : row_count;
    assign row_din = {cur_x0, cur_x1, (merge_valid ? merge_root : label_count[LABEL_W-1:0])};
    assign row_we = resetn && (state == S_APPLY_WRITE) && (merge_valid || label_count < MAX_LABELS);
    assign stats_a_we = resetn && ((state == S_MERGE_WRITE) ||
                        ((state == S_APPLY_WRITE) && (merge_valid || label_count < MAX_LABELS)));
    assign stats_a_re = resetn && ((state == S_MERGE_REQ) || (state == S_APPLY_REQ) ||
                                  (state == S_EMIT_REQ && emit_index < label_count));
    assign stats_b_re = resetn && (state == S_MERGE_REQ);
    assign stats_a_din = (state == S_MERGE_WRITE) ? {
            stats_a_q[135:104] + stats_b_q[135:104],
            stats_a_q[103:72] + stats_b_q[103:72],
            stats_a_q[71:40] + stats_b_q[71:40],
            (stats_a_q[39:30] < stats_b_q[39:30]) ? stats_a_q[39:30] : stats_b_q[39:30],
            (stats_a_q[29:20] < stats_b_q[29:20]) ? stats_a_q[29:20] : stats_b_q[29:20],
            (stats_a_q[19:10] > stats_b_q[19:10]) ? stats_a_q[19:10] : stats_b_q[19:10],
            (stats_a_q[9:0] > stats_b_q[9:0]) ? stats_a_q[9:0] : stats_b_q[9:0]
        } : merge_valid ? {
            stats_a_q[135:104] + cur_area,
            stats_a_q[103:72] + cur_sx,
            stats_a_q[71:40] + cur_sy,
            (cur_x0 < stats_a_q[39:30]) ? cur_x0 : stats_a_q[39:30],
            (cur_y < stats_a_q[29:20]) ? cur_y : stats_a_q[29:20],
            (cur_x1 > stats_a_q[19:10]) ? cur_x1 : stats_a_q[19:10],
            (cur_y > stats_a_q[9:0]) ? cur_y : stats_a_q[9:0]
        } : {cur_area,cur_sx,cur_sy,cur_x0,cur_y,cur_x1,cur_y};

    function [31:0] calc_area;
        input [9:0] a, b;
        begin calc_area = {22'd0,b} - {22'd0,a} + 32'd1; end
    endfunction
    function [31:0] calc_sum_x;
        input [9:0] a, b;
        reg [10:0] sum_ends, width;
        reg [21:0] product;
        begin
            sum_ends = {1'b0,a} + {1'b0,b};
            width = {1'b0,b} - {1'b0,a} + 11'd1;
            product = sum_ends * width;
            calc_sum_x = {11'd0,product[20:1]};
        end
    endfunction
    function [31:0] calc_sum_y;
        input [9:0] y, a, b;
        reg [9:0] width;
        reg [19:0] product;
        begin
            width = {1'b0,b} - {1'b0,a} + 10'd1;
            product = y * width;
            calc_sum_y = {12'd0,product};
        end
    endfunction
    function overlap8;
        input [9:0] ax0, ax1, bx0, bx1;
        begin overlap8 = ({1'b0,ax1}+11'd1 >= {1'b0,bx0}) &&
                         ({1'b0,bx1}+11'd1 >= {1'b0,ax0}); end
    endfunction

    // Preserve the synchronous memory boundary when the FSM is optimized.
    // This helper is in this file so the integration source list stays fixed.
    (* keep_hierarchy = "yes" *) rle_region_stats_bram #(
        .DEPTH(MAX_LABELS), .ADDR_W(LABEL_W)
    ) stats_store (
        .clk(clk), .a_we(stats_a_we), .a_re(stats_a_re), .b_re(stats_b_re),
        .a_addr(stats_a_addr), .b_addr(stats_b_addr), .a_din(stats_a_din),
        .a_q(stats_a_q), .b_q(stats_b_q)
    );
    always @(posedge clk) begin
        row_q <= row_mem[row_rd_addr];
        if (row_we) row_mem[row_wr_addr] <= row_din;
    end

    always @(posedge clk) begin
      if (!resetn) begin
        state<=S_IDLE; busy<=0; done<=0; overflow<=0; result_valid<=0;
        record_count<=0; label_count<=0; emit_index<=0; component_count<=0;
        row_bank<=0; prev_bank<=1; row_count<=0; prev_count<=0; row_run_count<=0;
        row_seen<=0; last_y<=0; last_x1<=0;
        cur_y<=0; cur_x0<=0; cur_x1<=0; cur_area<=0; cur_sx<=0; cur_sy<=0;
        compare_index<=0; walk_label<=0; merge_root<=0; low_root<=0; high_root<=0;
        merge_valid<=0; frame_id_reg<=0; finish_pending<=0; row_delta<=0;
        result_area<=0; result_sum_x<=0; result_sum_y<=0;
        result_x0<=0; result_y0<=0; result_x1<=0; result_y1<=0; result_frame_id<=0;
        candidate_flag<=0; candidate_reason<=0;
      end else begin
        done<=0;
        case (state)
          S_IDLE: begin
            busy<=0; result_valid<=0;
            if (start) begin
              busy<=1; overflow<=0; record_count<=0; label_count<=0;
              emit_index<=0; component_count<=0; row_bank<=0; prev_bank<=1;
              row_count<=0; prev_count<=0; row_run_count<=0; row_seen<=0;
              frame_id_reg<=frame_id; state<=S_ACCEPT; finish_pending<=0;
            end
          end
          S_ACCEPT: begin
            if (finish || finish_pending) begin
              finish_pending<=0; emit_index<=0;
              if (overflow) state<=S_DONE; else state<=S_EMIT_REQ;
            end else if (run_valid && !overflow) begin
              if (run_x0 > run_x1 || run_x1 >= 10'd640 || run_y >= 10'd400) overflow<=1;
              else if (row_seen && run_y < last_y) overflow<=1;
              else if (row_seen && run_y == last_y && ({1'b0,run_x0} <= ({1'b0,last_x1}+11'd1))) overflow<=1;
              else if (row_seen && run_y == last_y && row_run_count >= MAX_RUNS_PER_ROW) overflow<=1;
              else begin
                cur_y<=run_y; cur_x0<=run_x0; cur_x1<=run_x1;
                cur_area<=calc_area(run_x0,run_x1);
                cur_sx<=calc_sum_x(run_x0,run_x1);
                cur_sy<=calc_sum_y(run_y,run_x0,run_x1);
                merge_valid<=0;
                if (!row_seen) begin
                  row_seen<=1; row_bank<=0; prev_bank<=1; prev_count<=0;
                  row_count<=0; row_run_count<=1; last_y<=run_y; last_x1<=run_x1;
                  state<=S_APPLY_WRITE;
                end else if (run_y != last_y) begin
                  row_delta={1'b0,run_y}-{1'b0,last_y};
                  prev_bank<=row_bank; prev_count<=row_count; row_bank<=~row_bank;
                  row_count<=0; row_run_count<=1; last_y<=run_y; last_x1<=run_x1;
                  compare_index<=0;
                  if (row_delta==11'd1 && row_count!=0) state<=S_ROW_REQ;
                  else state<=S_APPLY_WRITE;
                end else begin
                  row_run_count<=row_run_count+1'b1; last_x1<=run_x1;
                  compare_index<=0;
                  if (prev_count!=0) state<=S_ROW_REQ; else state<=S_APPLY_WRITE;
                end
              end
            end
          end
          S_ROW_REQ: state<=S_ROW_WAIT;
          S_ROW_WAIT: begin
            if (overlap8(row_q[19+LABEL_W:10+LABEL_W],row_q[9+LABEL_W:LABEL_W],cur_x0,cur_x1)) begin
              walk_label<=parent[row_q[LABEL_W-1:0]];
              state<=S_FIND;
            end else if (compare_index+1'b1 >= prev_count) begin
              if (merge_valid) state<=S_APPLY_REQ; else state<=S_APPLY_WRITE;
            end
            else begin compare_index<=compare_index+1'b1; state<=S_ROW_REQ; end
          end
          S_FIND: begin
            if (parent[walk_label] != walk_label) walk_label<=parent[walk_label];
            else if (!merge_valid) begin
              merge_root<=walk_label; merge_valid<=1;
              if (compare_index+1'b1 >= prev_count) state<=S_APPLY_REQ;
              else begin compare_index<=compare_index+1'b1; state<=S_ROW_REQ; end
            end else if (walk_label == merge_root) begin
              if (compare_index+1'b1 >= prev_count) state<=S_APPLY_REQ;
              else begin compare_index<=compare_index+1'b1; state<=S_ROW_REQ; end
            end else begin
              if (walk_label < merge_root) begin low_root<=walk_label; high_root<=merge_root; end
              else begin low_root<=merge_root; high_root<=walk_label; end
              state<=S_MERGE_REQ;
            end
          end
          S_MERGE_REQ: state<=S_MERGE_WAIT;
          S_MERGE_WAIT: state<=S_MERGE_WRITE;
          S_MERGE_WRITE: begin
            parent[high_root]<=low_root; merge_root<=low_root;
            if (compare_index+1'b1 >= prev_count) state<=S_APPLY_REQ;
            else begin compare_index<=compare_index+1'b1; state<=S_ROW_REQ; end
          end
          S_APPLY_REQ: state<=S_APPLY_WAIT;
          S_APPLY_WAIT: state<=S_APPLY_WRITE;
          S_APPLY_WRITE: begin
            if (!merge_valid && label_count >= MAX_LABELS) begin
              overflow<=1; state<=S_ACCEPT;
            end else begin
              if (!merge_valid) begin
                parent[label_count[LABEL_W-1:0]]<=label_count[LABEL_W-1:0];
                label_count<=label_count+1'b1;
              end
              row_count<=row_count+1'b1;
              state<=S_ACCEPT;
            end
          end
          S_EMIT_REQ: begin
            if (emit_index >= label_count) state<=S_DONE;
            else state<=S_EMIT_WAIT;
          end
          S_EMIT_WAIT: begin
            if (parent[emit_index[LABEL_W-1:0]] == emit_index[LABEL_W-1:0] && stats_a_q[135:104] != 0) begin
              result_valid<=1;
              result_area<=stats_a_q[135:104]; result_sum_x<=stats_a_q[103:72]; result_sum_y<=stats_a_q[71:40];
              result_x0<=stats_a_q[39:30]; result_y0<=stats_a_q[29:20];
              result_x1<=stats_a_q[19:10]; result_y1<=stats_a_q[9:0]; result_frame_id<=frame_id_reg;
              candidate_reason<=emit_candidate_reason;
              candidate_flag<=|emit_candidate_reason;
              component_count<=component_count+1'b1; state<=S_EMIT_HOLD;
            end else begin emit_index<=emit_index+1'b1; state<=S_EMIT_REQ; end
          end
          S_EMIT_HOLD: if (result_ready) begin result_valid<=0; emit_index<=emit_index+1'b1; state<=S_EMIT_REQ; end
          S_DONE: begin
            if (!result_valid || result_ready) begin
              result_valid<=0; record_count<=component_count; done<=1; busy<=0; state<=S_IDLE;
            end
          end
          default: begin state<=S_IDLE; busy<=0; overflow<=1; result_valid<=0; end
        endcase
        if (finish && busy && state!=S_ACCEPT) finish_pending<=1;
      end
    end
endmodule

// Two synchronous memory ports: A reads or writes, B only reads.
// No reset/clear of contents is required: allocation writes every statistic
// before a label can be read during the current frame.
(* keep_hierarchy = "yes" *) module rle_region_stats_bram #(
    parameter integer DEPTH = 512,
    parameter integer ADDR_W = 9
)(
    input wire clk, a_we, a_re, b_re,
    input wire [ADDR_W-1:0] a_addr, b_addr,
    input wire [135:0] a_din,
    output reg [135:0] a_q, b_q
);
    (* ram_style = "block" *) reg [135:0] stats_mem [0:DEPTH-1];
    always @(posedge clk) begin
        if (a_we) stats_mem[a_addr] <= a_din;
        else if (a_re) a_q <= stats_mem[a_addr];
    end
    always @(posedge clk) begin
        if (b_re) b_q <= stats_mem[b_addr];
    end
endmodule
