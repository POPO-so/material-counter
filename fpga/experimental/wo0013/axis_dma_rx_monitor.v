// Isolated WO-0013 Phase A sink and AXI-Lite observation registers.
// This is not part of the production video chain or Interface B.
module axis_dma_rx_monitor #(
    parameter [31:0] EXPECTED_BYTES = 32'd76816
) (
    input  wire        aclk,
    input  wire        aresetn,
    input  wire [31:0] s_axi_awaddr,
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    output reg  [1:0]  s_axi_bresp,
    output reg         s_axi_bvalid,
    input  wire        s_axi_bready,
    input  wire [31:0] s_axi_araddr,
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
    output reg  [31:0] s_axi_rdata,
    output reg  [1:0]  s_axi_rresp,
    output reg         s_axi_rvalid,
    input  wire        s_axi_rready,
    input  wire [31:0] s_axis_tdata,
    input  wire [3:0]  s_axis_tkeep,
    input  wire        s_axis_tlast,
    input  wire        s_axis_tvalid,
    output wire        s_axis_tready
);
    localparam [2:0] ERR_BAD_TKEEP = 3'd1;
    localparam [2:0] ERR_EARLY_TLAST = 3'd2;
    localparam [2:0] ERR_MISSING_TLAST = 3'd3;

    reg [31:0] expected_bytes;
    reg [31:0] byte_count;
    reg [31:0] beat_count;
    reg [31:0] first_word;
    reg [31:0] last_word;
    reg [31:0] tlast_count;
    reg [31:0] checksum;
    reg [2:0]  error_code;
    reg        armed;
    reg        done;
    reg        error;
    reg        aw_held;
    reg [31:0] awaddr_held;
    reg        w_held;
    reg [31:0] wdata_held;
    reg [3:0]  wstrb_held;
    reg        ar_held;
    reg [31:0] araddr_held;
    reg [2:0]  keep_bytes;

    wire aw_take = s_axi_awvalid && s_axi_awready;
    wire w_take = s_axi_wvalid && s_axi_wready;
    wire [3:0] wr_reg = aw_held ? awaddr_held[5:2] : s_axi_awaddr[5:2];
    wire wr_start = w_held ? wdata_held[0] : s_axi_wdata[0];
    wire wr_byte_enable = w_held ? wstrb_held[0] : s_axi_wstrb[0];
    wire wr_complete = !s_axi_bvalid && (aw_held || aw_take) && (w_held || w_take);
    wire [32:0] next_byte_count = {1'b0,byte_count} + keep_bytes;
    wire axis_fire = s_axis_tvalid && s_axis_tready;
    reg [31:0] read_value;

    always @* begin
        case (s_axis_tkeep)
            4'b0000: keep_bytes = 3'd0;
            4'b0001,4'b0010,4'b0100,4'b1000: keep_bytes = 3'd1;
            4'b0011,4'b0101,4'b0110,4'b1001,4'b1010,4'b1100: keep_bytes = 3'd2;
            4'b0111,4'b1011,4'b1101,4'b1110: keep_bytes = 3'd3;
            4'b1111: keep_bytes = 3'd4;
            default: keep_bytes = 3'd0;
        endcase
    end

    assign s_axi_awready = !aw_held && !s_axi_bvalid;
    assign s_axi_wready = !w_held && !s_axi_bvalid;
    assign s_axi_arready = !s_axi_rvalid && !ar_held;
    assign s_axis_tready = armed && !done && !error;

    always @* begin
        case (araddr_held[5:2])
            4'h0: read_value = 32'b0;
            4'h1: read_value = {29'b0,error,done,armed};
            4'h2: read_value = byte_count;
            4'h3: read_value = beat_count;
            4'h4: read_value = first_word;
            4'h5: read_value = last_word;
            4'h6: read_value = tlast_count;
            4'h7: read_value = checksum;
            4'h8: read_value = {29'b0,error_code};
            4'h9: read_value = expected_bytes;
            default: read_value = 32'b0;
        endcase
    end

    always @(posedge aclk) begin
        if (!aresetn) begin
            aw_held <= 1'b0;
            w_held <= 1'b0;
            ar_held <= 1'b0;
            s_axi_bresp <= 2'b00;
            s_axi_bvalid <= 1'b0;
            s_axi_rdata <= 32'b0;
            s_axi_rresp <= 2'b00;
            s_axi_rvalid <= 1'b0;
            expected_bytes <= EXPECTED_BYTES;
            byte_count <= 32'b0;
            beat_count <= 32'b0;
            first_word <= 32'b0;
            last_word <= 32'b0;
            tlast_count <= 32'b0;
            checksum <= 32'b0;
            error_code <= 3'b0;
            armed <= 1'b0;
            done <= 1'b0;
            error <= 1'b0;
        end else begin
            if (aw_take) begin
                awaddr_held <= s_axi_awaddr;
                aw_held <= 1'b1;
            end
            if (w_take) begin
                wdata_held <= s_axi_wdata;
                wstrb_held <= s_axi_wstrb;
                w_held <= 1'b1;
            end
            if (wr_complete) begin
                aw_held <= 1'b0;
                w_held <= 1'b0;
                s_axi_bresp <= 2'b00;
                s_axi_bvalid <= 1'b1;
                if (wr_reg == 4'h0 && wr_byte_enable && wr_start) begin
                    byte_count <= 32'b0;
                    beat_count <= 32'b0;
                    first_word <= 32'b0;
                    last_word <= 32'b0;
                    tlast_count <= 32'b0;
                    checksum <= 32'b0;
                    error_code <= 3'b0;
                    done <= 1'b0;
                    error <= 1'b0;
                    armed <= 1'b1;
                end
            end else if (s_axi_bvalid && s_axi_bready) begin
                s_axi_bvalid <= 1'b0;
            end

            if (s_axi_arready && s_axi_arvalid) begin
                araddr_held <= s_axi_araddr;
                ar_held <= 1'b1;
            end
            if (ar_held && !s_axi_rvalid) begin
                s_axi_rdata <= read_value;
                s_axi_rresp <= 2'b00;
                s_axi_rvalid <= 1'b1;
                ar_held <= 1'b0;
            end else if (s_axi_rvalid && s_axi_rready) begin
                s_axi_rvalid <= 1'b0;
            end

            if (axis_fire) begin
                if (beat_count == 0)
                    first_word <= s_axis_tdata;
                last_word <= s_axis_tdata;
                beat_count <= beat_count + 1'b1;
                byte_count <= next_byte_count[31:0];
                checksum <= checksum + s_axis_tdata;
                if (s_axis_tlast)
                    tlast_count <= tlast_count + 1'b1;

                if (s_axis_tkeep != 4'b1111) begin
                    error <= 1'b1;
                    done <= s_axis_tlast;
                    armed <= 1'b0;
                    error_code <= ERR_BAD_TKEEP;
                end else if (s_axis_tlast) begin
                    armed <= 1'b0;
                    done <= 1'b1;
                    if (next_byte_count != expected_bytes) begin
                        error <= 1'b1;
                        error_code <= ERR_EARLY_TLAST;
                    end
                end else if (next_byte_count >= expected_bytes) begin
                    // A size-complete stream with no TLAST is left stalled; software timeout/reset recovers it.
                    error <= 1'b1;
                    error_code <= ERR_MISSING_TLAST;
                    armed <= 1'b0;
                end
            end
        end
    end
endmodule
