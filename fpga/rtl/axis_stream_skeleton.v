// One-beat AXI4-Stream buffer for the RGB888 interface A experiment.
module axis_stream_skeleton (
    input  wire        aclk,
    input  wire        aresetn,
    input  wire [23:0] s_tdata,
    input  wire [0:0]  s_tuser,
    input  wire        s_tlast,
    input  wire        s_tvalid,
    output wire        s_tready,
    output reg  [23:0] m_tdata,
    output reg  [0:0]  m_tuser,
    output reg         m_tlast,
    output reg         m_tvalid,
    input  wire        m_tready
);
    // Empty accepts a beat. Full can replace its beat only when it is consumed.
    assign s_tready = !m_tvalid || m_tready;

    always @(posedge aclk) begin
        if (!aresetn) begin
            m_tvalid <= 1'b0;
        end else if (s_tready) begin
            m_tvalid <= s_tvalid;
            if (s_tvalid) begin
                m_tdata <= s_tdata;
                m_tuser <= s_tuser;
                m_tlast <= s_tlast;
            end
        end
    end
endmodule
