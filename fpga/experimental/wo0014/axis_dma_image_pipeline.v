// Isolated WO-0014 packet-to-binary image pipeline.
// Packet metadata status is passed through independently from pixel geometry.
module axis_dma_image_pipeline #(
    parameter integer WIDTH = 320,
    parameter integer HEIGHT = 240,
    parameter integer THRESHOLD = 128
) (
    input  wire        aclk,
    input  wire        aresetn,
    input  wire [31:0] s_tdata,
    input  wire [3:0]  s_tkeep,
    input  wire        s_tlast,
    input  wire        s_tvalid,
    output wire        s_tready,
    output wire [7:0]  m_tdata,
    output wire [0:0]  m_tuser,
    output wire        m_tlast,
    output wire        m_tvalid,
    input  wire        m_tready,
    output wire        packet_done,
    output wire        packet_error,
    output wire [3:0]  error_code,
    output wire [31:0] frame_id,
    output wire        checker_error,
    output wire [31:0] observed_pixels,
    output wire [31:0] observed_sofs,
    output wire [31:0] observed_eols,
    output wire [31:0] observed_checksum
);
    wire [23:0] rgb_data;
    wire [0:0]  rgb_sof;
    wire        rgb_eol;
    wire        rgb_valid;
    wire        rgb_ready;

    wire [7:0]  gray_data;
    wire [0:0]  gray_sof;
    wire        gray_eol;
    wire        gray_valid;
    wire        gray_ready;

    axis_dma_rgb_adapter #(
        .WIDTH(WIDTH),
        .HEIGHT(HEIGHT)
    ) packet_adapter (
        .aclk(aclk),
        .aresetn(aresetn),
        .s_tdata(s_tdata),
        .s_tkeep(s_tkeep),
        .s_tlast(s_tlast),
        .s_tvalid(s_tvalid),
        .s_tready(s_tready),
        .m_tdata(rgb_data),
        .m_tuser(rgb_sof),
        .m_tlast(rgb_eol),
        .m_tvalid(rgb_valid),
        .m_tready(rgb_ready),
        .packet_done(packet_done),
        .packet_error(packet_error),
        .error_code(error_code),
        .frame_id(frame_id)
    );

    axis_rgb2gray rgb_to_gray (
        .aclk(aclk),
        .aresetn(aresetn),
        .s_tdata(rgb_data),
        .s_tuser(rgb_sof),
        .s_tlast(rgb_eol),
        .s_tvalid(rgb_valid),
        .s_tready(rgb_ready),
        .m_tdata(gray_data),
        .m_tuser(gray_sof),
        .m_tlast(gray_eol),
        .m_tvalid(gray_valid),
        .m_tready(gray_ready)
    );

    axis_gray2binary #(
        .THRESHOLD(THRESHOLD)
    ) gray_to_binary (
        .aclk(aclk),
        .aresetn(aresetn),
        .s_tdata(gray_data),
        .s_tuser(gray_sof),
        .s_tlast(gray_eol),
        .s_tvalid(gray_valid),
        .s_tready(gray_ready),
        .m_tdata(m_tdata),
        .m_tuser(m_tuser),
        .m_tlast(m_tlast),
        .m_tvalid(m_tvalid),
        .m_tready(m_tready)
    );

    // This block only observes completed output handshakes; it never enters
    // the ready path and therefore cannot add output backpressure.
    axis_binary_output_checker #(
        .WIDTH(WIDTH),
        .HEIGHT(HEIGHT)
    ) output_checker (
        .aclk(aclk),
        .aresetn(aresetn),
        .s_tdata(m_tdata),
        .s_tuser(m_tuser),
        .s_tlast(m_tlast),
        .s_tvalid(m_tvalid),
        .s_tready(m_tready),
        .checker_error(checker_error),
        .observed_pixels(observed_pixels),
        .observed_sofs(observed_sofs),
        .observed_eols(observed_eols),
        .observed_checksum(observed_checksum)
    );
endmodule
