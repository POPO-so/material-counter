`timescale 1ns/1ps
module tb_axis_dma_rx_monitor;
    localparam integer PIXELS = 320*240;
    localparam integer PIXEL_WORDS = PIXELS/4;
    localparam integer FRAME_BYTES = PIXELS+16;
    localparam integer FRAME_WORDS = FRAME_BYTES/4;

    reg clk = 1'b0;
    always #5 clk = ~clk;
    reg resetn = 1'b0;
    reg [31:0] awaddr = 0, wdata = 0, araddr = 0;
    reg [3:0] wstrb = 4'hf;
    reg awvalid = 0, wvalid = 0, bready = 1, arvalid = 0, rready = 1;
    wire awready, wready, bvalid, arready, rvalid;
    wire [1:0] bresp, rresp;
    wire [31:0] rdata;
    reg [31:0] tdata = 0;
    reg [3:0] tkeep = 4'hf;
    reg tlast = 0, tvalid = 0;
    wire tready;
    integer expected_fg = 0;
    reg [31:0] expected_sum = 0;
    integer i;
    reg [31:0] value;

    axis_dma_rx_monitor #(.EXPECTED_BYTES(FRAME_BYTES)) dut (
        .aclk(clk), .aresetn(resetn),
        .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata), .s_axi_wstrb(wstrb), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
        .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
        .s_axi_araddr(araddr), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rvalid(rvalid), .s_axi_rready(rready),
        .s_axis_tdata(tdata), .s_axis_tkeep(tkeep), .s_axis_tlast(tlast),
        .s_axis_tvalid(tvalid), .s_axis_tready(tready)
    );

    function [7:0] pixel_byte(input integer n);
        begin pixel_byte = ((n*13 + n/7) % 19 < 8) ? 8'hff : 8'h00; end
    endfunction

    function [31:0] frame_word(input integer n);
        reg [31:0] total_fg;
        begin
            if (n < PIXEL_WORDS)
                frame_word = {pixel_byte(n*4+3), pixel_byte(n*4+2), pixel_byte(n*4+1), pixel_byte(n*4)};
            else begin
                total_fg = expected_fg;
                case (n-PIXEL_WORDS)
                    0: frame_word = 32'h10203040;
                    1: frame_word = 32'h50607080;
                    2: frame_word = total_fg;
                    3: frame_word = 32'h12340000; // epoch=0x1234 in trailer word 3; no header or padding.
                    default: frame_word = 0;
                endcase
            end
        end
    endfunction

    task automatic write_reg(input [31:0] addr, input [31:0] data);
        reg aw_done, w_done;
        begin
            @(negedge clk); awaddr=addr; wdata=data; awvalid=1; wvalid=1; aw_done=0; w_done=0;
            while (!aw_done || !w_done) begin
                @(posedge clk);
                if (awvalid && awready) aw_done=1;
                if (wvalid && wready) w_done=1;
                @(negedge clk);
                if (aw_done) awvalid=0;
                if (w_done) wvalid=0;
            end
            wait (bvalid); @(posedge clk);
            if (bresp != 0) $fatal(1, "AXI-Lite write response error");
            @(negedge clk);
        end
    endtask

    task automatic read_reg(input [31:0] addr, output [31:0] data);
        begin
            @(negedge clk); araddr=addr; arvalid=1;
            do @(posedge clk); while (!arready);
            @(negedge clk); arvalid=0;
            wait (rvalid); data=rdata;
            if (rresp != 0) $fatal(1, "AXI-Lite read response error at %h", addr);
            @(posedge clk); @(negedge clk);
        end
    endtask

    task automatic send_word(input integer n, input bit last_beat);
        integer gap;
        begin
            gap = (n*17 + 3) % 6;
            repeat (gap) @(posedge clk);
            @(negedge clk);
            tdata=frame_word(n); tkeep=4'hf; tlast=last_beat; tvalid=1;
            do @(posedge clk); while (!tready);
            @(negedge clk); tvalid=0; tlast=0;
        end
    endtask

    initial begin
        for (i=0; i<PIXELS; i=i+1)
            if (pixel_byte(i) == 8'hff) expected_fg = expected_fg + 1;
        for (i=0; i<FRAME_WORDS; i=i+1)
            expected_sum = expected_sum + frame_word(i);

        repeat (5) @(posedge clk); @(negedge clk); resetn=1;

        // Interrupted packet: no TLAST means no successful completion. Reset must recover the sink.
        write_reg(32'h0, 32'h1);
        for (i=0; i<100; i=i+1) send_word(i, 1'b0);
        read_reg(32'h4, value);
        if (value[1] !== 1'b0 || value[2] !== 1'b0) $fatal(1, "partial packet incorrectly completed: %h", value);
        @(negedge clk); resetn=0; repeat (3) @(posedge clk); @(negedge clk); resetn=1;

        write_reg(32'h0, 32'h1);
        for (i=0; i<FRAME_WORDS; i=i+1)
            send_word(i, i == FRAME_WORDS-1);

        read_reg(32'h4, value);
        if (value !== 32'h2) $fatal(1, "expected done/no-error status, got %h", value);
        read_reg(32'h8, value);
        if (value !== FRAME_BYTES) $fatal(1, "byte count %0d expected %0d", value, FRAME_BYTES);
        read_reg(32'hc, value);
        if (value !== FRAME_WORDS) $fatal(1, "beat count %0d expected %0d", value, FRAME_WORDS);
        read_reg(32'h10, value);
        if (value !== frame_word(0)) $fatal(1, "first word mismatch got %h expected %h", value, frame_word(0));
        read_reg(32'h14, value);
        if (value !== 32'h12340000) $fatal(1, "final trailer word/TLAST data mismatch %h", value);
        read_reg(32'h18, value);
        if (value !== 1) $fatal(1, "TLAST count %0d expected 1", value);
        read_reg(32'h1c, value);
        if (value !== expected_sum) $fatal(1, "checksum %h expected %h", value, expected_sum);
        read_reg(32'h20, value);
        if (value !== 0) $fatal(1, "unexpected protocol error code %0d", value);

        $display("WO0013_AXIS_RX_PASS bytes=%0d beats=%0d first=%08h last=%08h tlast_count=1 checksum=%08h fg=%0d", FRAME_BYTES, FRAME_WORDS, frame_word(0), 32'h12340000, expected_sum, expected_fg);
        $finish;
    end

    initial begin
        #5000000;
        $fatal(1, "watchdog timeout");
    end
endmodule
