`timescale 1ns/1ps

module tb_axis_rgb2gray;
    localparam integer DIRECTED_VECTORS = 512;
    localparam integer FRAME_WIDTH = 320;
    localparam integer FRAME_HEIGHT = 240;
    localparam integer FRAME_VECTORS = FRAME_WIDTH * FRAME_HEIGHT;
    localparam integer TOTAL_VECTORS = DIRECTED_VECTORS + 3 * FRAME_VECTORS;

    localparam [1:0] STATE_RESET = 2'd0;
    localparam [1:0] STATE_FLUSH = 2'd1;
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
    integer reset_flush = 0;
    integer pause_run = 0;
    integer longest_pause = 0;
    integer throughput_pairs = 0;
    integer quiet_cycles = 0;
    integer output_fd = 0;
    integer i;
    integer parsed;
    reg source_hold = 1'b0;
    reg was_input_stalled = 1'b0;
    reg was_output_stalled = 1'b0;
    reg prev_sustained = 1'b0;
    reg drain_wait = 1'b0;
    reg test_done = 1'b0;
    reg [23:0] held_input_data;
    reg [0:0] held_input_user;
    reg held_input_last;
    reg [7:0] held_output_data;
    reg [0:0] held_output_user;
    reg held_output_last;
    string rgb_path;
    string expected_path;
    string output_path;

    reg [23:0] input_vectors [0:TOTAL_VECTORS-1];
    reg [9:0] expected_vectors [0:TOTAL_VECTORS-1];

    wire [23:0] s_tdata;
    wire [0:0] s_tuser;
    wire s_tlast;
    wire s_tvalid;
    wire s_tready;
    wire [7:0] m_tdata;
    wire [0:0] m_tuser;
    wire m_tlast;
    wire m_tvalid;
    wire m_tready;

    assign s_tdata = (state == STATE_FLUSH) ? 24'hA5_3C_E1 :
                     ((state == STATE_RUN && input_count < N) ? input_vectors[input_count] : 24'd0);

    assign s_tuser = (state == STATE_RUN && input_count >= DIRECTED_VECTORS) ?
                     (((input_count - DIRECTED_VECTORS) % FRAME_VECTORS) == 0) :
                     (state == STATE_RUN && input_count == 0);

    assign s_tlast = (state == STATE_RUN && input_count >= DIRECTED_VECTORS) ?
                     (((input_count - DIRECTED_VECTORS) % FRAME_WIDTH) == FRAME_WIDTH - 1) :
                     (state == STATE_RUN && input_count < DIRECTED_VECTORS &&
                      (input_count % 32) == 31);

    assign s_tvalid = (state == STATE_FLUSH) ? 1'b1 :
                      (state == STATE_RUN && input_count < N &&
                       (source_hold || (cycle % 11 != 10)));

    assign m_tready = (state == STATE_RUN) ?
                      ((ready_mode == 0) || ((cycle % 19) > 4)) : 1'b0;

    axis_rgb2gray dut (
        .aclk(aclk), .aresetn(aresetn),
        .s_tdata(s_tdata), .s_tuser(s_tuser), .s_tlast(s_tlast),
        .s_tvalid(s_tvalid), .s_tready(s_tready),
        .m_tdata(m_tdata), .m_tuser(m_tuser), .m_tlast(m_tlast),
        .m_tvalid(m_tvalid), .m_tready(m_tready)
    );

    always @(posedge aclk) begin
        if (state == STATE_RUN && aresetn) begin
            cycle <= cycle + 1;
            source_hold <= s_tvalid && !s_tready;

            if (was_input_stalled &&
                (!s_tvalid || s_tdata !== held_input_data ||
                 s_tuser !== held_input_user || s_tlast !== held_input_last))
                $fatal(1, "input beat changed while waiting for s_tready");

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
            prev_sustained = m_tvalid && m_tready && s_tvalid && s_tready;

            if (drain_wait) begin
                if (m_tvalid !== 1'b0)
                    $fatal(1, "unexpected repeated output after all expected beats");
                if (s_tvalid !== 1'b0)
                    $fatal(1, "input remained active after all expected beats");
                quiet_cycles = quiet_cycles + 1;
                if (quiet_cycles == 5) begin
                    if (input_count != N || output_count != N)
                        $fatal(1, "count mismatch: in=%0d out=%0d N=%0d",
                               input_count, output_count, N);
                    if (bubble_cycles == 0 || throughput_pairs == 0)
                        $fatal(1, "missing bubble/continuous-throughput coverage: bubbles=%0d throughput_pairs=%0d",
                               bubble_cycles, throughput_pairs);
                    if (reset_flush != 1)
                        $fatal(1, "reset flush coverage mismatch: reset_flush=%0d", reset_flush);
                    if (ready_mode == 1 &&
                        (stall_cycles == 0 || blocked_cycles == 0 || longest_pause < 2))
                        $fatal(1, "backpressure coverage missing: stalls=%0d blocked=%0d longest_pause=%0d",
                               stall_cycles, blocked_cycles, longest_pause);
                    if (ready_mode == 0 &&
                        (stall_cycles != 0 || blocked_cycles != 0))
                        $fatal(1, "all-ready mode unexpectedly stalled: stalls=%0d blocked=%0d",
                               stall_cycles, blocked_cycles);
                    if (output_fd != 0) begin
                        $fclose(output_fd);
                        output_fd = 0;
                    end
                    test_done = 1'b1;
                    $display("TEST_PASS in=%0d out=%0d stalls=%0d blocked=%0d bubbles=%0d reset_flush=%0d",
                             input_count, output_count, stall_cycles, blocked_cycles,
                             bubble_cycles, reset_flush);
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
                if (m_tdata !== expected_vectors[output_count][7:0] ||
                    m_tuser[0] !== expected_vectors[output_count][9] ||
                    m_tlast !== expected_vectors[output_count][8])
                    $fatal(1, "output beat %0d mismatch: got gray=%02x sof=%0b eol=%0b expected=%03x",
                           output_count, m_tdata, m_tuser[0], m_tlast,
                           expected_vectors[output_count]);
                if (output_fd != 0)
                    $fdisplay(output_fd, "%02x", m_tdata);
                output_count = output_count + 1;
                if (output_count == N)
                    drain_wait = 1'b1;
            end
        end else begin
            was_input_stalled = 1'b0;
            was_output_stalled = 1'b0;
            prev_sustained = 1'b0;
        end
    end

    initial begin
        rgb_path = "vectors_rgb.hex";
        expected_path = "expected_gray.hex";
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

        parsed = $value$plusargs("RGB=%s", rgb_path);
        parsed = $value$plusargs("EXPECTED=%s", expected_path);
        $readmemh(rgb_path, input_vectors);
        $readmemh(expected_path, expected_vectors);
        for (i = 0; i < N; i = i + 1) begin
            if ((^input_vectors[i]) === 1'bx)
                $fatal(1, "%s has no known 24-bit value at index %0d", rgb_path, i);
            if ((^expected_vectors[i]) === 1'bx)
                $fatal(1, "%s has no known 10-bit value at index %0d", expected_path, i);
        end

        if ($value$plusargs("OUTPUT=%s", output_path)) begin
            output_fd = $fopen(output_path, "w");
            if (output_fd == 0)
                $fatal(1, "cannot open OUTPUT path: %s", output_path);
        end

        repeat (2) @(negedge aclk);
        state = STATE_FLUSH;
        aresetn = 1'b1;
        @(posedge aclk);
        #1;
        if (m_tvalid !== 1'b1)
            $fatal(1, "flush setup failed: color beat was not pending");

        state = STATE_RESET;
        aresetn = 1'b0;
        @(posedge aclk);
        #1;
        if (m_tvalid !== 1'b0)
            $fatal(1, "aresetn did not clear pending output valid");
        @(posedge aclk);
        #1;
        if (m_tvalid !== 1'b0)
            $fatal(1, "output valid returned while reset remained asserted");
        reset_flush = 1;

        @(negedge aclk);
        cycle = 0;
        input_count = 0;
        output_count = 0;
        source_hold = 1'b0;
        state = STATE_RUN;
        aresetn = 1'b1;
    end

    initial begin
        repeat (1000000) @(posedge aclk);
        #1;
        if (!test_done)
            $fatal(1, "watchdog timeout: in=%0d out=%0d cycles=%0d",
                   input_count, output_count, cycle);
    end
endmodule
