`timescale 1ns/1ps

// Standalone bounded 8-connected run-component feature core.
// Labels represent provisional components, not individual runs. Only the
// previous row's runs are retained; component statistics are accumulated at
// their current minimum-label root. Input runs must describe maximal runs in
// a 640x400 frame, ordered by y then x0. A finish pulse closes input after all
// runs have been sent; busy-time pulses are latched until the FSM can finish.
// Output fields remain stable while result_valid && !result_ready. An overflow
// frame drains with no result records and reports done only after finish.
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
    output reg busy
);
    localparam [3:0] S_IDLE=0, S_ACCEPT=1, S_COMPARE=2, S_FIND=3,
                     S_APPLY=4, S_EMIT=5, S_DONE=6;
    reg [3:0] state;

    reg [LABEL_W-1:0] parent [0:MAX_LABELS-1];
    reg [31:0] area_acc [0:MAX_LABELS-1];
    reg [31:0] sx_acc [0:MAX_LABELS-1];
    reg [31:0] sy_acc [0:MAX_LABELS-1];
    reg [9:0] bbox_x0 [0:MAX_LABELS-1];
    reg [9:0] bbox_y0 [0:MAX_LABELS-1];
    reg [9:0] bbox_x1 [0:MAX_LABELS-1];
    reg [9:0] bbox_y1 [0:MAX_LABELS-1];

    reg [9:0] row_x0 [0:1][0:MAX_RUNS_PER_ROW-1];
    reg [9:0] row_x1 [0:1][0:MAX_RUNS_PER_ROW-1];
    reg [LABEL_W-1:0] row_label [0:1][0:MAX_RUNS_PER_ROW-1];
    reg row_bank, prev_bank;
    reg [ROW_COUNT_W-1:0] row_count, prev_count;
    reg [ROW_COUNT_W-1:0] row_run_count;
    reg row_seen;
    reg [9:0] last_y, last_x1;

    reg [9:0] cur_y, cur_x0, cur_x1;
    reg [31:0] cur_area, cur_sx, cur_sy;
    reg [ROW_COUNT_W-1:0] compare_index;
    reg [LABEL_W-1:0] walk_label, merge_root;
    reg merge_valid;
    reg [COUNT_W-1:0] label_count, emit_index, component_count;
    reg [FRAME_ID_W-1:0] frame_id_reg;
    reg finish_pending;

    assign run_ready = (state == S_ACCEPT) && !overflow;

    function [31:0] calc_area;
        input [9:0] a, b;
        begin calc_area = {22'd0,b} - {22'd0,a} + 32'd1; end
    endfunction
    function [31:0] calc_sum_x;
        input [9:0] a, b;
        reg [10:0] sum_ends;
        reg [10:0] width;
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

    reg [LABEL_W-1:0] old_label;
    reg [LABEL_W-1:0] low_root, high_root;
    reg [10:0] row_delta;

    always @(posedge clk) begin
      if (!resetn) begin
        state<=S_IDLE; busy<=0; done<=0; overflow<=0; result_valid<=0;
        record_count<=0; label_count<=0; emit_index<=0; component_count<=0;
        row_bank<=0; prev_bank<=1; row_count<=0; prev_count<=0;
        row_run_count<=0; row_seen<=0; last_y<=0; last_x1<=0;
        cur_y<=0; cur_x0<=0; cur_x1<=0; cur_area<=0; cur_sx<=0; cur_sy<=0;
        compare_index<=0; walk_label<=0; merge_root<=0; merge_valid<=0;
        frame_id_reg<=0;
        finish_pending<=0;
        result_area<=0; result_sum_x<=0; result_sum_y<=0;
        result_x0<=0; result_y0<=0; result_x1<=0; result_y1<=0; result_frame_id<=0;
      end else begin
        done<=0;
        case (state)
          S_IDLE: begin
            busy<=0; result_valid<=0;
            if (start) begin
              busy<=1; overflow<=0; record_count<=0; label_count<=0;
              emit_index<=0; component_count<=0; row_bank<=0; prev_bank<=1;
              row_count<=0; prev_count<=0; row_run_count<=0; row_seen<=0;
              frame_id_reg<=frame_id; state<=S_ACCEPT;
              finish_pending<=0;
            end
          end
          S_ACCEPT: begin
            if (finish || finish_pending) begin
              finish_pending<=0;
              if (overflow) begin emit_index<=0; state<=S_DONE; end
              else begin emit_index<=0; state<=S_EMIT; end
            end else if (run_valid && !overflow) begin
              if (run_x0 > run_x1 || run_x1 >= 10'd640 || run_y >= 10'd400) overflow<=1;
              else if (row_seen && (run_y < last_y ||
                       ((run_y == last_y) && ({1'b0,run_x0} <= ({1'b0,last_x1}+11'd1))))) overflow<=1;
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
                  state<=S_APPLY;
                end else if (run_y != last_y) begin
                  row_delta={1'b0,run_y}-{1'b0,last_y};
                  prev_bank<=row_bank; prev_count<=row_count; row_bank<=~row_bank;
                  row_count<=0; row_run_count<=1; last_y<=run_y; last_x1<=run_x1;
                  compare_index<=0;
                  if (row_delta==11'd1 && row_count!=0) state<=S_COMPARE;
                  else state<=S_APPLY;
                end else begin
                  row_run_count<=row_run_count+1'b1; last_x1<=run_x1;
                  compare_index<=0;
                  if (prev_count!=0) state<=S_COMPARE;
                  else state<=S_APPLY;
                end
              end
            end
          end
          S_COMPARE: begin
            if (compare_index >= prev_count) state<=S_APPLY;
            else if (overlap8(row_x0[prev_bank][compare_index], row_x1[prev_bank][compare_index], cur_x0, cur_x1)) begin
              old_label=row_label[prev_bank][compare_index];
              walk_label<=parent[old_label]; state<=S_FIND;
            end else compare_index<=compare_index+1'b1;
          end
          S_FIND: begin
            if (parent[walk_label] != walk_label) walk_label<=parent[walk_label];
            else begin
              if (!merge_valid) begin merge_root<=walk_label; merge_valid<=1; end
              else if (walk_label != merge_root) begin
                if (walk_label < merge_root) begin low_root=walk_label; high_root=merge_root; end
                else begin low_root=merge_root; high_root=walk_label; end
                parent[high_root]<=low_root;
                area_acc[low_root]<=area_acc[low_root]+area_acc[high_root];
                sx_acc[low_root]<=sx_acc[low_root]+sx_acc[high_root];
                sy_acc[low_root]<=sy_acc[low_root]+sy_acc[high_root];
                if (bbox_x0[high_root]<bbox_x0[low_root]) bbox_x0[low_root]<=bbox_x0[high_root];
                if (bbox_y0[high_root]<bbox_y0[low_root]) bbox_y0[low_root]<=bbox_y0[high_root];
                if (bbox_x1[high_root]>bbox_x1[low_root]) bbox_x1[low_root]<=bbox_x1[high_root];
                if (bbox_y1[high_root]>bbox_y1[low_root]) bbox_y1[low_root]<=bbox_y1[high_root];
                merge_root<=low_root;
              end
              compare_index<=compare_index+1'b1; state<=S_COMPARE;
            end
          end
          S_APPLY: begin
            if (!merge_valid && label_count >= MAX_LABELS) begin
              overflow<=1; state<=S_ACCEPT;
            end else if (!merge_valid) begin
              parent[label_count]<=label_count[LABEL_W-1:0];
              label_count<=label_count+1'b1;
            end
            if (merge_valid || label_count < MAX_LABELS) begin
              // A newly allocated label is initialized directly with its first run.
              if (merge_valid) begin
                area_acc[merge_root]<=area_acc[merge_root]+cur_area;
                sx_acc[merge_root]<=sx_acc[merge_root]+cur_sx;
                sy_acc[merge_root]<=sy_acc[merge_root]+cur_sy;
                if (cur_x0<bbox_x0[merge_root]) bbox_x0[merge_root]<=cur_x0;
                if (cur_y<bbox_y0[merge_root]) bbox_y0[merge_root]<=cur_y;
                if (cur_x1>bbox_x1[merge_root]) bbox_x1[merge_root]<=cur_x1;
                if (cur_y>bbox_y1[merge_root]) bbox_y1[merge_root]<=cur_y;
                row_x0[row_bank][row_count]<=cur_x0; row_x1[row_bank][row_count]<=cur_x1;
                row_label[row_bank][row_count]<=merge_root; row_count<=row_count+1'b1;
                state<=S_ACCEPT;
              end else begin
                // Newly allocated label stats are assigned directly.
                area_acc[label_count]<=cur_area; sx_acc[label_count]<=cur_sx; sy_acc[label_count]<=cur_sy;
                bbox_x0[label_count]<=cur_x0; bbox_y0[label_count]<=cur_y;
                bbox_x1[label_count]<=cur_x1; bbox_y1[label_count]<=cur_y;
                row_x0[row_bank][row_count]<=cur_x0; row_x1[row_bank][row_count]<=cur_x1;
                row_label[row_bank][row_count]<=label_count[LABEL_W-1:0]; row_count<=row_count+1'b1;
                state<=S_ACCEPT;
              end
            end
          end
          S_EMIT: begin
            if (result_valid) begin
              if (result_ready) begin result_valid<=0; emit_index<=emit_index+1'b1; end
            end else if (emit_index >= label_count) state<=S_DONE;
            else if (parent[emit_index[LABEL_W-1:0]] == emit_index[LABEL_W-1:0] &&
                     area_acc[emit_index[LABEL_W-1:0]] != 0) begin
              result_valid<=1; result_area<=area_acc[emit_index[LABEL_W-1:0]];
              result_sum_x<=sx_acc[emit_index[LABEL_W-1:0]]; result_sum_y<=sy_acc[emit_index[LABEL_W-1:0]];
              result_x0<=bbox_x0[emit_index[LABEL_W-1:0]]; result_y0<=bbox_y0[emit_index[LABEL_W-1:0]];
              result_x1<=bbox_x1[emit_index[LABEL_W-1:0]]; result_y1<=bbox_y1[emit_index[LABEL_W-1:0]];
              result_frame_id<=frame_id_reg; component_count<=component_count+1'b1;
            end else emit_index<=emit_index+1'b1;
          end
          S_DONE: begin
            if (!result_valid || result_ready) begin
              result_valid<=0; record_count<=component_count;
              done<=1; busy<=0; state<=S_IDLE;
            end
          end
          default: begin state<=S_IDLE; busy<=0; overflow<=1; result_valid<=0; end
        endcase
        if (finish && busy && state!=S_ACCEPT) finish_pending<=1;
      end
    end

    // Feature arrays are written by this single state-machine process only.
    // Each newly allocated label is initialized directly with its first run;
    // no frame-wide array clear is required or inferred.
endmodule
