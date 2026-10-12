// One-beat AXI4-Stream Gray8 to Binary8 fixed-threshold converter.
module axis_gray2binary #(
    parameter integer THRESHOLD = 128
) (
    input  wire        aclk,
    input  wire        aresetn,
    input  wire [7:0]  s_tdata,
    input  wire [0:0]  s_tuser,
    input  wire        s_tlast,
    input  wire        s_tvalid,
    output wire        s_tready,
    output reg  [7:0]  m_tdata,
    output reg  [0:0]  m_tuser,
    output reg         m_tlast,
    output reg         m_tvalid,
    input  wire        m_tready
);
    // Retain the integer parameter for range checking before narrowing.
    localparam [7:0] THRESHOLD_VALUE = THRESHOLD[7:0];
    generate
        if (THRESHOLD < 0 || THRESHOLD > 255) begin : g_invalid_threshold
            initial $fatal(1, "THRESHOLD must be in [0,255], got %0d", THRESHOLD);
        end
    endgenerate

    // Match WO-0006: reset clears VALID synchronously; ignore transfers
    // during reset. An empty register can accept even with the sink stalled.
    assign s_tready = !m_tvalid || m_tready;

    always @(posedge aclk) begin
        if (!aresetn) begin
            m_tvalid <= 1'b0;
        end else if (s_tready) begin
            m_tvalid <= s_tvalid;
            if (s_tvalid) begin
                // Both operands are unsigned Gray8. Equality is foreground.
                m_tdata <= (s_tdata >= THRESHOLD_VALUE) ? 8'hFF : 8'h00;
                m_tuser <= s_tuser;
                m_tlast <= s_tlast;
            end
        end
    end
endmodule
