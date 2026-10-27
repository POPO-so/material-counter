`timescale 1ns/1ps

module tb_axis_gray2binary #(
    parameter integer THRESHOLD = 128,
    parameter integer CHAIN = 0
);
    localparam integer DIRECT_GRAY_VECTORS = 256;
    localparam integer DIRECTED_RGB_VECTORS = 512;
    localparam integer FRAME_WIDTH = 320;
    localparam integer FRAME_HEIGHT = 240;
    localparam integer FRAME_VECTORS = FRAME_WIDTH * FRAME_HEIGHT;
    localparam integer RGB_VECTORS = DIRECTED_RGB_VECTORS + 3 * FRAME_VECTORS;
    localparam integer TOTAL_VECTORS = DIRECT_GRAY_VECTORS + RGB_VECTORS;

    localparam [1:0] STATE_RESET = 2'd0;
    localparam [1:0] STATE_FILL = 2'd1;
    localparam [1:0] STATE_RUN = 2'd2;

    reg aclk = 1'b0;
    always #5 aclk = ~aclk;

    reg aresetn = 1'b0;
    reg [1:0] state = STATE_RESET;
    integer ready_mode = 1;
    integer N = TOTAL_VECTORS;
    integer cycle = 0;
    integer input_count = 0;
    integer output_count = 0;
    integer stall_cycles = 0;
    integer blocked_cycles = 0;
    integer bubble_cycles = 0;
    integer gray_stall_cycles = 0;
    integer reset_flush = 0;
    integer pause_run = 0;
    integer longest_pause = 0;
    integer throughput_pairs = 0;
    integer quiet_cycles = 0;
    integer output_fd = 0;
    integer parsed;
    integer i;

    reg source_hold = 1'b0;
    reg was_input_stalled = 1'b0;
    reg was_gray_stalled = 1'b0;
    reg was_output_stalled = 1'b0;
    reg prev_sustained = 1'b0;
    reg drain_wait = 1'b0;
    reg test_done = 1'b0;
    reg [15:0] rng_lfsr = 16'h1ace;
    reg [23:0] held_input_data;
    reg [0:0] held_input_user;
    reg held_input_last;
    reg [7:0] held_gray_data;
    reg [0:0] held_gray_user;
    reg held_gray_last;
    reg [7:0] held_output_data;
    reg [0:0] held_output_user;
    reg held_output_last;
    reg [23:0] input_rgb_vectors [0:TOTAL_VECTORS-1];
    reg [7:0] input_gray_vectors [0:TOTAL_VECTORS-1];
    reg [9:0] expected_vectors [0:TOTAL_VECTORS-1];

    string rgb_path;
    string gray_path;
    string expected_path;
    string output_path;

    wire [23:0] s_tdata;
    wire [0:0] s_tuser;
    wire s_tlast;
    wire s_tvalid;
    wire s_tready;
    wire [7:0] gray_tdata;
    wire [0:0] gray_tuser;
    wire gray_tlast;
    wire gray_tvalid;
    wire gray_tready;
    wire gray_stage_valid;
    wire [7:0] m_tdata;
    wire [0:0] m_tuser;
    wire m_tlast;
    wire m_tvalid;
    wire m_tready;

    assign s_tdata = (state == STATE_FILL) ?
                     ((CHAIN != 0) ? 24'hff_00_00 : 24'h00_00_00) :
                     ((state == STATE_RUN && input_count < N) ?
                      ((CHAIN != 0) ? input_rgb_vectors[input_count] :
                                      {16'd0, input_gray_vectors[input_count]}) : 24'd0);
    assign s_tuser = (state == STATE_FILL) ? 1'b1 :
                     ((state == STATE_RUN && input_count < N) ?
                      expected_vectors[input_count][9] : 1'b0);
    assign s_tlast = (state == STATE_FILL) ? 1'b0 :
                     ((state == STATE_RUN && input_count < N) ?
                      expected_vectors[input_count][8] : 1'b0);
    assign s_tvalid = (state == STATE_FILL) ? 1'b1 :
                      (state == STATE_RUN && input_count < N &&
                       (source_hold || ready_mode == 0 ||
                        ((cycle % 17) != 3 && (cycle % 17) != 4 && (cycle % 17) != 5)));
    assign m_tready = (state == STATE_RUN) ?
                      ((ready_mode == 0) ||
                       (!((cycle % 211) >= 80 && (cycle % 211) < 128) &&
                        (rng_lfsr[0] || rng_lfsr[3]))) : 1'b0;

    generate
        if (CHAIN != 0) begin : g_retired_source_mode
            // RGB input mode was retired; direct Gray8/Binary8 tests remain available.
            initial $fatal(1, "CHAIN=1 input mode retired; use direct Gray8/Binary8 CHAIN=0");
            assign s_tready = 1'b0;
            assign gray_tdata = 8'd0;
            assign gray_tuser = 1'b0;
            assign gray_tlast = 1'b0;
            assign gray_tvalid = 1'b0;

            axis_gray2binary #(.THRESHOLD(THRESHOLD)) gray2binary (
                .aclk(aclk), .aresetn(aresetn),
                .s_tdata(gray_tdata), .s_tuser(gray_tuser), .s_tlast(gray_tlast),
                .s_tvalid(gray_tvalid), .s_tready(gray_tready),
                .m_tdata(m_tdata), .m_tuser(m_tuser), .m_tlast(m_tlast),
                .m_tvalid(m_tvalid), .m_tready(m_tready)
            );
            assign gray_stage_valid = gray_tvalid;
        end else begin : g_gray_binary_only
            assign gray_tdata = s_tdata[7:0];
            assign gray_tuser = s_tuser;
            assign gray_tlast = s_tlast;
            assign gray_tvalid = s_tvalid;
            assign gray_stage_valid = 1'b0;

            axis_gray2binary #(.THRESHOLD(THRESHOLD)) gray2binary (
                .aclk(aclk), .aresetn(aresetn),
                .s_tdata(gray_tdata), .s_tuser(gray_tuser), .s_tlast(gray_tlast),
                .s_tvalid(gray_tvalid), .s_tready(s_tready),
                .m_tdata(m_tdata), .m_tuser(m_tuser), .m_tlast(m_tlast),
                .m_tvalid(m_tvalid), .m_tready(m_tready)
            );
        end
    endgenerate

    function automatic [7:0] reference_pixel(input [7:0] gray_value);
        begin
            reference_pixel = (gray_value >= THRESHOLD) ? 8'hff : 8'h00;
        end
    endfunction

    task automatic assert_reference(input [7:0] gray_value,
                                    input [7:0] expected_value);
        begin
            if (reference_pixel(gray_value) !== expected_value)
                $fatal(1, "TB_REFERENCE_THRESHOLD_ERROR threshold=%0d gray=%0d got=%02x expected=%02x",
                       THRESHOLD, gray_value, reference_pixel(gray_value), expected_value);
        end
    endtask

    always @(posedge aclk) begin
        rng_lfsr <= {rng_lfsr[14:0], rng_lfsr[15] ^ rng_lfsr[13] ^
                     rng_lfsr[12] ^ rng_lfsr[10]};

        if (state == STATE_RUN && aresetn) begin
            cycle <= cycle + 1;
            source_hold <= s_tvalid && !s_tready;

            if (was_input_stalled &&
                (!s_tvalid || s_tdata !== held_input_data ||
                 s_tuser !== held_input_user || s_tlast !== held_input_last))
                $fatal(1, "input beat changed while waiting for s_tready");

            if (was_gray_stalled &&
                (!gray_tvalid || gray_tdata !== held_gray_data ||
                 gray_tuser !== held_gray_user || gray_tlast !== held_gray_last))
                $fatal(1, "Gray beat changed while waiting for binary-stage ready");

            if (was_output_stalled &&
                (!m_tvalid || m_tdata !== held_output_data ||
                 m_tuser !== held_output_user || m_tlast !== held_output_last))
                $fatal(1, "output beat changed while waiting for m_tready");

            was_input_stalled = s_tvalid && !s_tready;
            if (was_input_stalled) begin
                held_input_data = s_tdata;
                held_input_user = s_tuser;
                held_input_last = s_tlast;
            end

            was_gray_stalled = (CHAIN != 0) && gray_tvalid && !gray_tready;
            if (was_gray_stalled) begin
                held_gray_data = gray_tdata;
                held_gray_user = gray_tuser;
                held_gray_last = gray_tlast;
                gray_stall_cycles = gray_stall_cycles + 1;
            end

            was_output_stalled = m_tvalid && !m_tready;
            if (was_output_stalled) begin
                held_output_data = m_tdata;
                held_output_user = m_tuser;
                held_output_last = m_tlast;
            end

            if (s_tvalid && !s_tready)
                blocked_cycles = blocked_cycles + 1;
            if (input_count < N && !s_tvalid)
                bubble_cycles = bubble_cycles + 1;

            if (m_tvalid && !m_tready) begin
                stall_cycles = stall_cycles + 1;
                pause_run = pause_run + 1;
                if (pause_run > longest_pause)
                    longest_pause = pause_run;
            end else begin
                pause_run = 0;
            end

            if (prev_sustained && m_tready && s_tvalid) begin
                if (!m_tvalid || !s_tready)
                    $fatal(1, "throughput bubble during consecutive ready/valid beats");
                throughput_pairs = throughput_pairs + 1;
            end
            prev_sustained = m_tvalid && m_tready && s_tvalid && s_tready &&
                             ((CHAIN == 0) || (gray_tvalid && gray_tready));

            if (drain_wait) begin
                if (m_tvalid !== 1'b0 || s_tvalid !== 1'b0)
                    $fatal(1, "unexpected beat after all expected outputs");
                quiet_cycles = quiet_cycles + 1;
                if (quiet_cycles == 5) begin
                    if (input_count != N || output_count != N)
                        $fatal(1, "count mismatch: in=%0d out=%0d N=%0d",
                               input_count, output_count, N);
                    if (throughput_pairs == 0)
                        $fatal(1, "continuous-throughput coverage missing");
                    if (reset_flush != 1)
                        $fatal(1, "reset flush coverage mismatch: %0d", reset_flush);
                    if (ready_mode == 0 &&
                        (stall_cycles != 0 || blocked_cycles != 0 || bubble_cycles != 0))
                        $fatal(1, "continuous mode unexpectedly stalled or bubbled: stalls=%0d blocked=%0d bubbles=%0d",
                               stall_cycles, blocked_cycles, bubble_cycles);
                    if (ready_mode == 1 &&
                        (stall_cycles == 0 || blocked_cycles == 0 || bubble_cycles == 0 ||
                         longest_pause < 32))
                        $fatal(1, "random backpressure coverage missing: stalls=%0d blocked=%0d bubbles=%0d longest=%0d",
                               stall_cycles, blocked_cycles, bubble_cycles, longest_pause);
                    if (CHAIN != 0 && ready_mode == 1 && gray_stall_cycles == 0)
                        $fatal(1, "chain did not backpressure the RGB-to-Gray stage");
                    if (output_fd != 0) begin
                        $fclose(output_fd);
                        output_fd = 0;
                    end
                    test_done = 1'b1;
                    $display("TEST_PASS threshold=%0d chain=%0d mode=%0d in=%0d out=%0d stalls=%0d blocked=%0d bubbles=%0d longest_pause=%0d throughput_pairs=%0d gray_stalls=%0d reset_flush=%0d",
                             THRESHOLD, CHAIN, ready_mode, input_count, output_count,
                             stall_cycles, blocked_cycles, bubble_cycles, longest_pause,
                             throughput_pairs, gray_stall_cycles, reset_flush);
                    $finish;
                end
            end

            if (s_tvalid && s_tready) begin
                if (input_count >= N)
                    $fatal(1, "accepted more than N input beats");
                input_count <= input_count + 1;
            end

            if (m_tvalid && m_tready) begin
                if (output_count >= N)
                    $fatal(1, "received more than N output beats");
                if ({m_tuser[0], m_tlast, m_tdata} !== expected_vectors[output_count])
                    $fatal(1, "output beat %0d mismatch: got=%03x expected=%03x threshold=%0d chain=%0d",
                           output_count, {m_tuser[0], m_tlast, m_tdata},
                           expected_vectors[output_count], THRESHOLD, CHAIN);
                if (output_fd != 0)
                    $fdisplay(output_fd, "%03x", {m_tuser[0], m_tlast, m_tdata});
                output_count = output_count + 1;
                if (output_count == N)
                    drain_wait = 1'b1;
            end
        end else begin
            was_input_stalled = 1'b0;
            was_gray_stalled = 1'b0;
            was_output_stalled = 1'b0;
            prev_sustained = 1'b0;
        end
    end

    initial begin
        rgb_path = "vectors_rgb2binary.hex";
        gray_path = "vectors_gray.hex";
        expected_path = "expected_binary.hex";
        output_path = "";

        parsed = $value$plusargs("N=%d", N);
        if (!parsed)
            N = TOTAL_VECTORS;
        parsed = $value$plusargs("READY_MODE=%d", ready_mode);
        if (!parsed)
            ready_mode = 1;
        if (N < 1 || N > TOTAL_VECTORS)
            $fatal(1, "N must be in [1,%0d], got %0d", TOTAL_VECTORS, N);
        if (ready_mode != 0 && ready_mode != 1)
            $fatal(1, "READY_MODE must be 0 or 1, got %0d", ready_mode);
        if (CHAIN != 0 && CHAIN != 1)
            $fatal(1, "CHAIN must be 0 or 1, got %0d", CHAIN);

        if (THRESHOLD >= 0 && THRESHOLD <= 255) begin
            assert_reference(8'd0, (THRESHOLD == 0) ? 8'hff : 8'h00);
            assert_reference(8'd127, (THRESHOLD <= 127) ? 8'hff : 8'h00);
            assert_reference(8'd128, (THRESHOLD <= 128) ? 8'hff : 8'h00);
            assert_reference(8'd129, (THRESHOLD <= 129) ? 8'hff : 8'h00);
            assert_reference(8'd255, 8'hff);
            if (THRESHOLD > 0)
                assert_reference(THRESHOLD - 1, 8'h00);
            assert_reference(THRESHOLD, 8'hff);
        end

        parsed = $value$plusargs("RGB=%s", rgb_path);
        parsed = $value$plusargs("GRAY=%s", gray_path);
        parsed = $value$plusargs("EXPECTED=%s", expected_path);
        $readmemh(rgb_path, input_rgb_vectors);
        $readmemh(gray_path, input_gray_vectors);
        $readmemh(expected_path, expected_vectors);
        for (i = 0; i < N; i = i + 1) begin
            if ((^input_rgb_vectors[i]) === 1'bx)
                $fatal(1, "%s has no known 24-bit value at index %0d", rgb_path, i);
            if ((^input_gray_vectors[i]) === 1'bx)
                $fatal(1, "%s has no known 8-bit value at index %0d", gray_path, i);
            if ((^expected_vectors[i]) === 1'bx)
                $fatal(1, "%s has no known 10-bit value at index %0d", expected_path, i);
        end

        if ($value$plusargs("OUTPUT=%s", output_path)) begin
            output_fd = $fopen(output_path, "w");
            if (output_fd == 0)
                $fatal(1, "cannot open OUTPUT path: %s", output_path);
        end

        repeat (2) @(negedge aclk);
        state = STATE_FILL;
        aresetn = 1'b1;
        repeat (3) @(posedge aclk);
        #1;
        if (m_tvalid !== 1'b1)
            $fatal(1, "reset-flush setup failed: binary output was not pending");
        if (CHAIN != 0 && gray_stage_valid !== 1'b1)
            $fatal(1, "reset-flush setup failed: Gray stage was not pending");

        @(negedge aclk);
        state = STATE_RESET;
        aresetn = 1'b0;
        @(posedge aclk);
        #1;
        if (m_tvalid !== 1'b0)
            $fatal(1, "aresetn did not clear pending binary output valid");
        if (CHAIN != 0 && gray_stage_valid !== 1'b0)
            $fatal(1, "aresetn did not clear pending Gray-stage valid");
        @(posedge aclk);
        #1;
        if (m_tvalid !== 1'b0)
            $fatal(1, "binary output valid returned while reset remained asserted");
        if (CHAIN != 0 && gray_stage_valid !== 1'b0)
            $fatal(1, "Gray-stage valid returned while reset remained asserted");
        reset_flush = 1;

        @(negedge aclk);
        cycle = 0;
        input_count = 0;
        output_count = 0;
        source_hold = 1'b0;
        drain_wait = 1'b0;
        quiet_cycles = 0;
        state = STATE_RUN;
        aresetn = 1'b1;
    end

    initial begin
        repeat (3000000) @(posedge aclk);
        #1;
        if (!test_done)
            $fatal(1, "watchdog timeout threshold=%0d chain=%0d mode=%0d in=%0d out=%0d cycles=%0d",
                   THRESHOLD, CHAIN, ready_mode, input_count, output_count, cycle);
    end
endmodule
