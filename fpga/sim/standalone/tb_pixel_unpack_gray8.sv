`timescale 1ns/1ps

module tb_pixel_unpack_gray8;
    reg ap_clk = 1'b0;
    always #5 ap_clk = ~ap_clk;

    reg ap_rst_n = 1'b0;

    reg  [31:0] stream_in_32_TDATA = 32'd0;
    reg         stream_in_32_TVALID = 1'b0;
    wire        stream_in_32_TREADY;
    reg  [3:0]  stream_in_32_TKEEP = 4'hf;
    reg  [3:0]  stream_in_32_TSTRB = 4'hf;
    reg         stream_in_32_TUSER = 1'b0;
    reg         stream_in_32_TLAST = 1'b0;

    wire [23:0] stream_out_24_TDATA;
    wire        stream_out_24_TVALID;
    reg         stream_out_24_TREADY = 1'b0;
    wire [2:0]  stream_out_24_TKEEP;
    wire [2:0]  stream_out_24_TSTRB;
    wire        stream_out_24_TUSER;
    wire        stream_out_24_TLAST;

    reg         s_axi_control_AWVALID = 1'b0;
    wire        s_axi_control_AWREADY;
    reg  [4:0]  s_axi_control_AWADDR = 5'd0;
    reg         s_axi_control_WVALID = 1'b0;
    wire        s_axi_control_WREADY;
    reg  [31:0] s_axi_control_WDATA = 32'd0;
    reg  [3:0]  s_axi_control_WSTRB = 4'hf;
    reg         s_axi_control_ARVALID = 1'b0;
    wire        s_axi_control_ARREADY;
    reg  [4:0]  s_axi_control_ARADDR = 5'd0;
    wire        s_axi_control_RVALID;
    reg         s_axi_control_RREADY = 1'b0;
    wire [31:0] s_axi_control_RDATA;
    wire [1:0]  s_axi_control_RRESP;
    wire        s_axi_control_BVALID;
    reg         s_axi_control_BREADY = 1'b0;
    wire [1:0]  s_axi_control_BRESP;

    localparam integer ROWS = 2;
    localparam integer PIXELS_PER_ROW = 16;
    localparam integer TOTAL_PIXELS = ROWS * PIXELS_PER_ROW;
    localparam integer TOTAL_BEATS = TOTAL_PIXELS / 4;
    localparam integer WATCHDOG_CYCLES = 20000;

    reg [7:0] expected_pixels [0:TOTAL_PIXELS-1];
    integer cycle_count = 0;
    integer output_count = 0;
    integer warmup_output_count = 0;
    integer accepted_gray8_beats = 0;
    integer stalled_output_cycles = 0;
    integer input_gap_cycles = 0;
    reg gray8_scoreboard_active = 1'b0;
    integer i;
    integer row;
    integer col;
    integer beat;
    integer wait_cycles;
    reg previous_input_valid = 1'b0;

    pixel_unpack dut (
        .ap_clk(ap_clk),
        .ap_rst_n(ap_rst_n),
        .stream_in_32_TDATA(stream_in_32_TDATA),
        .stream_in_32_TVALID(stream_in_32_TVALID),
        .stream_in_32_TREADY(stream_in_32_TREADY),
        .stream_in_32_TKEEP(stream_in_32_TKEEP),
        .stream_in_32_TSTRB(stream_in_32_TSTRB),
        .stream_in_32_TUSER(stream_in_32_TUSER),
        .stream_in_32_TLAST(stream_in_32_TLAST),
        .stream_out_24_TDATA(stream_out_24_TDATA),
        .stream_out_24_TVALID(stream_out_24_TVALID),
        .stream_out_24_TREADY(stream_out_24_TREADY),
        .stream_out_24_TKEEP(stream_out_24_TKEEP),
        .stream_out_24_TSTRB(stream_out_24_TSTRB),
        .stream_out_24_TUSER(stream_out_24_TUSER),
        .stream_out_24_TLAST(stream_out_24_TLAST),
        .s_axi_control_AWVALID(s_axi_control_AWVALID),
        .s_axi_control_AWREADY(s_axi_control_AWREADY),
        .s_axi_control_AWADDR(s_axi_control_AWADDR),
        .s_axi_control_WVALID(s_axi_control_WVALID),
        .s_axi_control_WREADY(s_axi_control_WREADY),
        .s_axi_control_WDATA(s_axi_control_WDATA),
        .s_axi_control_WSTRB(s_axi_control_WSTRB),
        .s_axi_control_ARVALID(s_axi_control_ARVALID),
        .s_axi_control_ARREADY(s_axi_control_ARREADY),
        .s_axi_control_ARADDR(s_axi_control_ARADDR),
        .s_axi_control_RVALID(s_axi_control_RVALID),
        .s_axi_control_RREADY(s_axi_control_RREADY),
        .s_axi_control_RDATA(s_axi_control_RDATA),
        .s_axi_control_RRESP(s_axi_control_RRESP),
        .s_axi_control_BVALID(s_axi_control_BVALID),
        .s_axi_control_BREADY(s_axi_control_BREADY),
        .s_axi_control_BRESP(s_axi_control_BRESP)
    );

    // Deterministic periodic stalls, including multi-cycle output backpressure.
    always @* begin
        stream_out_24_TREADY = ((cycle_count % 11) < 5) ||
                               ((cycle_count % 11) == 9);
    end

    always @(posedge ap_clk) begin
        if (!ap_rst_n) begin
            cycle_count <= 0;
            output_count <= 0;
            warmup_output_count <= 0;
            accepted_gray8_beats <= 0;
            stalled_output_cycles <= 0;
            input_gap_cycles <= 0;
            previous_input_valid <= 1'b0;
        end else begin
            cycle_count <= cycle_count + 1;
            if (gray8_scoreboard_active && stream_out_24_TVALID && !stream_out_24_TREADY)
                stalled_output_cycles <= stalled_output_cycles + 1;
            if (gray8_scoreboard_active && previous_input_valid && !stream_in_32_TVALID)
                input_gap_cycles <= input_gap_cycles + 1;
            previous_input_valid <= stream_in_32_TVALID;

            if (gray8_scoreboard_active && stream_in_32_TVALID && stream_in_32_TREADY)
                accepted_gray8_beats <= accepted_gray8_beats + 1;

            if (stream_out_24_TVALID && stream_out_24_TREADY) begin
                if (gray8_scoreboard_active) begin
                    if (output_count >= TOTAL_PIXELS)
                        $fatal(1, "unexpected extra output pixel at cycle %0d", cycle_count);
                    if (stream_out_24_TDATA[7:0] !== expected_pixels[output_count])
                        $fatal(1, "pixel %0d low byte mismatch: got %02x expected %02x",
                               output_count, stream_out_24_TDATA[7:0],
                               expected_pixels[output_count]);
                    if (stream_out_24_TDATA[23:8] !== 16'h0000)
                        $fatal(1, "pixel %0d upper bits not zero: %06x",
                               output_count, stream_out_24_TDATA);
                    if (stream_out_24_TKEEP !== 3'b111 || stream_out_24_TSTRB !== 3'b111)
                        $fatal(1, "pixel %0d unexpected TKEEP/TSTRB: %b/%b",
                               output_count, stream_out_24_TKEEP, stream_out_24_TSTRB);

                    row = output_count / PIXELS_PER_ROW;
                    col = output_count % PIXELS_PER_ROW;
                    if (stream_out_24_TUSER !== (col == 0))
                        $fatal(1, "pixel %0d TUSER mismatch: got %b expected %b",
                               output_count, stream_out_24_TUSER, (col == 0));
                    if (stream_out_24_TLAST !== (col == PIXELS_PER_ROW-1))
                        $fatal(1, "pixel %0d TLAST mismatch: got %b expected %b",
                               output_count, stream_out_24_TLAST,
                               (col == PIXELS_PER_ROW-1));
                    output_count <= output_count + 1;
                end else begin
                    warmup_output_count <= warmup_output_count + 1;
                    if (warmup_output_count >= 4)
                        $fatal(1, "unexpected extra warmup output");
                end
            end
        end
    end

    task automatic axi_write_mode_gray8;
        begin
            @(negedge ap_clk);
            s_axi_control_AWADDR = 5'h10;
            s_axi_control_AWVALID = 1'b1;
            do @(posedge ap_clk); while (!s_axi_control_AWREADY);
            @(negedge ap_clk);
            s_axi_control_AWVALID = 1'b0;
            s_axi_control_WDATA = 32'd2;
            s_axi_control_WSTRB = 4'hf;
            s_axi_control_WVALID = 1'b1;
            do @(posedge ap_clk); while (!s_axi_control_WREADY);
            @(negedge ap_clk);
            s_axi_control_WVALID = 1'b0;
            s_axi_control_BREADY = 1'b1;
            do @(posedge ap_clk); while (!s_axi_control_BVALID);
            @(negedge ap_clk);
            s_axi_control_BREADY = 1'b0;
        end
    endtask

    task automatic send_input_beat(input integer row_num, input integer beat_num);
        integer base;
        begin
            base = row_num * PIXELS_PER_ROW + beat_num * 4;
            @(negedge ap_clk);
            stream_in_32_TDATA = {expected_pixels[base+3], expected_pixels[base+2],
                                  expected_pixels[base+1], expected_pixels[base+0]};
            stream_in_32_TUSER = (beat_num == 0);
            stream_in_32_TLAST = (beat_num == (PIXELS_PER_ROW/4)-1);
            stream_in_32_TVALID = 1'b1;
            wait_cycles = 0;
            while (1) begin
                @(posedge ap_clk);
                if (stream_in_32_TREADY)
                    disable send_input_beat;
                wait_cycles = wait_cycles + 1;
                if (wait_cycles >= WATCHDOG_CYCLES)
                    $fatal(1, "watchdog waiting for input TREADY");
            end
        end
    endtask

    task automatic send_warmup_beat(input integer beat_num);
        begin
            @(negedge ap_clk);
            case (beat_num)
                0: stream_in_32_TDATA = 32'h11223344;
                1: stream_in_32_TDATA = 32'h55667788;
                default: stream_in_32_TDATA = 32'h99aabbcc;
            endcase
            stream_in_32_TUSER = (beat_num == 0);
            stream_in_32_TLAST = (beat_num == 2);
            stream_in_32_TVALID = 1'b1;
            wait_cycles = 0;
            while (1) begin
                @(posedge ap_clk);
                if (stream_in_32_TREADY)
                    disable send_warmup_beat;
                wait_cycles = wait_cycles + 1;
                if (wait_cycles >= WATCHDOG_CYCLES)
                    $fatal(1, "watchdog waiting for warmup input TREADY");
            end
        end
    endtask

    initial begin
        # (WATCHDOG_CYCLES * 10);
        $fatal(1, "PIXEL_UNPACK_GRAY8 watchdog expired");
    end

    initial begin
        for (i = 0; i < TOTAL_PIXELS; i = i + 1) begin
            row = i / PIXELS_PER_ROW;
            col = i % PIXELS_PER_ROW;
            expected_pixels[i] = (row * 53 + col * 37 + col * col * 3 + 11) & 8'hff;
        end
        expected_pixels[0] = 8'h00;
        expected_pixels[1] = 8'h80;
        expected_pixels[2] = 8'hff;
        expected_pixels[19] = 8'h00;
        expected_pixels[27] = 8'h80;
        expected_pixels[31] = 8'hff;

        repeat (5) @(posedge ap_clk);
        @(negedge ap_clk);
        ap_rst_n = 1'b1;
        axi_write_mode_gray8();

        // ap_ctrl_none starts in mode 0 and samples the live mode only when
        // its FSM returns to state1. Finish that initial frame before checking
        // the next frame, which will be sampled as Gray8 mode 2.
        for (beat = 0; beat < 3; beat = beat + 1) begin
            send_warmup_beat(beat);
            @(negedge ap_clk);
            stream_in_32_TVALID = 1'b0;
            stream_in_32_TUSER = 1'b0;
            stream_in_32_TLAST = 1'b0;
            repeat (1) @(negedge ap_clk);
        end

        wait_cycles = 0;
        while (warmup_output_count < 4) begin
            @(negedge ap_clk);
            wait_cycles = wait_cycles + 1;
            if (wait_cycles >= WATCHDOG_CYCLES)
                $fatal(1, "watchdog draining initial mode-0 frame: got %0d/4 outputs",
                       warmup_output_count);
        end
        repeat (10) @(posedge ap_clk);
        @(negedge ap_clk);
        gray8_scoreboard_active = 1'b1;

        for (beat = 0; beat < TOTAL_BEATS; beat = beat + 1) begin
            send_input_beat(beat / (PIXELS_PER_ROW/4), beat % (PIXELS_PER_ROW/4));
            @(negedge ap_clk);
            stream_in_32_TVALID = 1'b0;
            stream_in_32_TUSER = 1'b0;
            stream_in_32_TLAST = 1'b0;
            // A deterministic source bubble between accepted input beats.
            repeat ((beat % 3) + 1) @(negedge ap_clk);
        end

        @(negedge ap_clk);
        stream_in_32_TVALID = 1'b0;
        wait_cycles = 0;
        while (output_count < TOTAL_PIXELS) begin
            @(posedge ap_clk);
            wait_cycles = wait_cycles + 1;
            if (wait_cycles >= WATCHDOG_CYCLES)
                $fatal(1, "watchdog waiting for output pixels: got %0d/%0d",
                       output_count, TOTAL_PIXELS);
        end
        repeat (2) @(posedge ap_clk);

        if (accepted_gray8_beats != TOTAL_BEATS)
            $fatal(1, "accepted %0d Gray8 input beats, expected %0d",
                   accepted_gray8_beats, TOTAL_BEATS);
        if (stalled_output_cycles == 0)
            $fatal(1, "test did not exercise output backpressure");
        if (input_gap_cycles == 0)
            $fatal(1, "test did not exercise input gaps");

        $display("PIXEL_UNPACK_GRAY8_PASS");
        $finish;
    end
endmodule
