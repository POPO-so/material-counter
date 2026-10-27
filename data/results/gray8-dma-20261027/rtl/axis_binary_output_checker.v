// Read-only checker for the binary pixel stream at the pipeline output.
// packet_done/packet_error belong to the input packet adapter and are kept
// independent; this block checks only accepted output pixels and geometry.
module axis_binary_output_checker #(
    parameter integer WIDTH = 320,
    parameter integer HEIGHT = 240
) (
    input  wire        aclk,
    input  wire        aresetn,
    input  wire [7:0]  s_tdata,
    input  wire [0:0]  s_tuser,
    input  wire        s_tlast,
    input  wire        s_tvalid,
    input  wire        s_tready,
    output reg         checker_error,
    output reg  [31:0] observed_pixels,
    output reg  [31:0] observed_sofs,
    output reg  [31:0] observed_eols,
    output reg  [31:0] observed_checksum
);
    localparam [63:0] FRAME_PIXEL_COUNT = WIDTH * HEIGHT;

    wire beat_fire = s_tvalid && s_tready;
    wire [32:0] next_pixel_count = s_tuser[0]
        ? 33'd1
        : ({1'b0, observed_pixels} + 33'd1);
    localparam integer COLUMN_BITS = (WIDTH > 1) ? $clog2(WIDTH) : 1;
    reg [COLUMN_BITS-1:0] column;
    wire expected_eol = s_tuser[0] ? (WIDTH == 1) : (column == WIDTH-1);
    wire bad_binary_value = (s_tdata != 8'h00) && (s_tdata != 8'hFF);
    wire bad_eol_position;
    wire pixel_count_overflow = ({31'b0, next_pixel_count} > FRAME_PIXEL_COUNT);

    generate
        if (WIDTH <= 0 || HEIGHT <= 0 || FRAME_PIXEL_COUNT > 64'h00000000FFFFFFFF) begin : g_invalid_geometry
            initial $fatal(1, "WIDTH and HEIGHT must be positive and fit the 32-bit counters");
        end
    endgenerate

    assign bad_eol_position = (s_tlast != expected_eol);

    always @(posedge aclk) begin
        if (!aresetn) begin
            checker_error     <= 1'b0;
            observed_pixels   <= 32'd0;
            observed_sofs     <= 32'd0;
            observed_eols     <= 32'd0;
            observed_checksum <= 32'd0;
            column            <= 0;
        end else if (beat_fire) begin
            // Advance only on acceptance; SOF starts column zero independently
            // of any malformed preceding frame. Avoid a wide modulo datapath.
            if (s_tuser[0]) column <= (WIDTH == 1) ? 0 : 1;
            else if (expected_eol) column <= 0;
            else column <= column + 1'b1;
            // A SOF beat is the first pixel of the new observation window.
            // The checker intentionally does not wait for packet_done: that
            // adapter event may precede the final pipelined output handshake.
            if (s_tuser[0]) begin
                observed_pixels   <= 32'd1;
                observed_sofs     <= 32'd1;
                observed_eols     <= s_tlast ? 32'd1 : 32'd0;
                observed_checksum <= {24'd0, s_tdata};
                checker_error     <= bad_binary_value || bad_eol_position ||
                                     pixel_count_overflow;
            end else begin
                if (next_pixel_count[32])
                    observed_pixels <= 32'hFFFFFFFF;
                else
                    observed_pixels <= next_pixel_count[31:0];

                if (s_tlast)
                    observed_eols <= observed_eols + 1'b1;
                observed_checksum <= observed_checksum + {24'd0, s_tdata};

                // Data without a preceding accepted SOF is not a valid
                // geometry observation. A later SOF starts a fresh window.
                if ((observed_sofs == 0) || bad_binary_value ||
                    bad_eol_position || pixel_count_overflow)
                    checker_error <= 1'b1;
            end
        end
    end
endmodule
