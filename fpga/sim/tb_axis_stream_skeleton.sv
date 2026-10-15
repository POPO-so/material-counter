`timescale 1ns/1ps

module tb_axis_stream_skeleton;
    reg clk = 1'b0;
    always #5 clk = ~clk;

    reg rst_n = 1'b0;
    reg m_tready = 1'b0;
    wire s_tready;
    wire m_tvalid;
    wire [23:0] m_tdata;
    wire [0:0] m_tuser;
    wire m_tlast;

    integer cycle = 0;
    integer input_count = 0;
    integer output_count = 0;
    integer done_cycle = -1;
    integer stall_cycles = 0;
    integer input_blocked_cycles = 0;
    integer pause_run = 0;
    integer longest_pause = 0;
    reg was_stalled = 1'b0;
    reg [23:0] held_data;
    reg [0:0] held_user;
    reg held_last;

    wire s_tvalid = rst_n && (input_count < 8);
    wire [23:0] s_tdata = 24'h102030 + input_count * 24'h010101;
    wire [0:0] s_tuser = (input_count == 0);
    wire s_tlast = (input_count == 3 || input_count == 7);

    axis_stream_skeleton dut (
        .aclk(clk), .aresetn(rst_n),
        .s_tdata(s_tdata), .s_tuser(s_tuser), .s_tlast(s_tlast),
        .s_tvalid(s_tvalid), .s_tready(s_tready),
        .m_tdata(m_tdata), .m_tuser(m_tuser), .m_tlast(m_tlast),
        .m_tvalid(m_tvalid), .m_tready(m_tready)
    );

    initial begin
        repeat (2) @(negedge clk);
        rst_n = 1'b1;
    end

    always @(negedge clk) begin
        if (rst_n)
            m_tready = (cycle < 2) ||
                       (cycle >= 7 && cycle % 3 != 0);
    end

    always @(posedge clk) begin
        if (rst_n) begin
            cycle = cycle + 1;

            if (done_cycle >= 0 && m_tvalid !== 1'b0)
                $fatal(1, "unexpected beat after the eight expected beats");

            if (was_stalled &&
                (!m_tvalid || m_tdata !== held_data ||
                 m_tuser !== held_user || m_tlast !== held_last))
                $fatal(1, "output changed during backpressure");

            was_stalled = m_tvalid && !m_tready;
            if (was_stalled) begin
                held_data = m_tdata;
                held_user = m_tuser;
                held_last = m_tlast;
                stall_cycles = stall_cycles + 1;
            end

            if (s_tvalid && !s_tready)
                input_blocked_cycles = input_blocked_cycles + 1;

            if (!m_tready) begin
                pause_run = pause_run + 1;
                if (pause_run > longest_pause)
                    longest_pause = pause_run;
            end else begin
                pause_run = 0;
            end

            if (m_tvalid && m_tready) begin
                if (output_count >= 8 ||
                    m_tdata !== (24'h102030 + output_count * 24'h010101) ||
                    m_tuser !== (output_count == 0) ||
                    m_tlast !== (output_count == 3 || output_count == 7))
                    $fatal(1, "beat %0d differs from expected stream", output_count);
                output_count = output_count + 1;
            end

            if (s_tvalid && s_tready)
                input_count <= input_count + 1;

            if (output_count == 8 && done_cycle < 0)
                done_cycle = cycle;

            if (done_cycle >= 0 && cycle - done_cycle == 3) begin
                if (input_count != 8 || stall_cycles == 0 ||
                    input_blocked_cycles == 0 || longest_pause < 2)
                    $fatal(1, "coverage/count failure: in=%0d out=%0d stall=%0d blocked=%0d pause=%0d",
                           input_count, output_count, stall_cycles,
                           input_blocked_cycles, longest_pause);
                $display("PASS: in=%0d out=%0d stall=%0d blocked=%0d longest_pause=%0d",
                         input_count, output_count, stall_cycles,
                         input_blocked_cycles, longest_pause);
                $finish;
            end

            if (cycle > 80)
                $fatal(1, "timeout: in=%0d out=%0d", input_count, output_count);
        end
    end
endmodule
