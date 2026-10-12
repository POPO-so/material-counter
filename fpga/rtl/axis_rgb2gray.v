// One-beat AXI4-Stream RGB888 to Gray8 converter.
module axis_rgb2gray (
    input  wire        aclk,
    input  wire        aresetn,
    input  wire [23:0] s_tdata,
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
    wire [15:0] red_sample = {8'd0, s_tdata[23:16]};
    wire [15:0] green_sample = {8'd0, s_tdata[15:8]};
    wire [15:0] blue_sample = {8'd0, s_tdata[7:0]};
    wire [15:0] red_term;
    wire [15:0] green_term;
    wire [15:0] blue_term;
    wire [15:0] gray_sum;

    assign red_term = 16'd77 * red_sample;
    assign green_term = 16'd150 * green_sample;
    assign blue_term = 16'd29 * blue_sample;
    assign gray_sum = red_term + green_term + blue_term;

    // Empty accepts a beat; a full register accepts a replacement as its
    // current beat is consumed. The high byte implements floor(sum / 256).
    assign s_tready = !m_tvalid || m_tready;

    always @(posedge aclk) begin
        if (!aresetn) begin
            m_tvalid <= 1'b0;
        end else if (s_tready) begin
            m_tvalid <= s_tvalid;
            if (s_tvalid) begin
                m_tdata <= gray_sum[15:8];
                m_tuser <= s_tuser;
                m_tlast <= s_tlast;
            end
        end
    end
endmodule
