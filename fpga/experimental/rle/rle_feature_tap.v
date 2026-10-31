`timescale 1ns/1ps
// Nonblocking accepted-pixel tap, maximal-run FIFO and standalone CCL features.
// There is intentionally no ready/backpressure path to the pixel producer.
module rle_feature_tap #(
    parameter integer WIDTH=640,
    parameter integer HEIGHT=400,
    parameter integer FIFO_DEPTH=2048,
    parameter integer FIFO_AW=(FIFO_DEPTH<=2)?1:$clog2(FIFO_DEPTH)
)(
    input wire tap_clk, input wire resetn,
    input wire tap_valid, input wire tap_sof, input wire tap_eol,
    input wire tap_bit, input wire [31:0] tap_frame_id,
    input wire [11:0] record_word_address, input wire read_enable,
    output reg [31:0] record_word_data,
    output reg [31:0] frame_id, output reg done, output reg overflow,
    output reg busy, output reg [9:0] record_count,
    output reg [31:0] total_fg, output reg [31:0] dropped_frames
);
    reg [29:0] run_fifo [0:FIFO_DEPTH-1];
    reg [FIFO_AW-1:0] fifo_wr_ptr, fifo_rd_ptr;
    reg [FIFO_AW:0] fifo_count;
    reg [9:0] px_x, px_y, run_x0;
    reg run_active, frame_active, input_complete;
    reg enqueue_valid;
    reg [29:0] enqueue_data;
    reg fifo_flush, core_reset_pulse, core_start, core_start_pending;
    reg core_finish, finish_sent, core_done_seen, core_aborted;
    reg drop_counted;

    wire new_sof = tap_valid && tap_sof;
    wire core_resetn = resetn && !core_reset_pulse;
    wire [29:0] fifo_head = run_fifo[fifo_rd_ptr];
    wire core_run_valid = (fifo_count != 0) && !overflow && !fifo_flush && !new_sof;
    wire core_run_ready;
    wire core_run_fire = core_run_valid && core_run_ready;
    wire fifo_full = (fifo_count == FIFO_DEPTH);
    wire fifo_push_req = enqueue_valid && !overflow && !fifo_flush && !new_sof;
    wire fifo_push = fifo_push_req && (!fifo_full || core_run_fire);
    wire fifo_overrun_now = fifo_push_req && fifo_full && !core_run_fire;
    wire [9:0] core_run_y = fifo_head[29:20];
    wire [9:0] core_run_x0 = fifo_head[19:10];
    wire [9:0] core_run_x1 = fifo_head[9:0];

    wire core_result_valid, core_result_ready;
    wire [31:0] core_area, core_sum_x, core_sum_y, core_result_frame_id;
    wire [9:0] core_x0, core_y0, core_x1, core_y1;
    wire core_done, core_overflow, core_busy;
    wire [9:0] core_record_count;
    rle_region_features feature_core (
        .clk(tap_clk), .resetn(core_resetn), .start(core_start), .frame_id(frame_id),
        .run_valid(core_run_valid), .run_ready(core_run_ready),
        .run_y(core_run_y), .run_x0(core_run_x0), .run_x1(core_run_x1),
        .finish(core_finish), .result_valid(core_result_valid), .result_ready(core_result_ready),
        .result_area(core_area), .result_sum_x(core_sum_x), .result_sum_y(core_sum_y),
        .result_x0(core_x0), .result_y0(core_y0), .result_x1(core_x1), .result_y1(core_y1),
        .result_frame_id(core_result_frame_id), .done(core_done), .overflow(core_overflow),
        .record_count(core_record_count), .busy(core_busy)
    );

    // Single-port 4096x32 result RAM. CPU synchronous reads pause the writer.
    reg [31:0] record_ram [0:4095];
    reg record_active;
    reg [2:0] record_word_index;
    reg [31:0] rec_area, rec_sx, rec_sy, rec_frame_id;
    reg [9:0] rec_x0, rec_y0, rec_x1, rec_y1;
    wire [11:0] record_write_address = {record_count[8:0],3'b000} + record_word_index;
    wire record_write_enable = resetn && !read_enable && !new_sof && record_active;
    assign core_result_ready = record_write_enable && (record_word_index==3'd7) && !overflow;
    wire [31:0] record_write_data =
        (record_word_index==3'd0) ? rec_area :
        (record_word_index==3'd1) ? rec_sx :
        (record_word_index==3'd2) ? rec_sy :
        (record_word_index==3'd3) ? {6'd0,rec_x1,6'd0,rec_x0} :
        (record_word_index==3'd4) ? {6'd0,rec_y1,6'd0,rec_y0} :
        (record_word_index==3'd5) ? rec_frame_id : 32'd0;
    always @(posedge tap_clk) begin
        if (!resetn) record_word_data <= 0;
        else if (read_enable) record_word_data <= record_ram[record_word_address];
        else if (record_write_enable) record_ram[record_write_address] <= record_write_data;
    end

    // Bounded single-clock FIFO. The enqueue register absorbs run-close timing.
    always @(posedge tap_clk) begin
        if (!resetn || new_sof || fifo_flush) begin
            fifo_wr_ptr<=0; fifo_rd_ptr<=0; fifo_count<=0;
        end else begin
            if (fifo_push) begin
                run_fifo[fifo_wr_ptr] <= enqueue_data;
                if (fifo_wr_ptr==FIFO_DEPTH-1) fifo_wr_ptr<=0;
                else fifo_wr_ptr<=fifo_wr_ptr+1'b1;
            end
            if (core_run_fire) begin
                if (fifo_rd_ptr==FIFO_DEPTH-1) fifo_rd_ptr<=0;
                else fifo_rd_ptr<=fifo_rd_ptr+1'b1;
            end
            case ({fifo_push,core_run_fire})
                2'b10: fifo_count<=fifo_count+1'b1;
                2'b01: fifo_count<=fifo_count-1'b1;
                default: fifo_count<=fifo_count;
            endcase
        end
    end

    always @(posedge tap_clk) begin
        if (!resetn) begin
            frame_id<=0; done<=0; overflow<=0; busy<=0; record_count<=0;
            total_fg<=0; dropped_frames<=0;
            px_x<=0; px_y<=0; run_x0<=0; run_active<=0;
            frame_active<=0; input_complete<=0; enqueue_valid<=0; enqueue_data<=0;
            fifo_flush<=0; core_reset_pulse<=0; core_start<=0; core_start_pending<=0;
            core_finish<=0; finish_sent<=0; core_done_seen<=0; core_aborted<=0;
            drop_counted<=0; record_active<=0; record_word_index<=0;
            rec_area<=0; rec_sx<=0; rec_sy<=0; rec_frame_id<=0;
            rec_x0<=0; rec_y0<=0; rec_x1<=0; rec_y1<=0;
        end else begin
            enqueue_valid<=0; fifo_flush<=0; core_reset_pulse<=0;
            core_start<=0; core_finish<=0;

            if (new_sof) begin
                // Count an unfinished previous frame and a malformed new frame independently.
                if (busy && !done && !drop_counted) dropped_frames<=dropped_frames+1'b1;
                frame_id<=tap_frame_id; done<=0; overflow<=0; busy<=1;
                record_count<=0; total_fg<=tap_bit ? 32'd1 : 32'd0;
                frame_active<=0; input_complete<=0; run_active<=0;
                px_x<=0; px_y<=0; finish_sent<=0; core_done_seen<=0;
                core_aborted<=0; drop_counted<=0;
                fifo_flush<=1; core_reset_pulse<=1; core_start_pending<=1;
                record_active<=0; record_word_index<=0;
                if (WIDTH<1 || HEIGHT<1 || WIDTH>640 || HEIGHT>400 ||
                    tap_eol!=(WIDTH==1)) begin
                    overflow<=1; done<=1; busy<=0; core_start_pending<=0;
                    core_aborted<=1; drop_counted<=1;
                    if (busy && !done && !drop_counted) dropped_frames<=dropped_frames+2;
                    else dropped_frames<=dropped_frames+1'b1;
                end else begin
                    frame_active<=((WIDTH*HEIGHT)>1);
                    input_complete<=((WIDTH*HEIGHT)==1);
                    if (tap_bit) begin
                        run_active<=1; run_x0<=0;
                        if (tap_eol) begin
                            enqueue_valid<=1; enqueue_data<={10'd0,10'd0,10'd0};
                            run_active<=0;
                        end
                    end
                    if (WIDTH>1) px_x<=1;
                    else if (HEIGHT>1) px_y<=1;
                end
            end else begin
                if (core_start_pending && !core_reset_pulse) begin
                    core_start<=1; core_start_pending<=0;
                end

                if (tap_valid && busy && frame_active) begin
                    if (tap_bit) total_fg<=total_fg+1'b1;
                    if (tap_frame_id!=frame_id || tap_eol!=(px_x==WIDTH-1)) begin
                        overflow<=1; done<=1; busy<=0; frame_active<=0; input_complete<=1;
                        run_active<=0; core_reset_pulse<=1; core_start_pending<=0;
                        core_aborted<=1; fifo_flush<=1;
                        if (!drop_counted) dropped_frames<=dropped_frames+1'b1;
                        drop_counted<=1;
                    end else begin
                        if (tap_bit) begin
                            if (!run_active) begin run_active<=1; run_x0<=px_x; end
                            if (tap_eol) begin
                                enqueue_valid<=1; enqueue_data<={px_y,run_active?run_x0:px_x,px_x};
                                run_active<=0;
                            end
                        end else if (run_active) begin
                            enqueue_valid<=1; enqueue_data<={px_y,run_x0,px_x-10'd1};
                            run_active<=0;
                        end
                        if (tap_eol) begin
                            px_x<=0;
                            if (px_y==HEIGHT-1) begin frame_active<=0; input_complete<=1; end
                            else px_y<=px_y+1'b1;
                        end else px_x<=px_x+1'b1;
                    end
                end else if (tap_valid && busy && !frame_active && input_complete==0) begin
                    overflow<=1; done<=1; busy<=0; input_complete<=1;
                    core_aborted<=1; core_reset_pulse<=1; core_start_pending<=0; fifo_flush<=1;
                    if (!drop_counted) dropped_frames<=dropped_frames+1'b1;
                    drop_counted<=1;
                end

                if (fifo_overrun_now && !overflow) begin
                    overflow<=1; done<=1; busy<=0; frame_active<=0; input_complete<=1;
                    core_aborted<=1; fifo_flush<=1; core_reset_pulse<=1; core_start_pending<=0;
                    if (!drop_counted) dropped_frames<=dropped_frames+1'b1;
                    drop_counted<=1;
                end
                if (core_overflow && !overflow) begin
                    overflow<=1; done<=1; busy<=0; frame_active<=0; input_complete<=1;
                    core_aborted<=1; fifo_flush<=1; core_reset_pulse<=1; core_start_pending<=0;
                    if (!drop_counted) dropped_frames<=dropped_frames+1'b1;
                    drop_counted<=1;
                end

                if (input_complete && !overflow && !finish_sent && !enqueue_valid &&
                    fifo_count==0 && core_busy && core_run_ready) begin
                    core_finish<=1; finish_sent<=1;
                end
                if (core_done) core_done_seen<=1;

                if (!record_active && core_result_valid && !overflow) begin
                    if (core_result_frame_id!=frame_id || record_count>=10'd512) begin
                        overflow<=1; done<=1; busy<=0; core_aborted<=1;
                        fifo_flush<=1; core_reset_pulse<=1;
                        if (!drop_counted) dropped_frames<=dropped_frames+1'b1;
                        drop_counted<=1;
                    end else begin
                        record_active<=1; record_word_index<=0;
                        rec_area<=core_area; rec_sx<=core_sum_x; rec_sy<=core_sum_y;
                        rec_x0<=core_x0; rec_y0<=core_y0; rec_x1<=core_x1; rec_y1<=core_y1;
                        rec_frame_id<=core_result_frame_id;
                    end
                end else if (record_active && record_write_enable) begin
                    if (record_word_index==3'd7) begin
                        record_active<=0; record_word_index<=0;
                        record_count<=record_count+1'b1;
                    end else record_word_index<=record_word_index+1'b1;
                end

                if (input_complete && !overflow &&
                    (core_aborted || core_done_seen || core_done) &&
                    !record_active && !core_result_valid) begin
                    done<=1; busy<=0;
                end
            end
        end
    end
endmodule
