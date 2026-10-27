`timescale 1ns/1ps

module tb_axis_roi_crop #(
    parameter integer FRAME_WIDTH = 320,
    parameter integer FRAME_HEIGHT = 240,
    parameter integer ROI_X0 = 0,
    parameter integer ROI_Y0 = 0,
    parameter integer ROI_X1 = FRAME_WIDTH,
    parameter integer ROI_Y1 = FRAME_HEIGHT,
    parameter integer CHAIN = 0,
    parameter integer THRESHOLD = 128
);
    localparam integer MAX_INPUT_BEATS =
        ((FRAME_WIDTH > 0 && FRAME_HEIGHT > 0) ? FRAME_WIDTH * FRAME_HEIGHT * 4 : 4);
    localparam integer ROI_WIDTH = ROI_X1 - ROI_X0;
    localparam integer ROI_HEIGHT = ROI_Y1 - ROI_Y0;

    reg aclk = 1'b0;
    always #5 aclk = ~aclk;

    reg aresetn = 1'b0;
    reg main_phase = 1'b0;
    reg preamble_valid = 1'b0;
    reg [23:0] preamble_data = 24'd0;
    reg preamble_sof = 1'b0;
    reg preamble_eol = 1'b0;
    reg preamble_ready = 1'b1;

    integer input_count = 0;
    integer output_count = 0;
    integer N = 0;
    integer EXPECTED_N = 0;
    integer READY_MODE = 0;
    integer main_cycle = 0;
    integer quiet_cycles = 0;
    integer source_bubbles = 0;
    integer blocked_cycles = 0;
    integer stall_cycles = 0;
    integer longest_pause = 0;
    integer pause_run = 0;
    integer throughput_pairs = 0;
    integer last_output_cycle = -2;
    integer reset_midframe_checks = 0;
    integer reset_flush_checks = 0;
    integer pre_sof_ignored_checks = 0;
    integer resync_pending_checks = 0;
    integer output_fd = 0;
    integer parsed = 0;
    integer timeout_count = 0;
    integer i = 0;
    integer x = 0;
    integer y = 0;
    reg source_hold = 1'b0;
    integer was_input_stalled = 0;
    integer was_gray_stalled = 0;
    integer was_binary_stalled = 0;
    integer was_roi_input_stalled = 0;
    integer was_output_stalled = 0;
    reg forced_stall_started = 1'b0;
    integer forced_stall_remaining = 0;
    integer no_preamble_output_expected = 0;
    integer resync_expect_old_output = 0;
    integer resync_seen = 0;
    integer preamble_output_expected = 0;
    integer test_done = 0;
    integer seed = 16'h1ace;
    reg [15:0] source_lfsr = 16'h1ace;
    reg [15:0] ready_lfsr = 16'h2d39;

    reg [23:0] held_input_data = 24'd0;
    reg held_input_sof = 1'b0;
    reg held_input_eol = 1'b0;
    reg [7:0] held_gray_data = 8'd0;
    reg held_gray_sof = 1'b0;
    reg held_gray_eol = 1'b0;
    reg [7:0] held_binary_data = 8'd0;
    reg held_binary_sof = 1'b0;
    reg held_binary_eol = 1'b0;
    reg [7:0] held_roi_input_data = 8'd0;
    reg held_roi_input_sof = 1'b0;
    reg held_roi_input_eol = 1'b0;
    reg [7:0] held_output_data = 8'd0;
    reg held_output_sof = 1'b0;
    reg held_output_eol = 1'b0;
    reg [7:0] expected_pending_value = 8'd0;
    reg [23:0] input_rgb_vectors [0:MAX_INPUT_BEATS-1];
    reg [7:0] input_gray_vectors [0:MAX_INPUT_BEATS-1];
    reg [9:0] input_tag_vectors [0:MAX_INPUT_BEATS-1];
    reg [9:0] expected_vectors [0:MAX_INPUT_BEATS-1];

    string rgb_path;
    string gray_path;
    string input_tags_path;
    string expected_path;
    string output_path;

    wire [23:0] s_tdata;
    wire [0:0] s_tuser;
    wire s_tlast;
    wire s_tvalid;
    wire s_tready;
    wire [7:0] roi_s_tdata;
    wire [0:0] roi_s_tuser;
    wire roi_s_tlast;
    wire roi_s_tvalid;
    wire roi_s_tready;
    wire [7:0] m_tdata;
    wire [0:0] m_tuser;
    wire m_tlast;
    wire m_tvalid;
    wire m_tready;
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

    assign s_tdata = main_phase ?
                     ((input_count < N) ?
                      ((CHAIN != 0) ? input_rgb_vectors[input_count] :
                                      {16'd0, input_gray_vectors[input_count]}) : 24'd0) :
                     preamble_data;
    assign s_tuser = main_phase ?
                     ((input_count < N) ? input_tag_vectors[input_count][9] : 1'b0) :
                     preamble_sof;
    assign s_tlast = main_phase ?
                     ((input_count < N) ? input_tag_vectors[input_count][8] : 1'b0) :
                     preamble_eol;
    assign s_tvalid = main_phase ?
                      (input_count < N &&
                       (READY_MODE == 0 || source_hold != 0 ||
                        ((source_lfsr[0] || source_lfsr[4]) &&
                         (main_cycle % 13 != 2) && (main_cycle % 13 != 7)))) :
                      preamble_valid;
    assign m_tready = !main_phase ? preamble_ready :
                      (READY_MODE == 0 ? 1'b1 :
                       ((forced_stall_remaining > 0 ||
                         (!forced_stall_started && m_tvalid)) ? 1'b0 :
                        ((ready_lfsr[0] || ready_lfsr[3] || ready_lfsr[9]) &&
                         (main_cycle % 31 < 25))));

    generate
        if (CHAIN != 0) begin : g_retired_source_mode_roi
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
                .m_tdata(binary_tdata), .m_tuser(binary_tuser), .m_tlast(binary_tlast),
                .m_tvalid(binary_tvalid), .m_tready(binary_tready)
            );

            assign roi_s_tdata = binary_tdata;
            assign roi_s_tuser = binary_tuser;
            assign roi_s_tlast = binary_tlast;
            assign roi_s_tvalid = binary_tvalid;
            assign binary_tready = roi_s_tready;
        end else begin : g_gray_roi
            assign gray_tdata = 8'd0;
            assign gray_tuser = 1'b0;
            assign gray_tlast = 1'b0;
            assign gray_tvalid = 1'b0;
            assign gray_tready = 1'b0;
            assign binary_tdata = 8'd0;
            assign binary_tuser = 1'b0;
            assign binary_tlast = 1'b0;
            assign binary_tvalid = 1'b0;
            assign binary_tready = 1'b0;
            assign roi_s_tdata = s_tdata[7:0];
            assign roi_s_tuser = s_tuser;
            assign roi_s_tlast = s_tlast;
            assign roi_s_tvalid = s_tvalid;
            assign s_tready = roi_s_tready;
        end
    endgenerate

    axis_roi_crop #(
        .FRAME_WIDTH(FRAME_WIDTH), .FRAME_HEIGHT(FRAME_HEIGHT),
        .ROI_X0(ROI_X0), .ROI_Y0(ROI_Y0), .ROI_X1(ROI_X1), .ROI_Y1(ROI_Y1)
    ) dut (
        .aclk(aclk), .aresetn(aresetn),
        .s_tdata(roi_s_tdata), .s_tuser(roi_s_tuser), .s_tlast(roi_s_tlast),
        .s_tvalid(roi_s_tvalid), .s_tready(roi_s_tready),
        .m_tdata(m_tdata), .m_tuser(m_tuser), .m_tlast(m_tlast),
        .m_tvalid(m_tvalid), .m_tready(m_tready)
    );

    function automatic [7:0] preamble_gray(input integer pixel_index);
        begin
            preamble_gray = (pixel_index * 37 + 8'h31) & 8'hff;
        end
    endfunction

    function automatic [7:0] chain_value(input [7:0] gray_value);
        begin
            chain_value = (CHAIN != 0) ?
                          ((gray_value >= THRESHOLD) ? 8'hff : 8'h00) : gray_value;
        end
    endfunction

    task automatic send_preamble_beat(input [7:0] value,
                                       input bit sof,
                                       input bit eol);
        integer accepted;
        begin
            @(negedge aclk);
            preamble_data = {value, value, value};
            preamble_sof = sof;
            preamble_eol = eol;
            preamble_valid = 1'b1;
            accepted = 0;
            while (accepted == 0) begin
                @(posedge aclk);
                if (s_tready)
                    accepted = 1;
            end
            @(negedge aclk);
            preamble_valid = 1'b0;
            preamble_sof = 1'b0;
            preamble_eol = 1'b0;
        end
    endtask

    task automatic send_preamble_prefix;
        integer beat_index;
        integer target_index;
        integer last_index;
        integer gray_value;
        begin
            target_index = (ROI_WIDTH > 0 && ROI_HEIGHT > 0) ?
                           (ROI_Y0 * FRAME_WIDTH + ROI_X0) :
                           ((FRAME_WIDTH * FRAME_HEIGHT < 3) ?
                            FRAME_WIDTH * FRAME_HEIGHT - 1 : 2);
            last_index = target_index;
            for (beat_index = 0; beat_index <= last_index; beat_index = beat_index + 1) begin
                gray_value = preamble_gray(beat_index);
                send_preamble_beat(gray_value[7:0], beat_index == 0,
                                   (beat_index % FRAME_WIDTH) == FRAME_WIDTH - 1);
            end
            if (ROI_WIDTH > 0 && ROI_HEIGHT > 0) begin
                timeout_count = 0;
                while (m_tvalid !== 1'b1 && timeout_count < 100) begin
                    @(posedge aclk);
                    timeout_count = timeout_count + 1;
                end
                if (m_tvalid !== 1'b1)
                    $fatal(1, "preamble did not produce the first cropped pixel");
                gray_value = preamble_gray(target_index);
                expected_pending_value = chain_value(gray_value[7:0]);
                if ({m_tuser[0], m_tlast, m_tdata} !==
                    {1'b1, (ROI_WIDTH == 1), expected_pending_value})
                    $fatal(1, "first cropped pixel mismatch: got=%03x expected=%03x",
                           {m_tuser[0], m_tlast, m_tdata},
                           {1'b1, (ROI_WIDTH == 1), expected_pending_value});
                repeat (3) @(posedge aclk);
                if (m_tvalid !== 1'b1 || m_tdata !== expected_pending_value ||
                    m_tuser[0] !== 1'b1 || m_tlast !== (ROI_WIDTH == 1))
                    $fatal(1, "buffered output changed during preamble backpressure");
                preamble_output_expected = 1;
            end else begin
                repeat (8) @(posedge aclk);
                if (m_tvalid !== 1'b0)
                    $fatal(1, "empty ROI produced a preamble output");
            end
        end
    endtask

    task automatic pulse_reset;
        begin
            @(negedge aclk);
            preamble_valid = 1'b0;
            aresetn = 1'b0;
            @(posedge aclk);
            #1;
            if (m_tvalid !== 1'b0)
                $fatal(1, "reset did not clear pending ROI output valid");
            if (CHAIN != 0 && (gray_tvalid !== 1'b0 || binary_tvalid !== 1'b0))
                $fatal(1, "reset did not clear an upstream chain-stage valid");
            @(posedge aclk);
            #1;
            if (m_tvalid !== 1'b0)
                $fatal(1, "ROI output valid returned while reset remained asserted");
            if (CHAIN != 0 && (gray_tvalid !== 1'b0 || binary_tvalid !== 1'b0))
                $fatal(1, "chain-stage valid returned while reset remained asserted");
            @(negedge aclk);
            aresetn = 1'b1;
            preamble_ready = 1'b1;
        end
    endtask

    task automatic send_resync_sof_while_output_pending;
        integer accepted;
        integer wait_cycles;
        begin
            @(negedge aclk);
            preamble_data = {8'hd3, 8'hd3, 8'hd3};
            preamble_sof = 1'b1;
            preamble_eol = (FRAME_WIDTH == 1);
            preamble_valid = 1'b1;
            accepted = 0;
            if (CHAIN != 0) begin
                while (accepted == 0) begin
                    @(posedge aclk);
                    if (s_tready)
                        accepted = 1;
                end
                @(negedge aclk);
                preamble_valid = 1'b0;
                preamble_sof = 1'b0;
                preamble_eol = 1'b0;
            end

            wait_cycles = 0;
            while (!(roi_s_tvalid && roi_s_tuser[0] && !roi_s_tready) &&
                   wait_cycles < 200) begin
                @(posedge aclk);
                wait_cycles = wait_cycles + 1;
            end
            if (!(roi_s_tvalid && roi_s_tuser[0] && !roi_s_tready))
                $fatal(1, "could not queue a resynchronizing SOF behind the pending ROI output");
            if (m_tvalid !== 1'b1 || m_tready !== 1'b0)
                $fatal(1, "resynchronization setup lost its pending output");
            if ({m_tuser[0], m_tlast, m_tdata} !==
                {1'b1, (ROI_WIDTH == 1), expected_pending_value})
                $fatal(1, "pending output changed before resynchronization release: got=%03x expected=%03x",
                       {m_tuser[0], m_tlast, m_tdata},
                       {1'b1, (ROI_WIDTH == 1), expected_pending_value});

            resync_seen = 0;
            resync_expect_old_output = 1;
            @(negedge aclk);
            preamble_ready = 1'b1;
            wait_cycles = 0;
            while (resync_seen == 0 && wait_cycles < 10) begin
                @(posedge aclk);
                #1;
                wait_cycles = wait_cycles + 1;
            end
            if (resync_seen == 0)
                $fatal(1, "accepted SOF did not transfer the previously buffered output");
            resync_pending_checks = resync_pending_checks + 1;
            @(negedge aclk);
            resync_expect_old_output = 0;
            preamble_ready = 1'b0;
            preamble_valid = 1'b0;
            preamble_sof = 1'b0;
            preamble_eol = 1'b0;
        end
    endtask

    always @(posedge aclk) begin
        source_lfsr <= {source_lfsr[14:0], source_lfsr[15] ^ source_lfsr[13] ^
                        source_lfsr[12] ^ source_lfsr[10]};
        ready_lfsr <= {ready_lfsr[14:0], ready_lfsr[15] ^ ready_lfsr[13] ^
                       ready_lfsr[12] ^ ready_lfsr[10]};

        if (!aresetn) begin
            source_hold <= 1'b0;
            was_input_stalled = 0;
            was_gray_stalled = 0;
            was_binary_stalled = 0;
            was_roi_input_stalled = 0;
            was_output_stalled = 0;
            main_cycle <= 0;
            forced_stall_started <= 1'b0;
            forced_stall_remaining <= 0;
            pause_run = 0;
        end else begin
            if (main_phase)
                main_cycle <= main_cycle + 1;
            source_hold <= main_phase && s_tvalid && !s_tready;

            if (was_input_stalled &&
                (!s_tvalid || s_tdata !== held_input_data ||
                 s_tuser[0] !== held_input_sof || s_tlast !== held_input_eol))
                $fatal(1, "source beat changed while waiting for s_tready");
            was_input_stalled = s_tvalid && !s_tready;
            if (was_input_stalled) begin
                held_input_data = s_tdata;
                held_input_sof = s_tuser[0];
                held_input_eol = s_tlast;
            end

            if (CHAIN != 0) begin
                if (was_gray_stalled &&
                    (!gray_tvalid || gray_tdata !== held_gray_data ||
                     gray_tuser[0] !== held_gray_sof || gray_tlast !== held_gray_eol))
                    $fatal(1, "RGB-to-Gray beat changed while waiting for binary-stage ready");
                was_gray_stalled = gray_tvalid && !gray_tready;
                if (was_gray_stalled) begin
                    held_gray_data = gray_tdata;
                    held_gray_sof = gray_tuser[0];
                    held_gray_eol = gray_tlast;
                end

                if (was_binary_stalled &&
                    (!binary_tvalid || binary_tdata !== held_binary_data ||
                     binary_tuser[0] !== held_binary_sof || binary_tlast !== held_binary_eol))
                    $fatal(1, "binary beat changed while waiting for ROI-stage ready");
                was_binary_stalled = binary_tvalid && !binary_tready;
                if (was_binary_stalled) begin
                    held_binary_data = binary_tdata;
                    held_binary_sof = binary_tuser[0];
                    held_binary_eol = binary_tlast;
                end
            end

            if (was_roi_input_stalled &&
                (!roi_s_tvalid || roi_s_tdata !== held_roi_input_data ||
                 roi_s_tuser[0] !== held_roi_input_sof || roi_s_tlast !== held_roi_input_eol))
                $fatal(1, "ROI input beat changed while waiting for s_tready");
            was_roi_input_stalled = roi_s_tvalid && !roi_s_tready;
            if (was_roi_input_stalled) begin
                held_roi_input_data = roi_s_tdata;
                held_roi_input_sof = roi_s_tuser[0];
                held_roi_input_eol = roi_s_tlast;
            end

            if (was_output_stalled &&
                (!m_tvalid || m_tdata !== held_output_data ||
                 m_tuser[0] !== held_output_sof || m_tlast !== held_output_eol))
                $fatal(1, "ROI output changed while waiting for m_tready");
            was_output_stalled = m_tvalid && !m_tready;
            if (was_output_stalled) begin
                held_output_data = m_tdata;
                held_output_sof = m_tuser[0];
                held_output_eol = m_tlast;
            end

            if (no_preamble_output_expected && !main_phase && m_tvalid)
                $fatal(1, "output appeared before a fresh accepted SOF");

            if (resync_expect_old_output && roi_s_tvalid && roi_s_tready && roi_s_tuser[0]) begin
                if (!(m_tvalid && m_tready))
                    $fatal(1, "resynchronizing SOF cleared a pending output before its handshake");
                if ({m_tuser[0], m_tlast, m_tdata} !==
                    {1'b1, (ROI_WIDTH == 1), expected_pending_value})
                    $fatal(1, "resynchronizing SOF changed the pending output: got=%03x expected=%03x",
                           {m_tuser[0], m_tlast, m_tdata},
                           {1'b1, (ROI_WIDTH == 1), expected_pending_value});
                resync_seen = 1;
            end

            if (main_phase) begin
                if (s_tvalid && !s_tready)
                    blocked_cycles = blocked_cycles + 1;
                if (input_count < N && !s_tvalid)
                    source_bubbles = source_bubbles + 1;
                if (m_tvalid && !m_tready) begin
                    stall_cycles = stall_cycles + 1;
                    pause_run = pause_run + 1;
                    if (pause_run > longest_pause)
                        longest_pause = pause_run;
                end else begin
                    pause_run = 0;
                end

                if (READY_MODE == 1) begin
                    if (m_tvalid && !forced_stall_started) begin
                        forced_stall_started <= 1'b1;
                        forced_stall_remaining <= 48;
                    end else if (forced_stall_remaining > 0) begin
                        forced_stall_remaining <= forced_stall_remaining - 1;
                    end
                end

                if (s_tvalid && s_tready) begin
                    if (input_count >= N)
                        $fatal(1, "accepted more than N input beats");
                    input_count <= input_count + 1;
                end

                if (m_tvalid && m_tready) begin
                    if (output_count >= EXPECTED_N)
                        $fatal(1, "received more than EXPECTED_N output beats");
                    if ({m_tuser[0], m_tlast, m_tdata} !== expected_vectors[output_count])
                        $fatal(1, "output beat %0d mismatch: got=%03x expected=%03x chain=%0d mode=%0d",
                               output_count, {m_tuser[0], m_tlast, m_tdata},
                               expected_vectors[output_count], CHAIN, READY_MODE);
                    if (READY_MODE == 0 && output_count > 0 &&
                        expected_vectors[output_count-1][8] == 1'b0) begin
                        if (last_output_cycle != main_cycle - 1)
                            $fatal(1, "cropped pixels within one row did not sustain one beat per cycle");
                        throughput_pairs = throughput_pairs + 1;
                    end
                    last_output_cycle = main_cycle;
                    if (output_fd != 0)
                        $fdisplay(output_fd, "%03x", {m_tuser[0], m_tlast, m_tdata});
                    output_count = output_count + 1;
                end

                if (input_count == N && output_count == EXPECTED_N &&
                    !s_tvalid && !m_tvalid) begin
                    quiet_cycles = quiet_cycles + 1;
                    if (quiet_cycles == 5) begin
                        if (input_count != N || output_count != EXPECTED_N)
                            $fatal(1, "main count mismatch: in=%0d/%0d out=%0d/%0d",
                                   input_count, N, output_count, EXPECTED_N);
                        if (reset_midframe_checks != 1 || pre_sof_ignored_checks != 1)
                            $fatal(1, "reset/synchronization coverage missing: midframe=%0d pre_sof=%0d",
                                   reset_midframe_checks, pre_sof_ignored_checks);
                        if ((ROI_WIDTH > 0 && ROI_HEIGHT > 0) &&
                            (reset_flush_checks != 1 || resync_pending_checks != 1))
                            $fatal(1, "pending-output reset/resync coverage missing: flush=%0d resync=%0d",
                                   reset_flush_checks, resync_pending_checks);
                        if (READY_MODE == 0 &&
                            (stall_cycles != 0 || blocked_cycles != 0 || source_bubbles != 0))
                            $fatal(1, "continuous mode unexpectedly stalled or bubbled: stall=%0d blocked=%0d bubbles=%0d",
                                   stall_cycles, blocked_cycles, source_bubbles);
                        if (READY_MODE == 0 && ROI_WIDTH > 1 && ROI_HEIGHT > 0 &&
                            throughput_pairs == 0)
                            $fatal(1, "within-row continuous-throughput coverage missing");
                        if (READY_MODE == 1 && N > 1 && source_bubbles == 0)
                            $fatal(1, "random source did not produce a valid bubble");
                        if (READY_MODE == 1 && EXPECTED_N > 0 &&
                            (stall_cycles < 32 || longest_pause < 32))
                            $fatal(1, "random sink did not cover a long output stall: stalls=%0d longest=%0d",
                                   stall_cycles, longest_pause);
                        if (output_fd != 0) begin
                            $fclose(output_fd);
                            output_fd = 0;
                        end
                        test_done = 1;
                        $display("TEST_PASS roi=%0d,%0d,%0d,%0d frame=%0dx%0d chain=%0d mode=%0d in=%0d out=%0d stalls=%0d blocked=%0d bubbles=%0d longest_pause=%0d throughput_pairs=%0d reset_midframe=%0d reset_flush=%0d pre_sof_ignored=%0d resync_pending=%0d",
                                 ROI_X0, ROI_Y0, ROI_X1, ROI_Y1, FRAME_WIDTH, FRAME_HEIGHT,
                                 CHAIN, READY_MODE, input_count, output_count, stall_cycles,
                                 blocked_cycles, source_bubbles, longest_pause, throughput_pairs,
                                 reset_midframe_checks, reset_flush_checks,
                                 pre_sof_ignored_checks, resync_pending_checks);
                        $finish;
                    end
                end else begin
                    quiet_cycles = 0;
                end
            end
        end
    end

    initial begin
        rgb_path = "vectors_rgb.hex";
        gray_path = "vectors_gray.hex";
        input_tags_path = "input_tags.hex";
        expected_path = "expected_roi.hex";
        output_path = "output_roi.hex";

        if (CHAIN != 0 && CHAIN != 1)
            $fatal(1, "CHAIN must be 0 or 1, got %0d", CHAIN);
        if (THRESHOLD < 0 || THRESHOLD > 255)
            $fatal(1, "THRESHOLD must be in [0,255], got %0d", THRESHOLD);

        parsed = $value$plusargs("N=%d", N);
        parsed = $value$plusargs("EXPECTED_N=%d", EXPECTED_N);
        parsed = $value$plusargs("READY_MODE=%d", READY_MODE);
        parsed = $value$plusargs("SEED=%d", seed);
        parsed = $value$plusargs("RGB=%s", rgb_path);
        parsed = $value$plusargs("GRAY=%s", gray_path);
        parsed = $value$plusargs("INPUT_TAGS=%s", input_tags_path);
        parsed = $value$plusargs("EXPECTED=%s", expected_path);
        parsed = $value$plusargs("OUTPUT=%s", output_path);

        if (N <= 0 || N > MAX_INPUT_BEATS)
            $fatal(1, "N must be in [1,%0d], got %0d", MAX_INPUT_BEATS, N);
        if (EXPECTED_N < 0 || EXPECTED_N > MAX_INPUT_BEATS)
            $fatal(1, "EXPECTED_N must be in [0,%0d], got %0d", MAX_INPUT_BEATS, EXPECTED_N);
        if (READY_MODE != 0 && READY_MODE != 1)
            $fatal(1, "READY_MODE must be 0 or 1, got %0d", READY_MODE);
        if (seed == 0)
            seed = 16'h1ace;
        source_lfsr = seed[15:0];
        ready_lfsr = seed[15:0] ^ 16'h2d39;
        if (ready_lfsr == 0)
            ready_lfsr = 16'h2d39;

        $readmemh(rgb_path, input_rgb_vectors);
        $readmemh(gray_path, input_gray_vectors);
        $readmemh(input_tags_path, input_tag_vectors);
        if (EXPECTED_N > 0)
            $readmemh(expected_path, expected_vectors);

        for (i = 0; i < N; i = i + 1) begin
            if ((^input_rgb_vectors[i]) === 1'bx || (^input_gray_vectors[i]) === 1'bx ||
                (^input_tag_vectors[i]) === 1'bx)
                $fatal(1, "missing or malformed input vector at beat %0d", i);
        end
        for (i = 0; i < EXPECTED_N; i = i + 1)
            if ((^expected_vectors[i]) === 1'bx)
                $fatal(1, "missing or malformed expected ROI vector at beat %0d", i);

        output_fd = $fopen(output_path, "w");
        if (output_fd == 0)
            $fatal(1, "cannot open OUTPUT path: %s", output_path);

        repeat (3) @(posedge aclk);
        @(negedge aclk);
        aresetn = 1'b1;
        repeat (2) @(posedge aclk);

        no_preamble_output_expected = 1;
        send_preamble_beat(8'h11, 1'b0, 1'b0);
        send_preamble_beat(8'h22, 1'b0, 1'b0);
        repeat (8) @(posedge aclk);
        if (m_tvalid !== 1'b0)
            $fatal(1, "pre-SOF traffic generated an output");
        pre_sof_ignored_checks = 1;
        no_preamble_output_expected = 0;

        preamble_ready = 1'b0;
        send_preamble_prefix();
        reset_midframe_checks = 1;
        if (preamble_output_expected != 0) begin
            if (m_tvalid !== 1'b1 || m_tready !== 1'b0)
                $fatal(1, "reset-flush setup did not leave a stalled output pending");
            reset_flush_checks = 1;
        end
        pulse_reset();
        if (m_tvalid !== 1'b0)
            $fatal(1, "stale output reappeared immediately after reset");

        no_preamble_output_expected = 1;
        send_preamble_beat(8'h41, 1'b0, 1'b0);
        send_preamble_beat(8'h82, 1'b0, 1'b0);
        repeat (8) @(posedge aclk);
        if (m_tvalid !== 1'b0)
            $fatal(1, "unsynchronized post-reset traffic generated an output");
        pre_sof_ignored_checks = 1;
        no_preamble_output_expected = 0;

        if (ROI_WIDTH > 0 && ROI_HEIGHT > 0) begin
            preamble_ready = 1'b0;
            send_preamble_prefix();
            send_resync_sof_while_output_pending();
        end else begin
            preamble_ready = 1'b1;
            send_preamble_prefix();
        end

        pulse_reset();
        repeat (3) @(posedge aclk);
        @(negedge aclk);
        input_count = 0;
        output_count = 0;
        main_cycle = 0;
        quiet_cycles = 0;
        source_bubbles = 0;
        blocked_cycles = 0;
        stall_cycles = 0;
        longest_pause = 0;
        pause_run = 0;
        throughput_pairs = 0;
        last_output_cycle = -2;
        forced_stall_started = 0;
        forced_stall_remaining = 0;
        source_hold = 0;
        preamble_valid = 0;
        preamble_ready = 1;
        main_phase = 1;
    end

    initial begin
        repeat (3000000) @(posedge aclk);
        #1;
        if (!test_done)
            $fatal(1, "watchdog timeout roi=%0d,%0d,%0d,%0d chain=%0d mode=%0d in=%0d/%0d out=%0d/%0d cycles=%0d",
                   ROI_X0, ROI_Y0, ROI_X1, ROI_Y1, CHAIN, READY_MODE,
                   input_count, N, output_count, EXPECTED_N, main_cycle);
    end
endmodule
