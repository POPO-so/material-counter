`timescale 1ns/1ps

// Offline self-check for the AXI-Lite wrapper. The default 5x3 geometry
// exercises the Gray8 tail TKEEP path and packet recovery without external files.
// For the 320x240 replay, elaborate with WIDTH=320 / HEIGHT=240 and pass the
// packed Gray8 five-byte beat-record stream and Binary8 Golden via +INPUT/+EXPECTED.
module tb_axis_dma_image_pipeline_bd #(
    parameter integer WIDTH = 5,
    parameter integer HEIGHT = 3,
    parameter integer THRESHOLD = 128
);
    localparam integer PIXELS = WIDTH * HEIGHT;
    localparam integer PAYLOAD_BYTES = PIXELS;
    localparam integer MAX_WAIT_CYCLES = (WIDTH * HEIGHT * 6) + 100000;
    localparam integer MAX_AXI_WAIT_CYCLES = 1000;

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

    reg [7:0] expected_binary [0:PIXELS-1];
    reg [31:0] expected_input_bytes = 0;
    reg [31:0] expected_input_checksum = 0;
    integer expected_input_beats = 0;
    integer expected_input_tlast = 0;
    integer expected_frames = 2;
    integer expected_output_records = 0;
    integer output_records = 0;
    integer output_sofs = 0;
    integer output_pixel_index = -1;
    reg [31:0] software_output_checksum = 0;
    reg [31:0] golden_final_checksum = 0;
    integer vector_mode = 0;
    integer input_fd = 0;
    integer expected_fd = 0;
    integer plusarg_found;
    integer byte0, byte1, byte2, byte3, ctl;
    integer score_byte0, score_byte1;
    integer tail_byte;
    integer record_valid;
    integer loops;
    integer index;
    integer rd_resp_expect;
    reg [8*1024-1:0] input_path;
    reg [8*1024-1:0] expected_path;
    reg [31:0] read_value;
    reg [1:0] read_resp;
    reg [15:0] expected_word;
    reg [15:0] actual_word;
    reg previous_output_stalled = 0;
    reg [7:0] held_output_data = 0;
    reg held_output_sof = 0;
    reg held_output_eol = 0;
    reg previous_input_stalled = 0;
    reg [31:0] held_input_data = 0;
    reg [3:0] held_input_keep = 0;
    reg held_input_last = 0;

    axis_dma_image_pipeline_bd #(
        .WIDTH(WIDTH),
        .HEIGHT(HEIGHT),
        .THRESHOLD(THRESHOLD)
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
        .s_axis_tvalid(s_axis_tvalid), .s_axis_tready(s_axis_tready)
    );

    always #5 aclk = ~aclk;

    function [7:0] gray_pixel;
        input integer pixel;
        begin
            case (pixel % 5)
                0: gray_pixel = 8'hff;
                1: gray_pixel = 8'h7f;
                2: gray_pixel = 8'h80;
                3: gray_pixel = 8'hff;
                default: gray_pixel = 8'h00;
            endcase
        end
    endfunction

    task automatic axi_write;
        input [31:0] address;
        input [31:0] data;
        input integer order;
        input integer hold_cycles;
        input [1:0] expected_resp;
        integer accepted;
        integer wait_cycles;
        begin
            s_axi_bready = 1'b0;
            if (order == 0) begin
                @(negedge aclk);
                s_axi_awaddr = address;
                s_axi_awvalid = 1'b1;
                accepted = 0;
                wait_cycles = 0;
                while (!accepted && wait_cycles < MAX_AXI_WAIT_CYCLES) begin
                    @(posedge aclk);
                    if (s_axi_awready) accepted = 1;
                    wait_cycles = wait_cycles + 1;
                end
                if (!accepted) $fatal(1, "AW handshake timeout at 0x%08x", address);
                @(negedge aclk);
                s_axi_awvalid = 1'b0;
                repeat (2) @(posedge aclk);
                @(negedge aclk);
                s_axi_wdata = data;
                s_axi_wstrb = 4'hf;
                s_axi_wvalid = 1'b1;
                accepted = 0;
                wait_cycles = 0;
                while (!accepted && wait_cycles < MAX_AXI_WAIT_CYCLES) begin
                    @(posedge aclk);
                    if (s_axi_wready) accepted = 1;
                    wait_cycles = wait_cycles + 1;
                end
                if (!accepted) $fatal(1, "W handshake timeout at 0x%08x", address);
                @(negedge aclk);
                s_axi_wvalid = 1'b0;
            end else if (order == 1) begin
                @(negedge aclk);
                s_axi_wdata = data;
                s_axi_wstrb = 4'hf;
                s_axi_wvalid = 1'b1;
                accepted = 0;
                wait_cycles = 0;
                while (!accepted && wait_cycles < MAX_AXI_WAIT_CYCLES) begin
                    @(posedge aclk);
                    if (s_axi_wready) accepted = 1;
                    wait_cycles = wait_cycles + 1;
                end
                if (!accepted) $fatal(1, "W handshake timeout at 0x%08x", address);
                @(negedge aclk);
                s_axi_wvalid = 1'b0;
                repeat (2) @(posedge aclk);
                @(negedge aclk);
                s_axi_awaddr = address;
                s_axi_awvalid = 1'b1;
                accepted = 0;
                wait_cycles = 0;
                while (!accepted && wait_cycles < MAX_AXI_WAIT_CYCLES) begin
                    @(posedge aclk);
                    if (s_axi_awready) accepted = 1;
                    wait_cycles = wait_cycles + 1;
                end
                if (!accepted) $fatal(1, "AW handshake timeout at 0x%08x", address);
                @(negedge aclk);
                s_axi_awvalid = 1'b0;
            end else begin
                @(negedge aclk);
                s_axi_awaddr = address;
                s_axi_awvalid = 1'b1;
                s_axi_wdata = data;
                s_axi_wstrb = 4'hf;
                s_axi_wvalid = 1'b1;
                accepted = 0;
                wait_cycles = 0;
                while (!accepted && wait_cycles < MAX_AXI_WAIT_CYCLES) begin
                    @(posedge aclk);
                    if (s_axi_awready && s_axi_wready) accepted = 1;
                    wait_cycles = wait_cycles + 1;
                end
                if (!accepted) $fatal(1, "simultaneous AW/W handshake timeout at 0x%08x", address);
                @(negedge aclk);
                s_axi_awvalid = 1'b0;
                s_axi_wvalid = 1'b0;
            end
            wait (s_axi_bvalid === 1'b1);
            if (s_axi_bresp !== expected_resp)
                $fatal(1, "write 0x%02x BRESP=%0d expected=%0d",
                       address, s_axi_bresp, expected_resp);
            repeat (hold_cycles) begin
                @(posedge aclk);
                #1;
                if (!s_axi_bvalid || s_axi_bresp !== expected_resp)
                    $fatal(1, "B response changed while BREADY was low");
            end
            @(negedge aclk);
            s_axi_bready = 1'b1;
            @(posedge aclk);
            @(negedge aclk);
            s_axi_bready = 1'b0;
        end
    endtask

    task automatic axi_read;
        input [31:0] address;
        input integer hold_cycles;
        output [31:0] data;
        output [1:0] response;
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
            if (!accepted) $fatal(1, "AR handshake timeout at 0x%08x", address);
            @(negedge aclk);
            s_axi_arvalid = 1'b0;
            wait (s_axi_rvalid === 1'b1);
            data = s_axi_rdata;
            response = s_axi_rresp;
            repeat (hold_cycles) begin
                @(posedge aclk);
                #1;
                if (!s_axi_rvalid || s_axi_rdata !== data || s_axi_rresp !== response)
                    $fatal(1, "R response changed while RREADY was low");
            end
            @(negedge aclk);
            s_axi_rready = 1'b1;
            @(posedge aclk);
            @(negedge aclk);
            s_axi_rready = 1'b0;
        end
    endtask

    task automatic check_read;
        input [31:0] address;
        input [31:0] expected;
        input [1:0] expected_response;
        input integer hold_cycles;
        reg [31:0] value;
        reg [1:0] response;
        begin
            axi_read(address, hold_cycles, value, response);
            if (value !== expected || response !== expected_response)
                $fatal(1, "read 0x%02x = 0x%08x resp=%0d, expected 0x%08x resp=%0d",
                       address, value, response, expected, expected_response);
        end
    endtask

    task automatic send_beat;
        input [31:0] data;
        input [3:0] keep;
        input last;
        integer accepted;
        integer cycles;
        begin
            @(negedge aclk);
            s_axis_tdata = data;
            s_axis_tkeep = keep;
            s_axis_tlast = last;
            s_axis_tvalid = 1'b1;
            accepted = 0;
            cycles = 0;
            while (!accepted && cycles < 100000) begin
                @(posedge aclk);
                if (s_axis_tready) accepted = 1;
                cycles = cycles + 1;
            end
            if (!accepted)
                $fatal(1, "input beat timed out: data=0x%08x keep=%x last=%0d",
                       data, keep, last);
            expected_input_beats = expected_input_beats + 1;
            expected_input_bytes = expected_input_bytes + keep[0] + keep[1] + keep[2] + keep[3];
            expected_input_checksum = expected_input_checksum +
                (keep[0] ? {24'd0,data[7:0]} : 32'd0) +
                (keep[1] ? {24'd0,data[15:8]} : 32'd0) +
                (keep[2] ? {24'd0,data[23:16]} : 32'd0) +
                (keep[3] ? {24'd0,data[31:24]} : 32'd0);
            if (last) expected_input_tlast = expected_input_tlast + 1;
            @(negedge aclk);
            s_axis_tvalid = 1'b0;
        end
    endtask

    task automatic send_valid_frame;
        input [31:0] frame_id_value;
        integer pos;
        integer lane;
        integer remaining;
        reg [31:0] word;
        reg [3:0] keep;
        reg [31:0] payload_sum;
        reg [7:0] byte_value;
        begin
            payload_sum = 0;
            for (pos = 0; pos < PIXELS; pos = pos + 1)
                payload_sum = payload_sum + gray_pixel(pos);
            send_beat(32'h574f3134, 4'hf, 1'b0);
            send_beat((HEIGHT << 16) | WIDTH, 4'hf, 1'b0);
            send_beat(PAYLOAD_BYTES, 4'hf, 1'b0);
            send_beat(frame_id_value, 4'hf, 1'b0);
            for (pos = 0; pos < PAYLOAD_BYTES; pos = pos + 4) begin
                word = 0;
                remaining = PAYLOAD_BYTES - pos;
                case (remaining)
                    1: keep = 4'h1;
                    2: keep = 4'h3;
                    3: keep = 4'h7;
                    default: keep = 4'hf;
                endcase
                for (lane = 0; lane < 4; lane = lane + 1) begin
                    if (pos + lane < PAYLOAD_BYTES) begin
                        byte_value = gray_pixel(pos + lane);
                        word[lane*8 +: 8] = byte_value;
                    end
                end
                send_beat(word, keep, 1'b0);
            end
            send_beat(32'h454e4434, 4'hf, 1'b0);
            send_beat(frame_id_value, 4'hf, 1'b0);
            send_beat(payload_sum, 4'hf, 1'b0);
            send_beat(32'hc0dec014, 4'hf, 1'b1);
        end
    endtask

    task automatic read_vector_record;
        output integer valid_record;
        output reg [31:0] data;
        output reg [3:0] keep;
        output reg last;
        integer rec_b0, rec_b1, rec_b2, rec_b3, rec_ctl;
        begin
            valid_record = 0;
            data = 0;
            keep = 0;
            last = 0;
            rec_b0 = $fgetc(input_fd);
            if (rec_b0 != -1) begin
                rec_b1 = $fgetc(input_fd);
                rec_b2 = $fgetc(input_fd);
                rec_b3 = $fgetc(input_fd);
                rec_ctl = $fgetc(input_fd);
                if (rec_b1 == -1 || rec_b2 == -1 || rec_b3 == -1 || rec_ctl == -1)
                    $fatal(1, "truncated five-byte input vector record");
                if ((rec_ctl & 8'he0) != 0)
                    $fatal(1, "reserved vector control bits are nonzero");
                data = (rec_b3 << 24) | (rec_b2 << 16) | (rec_b1 << 8) | rec_b0;
                keep = rec_ctl[3:0];
                last = rec_ctl[4];
                valid_record = 1;
            end
        end
    endtask

    task automatic send_vector_file;
        reg [31:0] data;
        reg [3:0] keep;
        reg last;
        integer records;
        begin
            input_fd = $fopen(input_path, "rb");
            if (input_fd == 0) $fatal(1, "failed to open +INPUT vector file");
            records = 0;
            read_vector_record(record_valid, data, keep, last);
            while (record_valid) begin
                send_beat(data, keep, last);
                records = records + 1;
                read_vector_record(record_valid, data, keep, last);
            end
            $fclose(input_fd);
            input_fd = 0;
            $display("VECTOR INPUT COMPLETE: %0d five-byte records", records);
        end
    endtask

    task automatic wait_for_stall_and_release;
        integer cycles;
        reg [7:0] held_data;
        reg held_sof, held_eol;
        reg [31:0] stalled_cycles;
        begin
            cycles = 0;
            while (!(dut.pipe_m_tvalid && !dut.pipe_m_tready) && cycles < 200) begin
                @(negedge aclk);
                cycles = cycles + 1;
            end
            if (!(dut.pipe_m_tvalid && !dut.pipe_m_tready))
                $fatal(1, "did not reach the forced output backpressure state");
            held_data = dut.pipe_m_tdata;
            held_sof = dut.pipe_m_tuser[0];
            held_eol = dut.pipe_m_tlast;
            repeat (6) begin
                @(posedge aclk);
                #1;
                if (!dut.pipe_m_tvalid || dut.pipe_m_tready ||
                    dut.pipe_m_tdata !== held_data || dut.pipe_m_tuser[0] !== held_sof ||
                    dut.pipe_m_tlast !== held_eol)
                    $fatal(1, "output changed during forced backpressure");
            end
            axi_read(8'h48, 0, stalled_cycles, read_resp);
            if (read_resp != 0 || stalled_cycles < 6)
                $fatal(1, "stalled-cycle counter did not record forced backpressure");
            axi_write(8'h00, 32'd1, 1, 2, 2'b00);
        end
    endtask

    always @(posedge aclk) begin
        if (!aresetn) begin
            output_records = 0;
            output_sofs = 0;
            output_pixel_index = -1;
            software_output_checksum = 0;
            golden_final_checksum = 0;
            previous_output_stalled = 0;
            previous_input_stalled = 0;
        end else begin
            if (previous_output_stalled &&
                (!dut.pipe_m_tvalid || dut.pipe_m_tdata !== held_output_data ||
                 dut.pipe_m_tuser[0] !== held_output_sof || dut.pipe_m_tlast !== held_output_eol))
                $fatal(1, "pipeline output changed while stalled");
            previous_output_stalled = dut.pipe_m_tvalid && !dut.pipe_m_tready;
            if (previous_output_stalled) begin
                held_output_data = dut.pipe_m_tdata;
                held_output_sof = dut.pipe_m_tuser[0];
                held_output_eol = dut.pipe_m_tlast;
            end

            if (previous_input_stalled &&
                (!s_axis_tvalid || s_axis_tdata !== held_input_data ||
                 s_axis_tkeep !== held_input_keep || s_axis_tlast !== held_input_last))
                $fatal(1, "input source changed a beat while it was stalled");
            previous_input_stalled = s_axis_tvalid && !s_axis_tready;
            if (previous_input_stalled) begin
                held_input_data = s_axis_tdata;
                held_input_keep = s_axis_tkeep;
                held_input_last = s_axis_tlast;
            end

            if (dut.pipe_m_tvalid && dut.pipe_m_tready) begin
                output_records = output_records + 1;
                if (dut.pipe_m_tuser[0]) begin
                    output_sofs = output_sofs + 1;
                    output_pixel_index = 0;
                    software_output_checksum = dut.pipe_m_tdata;
                end else begin
                    output_pixel_index = output_pixel_index + 1;
                    software_output_checksum = software_output_checksum + dut.pipe_m_tdata;
                end
                actual_word = {6'b0, dut.pipe_m_tuser[0], dut.pipe_m_tlast,
                               dut.pipe_m_tdata};
                if (vector_mode) begin
                    score_byte0 = $fgetc(expected_fd);
                    score_byte1 = $fgetc(expected_fd);
                    if (score_byte0 == -1 || score_byte1 == -1)
                        $fatal(1, "more output pixels than golden vector records");
                    expected_word = (score_byte1 << 8) | score_byte0;
                end else begin
                    if (output_pixel_index < 0 || output_pixel_index >= PIXELS)
                        $fatal(1, "output pixel index outside configured geometry");
                    expected_word = {6'b0,
                        (output_pixel_index == 0),
                        ((output_pixel_index % WIDTH) == (WIDTH-1)),
                        expected_binary[output_pixel_index]};
                end
                if (actual_word !== expected_word)
                    $fatal(1, "output[%0d] 0x%03x != expected 0x%03x",
                           output_records-1, actual_word, expected_word);
                if (expected_word[9])
                    golden_final_checksum = {24'd0,expected_word[7:0]};
                else
                    golden_final_checksum = golden_final_checksum + expected_word[7:0];
            end
        end
    end

    initial begin : run_test
        integer r, g, b, gray;
        integer cycles;
        integer expected_pixel_sum;
        integer error_code_value;
        integer status_value;
        input_path = "";
        expected_path = "";
        vector_mode = $value$plusargs("INPUT=%s", input_path);
        if (vector_mode) begin
            if (!$value$plusargs("EXPECTED=%s", expected_path))
                $fatal(1, "+INPUT requires +EXPECTED output records");
            expected_fd = $fopen(expected_path, "rb");
            if (expected_fd == 0) $fatal(1, "failed to open +EXPECTED golden file");
            expected_frames = 1;
            plusarg_found = $value$plusargs("EXPECTED_FRAMES=%d", expected_frames);
        end else if (!(WIDTH == 5 && HEIGHT == 3)) begin
            $fatal(1, "non-small geometry requires +INPUT/+EXPECTED vectors");
        end

        for (index = 0; index < PIXELS; index = index + 1) begin
            gray = gray_pixel(index);
            expected_binary[index] = (gray >= THRESHOLD) ? 8'hff : 8'h00;
        end
        expected_pixel_sum = 0;
        for (index = 0; index < PIXELS; index = index + 1)
            expected_pixel_sum = expected_pixel_sum + expected_binary[index];
        expected_output_records = expected_frames * PIXELS;

        repeat (5) @(posedge aclk);
        @(negedge aclk);
        aresetn = 1'b1;
        check_read(8'h00, 32'd1, 2'b00, 2);
        check_read(8'h04, 32'h574f3134, 2'b00, 2);
        check_read(8'h08, 32'd0, 2'b00, 2);
        axi_write(8'h00, 32'd0, 0, 3, 2'b00);
        check_read(8'h00, 32'd0, 2'b00, 2);
        axi_write(8'h00, 32'd1, 1, 3, 2'b00);
        check_read(8'h00, 32'd1, 2'b00, 2);
        axi_write(8'h04, 32'h0, 2, 2, 2'b10);
        axi_write(8'h01, 32'd1, 2, 2, 2'b10);
        axi_write(32'h00000100, 32'd1, 2, 2, 2'b10);
        check_read(8'h4c, 32'd0, 2'b10, 2);
        check_read(32'h00000100, 32'd0, 2'b10, 2);
        check_read(32'h43c20000, 32'd1, 2'b00, 2);
        check_read(32'h43c20004, 32'h574f3134, 2'b00, 2);
        axi_write(32'h43c20000, 32'd0, 0, 2, 2'b00);
        check_read(32'h43c20000, 32'd0, 2'b00, 0);
        axi_write(32'h43c20000, 32'd1, 1, 2, 2'b00);
        check_read(32'h43c20000, 32'd1, 2'b00, 0);
        axi_write(32'h43c30000, 32'd0, 2, 2, 2'b10);
        check_read(32'h43c30004, 32'd0, 2'b10, 2);

        if (vector_mode) begin
            axi_write(8'h00, 32'd1, 0, 2, 2'b00);
            $display("VECTOR REPLAY START: INPUT=%0s EXPECTED=%0s frames=%0d",
                     input_path, expected_path, expected_frames);
            send_vector_file();
        end else begin
            axi_write(8'h00, 32'd0, 0, 2, 2'b00);
            fork
                send_valid_frame(32'h11110001);
                wait_for_stall_and_release();
            join
            cycles = 0;
            while (!(dut.packet_done_count == 1 && dut.output_complete) && cycles < 10000) begin
                @(posedge aclk);
                cycles = cycles + 1;
            end
            if (!(dut.packet_done_count == 1 && dut.output_complete))
                $fatal(1, "first valid frame did not complete");

            send_beat(32'hdeadbeef, 4'hf, 1'b0);
            cycles = 0;
            while (dut.packet_error_count != 1 && cycles < 1000) begin
                @(posedge aclk);
                cycles = cycles + 1;
            end
            if (dut.packet_error_count != 1)
                $fatal(1, "malformed header did not raise one packet error");
            axi_read(8'h0c, 0, read_value, read_resp);
            if (read_resp != 0 || read_value != 1)
                $fatal(1, "header error code was not readable");
            send_beat(32'hbad0c0de, 4'hf, 1'b1);
            send_valid_frame(32'h11110002);
        end

        cycles = 0;
        while ((dut.packet_done_count < expected_frames ||
                dut.accepted_output_total < expected_output_records) &&
               cycles < MAX_WAIT_CYCLES) begin
            @(posedge aclk);
            cycles = cycles + 1;
        end
        @(negedge aclk);
        if (dut.packet_done_count != expected_frames)
            $fatal(1, "completion timeout after %0d cycles: done=%0d/%0d error=%0d output=%0d/%0d",
                   cycles, dut.packet_done_count, expected_frames, dut.packet_error_count,
                   dut.accepted_output_total, expected_output_records);
        if (vector_mode && dut.packet_error_count != 0)
            $fatal(1, "valid vector replay raised packet errors");
        if (!vector_mode && dut.packet_error_count != 1)
            $fatal(1, "packet_error_count=%0d expected=1", dut.packet_error_count);
        if (dut.accepted_output_total != expected_output_records ||
            output_records != expected_output_records)
            $fatal(1, "output count register/TB=%0d/%0d expected=%0d",
                   dut.accepted_output_total, output_records, expected_output_records);
        if (dut.pipe_checker_error || !dut.output_complete)
            $fatal(1, "output checker status error=%0d complete=%0d",
                   dut.pipe_checker_error, dut.output_complete);
        if (output_sofs != expected_frames ||
            dut.observed_pixels != PIXELS || dut.observed_sofs != 1 ||
            dut.observed_eols != HEIGHT || dut.observed_checksum != golden_final_checksum)
            $fatal(1, "checker values pixels=%0d SOFs=%0d EOLs=%0d checksum=%0d expected=%0d",
                   dut.observed_pixels, dut.observed_sofs, dut.observed_eols,
                   dut.observed_checksum, golden_final_checksum);
        if (expected_fd != 0) begin
            tail_byte = $fgetc(expected_fd);
            if (tail_byte != -1) $fatal(1, "golden output file has trailing records");
            $fclose(expected_fd);
            expected_fd = 0;
        end

        check_read(8'h14, expected_frames, 2'b00, 0);
        check_read(8'h18, vector_mode ? 32'd0 : 32'd1, 2'b00, 0);
        check_read(8'h10, dut.pipe_frame_id, 2'b00, 0);
        check_read(8'h1c, expected_input_bytes, 2'b00, 0);
        check_read(8'h20, expected_input_beats, 2'b00, 0);
        check_read(8'h24, expected_input_tlast, 2'b00, 0);
        check_read(8'h28, 32'h574f3134, 2'b00, 0);
        check_read(8'h2c, 32'hc0dec014, 2'b00, 0);
        check_read(8'h30, expected_input_checksum, 2'b00, 0);
        check_read(8'h34, PIXELS, 2'b00, 0);
        check_read(8'h38, 32'd1, 2'b00, 0);
        check_read(8'h3c, HEIGHT, 2'b00, 0);
        check_read(8'h40, golden_final_checksum, 2'b00, 0);
        check_read(8'h44, expected_output_records, 2'b00, 0);
        if (!vector_mode) begin
            check_read(8'h48, dut.output_stalled_cycles, 2'b00, 0);
            if (dut.output_stalled_cycles < 6)
                $fatal(1, "forced backpressure was not counted");
            check_read(8'h08, 32'h0000000b, 2'b00, 0);
            check_read(8'h0c, 32'd0, 2'b00, 0);
        end else begin
            check_read(8'h08, 32'h00000009, 2'b00, 0);
            check_read(8'h0c, 32'd0, 2'b00, 0);
        end
        $display("RESULT PASS: geometry=%0dx%0d frames=%0d output_pixels=%0d input_beats=%0d input_bytes=%0d input_checksum=0x%08x stalls=%0d",
                 WIDTH, HEIGHT, expected_frames, output_records, expected_input_beats,
                 expected_input_bytes, expected_input_checksum, dut.output_stalled_cycles);

        @(negedge aclk);
        aresetn = 1'b0;
        repeat (4) @(posedge aclk);
        @(negedge aclk);
        aresetn = 1'b1;
        check_read(8'h00, 32'd1, 2'b00, 0);
        check_read(8'h08, 32'd0, 2'b00, 0);
        check_read(8'h10, 32'd0, 2'b00, 0);
        check_read(8'h14, 32'd0, 2'b00, 0);
        check_read(8'h18, 32'd0, 2'b00, 0);
        check_read(8'h1c, 32'd0, 2'b00, 0);
        check_read(8'h20, 32'd0, 2'b00, 0);
        check_read(8'h24, 32'd0, 2'b00, 0);
        check_read(8'h28, 32'd0, 2'b00, 0);
        check_read(8'h2c, 32'd0, 2'b00, 0);
        check_read(8'h30, 32'd0, 2'b00, 0);
        check_read(8'h34, 32'd0, 2'b00, 0);
        check_read(8'h38, 32'd0, 2'b00, 0);
        check_read(8'h3c, 32'd0, 2'b00, 0);
        check_read(8'h40, 32'd0, 2'b00, 0);
        check_read(8'h44, 32'd0, 2'b00, 0);
        check_read(8'h48, 32'd0, 2'b00, 0);
        $finish;
    end

    initial begin : watchdog
        integer elapsed;
        elapsed = 0;
        while (elapsed < MAX_WAIT_CYCLES) begin
            @(posedge aclk);
            elapsed = elapsed + 1;
        end
        $fatal(1, "global watchdog expired after %0d cycles", MAX_WAIT_CYCLES);
    end
endmodule
