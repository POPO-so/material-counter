`timescale 1ns/1ps

// Focused WO-0015 guards: malformed Binary8 pixels must fault without EOF,
// and a rejected busy-frame ARM must leave the active wrapper transfer intact.
module tb_axis_s2mm_guards;
    localparam integer WIDTH = 8;
    localparam integer HEIGHT = 2;
    localparam integer PIXELS = WIDTH * HEIGHT;
    localparam integer FRAME_BEATS = PIXELS / 4;
    localparam [31:0] FRAME_ID = 32'h000015a5;

    reg aclk = 1'b0;
    reg aresetn = 1'b0;
    always #5 aclk = ~aclk;

    // Direct packer input used to reach its SOF/EOL/value fault branches.
    reg p_arm = 1'b0;
    reg [7:0] p_s_tdata = 8'd0;
    reg [0:0] p_s_tuser = 1'b0;
    reg p_s_tlast = 1'b0;
    reg p_s_tvalid = 1'b0;
    wire p_s_tready;
    reg p_packet_done = 1'b0;
    reg p_packet_error = 1'b0;
    wire [31:0] p_m_tdata;
    wire [3:0] p_m_tkeep;
    wire p_m_tlast;
    wire p_m_tvalid;
    reg p_m_tready = 1'b1;
    wire p_busy, p_done, p_fault;
    wire [7:0] p_error_code;
    wire [31:0] p_accepted_pixels, p_sent_beats;

    axis_s2mm_frame_packer #(.WIDTH(WIDTH), .HEIGHT(HEIGHT)) guard_packer (
        .aclk(aclk), .aresetn(aresetn), .arm(p_arm),
        .s_tdata(p_s_tdata), .s_tuser(p_s_tuser), .s_tlast(p_s_tlast),
        .s_tvalid(p_s_tvalid), .s_tready(p_s_tready),
        .packet_done(p_packet_done), .packet_error(p_packet_error),
        .m_tdata(p_m_tdata), .m_tkeep(p_m_tkeep), .m_tlast(p_m_tlast),
        .m_tvalid(p_m_tvalid), .m_tready(p_m_tready),
        .busy(p_busy), .done(p_done), .fault(p_fault),
        .error_code(p_error_code), .accepted_pixels(p_accepted_pixels),
        .sent_beats(p_sent_beats)
    );

    // Small-geometry wrapper exercised through the real WO-0014 adapter and
    // Checker path, with one valid packet left in flight during the bad ARM.
    reg [31:0] s_axi_awaddr = 0;
    reg s_axi_awvalid = 0;
    wire s_axi_awready;
    reg [31:0] s_axi_wdata = 0;
    reg [3:0] s_axi_wstrb = 4'hf;
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
    reg [3:0] s_axis_tkeep = 4'hf;
    reg s_axis_tlast = 0;
    reg s_axis_tvalid = 0;
    wire s_axis_tready;
    wire [31:0] m_axis_tdata;
    wire [3:0] m_axis_tkeep;
    wire m_axis_tlast;
    wire m_axis_tvalid;
    reg m_axis_tready = 0;

    axis_dma_s2mm_pipeline_bd #(
        .WIDTH(WIDTH), .HEIGHT(HEIGHT), .THRESHOLD(128)
    ) guard_wrapper (
        .aclk(aclk), .aresetn(aresetn),
        .s_axi_awaddr(s_axi_awaddr), .s_axi_awvalid(s_axi_awvalid),
        .s_axi_awready(s_axi_awready), .s_axi_wdata(s_axi_wdata),
        .s_axi_wstrb(s_axi_wstrb), .s_axi_wvalid(s_axi_wvalid),
        .s_axi_wready(s_axi_wready), .s_axi_bresp(s_axi_bresp),
        .s_axi_bvalid(s_axi_bvalid), .s_axi_bready(s_axi_bready),
        .s_axi_araddr(s_axi_araddr), .s_axi_arvalid(s_axi_arvalid),
        .s_axi_arready(s_axi_arready), .s_axi_rdata(s_axi_rdata),
        .s_axi_rresp(s_axi_rresp), .s_axi_rvalid(s_axi_rvalid),
        .s_axi_rready(s_axi_rready),
        .s_axis_tdata(s_axis_tdata), .s_axis_tkeep(s_axis_tkeep),
        .s_axis_tlast(s_axis_tlast), .s_axis_tvalid(s_axis_tvalid),
        .s_axis_tready(s_axis_tready), .m_axis_tdata(m_axis_tdata),
        .m_axis_tkeep(m_axis_tkeep), .m_axis_tlast(m_axis_tlast),
        .m_axis_tvalid(m_axis_tvalid), .m_axis_tready(m_axis_tready)
    );

    reg [31:0] packet [0:19];
    reg [7:0] expected_pixels [0:PIXELS-1];
    reg [31:0] expected_words [0:FRAME_BEATS-1];
    integer output_beats = 0;
    integer eof_handshakes = 0;
    integer previous_output_stall = 0;
    reg [31:0] held_output_data = 0;
    reg [3:0] held_output_keep = 0;
    reg held_output_last = 0;
    integer source_done = 0;
    integer completed = 0;

    always @(posedge aclk) begin
        if (!aresetn) begin
            output_beats = 0;
            eof_handshakes = 0;
            previous_output_stall = 0;
            held_output_data = 0;
            held_output_keep = 0;
            held_output_last = 0;
        end else begin
            if (previous_output_stall &&
                (!m_axis_tvalid || m_axis_tdata !== held_output_data ||
                 m_axis_tkeep !== held_output_keep ||
                 m_axis_tlast !== held_output_last))
                $fatal(1, "wrapper output changed while busy ARM was rejected");

            previous_output_stall = m_axis_tvalid && !m_axis_tready;
            held_output_data = m_axis_tdata;
            held_output_keep = m_axis_tkeep;
            held_output_last = m_axis_tlast;

            if (m_axis_tvalid && m_axis_tready) begin
                if (output_beats >= FRAME_BEATS)
                    $fatal(1, "unexpected extra S2MM beat %0d", output_beats);
                if (m_axis_tdata !== expected_words[output_beats] ||
                    m_axis_tkeep !== 4'hf ||
                    m_axis_tlast !== (output_beats == FRAME_BEATS-1))
                    $fatal(1, "S2MM beat %0d data=%08x keep=%x last=%b expected=%08x",
                           output_beats, m_axis_tdata, m_axis_tkeep, m_axis_tlast,
                           expected_words[output_beats]);
                if (m_axis_tlast) eof_handshakes = eof_handshakes + 1;
                output_beats = output_beats + 1;
            end
        end
    end

    task automatic reset_design;
        begin
            @(negedge aclk);
            aresetn = 1'b0;
            p_arm = 1'b0;
            p_s_tvalid = 1'b0;
            p_packet_done = 1'b0;
            p_packet_error = 1'b0;
            s_axi_awvalid = 1'b0;
            s_axi_wvalid = 1'b0;
            s_axi_bready = 1'b0;
            s_axi_arvalid = 1'b0;
            s_axi_rready = 1'b0;
            s_axis_tvalid = 1'b0;
            m_axis_tready = 1'b0;
            repeat (3) @(posedge aclk);
            @(negedge aclk);
            aresetn = 1'b1;
            repeat (2) @(posedge aclk);
        end
    endtask

    task automatic expect_pixel_fault;
        input [0:0] user_value;
        input last_value;
        input [7:0] pixel_value;
        input [7:0] expected_code;
        input integer case_id;
        integer accepted;
        integer timeout;
        begin
            @(negedge aclk);
            p_arm = 1'b1;
            @(posedge aclk);
            @(negedge aclk);
            p_arm = 1'b0;
            if (!p_busy || p_fault || p_done)
                $fatal(1, "packer did not arm for malformed-pixel case %0d", case_id);

            p_s_tdata = pixel_value;
            p_s_tuser = user_value;
            p_s_tlast = last_value;
            p_s_tvalid = 1'b1;
            accepted = 0;
            timeout = 0;
            while (!accepted && timeout < 20) begin
                @(posedge aclk);
                if (p_s_tready) accepted = 1;
                timeout = timeout + 1;
            end
            if (!accepted)
                $fatal(1, "packer input handshake timeout in malformed case %0d", case_id);
            @(negedge aclk);
            p_s_tvalid = 1'b0;
            repeat (2) @(posedge aclk);
            if (!p_fault || p_error_code !== expected_code || p_done ||
                p_m_tvalid || p_accepted_pixels != 0 || p_sent_beats != 0)
                $fatal(1, "malformed case %0d fault=%b code=%0d done=%b valid=%b pixels=%0d beats=%0d",
                       case_id, p_fault, p_error_code, p_done, p_m_tvalid,
                       p_accepted_pixels, p_sent_beats);
            $display("GUARD_PACKER_FAULT_PASS case=%0d code=%0d eof=0", case_id, expected_code);
        end
    endtask

    task automatic axi_write_check;
        input [31:0] address;
        input [31:0] data;
        output [1:0] response;
        integer accepted_aw;
        integer accepted_w;
        integer timeout;
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
            timeout = 0;
            while ((!accepted_aw || !accepted_w) && timeout < 100) begin
                @(posedge aclk);
                if (s_axi_awready) accepted_aw = 1;
                if (s_axi_wready) accepted_w = 1;
                timeout = timeout + 1;
            end
            if (!accepted_aw || !accepted_w)
                $fatal(1, "AXI write handshake timeout addr=%08x", address);
            @(negedge aclk);
            s_axi_awvalid = 1'b0;
            s_axi_wvalid = 1'b0;
            timeout = 0;
            while (!s_axi_bvalid && timeout < 100) begin
                @(posedge aclk);
                timeout = timeout + 1;
            end
            if (!s_axi_bvalid)
                $fatal(1, "AXI B response timeout addr=%08x", address);
            response = s_axi_bresp;
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
        integer timeout;
        begin
            s_axi_rready = 1'b0;
            @(negedge aclk);
            s_axi_araddr = address;
            s_axi_arvalid = 1'b1;
            accepted = 0;
            timeout = 0;
            while (!accepted && timeout < 100) begin
                @(posedge aclk);
                if (s_axi_arready) accepted = 1;
                timeout = timeout + 1;
            end
            if (!accepted)
                $fatal(1, "AXI read address timeout addr=%08x", address);
            @(negedge aclk);
            s_axi_arvalid = 1'b0;
            timeout = 0;
            while (!s_axi_rvalid && timeout < 100) begin
                @(posedge aclk);
                timeout = timeout + 1;
            end
            if (!s_axi_rvalid || s_axi_rresp !== 2'b00)
                $fatal(1, "AXI read response error addr=%08x resp=%0d", address, s_axi_rresp);
            data = s_axi_rdata;
            @(negedge aclk);
            s_axi_rready = 1'b1;
            @(posedge aclk);
            @(negedge aclk);
            s_axi_rready = 1'b0;
        end
    endtask

    task automatic build_packet;
        integer i;
        integer channel;
        integer byte_index;
        integer checksum;
        integer word_index;
        reg [7:0] pixel_value;
        begin
            for (i = 0; i < 20; i = i + 1) packet[i] = 32'd0;
            packet[0] = 32'h574f3134;
            packet[1] = (HEIGHT << 16) | WIDTH;
            packet[2] = PIXELS * 3;
            packet[3] = FRAME_ID;
            checksum = 0;
            for (i = 0; i < PIXELS; i = i + 1) begin
                pixel_value = ((i % 3) == 0) ? 8'hff : 8'h00;
                expected_pixels[i] = pixel_value;
                checksum = checksum + pixel_value * 3;
                for (channel = 0; channel < 3; channel = channel + 1) begin
                    byte_index = i * 3 + channel;
                    word_index = byte_index / 4;
                    packet[4 + word_index] = packet[4 + word_index] |
                        ({24'd0,pixel_value} << ((byte_index % 4) * 8));
                end
            end
            for (i = 0; i < FRAME_BEATS; i = i + 1)
                expected_words[i] = {expected_pixels[i*4+3], expected_pixels[i*4+2],
                                     expected_pixels[i*4+1], expected_pixels[i*4]};
            packet[16] = 32'h454e4434;
            packet[17] = FRAME_ID;
            packet[18] = checksum;
            packet[19] = 32'hc0dec014;
        end
    endtask

    task automatic send_packet;
        integer index;
        integer accepted;
        integer timeout;
        begin
            source_done = 0;
            for (index = 0; index < 20; index = index + 1) begin
                @(negedge aclk);
                s_axis_tdata = packet[index];
                s_axis_tkeep = 4'hf;
                s_axis_tlast = (index == 19);
                s_axis_tvalid = 1'b1;
                accepted = 0;
                timeout = 0;
                while (!accepted && timeout < 10000) begin
                    @(posedge aclk);
                    if (s_axis_tready) accepted = 1;
                    timeout = timeout + 1;
                end
                if (!accepted)
                    $fatal(1, "wrapper source beat %0d timed out", index);
                @(negedge aclk);
                s_axis_tvalid = 1'b0;
            end
            source_done = 1;
        end
    endtask

    initial begin : watchdog
        repeat (200000) @(posedge aclk);
        if (!completed)
            $fatal(1, "global guard watchdog expired output_beats=%0d source_done=%0d",
                   output_beats, source_done);
    end

    initial begin : run_tests
        reg [1:0] response;
        reg [31:0] status_value;
        integer timeout;
        reg [31:0] stalled_data;
        reg [3:0] stalled_keep;
        reg stalled_last;
        begin
            p_s_tdata = 0;
            p_s_tuser = 0;
            p_s_tlast = 0;
            p_s_tvalid = 0;
            p_arm = 0;
            reset_design();

            $display("STAGE direct packer bad SOF");
            expect_pixel_fault(1'b0, 1'b0, 8'h00, 8'd3, 1);
            reset_design();
            $display("STAGE direct packer bad EOL");
            expect_pixel_fault(1'b1, 1'b1, 8'h00, 8'd4, 2);
            reset_design();
            $display("STAGE direct packer non-binary value");
            expect_pixel_fault(1'b1, 1'b0, 8'h7f, 8'd5, 3);
            reset_design();

            build_packet();
            $display("STAGE wrapper arm and start valid packet");
            axi_write_check(32'h00000000, 32'h00000001, response);
            if (response !== 2'b00)
                $fatal(1, "legacy drain-enable write BRESP=%0d", response);
            axi_write_check(32'h0000005c, 32'h00000001, response);
            if (response !== 2'b00)
                $fatal(1, "initial ARM BRESP=%0d", response);

            fork
                send_packet();
            join_none

            // The S2MM master is deliberately stalled so the busy ARM is
            // issued with a live, stable output beat already on the interface.
            wait (m_axis_tvalid && !m_axis_tready);
            stalled_data = m_axis_tdata;
            stalled_keep = m_axis_tkeep;
            stalled_last = m_axis_tlast;
            $display("STAGE wrapper busy ARM during output stall");
            axi_write_check(32'h0000005c, 32'h00000001, response);
            if (response !== 2'b10)
                $fatal(1, "busy ARM must return SLVERR, got BRESP=%0d", response);
            axi_read(32'h0000004c, status_value);
            if (status_value[2:0] !== 3'b001)
                $fatal(1, "busy ARM changed packer state: status=0x%08x", status_value);
            if (!m_axis_tvalid || m_axis_tdata !== stalled_data ||
                m_axis_tkeep !== stalled_keep || m_axis_tlast !== stalled_last)
                $fatal(1, "busy ARM changed the stalled output beat");
            if (output_beats != 0)
                $fatal(1, "stalled output unexpectedly handshook before release");

            @(negedge aclk);
            m_axis_tready = 1'b1;
            wait (source_done);
            timeout = 0;
            while (output_beats < FRAME_BEATS && timeout < 10000) begin
                @(posedge aclk);
                timeout = timeout + 1;
            end
            if (output_beats != FRAME_BEATS || eof_handshakes != 1)
                $fatal(1, "valid frame damaged after busy ARM beats=%0d EOF=%0d",
                       output_beats, eof_handshakes);
            repeat (4) @(posedge aclk);
            if (m_axis_tvalid)
                $fatal(1, "wrapper emitted a beat after the successful EOF");

            axi_read(32'h0000004c, status_value);
            if (status_value[2:0] !== 3'b010)
                $fatal(1, "wrapper did not finish normally: status=0x%08x", status_value);
            axi_read(32'h00000050, status_value);
            if (status_value != PIXELS)
                $fatal(1, "accepted_pixels=%0d expected=%0d", status_value, PIXELS);
            axi_read(32'h00000054, status_value);
            if (status_value != FRAME_BEATS)
                $fatal(1, "sent_beats=%0d expected=%0d", status_value, FRAME_BEATS);
            axi_read(32'h00000058, status_value);
            if (status_value != 0)
                $fatal(1, "unexpected packer error code=%0d", status_value);

            // Spot-check preserved Checker semantics on the accepted frame.
            axi_read(32'h00000008, status_value);
            if (status_value[3:0] !== 4'b1001)
                $fatal(1, "legacy Checker status=0x%08x", status_value);
            axi_read(32'h00000024, status_value);
            if (status_value != 1)
                $fatal(1, "legacy input TLAST count=%0d", status_value);
            axi_read(32'h00000034, status_value);
            if (status_value != PIXELS)
                $fatal(1, "legacy observed pixels=%0d", status_value);
            axi_read(32'h0000003c, status_value);
            if (status_value != HEIGHT)
                $fatal(1, "legacy observed EOLs=%0d", status_value);

            completed = 1;
            $display("WO0015_GUARDS_PASS bad_sof=1 bad_eol=1 bad_value=1 busy_arm_slverr=1 transfer_preserved=1");
            $finish;
        end
    end
endmodule
