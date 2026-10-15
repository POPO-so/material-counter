// Eight-bit AXI4-Stream true crop with regenerated SOF and EOL.
module axis_roi_crop #(
    parameter integer FRAME_WIDTH = 320,
    parameter integer FRAME_HEIGHT = 240,
    parameter integer ROI_X0 = 0,
    parameter integer ROI_Y0 = 0,
    parameter integer ROI_X1 = FRAME_WIDTH,
    parameter integer ROI_Y1 = FRAME_HEIGHT
) (
    input wire aclk,
    input wire aresetn,
    input wire [7:0] s_tdata,
    input wire [0:0] s_tuser,
    input wire s_tlast,
    input wire s_tvalid,
    output wire s_tready,
    output reg [7:0] m_tdata,
    output reg [0:0] m_tuser,
    output reg m_tlast,
    output reg m_tvalid,
    input wire m_tready
);
    localparam integer X_BITS = (FRAME_WIDTH > 1) ? $clog2(FRAME_WIDTH) : 1;
    localparam integer Y_BITS = (FRAME_HEIGHT > 1) ? $clog2(FRAME_HEIGHT) : 1;
    // Extra bit represents the exclusive endpoint even for power-of-two sizes.
    localparam [X_BITS:0] X0_VALUE = ROI_X0;
    localparam [X_BITS:0] X1_VALUE = ROI_X1;
    localparam [Y_BITS:0] Y0_VALUE = ROI_Y0;
    localparam [Y_BITS:0] Y1_VALUE = ROI_Y1;
    localparam [X_BITS:0] ROI_LAST_X = (ROI_X1 > 0) ? ROI_X1 - 1 : 0;
    localparam [Y_BITS-1:0] FRAME_LAST_Y = FRAME_HEIGHT - 1;

    generate
        if (FRAME_WIDTH < 1 || FRAME_HEIGHT < 1 ||
            ROI_X0 < 0 || ROI_Y0 < 0 || ROI_X1 < ROI_X0 || ROI_Y1 < ROI_Y0 ||
            ROI_X1 > FRAME_WIDTH || ROI_Y1 > FRAME_HEIGHT) begin : g_invalid_roi
            initial $fatal(1, "ROI parameter contract invalid: frame=%0dx%0d window=[%0d,%0d)x[%0d,%0d)",
                FRAME_WIDTH, FRAME_HEIGHT, ROI_X0, ROI_X1, ROI_Y0, ROI_Y1);
        end
    endgenerate

    reg [X_BITS-1:0] next_x;
    reg [Y_BITS-1:0] next_y;
    reg input_synced;
    wire [X_BITS-1:0] pixel_x = s_tuser[0] ? {X_BITS{1'b0}} : next_x;
    wire [Y_BITS-1:0] pixel_y = s_tuser[0] ? {Y_BITS{1'b0}} : next_y;
    wire [X_BITS:0] x_extended = {1'b0, pixel_x};
    wire [Y_BITS:0] y_extended = {1'b0, pixel_y};
    wire keep_pixel = (input_synced || s_tuser[0]) &&
        x_extended >= X0_VALUE && x_extended < X1_VALUE &&
        y_extended >= Y0_VALUE && y_extended < Y1_VALUE;

    // Same one-beat elastic handshake as WO-0006/0007. Stalling even outside
    // the ROI is intentional; only the explicit reset may discard a held beat.
    assign s_tready = !m_tvalid || m_tready;

    always @(posedge aclk) begin
        if (!aresetn) begin
            m_tvalid <= 1'b0;
            input_synced <= 1'b0;
            next_x <= {X_BITS{1'b0}};
            next_y <= {Y_BITS{1'b0}};
        end else if (s_tready) begin
            // Clear a consumed output even when the next input is discarded.
            m_tvalid <= s_tvalid && keep_pixel;
            if (s_tvalid) begin
                if (keep_pixel) begin
                    m_tdata <= s_tdata;
                    m_tuser[0] <= (x_extended == X0_VALUE && y_extended == Y0_VALUE);
                    m_tlast <= (x_extended == ROI_LAST_X);
                end
                if (input_synced || s_tuser[0]) begin
                    if (s_tlast) begin
                        next_x <= {X_BITS{1'b0}};
                        if (pixel_y == FRAME_LAST_Y) begin
                            next_y <= {Y_BITS{1'b0}};
                            input_synced <= 1'b0;
                        end else begin
                            next_y <= pixel_y + 1'b1;
                            input_synced <= 1'b1;
                        end
                    end else begin
                        next_x <= pixel_x + 1'b1;
                        next_y <= pixel_y;
                        input_synced <= 1'b1;
                    end
                end
            end
        end
    end
endmodule
