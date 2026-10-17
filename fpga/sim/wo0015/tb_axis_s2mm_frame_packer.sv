`timescale 1ns/1ps

// Small-geometry self-check for the production 8-to-32-bit packing rules.
// WIDTH*HEIGHT is 16 here, so each frame has exactly four little-endian words.
module tb_axis_s2mm_frame_packer;
    localparam integer WIDTH = 8;
    localparam integer HEIGHT = 2;
    localparam integer PIXELS = WIDTH * HEIGHT;
    localparam integer BEATS = PIXELS / 4;
    localparam integer WATCHDOG_CYCLES = 20000;

    reg aclk = 1'b0;
    reg aresetn = 1'b0;
    reg arm = 1'b0;
    reg [7:0] s_tdata = 8'h00;
    reg [0:0] s_tuser = 1'b0;
    reg s_tlast = 1'b0;
    reg s_tvalid = 1'b0;
    wire s_tready;
    reg packet_done = 1'b0;
    reg packet_error = 1'b0;
    wire [31:0] m_tdata;
    wire [3:0] m_tkeep;
    wire m_tlast;
    wire m_tvalid;
    reg m_tready = 1'b1;
    wire busy;
    wire done;
    wire fault;
    wire [7:0] error_code;
    wire [31:0] accepted_pixels;
    wire [31:0] sent_beats;

    integer frame_tag = 0;
    integer output_index = 0;
    integer watchdog_done = 0;
    reg check_output = 1'b0;
    reg was_stalled = 1'b0;
    reg [31:0] held_data = 0;
    reg [3:0] held_keep = 0;
    reg held_last = 0;
    reg held_valid = 0;

    axis_s2mm_frame_packer #(.WIDTH(WIDTH), .HEIGHT(HEIGHT)) dut (
        .aclk(aclk), .aresetn(aresetn), .arm(arm),
        .s_tdata(s_tdata), .s_tuser(s_tuser), .s_tlast(s_tlast),
        .s_tvalid(s_tvalid), .s_tready(s_tready),
        .packet_done(packet_done), .packet_error(packet_error),
        .m_tdata(m_tdata), .m_tkeep(m_tkeep), .m_tlast(m_tlast),
        .m_tvalid(m_tvalid), .m_tready(m_tready),
        .busy(busy), .done(done), .fault(fault), .error_code(error_code),
        .accepted_pixels(accepted_pixels), .sent_beats(sent_beats)
    );

    always #10 aclk = ~aclk;

    function [7:0] pixel_value;
        input integer index;
        input integer tag;
        begin
            pixel_value = ((index + tag) % 2) ? 8'hff : 8'h00;
        end
    endfunction

    function [31:0] expected_word;
        input integer beat_index;
        input integer tag;
        integer base;
        begin
            base = beat_index * 4;
            expected_word = {pixel_value(base+3, tag), pixel_value(base+2, tag),
                             pixel_value(base+1, tag), pixel_value(base, tag)};
        end
    endfunction

    always @(posedge aclk) begin
        if (!aresetn) begin
            was_stalled = 1'b0;
            held_valid = 1'b0;
        end else begin
            if (was_stalled &&
                (m_tvalid !== held_valid || m_tdata !== held_data ||
                 m_tkeep !== held_keep || m_tlast !== held_last))
                $fatal(1, "AXIS output changed while stalled");

            was_stalled = m_tvalid && !m_tready;
            held_valid = m_tvalid;
            held_data = m_tdata;
            held_keep = m_tkeep;
            held_last = m_tlast;

            if (m_tvalid && m_tready) begin
                if (!check_output)
                    $fatal(1, "unexpected output beat 0x%08x", m_tdata);
                if (output_index >= BEATS)
                    $fatal(1, "extra output beat after fixed frame length");
                if (m_tdata !== expected_word(output_index, frame_tag))
                    $fatal(1, "beat %0d data=0x%08x expected=0x%08x",
                           output_index, m_tdata, expected_word(output_index, frame_tag));
                if (m_tkeep !== 4'hf)
                    $fatal(1, "beat %0d TKEEP=%x expected=f", output_index, m_tkeep);
                if (m_tlast !== (output_index == BEATS-1))
                    $fatal(1, "beat %0d TLAST=%0d expected=%0d",
                           output_index, m_tlast, output_index == BEATS-1);
                output_index = output_index + 1;
            end
        end
    end

    task automatic arm_frame;
        begin
            @(negedge aclk);
            arm = 1'b1;
            @(negedge aclk);
            arm = 1'b0;
            repeat (2) @(posedge aclk);
            if (!busy || done || fault)
                $fatal(1, "arm did not enter the expected busy state");
        end
    endtask

    task automatic send_pixel;
        input integer index;
        input integer tag;
        integer cycles;
        reg accepted;
        begin
            @(negedge aclk);
            s_tdata = pixel_value(index, tag);
            s_tuser = (index == 0);
            s_tlast = ((index % WIDTH) == WIDTH-1);
            s_tvalid = 1'b1;
            accepted = 1'b0;
            cycles = 0;
            while (!accepted && cycles < WATCHDOG_CYCLES) begin
                @(posedge aclk);
                if (s_tready) accepted = 1'b1;
                cycles = cycles + 1;
            end
            if (!accepted)
                $fatal(1, "input pixel %0d did not handshake", index);
            @(negedge aclk);
            s_tvalid = 1'b0;
            s_tuser = 1'b0;
            s_tlast = 1'b0;
        end
    endtask

    task automatic send_frame;
        input integer tag;
        input integer first_pixel;
        input integer last_pixel;
        integer index;
        begin
            for (index = first_pixel; index < last_pixel; index = index + 1)
                send_pixel(index, tag);
        end
    endtask

    task automatic pulse_packet_done;
        begin
            @(negedge aclk);
            packet_done = 1'b1;
            @(negedge aclk);
            packet_done = 1'b0;
        end
    endtask

    task automatic check_frame_complete;
        input integer tag;
        integer cycles;
        begin
            cycles = 0;
            while ((!done || busy) && cycles < WATCHDOG_CYCLES) begin
                @(posedge aclk);
                cycles = cycles + 1;
            end
            if (!done || busy)
                $fatal(1, "frame %0d completion timeout", tag);
            if (fault || error_code != 0)
                $fatal(1, "frame %0d unexpectedly faulted code=%0d", tag, error_code);
            if (accepted_pixels != PIXELS || sent_beats != BEATS)
                $fatal(1, "frame %0d counters pixels=%0d beats=%0d expected=%0d/%0d",
                       tag, accepted_pixels, sent_beats, PIXELS, BEATS);
            if (output_index != BEATS)
                $fatal(1, "frame %0d observed %0d beats expected=%0d",
                       tag, output_index, BEATS);
        end
    endtask

    initial begin : watchdog
        repeat (WATCHDOG_CYCLES) @(posedge aclk);
        if (!watchdog_done)
            $fatal(1, "global watchdog expired");
    end

    initial begin
        repeat (5) @(posedge aclk);
        @(negedge aclk);
        aresetn = 1'b1;
        repeat (2) @(posedge aclk);

        // Frame 1: packet_done arrives before its pixels. Hold EOF under
        // backpressure and verify the complete final beat remains stable.
        frame_tag = 0;
        output_index = 0;
        check_output = 1'b1;
        $display("STAGE early packet_done before input");
        arm_frame();
        pulse_packet_done();
        send_frame(frame_tag, 0, PIXELS);
        @(negedge aclk);
        m_tready = 1'b0;
        wait (m_tvalid && m_tlast);
        repeat (5) begin
            @(posedge aclk);
            #1;
            if (!m_tvalid || !m_tlast || m_tkeep != 4'hf ||
                m_tdata !== expected_word(BEATS-1, frame_tag))
                $fatal(1, "stalled EOF beat did not remain valid and stable");
        end
        @(negedge aclk);
        m_tready = 1'b1;
        check_frame_complete(1);
        $display("STAGE early packet_done frame complete");

        // Frame 2: no reset between normal frames. packet_done is delayed
        // until all pixels have arrived; the final word must remain private.
        frame_tag = 1;
        output_index = 0;
        $display("STAGE late packet_done frame armed");
        arm_frame();
        send_frame(frame_tag, 0, PIXELS);
        repeat (5) begin
            @(posedge aclk);
            #1;
            if (m_tvalid)
                $fatal(1, "final word escaped before late packet_done commit");
        end
        // Remove READY before the commit pulse. Otherwise the one-cycle final
        // beat can handshake between packet_done and the later wait statement.
        @(negedge aclk);
        m_tready = 1'b0;
        pulse_packet_done();
        wait (m_tvalid && m_tlast);
        repeat (4) begin
            @(posedge aclk);
            #1;
            if (!m_tvalid || !m_tlast || m_tdata !== expected_word(BEATS-1, frame_tag))
                $fatal(1, "late-commit EOF changed under stall");
        end
        @(negedge aclk);
        m_tready = 1'b1;
        check_frame_complete(2);
        $display("STAGE late packet_done frame complete");

        // A packet validation error must latch a fault and must not turn a
        // partial frame into a successful EOF. Only reset clears the fault.
        frame_tag = 0;
        output_index = 0;
        $display("STAGE packet_error fault/reset check");
        arm_frame();
        send_frame(frame_tag, 0, 8);
        @(negedge aclk);
        packet_error = 1'b1;
        @(negedge aclk);
        packet_error = 1'b0;
        repeat (3) @(posedge aclk);
        if (!fault || error_code != 8'd2)
            $fatal(1, "packet_error did not latch the expected fault");
        if ((m_tvalid && m_tlast) || done)
            $fatal(1, "faulted partial frame reported successful EOF/done");
        @(negedge aclk);
        arm = 1'b1;
        @(negedge aclk);
        arm = 1'b0;
        repeat (2) @(posedge aclk);
        if (!fault)
            $fatal(1, "arm incorrectly cleared a latched fault");

        @(negedge aclk);
        aresetn = 1'b0;
        repeat (3) @(posedge aclk);
        if (fault || done || busy || accepted_pixels != 0 || sent_beats != 0)
            $fatal(1, "reset did not clear packer state");

        $display("WO0015_PACKER_PASS width=%0d height=%0d frames=2 early_done=1 late_done=1 stalled_eof=1 fault_reset=1",
                 WIDTH, HEIGHT);
        watchdog_done = 1;
        $finish;
    end
endmodule
