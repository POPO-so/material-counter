`timescale 1ns/1ps

// Offline WO-0014 self-checking driver. Input records are the five-byte
// encoding documented by image_vectors.py; expected output words are 16-bit
// little-endian {6'b0,SOF,EOL,Binary8} records.
module tb_axis_dma_image_pipeline;
`ifdef WO0014_WIDTH
    localparam integer WIDTH = `WO0014_WIDTH;
`elsif WO0014_SMALL
    localparam integer WIDTH = 5;
`else
    localparam integer WIDTH = 320;
`endif
`ifdef WO0014_HEIGHT
    localparam integer HEIGHT = `WO0014_HEIGHT;
`elsif WO0014_SMALL
    localparam integer HEIGHT = 3;
`else
    localparam integer HEIGHT = 240;
`endif
`ifdef WO0014_THRESHOLD
    localparam integer THRESHOLD = `WO0014_THRESHOLD;
`else
    localparam integer THRESHOLD = 128;
`endif
    localparam integer PIXELS_PER_FRAME = WIDTH * HEIGHT;
    localparam integer MAX_READY_WAIT = 1000;

    reg aclk = 1'b0;
    reg aresetn = 1'b0;
    reg [31:0] s_tdata = 32'd0;
    reg [3:0] s_tkeep = 4'd0;
    reg s_tlast = 1'b0;
    reg s_tvalid = 1'b0;
    wire s_tready;
    wire [7:0] m_tdata;
    wire [0:0] m_tuser;
    wire m_tlast;
    wire m_tvalid;
    reg m_tready = 1'b0;
    wire packet_done;
    wire packet_error;
    wire [3:0] error_code;
    wire [31:0] frame_id;
    wire checker_error;
    wire [31:0] observed_pixels;
    wire [31:0] observed_sofs;
    wire [31:0] observed_eols;
    wire [31:0] observed_checksum;

    integer input_fd = -1;
    integer expected_fd = -1;
    integer actual_fd = -1;
    integer input_ok;
    integer byte0, byte1, byte2, byte3, control_byte;
    integer negative_mode = 0;
    integer reset_stress = 0;
    integer recovery_phase_active = 0;
    integer ready_mode = 1;
    integer gap_mode = 1;
    integer expected_error = 0;
    integer expected_frames = 1;
    integer bad_records = 0;
    integer drain_records = 0;
    integer recovery_records = 0;
    integer check_frame_id = 1;
    integer max_cycles = 500000000;
    integer input_records = 0;
    integer output_records = 0;
    integer stalled_cycles = 0;
    integer packet_done_count = 0;
    integer packet_error_count = 0;
    integer expected_word_index = 0;
    integer expected_words_total = 0;
    integer recovery_score_started = 0;
    integer score_pixels = 0;
    integer score_sofs = 0;
    integer score_eols = 0;
    reg [31:0] score_checksum = 32'd0;
    reg previous_output_stalled = 1'b0;
    reg [7:0] held_output_data = 8'd0;
    reg held_output_user = 1'b0;
    reg held_output_last = 1'b0;
    reg [31:0] base_frame_id = 32'd0;
    reg [31:0] input_lfsr = 32'h0014C0DE;
    reg [31:0] ready_lfsr = 32'h0014C0DE;
    integer ready_cycle = 0;
    integer force_ready_override = 0;
    integer ready_override = 0;
    integer gap_cycles;
    integer wait_cycles;
    integer drain_cycles;
    integer i;
    integer seed_arg;
    integer plusarg_found;
    integer idle_cycles;
    integer tmp_word;
    integer expected_low, expected_high;
    integer do_score;
    integer last_pixel_count;
    reg [15:0] expected_word;
    reg [15:0] output_word;
    reg [31:0] expected_id;
    reg [31:0] stress_data [0:4];
    reg [3:0] stress_keep [0:4];
    reg stress_last [0:4];
    reg stress_ok;
    reg [31:0] stress_word;
    reg [3:0] stress_mask;
    reg stress_eof;
    integer stress_fd;
    reg [8*1024-1:0] input_name;
    reg [8*1024-1:0] expected_name;
    reg [8*1024-1:0] actual_name;

    axis_dma_image_pipeline #(
        .WIDTH(WIDTH),
        .HEIGHT(HEIGHT),
        .THRESHOLD(THRESHOLD)
    ) dut (
        .aclk(aclk), .aresetn(aresetn),
        .s_tdata(s_tdata), .s_tkeep(s_tkeep), .s_tlast(s_tlast),
        .s_tvalid(s_tvalid), .s_tready(s_tready),
        .m_tdata(m_tdata), .m_tuser(m_tuser), .m_tlast(m_tlast),
        .m_tvalid(m_tvalid), .m_tready(m_tready),
        .packet_done(packet_done), .packet_error(packet_error),
        .error_code(error_code), .frame_id(frame_id),
        .checker_error(checker_error), .observed_pixels(observed_pixels),
        .observed_sofs(observed_sofs), .observed_eols(observed_eols),
        .observed_checksum(observed_checksum)
    );

    always #5 aclk = ~aclk;

    // Deterministic output backpressure. Mode 1 is a fixed-seed LFSR pattern;
    // mode 2 inserts periodic stalls; mode 0 is always ready.
    always @(negedge aclk) begin
        if (!aresetn)
            m_tready <= 1'b0;
        else if (force_ready_override != 0)
            m_tready <= (ready_override != 0);
        else begin
            case (ready_mode)
                0: m_tready <= 1'b1;
                2: begin
                    m_tready <= ((ready_cycle % 29) >= 5);
                    ready_cycle = ready_cycle + 1;
                end
                default: begin
                    ready_lfsr <= {ready_lfsr[30:0],
                                   ready_lfsr[31] ^ ready_lfsr[21] ^
                                   ready_lfsr[1] ^ ready_lfsr[0]};
                    m_tready <= (ready_lfsr[1:0] != 2'b00);
                end
            endcase
        end
    end

    task read_record;
        input integer fd;
        output integer valid_record;
        output reg [31:0] data_word;
        output reg [3:0] keep_mask;
        output reg last_bit;
        integer b0, b1, b2, b3, ctl;
        begin
            valid_record = 0;
            data_word = 32'd0;
            keep_mask = 4'd0;
            last_bit = 1'b0;
            b0 = $fgetc(fd);
            if (b0 != -1) begin
                b1 = $fgetc(fd);
                b2 = $fgetc(fd);
                b3 = $fgetc(fd);
                ctl = $fgetc(fd);
                if ((b1 == -1) || (b2 == -1) || (b3 == -1) || (ctl == -1))
                    $fatal(1, "truncated 5-byte input beat record");
                data_word = (b3 << 24) | (b2 << 16) | (b1 << 8) | b0;
                keep_mask = ctl & 4'hf;
                last_bit = (ctl >> 4) & 1;
                if ((ctl & 8'he0) != 0)
                    $fatal(1, "reserved input control bits are nonzero: 0x%02x", ctl);
                valid_record = 1;
            end
        end
    endtask

    task send_record;
        input [31:0] data_word;
        input [3:0] keep_mask;
        input last_bit;
        integer accepted;
        integer gap;
        integer n;
        begin
            accepted = 0;
            if (gap_mode == 1) begin
                input_lfsr = {input_lfsr[30:0],
                              input_lfsr[31] ^ input_lfsr[21] ^
                              input_lfsr[1] ^ input_lfsr[0]};
                gap = input_lfsr[1:0];
            end else if (gap_mode == 2) begin
                gap = (input_records % 4);
            end else begin
                gap = 0;
            end
            repeat (gap) @(negedge aclk);
            @(negedge aclk);
            s_tdata = data_word;
            s_tkeep = keep_mask;
            s_tlast = last_bit;
            s_tvalid = 1'b1;
            n = 0;
            while ((accepted == 0) && (n < MAX_READY_WAIT)) begin
                @(posedge aclk);
                if (s_tready)
                    accepted = 1;
                else
                    n = n + 1;
            end
            if (accepted == 0)
                $fatal(1, "input beat timed out waiting for TREADY after %0d cycles", MAX_READY_WAIT);
            @(negedge aclk);
            s_tvalid = 1'b0;
            input_records = input_records + 1;
        end
    endtask

    task reset_and_check;
        begin
            @(negedge aclk);
            aresetn = 1'b0;
            s_tvalid = 1'b0;
            repeat (3) @(posedge aclk);
            @(negedge aclk);
            if (m_tvalid || packet_done || packet_error || checker_error ||
                observed_pixels != 0 || observed_sofs != 0 || observed_eols != 0 ||
                observed_checksum != 0)
                $fatal(1, "reset did not clear pipeline/checker state");
            aresetn = 1'b1;
            repeat (2) @(posedge aclk);
        end
    endtask

    task send_record_count;
        input integer fd;
        input integer count;
        integer n;
        integer valid_record;
        reg [31:0] data_word;
        reg [3:0] keep_mask;
        reg last_bit;
        begin
            for (n = 0; n < count; n = n + 1) begin
                read_record(fd, valid_record, data_word, keep_mask, last_bit);
                if (!valid_record)
                    $fatal(1, "input file ended before the declared phase length");
                send_record(data_word, keep_mask, last_bit);
            end
        end
    endtask

    task wait_for_packet_error;
        integer n;
        begin
            n = 0;
            while ((packet_error_count == 0) && (n < MAX_READY_WAIT * 10)) begin
                @(posedge aclk);
                n = n + 1;
            end
            if (packet_error_count != 1)
                $fatal(1, "malformed packet did not produce exactly one bounded packet_error");
        end
    endtask

    task wait_for_output_idle;
        integer n;
        integer idle;
        begin
            n = 0;
            idle = 0;
            while ((idle < 5) && (n < MAX_READY_WAIT * 10)) begin
                @(posedge aclk);
                n = n + 1;
                if (!m_tvalid && s_tready)
                    idle = idle + 1;
                else
                    idle = 0;
            end
            if (idle < 5)
                $fatal(1, "output did not drain to a stable idle boundary");
        end
    endtask

    task run_reset_stress;
        integer valid_record;
        integer j;
        begin
            gap_mode = 0;
            force_ready_override = 1;
            ready_override = 1;
            // Reset while the parser has only accepted the first header word.
            send_record(32'h574f3134, 4'hf, 1'b0);
            reset_and_check();

            // Read the first complete input packet's header plus one payload
            // word, then reset from inside payload assembly.
            stress_fd = $fopen(input_name, "rb");
            if (stress_fd == 0) $fatal(1, "cannot open input for reset stress");
            for (j = 0; j < 5; j = j + 1) begin
                read_record(stress_fd, valid_record, stress_word, stress_mask, stress_eof);
                if (!valid_record) $fatal(1, "reset stress input has fewer than five records");
                stress_data[j] = stress_word;
                stress_keep[j] = stress_mask;
                stress_last[j] = stress_eof;
            end
            $fclose(stress_fd);
            for (j = 0; j < 5; j = j + 1)
                send_record(stress_data[j], stress_keep[j], stress_last[j]);
            reset_and_check();

            // Send the same partial packet while the output is held not-ready.
            // Reset must discard the stable stalled pixel without an output handshake.
            force_ready_override = 1;
            ready_override = 0;
            for (j = 0; j < 5; j = j + 1)
                send_record(stress_data[j], stress_keep[j], stress_last[j]);
            wait_cycles = 0;
            while (!m_tvalid && wait_cycles < 100) begin
                @(posedge aclk);
                wait_cycles = wait_cycles + 1;
            end
            if (!m_tvalid || m_tready)
                $fatal(1, "failed to create a stable stalled output for reset test");
            reset_and_check();
            force_ready_override = 0;
            ready_override = 0;
            gap_mode = 1;
            $display("RESET_STRESS PASS: header, payload, and stalled-output resets");
        end
    endtask

    // AXI4-Stream payload and markers must hold stable until a handshake.
    always @(posedge aclk) begin
        if (!aresetn) begin
            previous_output_stalled = 1'b0;
        end else begin
            if (previous_output_stalled &&
                (!m_tvalid || m_tdata !== held_output_data ||
                 m_tuser[0] !== held_output_user || m_tlast !== held_output_last))
                $fatal(1, "m_tvalid/data/user/last changed while the previous beat was stalled");
            if (m_tvalid && !m_tready) begin
                stalled_cycles = stalled_cycles + 1;
                previous_output_stalled = 1'b1;
                held_output_data = m_tdata;
                held_output_user = m_tuser[0];
                held_output_last = m_tlast;
            end else begin
                previous_output_stalled = 1'b0;
            end
        end
    end

    always @(posedge aclk) begin
        if (!aresetn) begin
            packet_done_count = 0;
            packet_error_count = 0;
            output_records = 0;
            expected_word_index = 0;
            recovery_score_started = 0;
            recovery_phase_active = 0;
            score_pixels = 0;
            score_sofs = 0;
            score_eols = 0;
            score_checksum = 32'd0;
        end else begin
            if (packet_error) begin
                packet_error_count = packet_error_count + 1;
                $display("PACKET_ERROR count=%0d code=%0d time=%0t",
                         packet_error_count, error_code, $time);
                if (negative_mode && error_code != expected_error)
                    $fatal(1, "error code %0d != expected %0d", error_code, expected_error);
            end
            if (packet_done) begin
                if (check_frame_id) begin
                    expected_id = negative_mode ? base_frame_id :
                                  (base_frame_id + packet_done_count);
                    if (frame_id != expected_id)
                        $fatal(1, "packet_done frame_id 0x%08x != expected 0x%08x",
                               frame_id, expected_id);
                end
                packet_done_count = packet_done_count + 1;
            end
            if (m_tvalid && m_tready) begin
                output_records = output_records + 1;
                output_word = {6'b0, m_tuser[0], m_tlast, m_tdata};
                // XSim may return a valid descriptor with the sign bit set.
                if ((actual_fd != -1) && (actual_fd != 0))
                    $fwrite(actual_fd, "%c%c", output_word[7:0], output_word[15:8]);

                do_score = !negative_mode;
                if (negative_mode && recovery_phase_active && !recovery_score_started &&
                    (packet_error_count > 0) && m_tuser[0]) begin
                    recovery_score_started = 1;
                    do_score = 1;
                end else if (negative_mode && recovery_phase_active && recovery_score_started) begin
                    do_score = 1;
                end

                if (do_score) begin
                    expected_low = $fgetc(expected_fd);
                    expected_high = $fgetc(expected_fd);
                    if ((expected_low == -1) || (expected_high == -1))
                        $fatal(1, "more scored output pixels than Python golden records");
                    expected_word = (expected_high << 8) | expected_low;
                    if (output_word !== expected_word)
                        $fatal(1, "pixel[%0d] output 0x%03x != Python 0x%03x",
                               expected_word_index, output_word, expected_word);
                    expected_word_index = expected_word_index + 1;

                    if (m_tuser[0]) begin
                        score_pixels = 1;
                        score_sofs = 1;
                        score_eols = m_tlast ? 1 : 0;
                        score_checksum = {24'd0, m_tdata};
                    end else begin
                        score_pixels = score_pixels + 1;
                        if (m_tlast) score_eols = score_eols + 1;
                        score_checksum = score_checksum + {24'd0, m_tdata};
                    end
                end
            end
        end
    end

    initial begin
        input_name = "";
        expected_name = "";
        actual_name = "";
        if (!$value$plusargs("INPUT=%s", input_name)) $fatal(1, "missing +INPUT=<5-byte-beat-file>");
        if (!$value$plusargs("EXPECTED=%s", expected_name)) $fatal(1, "missing +EXPECTED=<golden-file>");
        if (!$value$plusargs("ACTUAL=%s", actual_name)) $fatal(1, "missing +ACTUAL=<output-file>");
        plusarg_found = $value$plusargs("NEGATIVE=%d", negative_mode);
        plusarg_found = $value$plusargs("RESET_STRESS=%d", reset_stress);
        plusarg_found = $value$plusargs("READY_MODE=%d", ready_mode);
        plusarg_found = $value$plusargs("GAP_MODE=%d", gap_mode);
        plusarg_found = $value$plusargs("EXPECTED_ERROR=%d", expected_error);
        plusarg_found = $value$plusargs("FRAME_COUNT=%d", expected_frames);
        plusarg_found = $value$plusargs("BAD_RECORDS=%d", bad_records);
        plusarg_found = $value$plusargs("DRAIN_RECORDS=%d", drain_records);
        plusarg_found = $value$plusargs("RECOVERY_RECORDS=%d", recovery_records);
        plusarg_found = $value$plusargs("MAX_CYCLES=%d", max_cycles);
        plusarg_found = $value$plusargs("BASE_FRAME_ID=%d", base_frame_id);
        plusarg_found = $value$plusargs("CHECK_FRAME_ID=%d", check_frame_id);
        seed_arg = 32'h0014C0DE;
        plusarg_found = $value$plusargs("SEED=%d", seed_arg);
        if (seed_arg == 0) seed_arg = 1;
        input_lfsr = seed_arg;
        ready_lfsr = seed_arg ^ 32'hA5A55A5A;
        if (ready_lfsr == 0) ready_lfsr = 1;
        if ((negative_mode != 0) && (expected_error < 1 || expected_error > 5))
            $fatal(1, "negative mode requires EXPECTED_ERROR in [1,5]");
        if (negative_mode && (bad_records <= 0 || recovery_records <= 0 || drain_records < 0))
            $fatal(1, "negative mode requires positive BAD_RECORDS/RECOVERY_RECORDS and nonnegative DRAIN_RECORDS");
        if (ready_mode < 0 || ready_mode > 2 || gap_mode < 0 || gap_mode > 2)
            $fatal(1, "READY_MODE and GAP_MODE must be 0, 1, or 2");

        repeat (5) @(posedge aclk);
        @(negedge aclk);
        aresetn = 1'b1;
        if (reset_stress) run_reset_stress();

        input_fd = $fopen(input_name, "rb");
        expected_fd = $fopen(expected_name, "rb");
        actual_fd = $fopen(actual_name, "wb");
        if (input_fd == 0 || expected_fd == 0 || actual_fd == 0)
            $fatal(1, "failed to open one or more stream files");
        expected_words_total = expected_frames * PIXELS_PER_FRAME;
        $display("CONFIG geometry=%0dx%0d threshold=%0d negative=%0d frames=%0d ready_mode=%0d gap_mode=%0d seed=%0d",
                 WIDTH, HEIGHT, THRESHOLD, negative_mode, expected_frames,
                 ready_mode, gap_mode, seed_arg);

        if (negative_mode) begin
            send_record_count(input_fd, bad_records);
            wait_for_packet_error();
            if (drain_records > 0)
                send_record_count(input_fd, drain_records);
            // Do not let a delayed SOF from the malformed prefix be mistaken
            // for recovery. Drain its egress under the same randomized ready
            // pattern before enabling scoreboard comparison for the next frame.
            wait_for_output_idle();
            recovery_phase_active = 1;
            send_record_count(input_fd, recovery_records);
            read_record(input_fd, input_ok, stress_word, stress_mask, stress_eof);
            if (input_ok)
                $fatal(1, "negative case has records beyond declared recovery packet");
        end else begin
            read_record(input_fd, input_ok, stress_word, stress_mask, stress_eof);
            while (input_ok) begin
                send_record(stress_word, stress_mask, stress_eof);
                read_record(input_fd, input_ok, stress_word, stress_mask, stress_eof);
            end
        end
        $fclose(input_fd);
        input_fd = -1;
        $display("INPUT_SENT beats=%0d", input_records);

        drain_cycles = 0;
        while (((packet_done_count < (negative_mode ? 1 : expected_frames)) ||
                (expected_word_index < expected_words_total)) &&
               (drain_cycles < max_cycles)) begin
            @(posedge aclk);
            drain_cycles = drain_cycles + 1;
        end
        if (packet_done_count != (negative_mode ? 1 : expected_frames))
            $fatal(1, "packet_done count %0d != expected %0d",
                   packet_done_count, negative_mode ? 1 : expected_frames);
        if (negative_mode && packet_error_count != 1)
            $fatal(1, "packet_error count %0d != expected exactly one", packet_error_count);
        if (!negative_mode && packet_error_count != 0)
            $fatal(1, "valid packets raised %0d packet errors", packet_error_count);
        if (expected_word_index != expected_words_total)
            $fatal(1, "scored pixel count %0d != expected %0d",
                   expected_word_index, expected_words_total);
        if (drain_cycles >= max_cycles)
            $fatal(1, "bounded output drain timeout after %0d cycles", max_cycles);
        // Let the checker nonblocking assignments for the last accepted pixel settle.
        @(negedge aclk);
        if (score_pixels != PIXELS_PER_FRAME || score_sofs != 1 || score_eols != HEIGHT)
            $fatal(1, "software scoreboard metrics pixels=%0d SOF=%0d EOL=%0d",
                   score_pixels, score_sofs, score_eols);
        if (checker_error || observed_pixels != PIXELS_PER_FRAME ||
            observed_sofs != 1 || observed_eols != HEIGHT ||
            observed_checksum != score_checksum)
            $fatal(1, "hardware checker mismatch err=%0d pixels=%0d SOF=%0d EOL=%0d checksum=0x%08x expected=0x%08x",
                   checker_error, observed_pixels, observed_sofs, observed_eols,
                   observed_checksum, score_checksum);
        expected_low = $fgetc(expected_fd);
        if (expected_low != -1)
            $fatal(1, "Python golden contains trailing output records");
        $fclose(expected_fd);
        $fclose(actual_fd);
        actual_fd = -1;
        $display("RESULT PASS: packets_done=%0d packet_errors=%0d scored_pixels=%0d all_output_records=%0d stalled_cycles=%0d checker_pixels=%0d checker_sofs=%0d checker_eols=%0d checker_checksum=0x%08x drain_cycles=%0d",
                 packet_done_count, packet_error_count, expected_word_index,
                 output_records, stalled_cycles, observed_pixels, observed_sofs, observed_eols,
                 observed_checksum, drain_cycles);
        $finish;
    end

    initial begin : watchdog
        integer elapsed;
        #1;
        elapsed = 0;
        while (elapsed < max_cycles) begin
            @(posedge aclk);
            elapsed = elapsed + 1;
        end
        $fatal(1, "global testbench watchdog expired at %0d cycles", max_cycles);
    end
endmodule
