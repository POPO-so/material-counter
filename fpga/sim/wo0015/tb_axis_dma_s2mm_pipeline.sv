`timescale 1ns/1ps

// Full-chain offline self-check: Gray8 packet -> Binary8 -> S2MM packer.
// Input beats are 40-bit hex records {3'b0, TLAST, TKEEP, TDATA}; golden.hex
// contains one Binary8 byte per line. No AXI DMA IP or board is modeled here.
module tb_axis_dma_s2mm_pipeline #(
    parameter integer WIDTH = 320,
    parameter integer HEIGHT = 240,
    parameter integer THRESHOLD = 128
);
    localparam integer PIXELS_PER_FRAME = WIDTH * HEIGHT;
    localparam integer BEATS_PER_FRAME = PIXELS_PER_FRAME / 4;
    localparam integer MAX_INPUT_BEATS = 200000;
    localparam integer MAX_GOLDEN_PIXELS = PIXELS_PER_FRAME * 3;
    localparam integer MAX_WAIT_CYCLES = 5000000;
    localparam integer MAX_AXI_WAIT_CYCLES = 5000;
    localparam integer MODE_NORMAL = 0;
    localparam integer MODE_RANDOM = 1;
    localparam integer MODE_FAULT = 2;
    localparam integer MODE_RESET = 3;

    reg aclk = 1'b0;
    reg aresetn = 1'b0;
    reg [31:0] s_axi_awaddr = 0;
    reg s_axi_awvalid = 0;
    wire s_axi_awready;
    reg [31:0] s_axi_wdata = 0;
    reg [3:0] s_axi_wstrb = 0;
    reg s_axi_wvalid = 0;
    wire s_axi_wready;
    wire [1:0] s_axi_bresp;
    wire s_axi_bvalid;
    reg s_axi_bready = 0;
    reg [31:0] s_axi_araddr = 0;
    reg s_axi_arvalid = 0;
    wire s_axi_arready;
    wire [31:0] s_axi_rdata;
    wire [1:0] s_axi_rresp;
    wire s_axi_rvalid;
    reg s_axi_rready = 0;

    reg [31:0] s_axis_tdata = 0;
    reg [3:0] s_axis_tkeep = 0;
    reg s_axis_tlast = 0;
    reg s_axis_tvalid = 0;
    wire s_axis_tready;
    wire [31:0] m_axis_tdata;
    wire [3:0] m_axis_tkeep;
    wire m_axis_tlast;
    wire m_axis_tvalid;
    reg m_axis_tready = 0;

    reg [39:0] input_beats [0:MAX_INPUT_BEATS-1];
    reg [7:0] golden_pixels [0:MAX_GOLDEN_PIXELS-1];
    reg [8*4096-1:0] input_path = 0;
    reg [8*4096-1:0] golden_path = 0;
    reg [8*4096-1:0] actual_path = 0;
    reg [8*4096-1:0] metrics_path = 0;
    integer input_fd = 0;
    integer golden_fd = 0;
    integer actual_fd = 0;
    integer metrics_fd = 0;
    integer input_count = 0;
    integer golden_count = 0;
    integer frame_count = 1;
    integer mode = MODE_NORMAL;
    integer seed = 1;
    integer index;
    integer lane;
    integer frame_index;
    integer cycles;
    integer timeout_count;
    integer completed = 0;
    integer source_done = 0;
    integer abort_source = 0;
    integer eof_count = 0;
    integer output_beats = 0;
    integer output_pixels = 0;
    integer previous_input_stall = 0;
    integer previous_output_stall = 0;
    integer input_hold_seen = 0;
    integer output_hold_seen = 0;
    integer ready_stall_remaining = 0;
    integer last_forced_stall_beat = -1;
    integer forced_stall_length = 5;
    reg [31:0] held_input_data = 0;
    reg [3:0] held_input_keep = 0;
    reg held_input_last = 0;
    reg [31:0] held_output_data = 0;
    reg [3:0] held_output_keep = 0;
    reg held_output_last = 0;
    reg [31:0] input_rng = 32'h00000001;
    reg [31:0] ready_rng = 32'h00000001;
    reg [31:0] read_value = 0;
    reg [31:0] status_value = 0;
    reg [31:0] pixels_value = 0;
    reg [31:0] beats_value = 0;
    reg [31:0] error_value = 0;
    reg [31:0] legacy_value = 0;
    reg [31:0] frame_checksum = 0;
    reg [8*128-1:0] case_label = 0;

    axis_dma_gray_s2mm_pipeline_bd #(
        .WIDTH(WIDTH), .HEIGHT(HEIGHT), .THRESHOLD(THRESHOLD)
    ) dut (
        .aclk(aclk), .aresetn(aresetn),
        .s_axi_awaddr(s_axi_awaddr), .s_axi_awvalid(s_axi_awvalid),
        .s_axi_awready(s_axi_awready), .s_axi_wdata(s_axi_wdata),
        .s_axi_wstrb(s_axi_wstrb), .s_axi_wvalid(s_axi_wvalid),
        .s_axi_wready(s_axi_wready), .s_axi_bresp(s_axi_bresp),
        .s_axi_bvalid(s_axi_bvalid), .s_axi_bready(s_axi_bready),
        .s_axi_araddr(s_axi_araddr), .s_axi_arvalid(s_axi_arvalid),
        .s_axi_arready(s_axi_arready), .s_axi_rdata(s_axi_rdata),
        .s_axi_rresp(s_axi_rresp), .s_axi_rvalid(s_axi_rvalid),
        .s_axi_rready(s_axi_rready), .s_axis_tdata(s_axis_tdata),
        .s_axis_tkeep(s_axis_tkeep), .s_axis_tlast(s_axis_tlast),
        .s_axis_tvalid(s_axis_tvalid), .s_axis_tready(s_axis_tready),
        .m_axis_tdata(m_axis_tdata), .m_axis_tkeep(m_axis_tkeep),
        .m_axis_tlast(m_axis_tlast), .m_axis_tvalid(m_axis_tvalid),
        .m_axis_tready(m_axis_tready)
    );

    always #10 aclk = ~aclk;

    function [31:0] next_random;
        input [31:0] previous;
        reg [31:0] value;
        begin
            value = previous;
            value = value ^ (value << 13);
            value = value ^ (value >> 17);
            value = value ^ (value << 5);
            next_random = (value == 0) ? 32'h1 : value;
        end
    endfunction

    task automatic axi_write;
        input [31:0] address;
        input [31:0] data;
        integer accepted_aw;
        integer accepted_w;
        integer wait_cycles;
        begin
            s_axi_bready = 1'b0;
            @(negedge aclk);
            s_axi_awaddr = address;
            s_axi_awvalid = 1'b1;
            s_axi_wdata = data;
            s_axi_wstrb = 4'hf;
            s_axi_wvalid = 1'b1;
            accepted_aw = 0;
            accepted_w = 0;
            wait_cycles = 0;
            while ((!accepted_aw || !accepted_w) && wait_cycles < MAX_AXI_WAIT_CYCLES) begin
                @(posedge aclk);
                if (s_axi_awready) accepted_aw = 1;
                if (s_axi_wready) accepted_w = 1;
                wait_cycles = wait_cycles + 1;
            end
            if (!accepted_aw || !accepted_w)
                $fatal(1, "AXI write address/data timeout at 0x%08x", address);
            @(negedge aclk);
            s_axi_awvalid = 1'b0;
            s_axi_wvalid = 1'b0;
            wait_cycles = 0;
            while (!s_axi_bvalid && wait_cycles < MAX_AXI_WAIT_CYCLES) begin
                @(posedge aclk);
                wait_cycles = wait_cycles + 1;
            end
            if (!s_axi_bvalid || s_axi_bresp !== 2'b00)
                $fatal(1, "AXI write response error at 0x%08x resp=%0d", address, s_axi_bresp);
            @(negedge aclk);
            s_axi_bready = 1'b1;
            @(posedge aclk);
            @(negedge aclk);
            s_axi_bready = 1'b0;
        end
    endtask

    task automatic axi_read;
        input [31:0] address;
        output [31:0] data;
        integer accepted;
        integer wait_cycles;
        begin
            s_axi_rready = 1'b0;
            @(negedge aclk);
            s_axi_araddr = address;
            s_axi_arvalid = 1'b1;
            accepted = 0;
            wait_cycles = 0;
            while (!accepted && wait_cycles < MAX_AXI_WAIT_CYCLES) begin
                @(posedge aclk);
                if (s_axi_arready) accepted = 1;
                wait_cycles = wait_cycles + 1;
            end
            if (!accepted)
                $fatal(1, "AXI read address timeout at 0x%08x", address);
            @(negedge aclk);
            s_axi_arvalid = 1'b0;
            wait_cycles = 0;
            while (!s_axi_rvalid && wait_cycles < MAX_AXI_WAIT_CYCLES) begin
                @(posedge aclk);
                wait_cycles = wait_cycles + 1;
            end
            if (!s_axi_rvalid || s_axi_rresp !== 2'b00)
                $fatal(1, "AXI read response error at 0x%08x resp=%0d", address, s_axi_rresp);
            data = s_axi_rdata;
            @(negedge aclk);
            s_axi_rready = 1'b1;
            @(posedge aclk);
            @(negedge aclk);
            s_axi_rready = 1'b0;
        end
    endtask

    task automatic arm_frame;
        begin
            axi_write(32'h0000005c, 32'h00000001);
        end
    endtask

    task automatic send_all_inputs;
        integer beat_index;
        integer gap_cycles;
        integer wait_cycles;
        integer accepted;
        reg [39:0] record;
        begin
            for (beat_index = 0; beat_index < input_count && !abort_source; beat_index = beat_index + 1) begin
                record = input_beats[beat_index];
                if (mode == MODE_RANDOM) begin
                    input_rng = next_random(input_rng);
                    gap_cycles = input_rng[1:0];
                    repeat (gap_cycles) @(negedge aclk);
                end
                @(negedge aclk);
                s_axis_tdata = record[31:0];
                s_axis_tkeep = record[35:32];
                s_axis_tlast = record[36];
                s_axis_tvalid = 1'b1;
                accepted = 0;
                wait_cycles = 0;
                while (!accepted && !abort_source && wait_cycles < MAX_AXI_WAIT_CYCLES) begin
                    @(posedge aclk);
                    if (s_axis_tready) accepted = 1;
                    if (mode == MODE_FAULT && (dut.packer_fault || dut.pipe_packet_error))
                        abort_source = 1;
                    wait_cycles = wait_cycles + 1;
                end
                if (!accepted && !abort_source && wait_cycles >= MAX_AXI_WAIT_CYCLES)
                    $fatal(1, "input beat %0d handshake timeout", beat_index);
                @(negedge aclk);
                // Once presented, a beat rejected by the fault/stop path stays
                // asserted with unchanged payload through simulation end/reset.
                // Do not withdraw VALID to make the test terminate.
                if (accepted) s_axis_tvalid = 1'b0;
                if (abort_source) beat_index = input_count;
            end
            source_done = 1;
        end
    endtask

    always @(negedge aclk) begin
        if (!aresetn) begin
            m_axis_tready = 1'b0;
            ready_stall_remaining = 0;
            last_forced_stall_beat = -1;
        end else if (mode == MODE_NORMAL || mode == MODE_FAULT) begin
            m_axis_tready = 1'b1;
        end else if (mode == MODE_RESET) begin
            if (output_beats >= 4) m_axis_tready = 1'b0;
            else m_axis_tready = 1'b1;
        end else begin
            // Force a short stall at beat 3 and EOF, and a >one-row stall on
            // the beat containing the first EOL; other cycles use a seeded PRNG.
            if ((output_beats % BEATS_PER_FRAME == 3 ||
                 output_beats % BEATS_PER_FRAME == BEATS_PER_FRAME-1 ||
                 output_beats % BEATS_PER_FRAME == (WIDTH/4)-1) &&
                last_forced_stall_beat != output_beats) begin
                last_forced_stall_beat = output_beats;
                if (output_beats % BEATS_PER_FRAME == (WIDTH/4)-1)
                    ready_stall_remaining = WIDTH + 12;
                else
                    ready_stall_remaining = 5;
            end
            if (ready_stall_remaining > 0) begin
                m_axis_tready = 1'b0;
                ready_stall_remaining = ready_stall_remaining - 1;
            end else begin
                ready_rng = next_random(ready_rng);
                m_axis_tready = ready_rng[0] | ready_rng[2];
            end
        end
    end

    always @(posedge aclk) begin
        if (!aresetn) begin
            previous_input_stall = 0;
            previous_output_stall = 0;
            held_input_data = 0;
            held_input_keep = 0;
            held_input_last = 0;
            held_output_data = 0;
            held_output_keep = 0;
            held_output_last = 0;
        end else begin
            if (previous_input_stall &&
                (!s_axis_tvalid || s_axis_tdata !== held_input_data ||
                 s_axis_tkeep !== held_input_keep || s_axis_tlast !== held_input_last))
                $fatal(1, "input source changed payload while stalled");
            if (previous_output_stall &&
                (!m_axis_tvalid || m_axis_tdata !== held_output_data ||
                 m_axis_tkeep !== held_output_keep || m_axis_tlast !== held_output_last))
                $fatal(1, "S2MM output changed while stalled");
            previous_input_stall = s_axis_tvalid && !s_axis_tready;
            previous_output_stall = m_axis_tvalid && !m_axis_tready;
            if (previous_input_stall) input_hold_seen = input_hold_seen + 1;
            if (previous_output_stall) output_hold_seen = output_hold_seen + 1;
            held_input_data = s_axis_tdata;
            held_input_keep = s_axis_tkeep;
            held_input_last = s_axis_tlast;
            held_output_data = m_axis_tdata;
            held_output_keep = m_axis_tkeep;
            held_output_last = m_axis_tlast;

            if (m_axis_tvalid && m_axis_tready) begin
                if (m_axis_tkeep !== 4'hf)
                    $fatal(1, "output beat %0d TKEEP=%x expected=f", output_beats, m_axis_tkeep);
                if (mode == MODE_NORMAL || mode == MODE_RANDOM) begin
                    if (output_pixels + 4 > golden_count)
                        $fatal(1, "output exceeded golden byte count");
                    for (lane = 0; lane < 4; lane = lane + 1) begin
                        if (m_axis_tdata[lane*8 +: 8] !== golden_pixels[output_pixels + lane])
                            $fatal(1, "pixel %0d mismatch actual=%02x expected=%02x",
                                   output_pixels + lane, m_axis_tdata[lane*8 +: 8],
                                   golden_pixels[output_pixels + lane]);
                    end
                    if (m_axis_tlast !== ((output_beats % BEATS_PER_FRAME) == BEATS_PER_FRAME-1))
                        $fatal(1, "beat %0d TLAST=%0d expected=%0d",
                               output_beats, m_axis_tlast,
                               (output_beats % BEATS_PER_FRAME) == BEATS_PER_FRAME-1);
                    output_pixels = output_pixels + 4;
                end else if (m_axis_tlast) begin
                    $fatal(1, "abnormal case produced an EOF");
                end
                $fdisplay(actual_fd, "%08x %1x %1x", m_axis_tdata, m_axis_tkeep, m_axis_tlast);
                output_beats = output_beats + 1;
                if (m_axis_tlast) eof_count = eof_count + 1;
            end
        end
    end

    task automatic report_normal_frame;
        input integer completed_frame;
        integer lane_index;
        integer base;
        begin
            axi_read(32'h0000004c, status_value);
            axi_read(32'h00000050, pixels_value);
            axi_read(32'h00000054, beats_value);
            axi_read(32'h00000058, error_value);
            if (status_value[0] || !status_value[1] || status_value[2])
                $fatal(1, "frame %0d packer status=0x%08x", completed_frame, status_value);
            if (pixels_value != PIXELS_PER_FRAME || beats_value != BEATS_PER_FRAME || error_value != 0)
                $fatal(1, "frame %0d counters pixels=%0d beats=%0d err=%0d",
                       completed_frame, pixels_value, beats_value, error_value);

            // Spot-check legacy map semantics while the corresponding frame is current.
            axi_read(32'h00000008, legacy_value);
            if (!legacy_value[3] || legacy_value[2] || legacy_value[1])
                $fatal(1, "legacy status/error register 0x08=0x%08x", legacy_value);
            axi_read(32'h00000024, legacy_value);
            if (legacy_value != completed_frame + 1)
                $fatal(1, "legacy input-TLAST count=%0d expected=%0d",
                       legacy_value, completed_frame + 1);
            axi_read(32'h00000034, legacy_value);
            if (legacy_value != PIXELS_PER_FRAME)
                $fatal(1, "legacy observed-pixel count=%0d", legacy_value);
            axi_read(32'h00000038, legacy_value);
            if (legacy_value != 1)
                $fatal(1, "legacy SOF count=%0d", legacy_value);
            axi_read(32'h0000003c, legacy_value);
            if (legacy_value != HEIGHT)
                $fatal(1, "legacy EOL count=%0d expected=%0d", legacy_value, HEIGHT);
            base = completed_frame * PIXELS_PER_FRAME;
            frame_checksum = 0;
            for (lane_index = 0; lane_index < PIXELS_PER_FRAME; lane_index = lane_index + 1)
                frame_checksum = frame_checksum + golden_pixels[base + lane_index];
            axi_read(32'h00000040, legacy_value);
            if (legacy_value != frame_checksum)
                $fatal(1, "legacy checksum=0x%08x expected=0x%08x", legacy_value, frame_checksum);

            $fdisplay(metrics_fd, "FRAME\t%0d\t%0d\t%0d\t1\t0\t0",
                      completed_frame, pixels_value, beats_value);
            $display("FRAME_PASS index=%0d pixels=%0d beats=%0d eof=1 checksum=%0d",
                     completed_frame, pixels_value, beats_value, frame_checksum);
        end
    endtask

    task automatic poll_fault;
        output [31:0] status;
        integer poll;
        begin
            status = 0;
            // A short packet is not recognizable until its early TLAST reaches
            // the adapter (the current 115 KB stream can take ~58k cycles).
            for (poll = 0; poll < (MAX_WAIT_CYCLES/110) && !status[2]; poll = poll + 1) begin
                axi_read(32'h0000004c, status);
                if (!status[2]) repeat (100) @(posedge aclk);
            end
            if (!status[2])
                $fatal(1, "fault polling watchdog expired status=0x%08x", status);
        end
    endtask

    initial begin : watchdog
        repeat (MAX_WAIT_CYCLES) @(posedge aclk);
        if (!completed)
            $fatal(1, "global watchdog expired case=%0s mode=%0d input=%0d/%0d output=%0d eofs=%0d",
                   case_label, mode, source_done, input_count, output_beats, eof_count);
    end

    initial begin : test_sequence
        integer found;
        integer expected_eof_count;
        integer frame_done;
        integer setup_fd;
        integer cycle_count;
        reg [31:0] observed_status;
        reg [31:0] input_word;
        reg [31:0] temp_word;
        begin
            found = $value$plusargs("INPUT=%s", input_path);
            if (!found) $fatal(1, "missing +INPUT=<beats.hex>");
            found = $value$plusargs("GOLDEN=%s", golden_path);
            if (!found) $fatal(1, "missing +GOLDEN=<golden.hex>");
            found = $value$plusargs("ACTUAL=%s", actual_path);
            if (!found) $fatal(1, "missing +ACTUAL=<actual.hex>");
            found = $value$plusargs("METRICS=%s", metrics_path);
            if (!found) $fatal(1, "missing +METRICS=<metrics.tsv>");
            found = $value$plusargs("FRAMES=%d", frame_count);
            if (!found) $fatal(1, "missing +FRAMES=<count>");
            found = $value$plusargs("MODE=%d", mode);
            if (!found) mode = MODE_NORMAL;
            found = $value$plusargs("SEED=%d", seed);
            if (!found) seed = 1;
            found = $value$plusargs("CASE=%s", case_label);
            if (!found) case_label = "unnamed";
            input_rng = (seed == 0) ? 32'h1 : seed;
            ready_rng = (seed == 0) ? 32'h1 : seed ^ 32'h9e3779b9;

            input_fd = $fopen(input_path, "r");
            if (!input_fd) $fatal(1, "cannot open input vector %0s", input_path);
            while (!$feof(input_fd) && input_count < MAX_INPUT_BEATS) begin
                found = $fscanf(input_fd, "%h", input_beats[input_count]);
                if (found == 1) input_count = input_count + 1;
                else break;
            end
            $fclose(input_fd);
            if (input_count == 0 || input_count == MAX_INPUT_BEATS)
                $fatal(1, "invalid/oversized input vector count=%0d", input_count);

            golden_fd = $fopen(golden_path, "r");
            if (!golden_fd) $fatal(1, "cannot open golden vector %0s", golden_path);
            while (!$feof(golden_fd) && golden_count < MAX_GOLDEN_PIXELS) begin
                found = $fscanf(golden_fd, "%h", temp_word);
                if (found == 1) begin
                    golden_pixels[golden_count] = temp_word[7:0];
                    golden_count = golden_count + 1;
                end else break;
            end
            $fclose(golden_fd);
            if (mode == MODE_NORMAL || mode == MODE_RANDOM) begin
                if (frame_count < 1 || frame_count > 3 || golden_count != frame_count*PIXELS_PER_FRAME)
                    $fatal(1, "golden size=%0d frames=%0d expected=%0d",
                           golden_count, frame_count, frame_count*PIXELS_PER_FRAME);
            end else if (golden_count != PIXELS_PER_FRAME) begin
                $fatal(1, "abnormal-case golden size=%0d expected=%0d",
                       golden_count, PIXELS_PER_FRAME);
            end

            actual_fd = $fopen(actual_path, "w");
            metrics_fd = $fopen(metrics_path, "w");
            if (!actual_fd || !metrics_fd)
                $fatal(1, "cannot create actual trace or metrics file");
            $fdisplay(metrics_fd, "kind\tframe\taccepted_pixels\tsent_beats\teof\tfault\terror_code");

            repeat (5) @(posedge aclk);
            @(negedge aclk);
            aresetn = 1'b1;
            repeat (2) @(posedge aclk);
            axi_read(32'h00000004, read_value);
            if (read_value != 32'h47525938)
                $fatal(1, "legacy signature 0x04=0x%08x", read_value);
            axi_write(32'h00000000, 32'h00000001);
            axi_read(32'h00000000, read_value);
            if (read_value != 1)
                $fatal(1, "legacy drain control 0x00=%0d expected=1", read_value);
            axi_read(32'h00000060, read_value);
            if (read_value != 32'h574f3135)
                $fatal(1, "WO-0015 signature 0x60=0x%08x", read_value);

            if (mode == MODE_NORMAL || mode == MODE_RANDOM) begin
                expected_eof_count = frame_count;
                $display("STAGE normal start case=%0s frames=%0d seed=%0d beats=%0d",
                         case_label, frame_count, seed, input_count);
                arm_frame();
                fork
                    send_all_inputs();
                    begin : collect_frames
                        for (frame_done = 0; frame_done < frame_count; frame_done = frame_done + 1) begin
                            timeout_count = 0;
                            while (eof_count < frame_done + 1 && timeout_count < MAX_WAIT_CYCLES) begin
                                @(posedge aclk);
                                timeout_count = timeout_count + 1;
                            end
                            if (eof_count != frame_done + 1)
                                $fatal(1, "frame %0d EOF timeout", frame_done);
                            report_normal_frame(frame_done);
                            if (frame_done + 1 < frame_count) begin
                                $display("STAGE rearm frame=%0d input_valid_waiting=%0d", frame_done+1, s_axis_tvalid);
                                arm_frame();
                            end
                        end
                    end
                join
                if (!source_done || output_pixels != golden_count || eof_count != expected_eof_count)
                    $fatal(1, "normal totals source_done=%0d pixels=%0d/%0d eof=%0d/%0d",
                           source_done, output_pixels, golden_count, eof_count, expected_eof_count);
                $display("WO0015_PIPELINE_PASS case=%0s frames=%0d pixels=%0d beats=%0d eofs=%0d seed=%0d",
                         case_label, frame_count, output_pixels, output_beats, eof_count, seed);
            end else if (mode == MODE_FAULT) begin
                $display("STAGE malformed packet start case=%0s beats=%0d", case_label, input_count);
                arm_frame();
                fork
                    send_all_inputs();
                    begin : wait_fault
                        poll_fault(observed_status);
                    end
                join
                if (!observed_status[2] || eof_count != 0)
                    $fatal(1, "malformed packet did not fault-stop: status=0x%08x EOFs=%0d",
                           observed_status, eof_count);
                axi_read(32'h00000050, pixels_value);
                axi_read(32'h00000054, beats_value);
                axi_read(32'h00000058, error_value);
                if (error_value == 0)
                    $fatal(1, "malformed packet fault code is zero");
                $fdisplay(metrics_fd, "FAULT\t0\t%0d\t%0d\t0\t1\t%0d",
                          pixels_value, beats_value, error_value);
                $display("WO0015_FAULT_PASS case=%0s status=0x%08x accepted=%0d beats=%0d code=%0d eof=0",
                         case_label, observed_status, pixels_value, beats_value, error_value);
            end else if (mode == MODE_RESET) begin
                $display("STAGE abnormal reset start case=%0s", case_label);
                arm_frame();
                fork
                    send_all_inputs();
                    begin : reset_during_stall
                        wait (m_axis_tvalid && !m_axis_tready);
                        abort_source = 1;
                        @(negedge aclk);
                        aresetn = 1'b0;
                        repeat (3) @(posedge aclk);
                        @(negedge aclk);
                        aresetn = 1'b1;
                    end
                join
                repeat (3) @(posedge aclk);
                axi_read(32'h0000004c, status_value);
                axi_read(32'h00000050, pixels_value);
                axi_read(32'h00000054, beats_value);
                axi_read(32'h00000058, error_value);
                if (eof_count != 0 || status_value[2:0] != 0 ||
                    pixels_value != 0 || beats_value != 0 || error_value != 0)
                    $fatal(1, "abnormal reset did not clear without EOF: status=%08x pixels=%0d beats=%0d err=%0d eof=%0d",
                           status_value, pixels_value, beats_value, error_value, eof_count);
                $fdisplay(metrics_fd, "RESET\t0\t%0d\t%0d\t0\t0\t0",
                          pixels_value, beats_value);
                $display("WO0015_RESET_PASS case=%0s stalled_beats=%0d eof=0 state_cleared=1",
                         case_label, output_beats);
            end else begin
                $fatal(1, "unknown MODE=%0d", mode);
            end

            $fflush(actual_fd);
            $fflush(metrics_fd);
            $fclose(actual_fd);
            $fclose(metrics_fd);
            completed = 1;
            repeat (3) @(posedge aclk);
            $finish;
        end
    end
endmodule
