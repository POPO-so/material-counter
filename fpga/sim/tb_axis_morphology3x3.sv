`timescale 1ns/1ps

module tb_axis_morphology3x3 #(
    parameter integer FRAME_WIDTH  = 9,
    parameter integer FRAME_HEIGHT = 7,
    parameter integer MODE          = 0,
    parameter integer CHAIN         = 0
);
    localparam integer MAX_BEATS = 3 * 320 * 240;
    localparam integer FRAME_BEATS = FRAME_WIDTH * FRAME_HEIGHT;

    localparam integer PH_RESET = 0;
    localparam integer PH_PRIME = 1;
    localparam integer PH_PROBE = 2;
    localparam integer PH_RUN   = 3;
    localparam integer PH_DONE  = 4;

    reg aclk = 1'b0;
    always #5 aclk = ~aclk;

    reg aresetn = 1'b0;
    integer phase = PH_RESET;
    integer ready_mode = 1;
    integer reset_case = 1;
    integer seed_value = 16'h1ace;
    integer N = FRAME_BEATS;
    integer cycle = 0;
    integer prime_count = 0;
    integer prime_limit = 0;
    integer probe_count = 0;
    integer probe_limit = 0;
    integer input_count = 0;
    integer output_count = 0;
    integer frame_count = 0;
    integer output_stalls = 0;
    integer input_blocked = 0;
    integer gray_stalls = 0;
    integer binary_stalls = 0;
    integer input_bubbles = 0;
    integer longest_output_stall = 0;
    integer output_stall_run = 0;
    integer cross_frame_stall_remaining = 0;
    integer cross_frame_stall_started = 0;
    integer observed_last_stall_cycles = 0;
    integer cross_frame_overlap = 0;
    integer reset_flush = 0;
    integer drain_wait = 0;
    integer quiet_cycles = 0;
    integer test_done = 0;
    integer output_fd = 0;
    integer parsed = 0;
    integer i = 0;
    integer wait_cycles = 0;
    integer input_fire = 0;

    reg [15:0] rng_lfsr = 16'h1ace;
    reg source_valid = 1'b0;
    reg [23:0] source_rgb_data = 24'd0;
    reg [7:0] source_binary_data = 8'd0;
    reg source_user = 1'b0;
    reg source_last = 1'b0;
    reg sink_ready = 1'b0;

    reg was_source_stalled = 1'b0;
    reg was_gray_stalled = 1'b0;
    reg was_binary_stalled = 1'b0;
    reg was_output_stalled = 1'b0;
    reg [23:0] held_rgb_data = 24'd0;
    reg [7:0] held_binary_data = 8'd0;
    reg held_source_user = 1'b0;
    reg held_source_last = 1'b0;
    reg [7:0] held_gray_data = 8'd0;
    reg held_gray_user = 1'b0;
    reg held_gray_last = 1'b0;
    reg [7:0] held_binary_stage_data = 8'd0;
    reg held_binary_stage_user = 1'b0;
    reg held_binary_stage_last = 1'b0;
    reg [7:0] held_output_data = 8'd0;
    reg held_output_user = 1'b0;
    reg held_output_last = 1'b0;
    reg source_hold = 1'b0;

    reg [23:0] input_rgb24 [0:MAX_BEATS-1];
    reg [7:0] input_binary8 [0:MAX_BEATS-1];
    reg [9:0] expected_vectors [0:MAX_BEATS-1];

    string rgb_path;
    string binary_path;
    string expected_path;
    string output_path;
    string scene_name;

    wire [0:0] source_user_bus = source_user;
    wire source_last_bus = source_last;
    wire source_valid_bus = source_valid;
    wire source_ready_bus;

    wire [7:0] gray_tdata;
    wire [0:0] gray_tuser;
    wire gray_tlast;
    wire gray_tvalid;
    wire gray_tready;

    wire [7:0] binary_tdata;
    wire [0:0] binary_tuser;
    wire binary_tlast;
    wire binary_tvalid;
    wire binary_tready;

    wire [7:0] morph_tdata;
    wire [0:0] morph_tuser;
    wire morph_tlast;
    wire morph_tvalid;
    wire morph_tready;

    generate
        if (CHAIN != 0) begin : g_rgb_gray_binary_morph
            axis_rgb2gray rgb2gray (
                .aclk(aclk), .aresetn(aresetn),
                .s_tdata(source_rgb_data),
                .s_tuser(source_user_bus), .s_tlast(source_last_bus),
                .s_tvalid(source_valid_bus), .s_tready(source_ready_bus),
                .m_tdata(gray_tdata),
                .m_tuser(gray_tuser), .m_tlast(gray_tlast),
                .m_tvalid(gray_tvalid), .m_tready(gray_tready)
            );

            axis_gray2binary #(.THRESHOLD(128)) gray2binary (
                .aclk(aclk), .aresetn(aresetn),
                .s_tdata(gray_tdata),
                .s_tuser(gray_tuser), .s_tlast(gray_tlast),
                .s_tvalid(gray_tvalid), .s_tready(gray_tready),
                .m_tdata(binary_tdata),
                .m_tuser(binary_tuser), .m_tlast(binary_tlast),
                .m_tvalid(binary_tvalid), .m_tready(binary_tready)
            );

            assign binary_tready = morph_tready;

            axis_morphology3x3 #(FRAME_WIDTH, FRAME_HEIGHT, MODE) morphology (
                .aclk(aclk), .aresetn(aresetn),
                .s_tdata(binary_tdata),
                .s_tuser(binary_tuser), .s_tlast(binary_tlast),
                .s_tvalid(binary_tvalid), .s_tready(morph_tready),
                .m_tdata(morph_tdata),
                .m_tuser(morph_tuser), .m_tlast(morph_tlast),
                .m_tvalid(morph_tvalid), .m_tready(sink_ready)
            );
        end else begin : g_binary_morph
            assign gray_tdata = 8'd0;
            assign gray_tuser = 1'b0;
            assign gray_tlast = 1'b0;
            assign gray_tvalid = 1'b0;
            assign gray_tready = 1'b0;

            assign binary_tdata = source_binary_data;
            assign binary_tuser = source_user_bus;
            assign binary_tlast = source_last_bus;
            assign binary_tvalid = source_valid_bus;
            assign binary_tready = morph_tready;
            assign source_ready_bus = morph_tready;

            axis_morphology3x3 #(FRAME_WIDTH, FRAME_HEIGHT, MODE) morphology (
                .aclk(aclk), .aresetn(aresetn),
                .s_tdata(binary_tdata),
                .s_tuser(binary_tuser), .s_tlast(binary_tlast),
                .s_tvalid(binary_tvalid), .s_tready(morph_tready),
                .m_tdata(morph_tdata),
                .m_tuser(morph_tuser), .m_tlast(morph_tlast),
                .m_tvalid(morph_tvalid), .m_tready(sink_ready)
            );
        end
    endgenerate

    function automatic [15:0] lfsr_next(input [15:0] value);
        begin
            lfsr_next = {value[14:0], value[15] ^ value[13] ^ value[12] ^ value[10]};
        end
    endfunction

    // All source and sink controls change on the falling edge. The DUT samples
    // stable AXI signals on the rising edge, including during randomized stalls.
    always @(negedge aclk) begin
        source_valid = 1'b0;
        source_rgb_data = 24'd0;
        source_binary_data = 8'd0;
        source_user = 1'b0;
        source_last = 1'b0;
        sink_ready = 1'b0;

        case (phase)
            PH_PRIME: begin
                if (prime_count < prime_limit) begin
                    source_valid = 1'b1;
                    source_rgb_data = input_rgb24[prime_count];
                    source_binary_data = input_binary8[prime_count];
                    source_user = expected_vectors[prime_count][9];
                    source_last = expected_vectors[prime_count][8];
                end
            end
            PH_PROBE: begin
                if (probe_count < probe_limit) begin
                    source_valid = 1'b1;
                    source_rgb_data = input_rgb24[probe_count];
                    source_binary_data = input_binary8[probe_count];
                    source_user = 1'b0;
                    source_last = 1'b0;
                end
            end
            PH_RUN: begin
                if (input_count < N) begin
                    if (ready_mode == 0 || source_hold ||
                        cross_frame_stall_remaining > 0 ||
                        (((cycle % 29) != 3) && ((cycle % 29) != 4) &&
                         ((cycle % 29) != 5) && (rng_lfsr[0] || rng_lfsr[3]))) begin
                        source_valid = 1'b1;
                        source_rgb_data = input_rgb24[input_count];
                        source_binary_data = input_binary8[input_count];
                        source_user = expected_vectors[input_count][9];
                        source_last = expected_vectors[input_count][8];
                    end
                end

                if (ready_mode == 0) begin
                    sink_ready = 1'b1;
                end else if (!(((cycle % 211) < 4) ||
                               ((cycle % 211) >= 80 && (cycle % 211) < 128) ||
                               ((cycle % 17) == 5) || ((cycle % 17) == 6))) begin
                    sink_ready = rng_lfsr[0] || rng_lfsr[3];
                end

                // In multi-frame random-ready runs, hold the first frame's
                // final EOL beat for exactly 12 clocks. Keep the source active
                // so frame 2's SOF can reach the morphology input while that
                // final output is still pending.
                if (ready_mode == 1 && N >= (2 * FRAME_BEATS) &&
                    !cross_frame_stall_started &&
                    output_count == (FRAME_BEATS - 1) &&
                    morph_tvalid && morph_tlast) begin
                    cross_frame_stall_started = 1;
                    cross_frame_stall_remaining = 12;
                end
                if (cross_frame_stall_remaining > 0)
                    sink_ready = 1'b0;
            end
            default: begin
                // Reset, completion, and probe phases keep the sink stalled.
            end
        endcase
    end

    always @(posedge aclk) begin
        if (!aresetn) begin
            was_source_stalled = 1'b0;
            was_gray_stalled = 1'b0;
            was_binary_stalled = 1'b0;
            was_output_stalled = 1'b0;
            source_hold = 1'b0;
        end else begin
            if (was_source_stalled &&
                (!source_valid || source_user !== held_source_user ||
                 source_last !== held_source_last ||
                 ((CHAIN != 0) && source_rgb_data !== held_rgb_data) ||
                 ((CHAIN == 0) && source_binary_data !== held_binary_data)))
                $fatal(1, "source beat changed while waiting for s_tready");

            if (was_gray_stalled &&
                (!gray_tvalid || gray_tdata !== held_gray_data ||
                 gray_tuser !== held_gray_user || gray_tlast !== held_gray_last))
                $fatal(1, "RGB-to-Gray beat changed while waiting for Gray-to-Binary ready");

            if (was_binary_stalled &&
                (!binary_tvalid || binary_tdata !== held_binary_stage_data ||
                 binary_tuser !== held_binary_stage_user ||
                 binary_tlast !== held_binary_stage_last))
                $fatal(1, "Binary beat changed while waiting for morphology ready");

            if (was_output_stalled &&
                (!morph_tvalid || morph_tdata !== held_output_data ||
                 morph_tuser !== held_output_user || morph_tlast !== held_output_last))
                $fatal(1, "morphology output changed while waiting for m_tready");

            was_source_stalled = source_valid && !source_ready_bus;
            if (was_source_stalled) begin
                held_source_user = source_user;
                held_source_last = source_last;
                held_rgb_data = source_rgb_data;
                held_binary_data = source_binary_data;
            end

            was_gray_stalled = (CHAIN != 0) && gray_tvalid && !gray_tready;
            if (was_gray_stalled) begin
                held_gray_data = gray_tdata;
                held_gray_user = gray_tuser;
                held_gray_last = gray_tlast;
            end

            was_binary_stalled = (CHAIN != 0) && binary_tvalid && !binary_tready;
            if (was_binary_stalled) begin
                held_binary_stage_data = binary_tdata;
                held_binary_stage_user = binary_tuser;
                held_binary_stage_last = binary_tlast;
            end

            was_output_stalled = morph_tvalid && !sink_ready;
            if (was_output_stalled) begin
                held_output_data = morph_tdata;
                held_output_user = morph_tuser;
                held_output_last = morph_tlast;
            end

            rng_lfsr = lfsr_next(rng_lfsr);

            if (phase == PH_PRIME) begin
                if (source_valid && source_ready_bus)
                    prime_count = prime_count + 1;
            end else if (phase == PH_PROBE) begin
                if (source_valid && source_ready_bus)
                    probe_count = probe_count + 1;
                if (morph_tvalid)
                    $fatal(1, "morphology emitted output before seeing SOF after reset");
            end else if (phase == PH_RUN) begin
                cycle = cycle + 1;
                input_fire = source_valid && source_ready_bus;
                source_hold = source_valid && !source_ready_bus;

                if (cross_frame_stall_remaining > 0) begin
                    if (morph_tvalid && morph_tlast &&
                        output_count == (FRAME_BEATS - 1) && !sink_ready)
                        observed_last_stall_cycles = observed_last_stall_cycles + 1;
                    cross_frame_stall_remaining = cross_frame_stall_remaining - 1;
                end

                if (binary_tvalid && binary_tready && binary_tuser[0] &&
                    morph_tvalid && morph_tlast && !sink_ready &&
                    output_count == (FRAME_BEATS - 1))
                    cross_frame_overlap = cross_frame_overlap + 1;

                if (source_valid && !source_ready_bus)
                    input_blocked = input_blocked + 1;
                if (input_count < N && !source_valid)
                    input_bubbles = input_bubbles + 1;

                if (morph_tvalid && !sink_ready) begin
                    output_stalls = output_stalls + 1;
                    output_stall_run = output_stall_run + 1;
                    if (output_stall_run > longest_output_stall)
                        longest_output_stall = output_stall_run;
                end else begin
                    output_stall_run = 0;
                end
                if (CHAIN != 0 && gray_tvalid && !gray_tready)
                    gray_stalls = gray_stalls + 1;
                if (CHAIN != 0 && binary_tvalid && !binary_tready)
                    binary_stalls = binary_stalls + 1;

                if (drain_wait != 0) begin
                    if (morph_tvalid !== 1'b0 || source_valid !== 1'b0)
                        $fatal(1, "unexpected beat after all expected morphology outputs");
                    quiet_cycles = quiet_cycles + 1;
                    if (quiet_cycles == 5) begin
                        if (input_count != N || output_count != N)
                            $fatal(1, "count mismatch: in=%0d out=%0d N=%0d",
                                   input_count, output_count, N);
                        if (ready_mode == 1 && N >= (2 * FRAME_BEATS)) begin
                            if (!cross_frame_stall_started || observed_last_stall_cycles != 12)
                                $fatal(1, "cross-frame final-beat stall mismatch: started=%0d observed=%0d expected=12",
                                       cross_frame_stall_started, observed_last_stall_cycles);
                            if (cross_frame_overlap == 0)
                                $fatal(1, "next-frame SOF was not accepted by morphology while prior final output was pending");
                        end
                        if (output_fd != 0) begin
                            $fclose(output_fd);
                            output_fd = 0;
                        end
                        test_done = 1;
                        phase = PH_DONE;
                        $display("TEST_PASS scene=%s mode=%0d chain=%0d ready=%0d beats=%0d seed=%0d in=%0d out=%0d frames=%0d stalls_out=%0d blocked_in=%0d gray_stalls=%0d binary_stalls=%0d bubbles=%0d longest_stall=%0d last_stall=%0d frame_overlap=%0d reset=%0d",
                                 scene_name, MODE, CHAIN, ready_mode, N, seed_value,
                                 input_count, output_count, frame_count,
                                 output_stalls, input_blocked, gray_stalls,
                                 binary_stalls, input_bubbles, longest_output_stall,
                                 observed_last_stall_cycles, cross_frame_overlap,
                                 reset_flush);
                        $finish;
                    end
                end

                if (input_fire) begin
                    if (input_count >= N)
                        $fatal(1, "accepted more than N input beats");
                    input_count = input_count + 1;
                end

                if (morph_tvalid && !sink_ready) begin
                    // The stall is counted above; no output is retired here.
                end
                if (morph_tvalid && sink_ready) begin
                    if (output_count >= N)
                        $fatal(1, "received more than N output beats");
                    if (output_count >= input_count)
                        $fatal(1, "output preceded accepted input: in=%0d out=%0d",
                               input_count, output_count);
                    if ({morph_tuser[0], morph_tlast, morph_tdata} !==
                        expected_vectors[output_count])
                        $fatal(1, "output beat %0d mismatch: got=%03x expected=%03x mode=%0d chain=%0d",
                               output_count,
                               {morph_tuser[0], morph_tlast, morph_tdata},
                               expected_vectors[output_count], MODE, CHAIN);
                    if (morph_tdata !== 8'h00 && morph_tdata !== 8'hff)
                        $fatal(1, "non-binary output at beat %0d: %02x",
                               output_count, morph_tdata);
                    if (morph_tuser[0] !== (output_count % FRAME_BEATS == 0))
                        $fatal(1, "SOF coordinate mismatch at output %0d", output_count);
                    if (morph_tlast !== (output_count % FRAME_WIDTH == FRAME_WIDTH - 1))
                        $fatal(1, "EOL coordinate mismatch at output %0d", output_count);
                    if (output_count == 0 && morph_tuser[0] !== 1'b1)
                        $fatal(1, "first output beat did not carry SOF");

                    if (output_fd != 0)
                        $fdisplay(output_fd, "%03x",
                                  {morph_tuser[0], morph_tlast, morph_tdata});
                    output_count = output_count + 1;
                    if (output_count == N)
                        drain_wait = 1;
                end

                if (input_count > N)
                    $fatal(1, "accepted input count exceeded N");
            end
        end
    end

    initial begin
        rgb_path = "vectors_rgb.hex";
        binary_path = "vectors_binary.hex";
        expected_path = "expected_morphology.hex";
        output_path = "";
        scene_name = "unnamed";

        if (FRAME_WIDTH < 1 || FRAME_HEIGHT < 1 || FRAME_BEATS > MAX_BEATS)
            $fatal(1, "invalid frame geometry %0dx%0d", FRAME_WIDTH, FRAME_HEIGHT);
        if (MODE < 0 || MODE > 2)
            $fatal(1, "MODE must be 0=bypass, 1=erosion, or 2=dilation; got %0d", MODE);
        if (CHAIN != 0 && CHAIN != 1)
            $fatal(1, "CHAIN must be 0 or 1, got %0d", CHAIN);

        parsed = $value$plusargs("N=%d", N);
        parsed = $value$plusargs("READY_MODE=%d", ready_mode);
        parsed = $value$plusargs("SEED=%d", seed_value);
        parsed = $value$plusargs("RESET_CASE=%d", reset_case);
        parsed = $value$plusargs("RGB=%s", rgb_path);
        parsed = $value$plusargs("BINARY=%s", binary_path);
        parsed = $value$plusargs("EXPECTED=%s", expected_path);
        parsed = $value$plusargs("OUTPUT=%s", output_path);
        parsed = $value$plusargs("SCENE=%s", scene_name);

        if (N < 1 || N > MAX_BEATS || (N % FRAME_BEATS) != 0)
            $fatal(1, "N must contain complete frames and be in [1,%0d], got %0d",
                   MAX_BEATS, N);
        if (ready_mode != 0 && ready_mode != 1)
            $fatal(1, "READY_MODE must be 0 or 1, got %0d", ready_mode);
        if (seed_value < 1 || seed_value > 65535)
            $fatal(1, "SEED must be a nonzero 16-bit decimal value, got %0d", seed_value);
        if (reset_case != 0 && reset_case != 1)
            $fatal(1, "RESET_CASE must be 0 or 1, got %0d", reset_case);

        if (CHAIN != 0)
            $readmemh(rgb_path, input_rgb24);
        else
            $readmemh(binary_path, input_binary8);
        $readmemh(expected_path, expected_vectors);

        for (i = 0; i < N; i = i + 1) begin
            if ((^expected_vectors[i]) === 1'bx)
                $fatal(1, "%s has no known 10-bit value at index %0d", expected_path, i);
            if ((expected_vectors[i][7:0] !== 8'h00) &&
                (expected_vectors[i][7:0] !== 8'hff))
                $fatal(1, "%s contains a non-binary output at index %0d",
                       expected_path, i);
            if (expected_vectors[i][9] !== (i % FRAME_BEATS == 0))
                $fatal(1, "expected SOF mismatch at input coordinate %0d", i);
            if (expected_vectors[i][8] !== (i % FRAME_WIDTH == FRAME_WIDTH - 1))
                $fatal(1, "expected EOL mismatch at input coordinate %0d", i);
            if (CHAIN != 0) begin
                if ((^input_rgb24[i]) === 1'bx)
                    $fatal(1, "%s has no known 24-bit value at index %0d", rgb_path, i);
            end else begin
                if ((^input_binary8[i]) === 1'bx)
                    $fatal(1, "%s has no known 8-bit value at index %0d", binary_path, i);
                if (input_binary8[i] !== 8'h00 && input_binary8[i] !== 8'hff)
                    $fatal(1, "%s contains a non-binary input at index %0d",
                           binary_path, i);
            end
        end
        frame_count = N / FRAME_BEATS;

        if (output_path != "") begin
            output_fd = $fopen(output_path, "w");
            if (output_fd == 0)
                $fatal(1, "cannot open OUTPUT path: %s", output_path);
        end

        rng_lfsr = seed_value[15:0];
        repeat (2) @(negedge aclk);

        if (reset_case != 0) begin
            prime_limit = (N < (2 * FRAME_WIDTH)) ? N : (2 * FRAME_WIDTH);
            phase = PH_PRIME;
            aresetn = 1'b1;

            wait_cycles = 0;
            while (morph_tvalid !== 1'b1 && wait_cycles < (2 * FRAME_WIDTH + 64)) begin
                @(posedge aclk);
                #1;
                wait_cycles = wait_cycles + 1;
            end
            if (morph_tvalid !== 1'b1)
                $fatal(1, "reset test could not prime a pending morphology output within %0d cycles",
                       wait_cycles);

            @(negedge aclk);
            phase = PH_RESET;
            aresetn = 1'b0;
            repeat (2) begin
                @(posedge aclk);
                #1;
                if (morph_tvalid !== 1'b0)
                    $fatal(1, "reset did not clear pending morphology output VALID");
                if (CHAIN != 0 && (gray_tvalid !== 1'b0 || binary_tvalid !== 1'b0))
                    $fatal(1, "reset did not clear RGB/Gray/Binary stage VALID");
            end
            reset_flush = 1;

            @(negedge aclk);
            phase = PH_PROBE;
            aresetn = 1'b1;
            probe_limit = (N < 3) ? N : 3;
            wait_cycles = 0;
            while (probe_count < probe_limit && wait_cycles < 256) begin
                @(posedge aclk);
                #1;
                wait_cycles = wait_cycles + 1;
            end
            if (probe_count != probe_limit)
                $fatal(1, "no-SOF probe input stalled: accepted=%0d expected=%0d",
                       probe_count, probe_limit);
            repeat (12) begin
                @(posedge aclk);
                #1;
                if (morph_tvalid !== 1'b0)
                    $fatal(1, "morphology emitted data from no-SOF probe after reset");
            end
            if (CHAIN != 0 && (gray_tvalid !== 1'b0 || binary_tvalid !== 1'b0))
                $fatal(1, "pipeline did not drain the no-SOF probe");
        end

        @(negedge aclk);
        phase = PH_RUN;
        aresetn = 1'b1;
        cycle = 0;
        input_count = 0;
        output_count = 0;
        prime_count = 0;
        probe_count = 0;
        source_hold = 1'b0;
        was_source_stalled = 1'b0;
        was_gray_stalled = 1'b0;
        was_binary_stalled = 1'b0;
        was_output_stalled = 1'b0;
        drain_wait = 0;
        quiet_cycles = 0;
        rng_lfsr = seed_value[15:0];
    end

    initial begin
        repeat (10000000) @(posedge aclk);
        #1;
        if (test_done == 0)
            $fatal(1, "watchdog timeout mode=%0d chain=%0d ready=%0d seed=%0d in=%0d out=%0d cycles=%0d",
                   MODE, CHAIN, ready_mode, seed_value, input_count, output_count, cycle);
    end
endmodule
