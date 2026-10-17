// Isolated WO-0014 Block Design wrapper. It exposes the RGB packet input as
// AXI4-Stream and a small read-only status map plus one drain-enable control.
module axis_dma_image_pipeline_bd #(
    parameter integer WIDTH = 320,
    parameter integer HEIGHT = 240,
    parameter integer THRESHOLD = 128,
    parameter [31:0] BASE_ADDR = 32'h43c20000
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK",
       X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aclk, ASSOCIATED_BUSIF s_axi:s_axis, ASSOCIATED_RESET aresetn" *)
    input  wire        aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST",
       X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aresetn, POLARITY ACTIVE_LOW" *)
    input  wire        aresetn,

    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWADDR",
       X_INTERFACE_PARAMETER = "XIL_INTERFACENAME s_axi, PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 32" *)
    input  wire [31:0] s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWVALID" *)
    input  wire        s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWREADY" *)
    output wire        s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WDATA" *)
    input  wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WSTRB" *)
    input  wire [3:0]  s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WVALID" *)
    input  wire        s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WREADY" *)
    output wire        s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BRESP" *)
    output reg  [1:0]  s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BVALID" *)
    output reg         s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BREADY" *)
    input  wire        s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARADDR" *)
    input  wire [31:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARVALID" *)
    input  wire        s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARREADY" *)
    output wire        s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RDATA" *)
    output reg  [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RRESP" *)
    output reg  [1:0]  s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RVALID" *)
    output reg         s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RREADY" *)
    input  wire        s_axi_rready,

    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 s_axis TDATA",
       X_INTERFACE_PARAMETER = "XIL_INTERFACENAME s_axis, TDATA_NUM_BYTES 4, HAS_TKEEP 1, HAS_TLAST 1, HAS_TREADY 1, HAS_TSTRB 0, HAS_TUSER 0, HAS_TID 0, HAS_TDEST 0" *)
    input  wire [31:0] s_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 s_axis TKEEP" *)
    input  wire [3:0]  s_axis_tkeep,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 s_axis TLAST" *)
    input  wire        s_axis_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 s_axis TVALID" *)
    input  wire        s_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 s_axis TREADY" *)
    output wire        s_axis_tready
);
    localparam [31:0] MAGIC = 32'h574f3134;

    reg drain_enable;
    reg packet_done_seen;
    reg packet_error_seen;
    reg [31:0] packet_done_count;
    reg [31:0] packet_error_count;
    reg [31:0] input_accepted_bytes;
    reg [31:0] input_beats;
    reg [31:0] input_tlast_count;
    reg [31:0] input_first_word;
    reg [31:0] input_last_word;
    reg [31:0] input_byte_checksum;
    reg [31:0] accepted_output_total;
    reg [31:0] output_stalled_cycles;

    reg aw_pending;
    reg [31:0] awaddr_hold;
    reg w_pending;
    reg [31:0] wdata_hold;
    reg [3:0] wstrb_hold;

    wire [7:0]  pipe_m_tdata;
    wire [0:0]  pipe_m_tuser;
    wire        pipe_m_tlast;
    wire        pipe_m_tvalid;
    wire        pipe_m_tready;
    wire        pipe_packet_done;
    wire        pipe_packet_error;
    wire [3:0]  pipe_error_code;
    wire [31:0] pipe_frame_id;
    wire        pipe_checker_error;
    wire [31:0] observed_pixels;
    wire [31:0] observed_sofs;
    wire [31:0] observed_eols;
    wire [31:0] observed_checksum;

    wire aw_fire = s_axi_awvalid && s_axi_awready;
    wire w_fire = s_axi_wvalid && s_axi_wready;
    wire have_aw = aw_pending || aw_fire;
    wire have_w = w_pending || w_fire;
    wire [31:0] effective_awaddr = aw_pending ? awaddr_hold : s_axi_awaddr;
    wire [31:0] effective_wdata = w_pending ? wdata_hold : s_axi_wdata;
    wire [3:0] effective_wstrb = w_pending ? wstrb_hold : s_axi_wstrb;
    wire write_commit = !s_axi_bvalid && have_aw && have_w;
    wire input_fire = s_axis_tvalid && s_axis_tready;
    wire output_fire = pipe_m_tvalid && pipe_m_tready;
    wire output_complete = (observed_pixels == WIDTH*HEIGHT) &&
                           (observed_sofs == 1) && (observed_eols == HEIGHT);
    wire [31:0] status_word = {28'd0, output_complete, pipe_checker_error,
                               packet_error_seen, packet_done_seen};
    wire [2:0] accepted_lane_count = {2'd0,s_axis_tkeep[0]} +
                                     {2'd0,s_axis_tkeep[1]} +
                                     {2'd0,s_axis_tkeep[2]} +
                                     {2'd0,s_axis_tkeep[3]};
    wire [31:0] accepted_lane_sum =
        (s_axis_tkeep[0] ? {24'd0,s_axis_tdata[7:0]} : 32'd0) +
        (s_axis_tkeep[1] ? {24'd0,s_axis_tdata[15:8]} : 32'd0) +
        (s_axis_tkeep[2] ? {24'd0,s_axis_tdata[23:16]} : 32'd0) +
        (s_axis_tkeep[3] ? {24'd0,s_axis_tdata[31:24]} : 32'd0);

    assign s_axi_awready = aresetn && !aw_pending && !s_axi_bvalid;
    assign s_axi_wready = aresetn && !w_pending && !s_axi_bvalid;
    assign s_axi_arready = aresetn && !s_axi_rvalid;
    assign pipe_m_tready = drain_enable;

    axis_dma_image_pipeline #(
        .WIDTH(WIDTH),
        .HEIGHT(HEIGHT),
        .THRESHOLD(THRESHOLD)
    ) image_pipeline (
        .aclk(aclk),
        .aresetn(aresetn),
        .s_tdata(s_axis_tdata),
        .s_tkeep(s_axis_tkeep),
        .s_tlast(s_axis_tlast),
        .s_tvalid(s_axis_tvalid),
        .s_tready(s_axis_tready),
        .m_tdata(pipe_m_tdata),
        .m_tuser(pipe_m_tuser),
        .m_tlast(pipe_m_tlast),
        .m_tvalid(pipe_m_tvalid),
        .m_tready(pipe_m_tready),
        .packet_done(pipe_packet_done),
        .packet_error(pipe_packet_error),
        .error_code(pipe_error_code),
        .frame_id(pipe_frame_id),
        .checker_error(pipe_checker_error),
        .observed_pixels(observed_pixels),
        .observed_sofs(observed_sofs),
        .observed_eols(observed_eols),
        .observed_checksum(observed_checksum)
    );

    always @(posedge aclk) begin
        if (!aresetn) begin
            drain_enable <= 1'b1;
            packet_done_seen <= 1'b0;
            packet_error_seen <= 1'b0;
            packet_done_count <= 32'd0;
            packet_error_count <= 32'd0;
            input_accepted_bytes <= 32'd0;
            input_beats <= 32'd0;
            input_tlast_count <= 32'd0;
            input_first_word <= 32'd0;
            input_last_word <= 32'd0;
            input_byte_checksum <= 32'd0;
            accepted_output_total <= 32'd0;
            output_stalled_cycles <= 32'd0;
            aw_pending <= 1'b0;
            awaddr_hold <= 32'd0;
            w_pending <= 1'b0;
            wdata_hold <= 32'd0;
            wstrb_hold <= 4'd0;
            s_axi_bresp <= 2'b00;
            s_axi_bvalid <= 1'b0;
            s_axi_rdata <= 32'd0;
            s_axi_rresp <= 2'b00;
            s_axi_rvalid <= 1'b0;
        end else begin
            if (pipe_packet_done) begin
                packet_done_seen <= 1'b1;
                packet_done_count <= packet_done_count + 1'b1;
            end
            if (pipe_packet_error) begin
                packet_error_seen <= 1'b1;
                packet_error_count <= packet_error_count + 1'b1;
            end
            if (input_fire) begin
                if (input_beats == 0) input_first_word <= s_axis_tdata;
                input_last_word <= s_axis_tdata;
                input_beats <= input_beats + 1'b1;
                input_accepted_bytes <= input_accepted_bytes + accepted_lane_count;
                input_byte_checksum <= input_byte_checksum + accepted_lane_sum;
                if (s_axis_tlast) input_tlast_count <= input_tlast_count + 1'b1;
            end
            if (output_fire) accepted_output_total <= accepted_output_total + 1'b1;
            if (pipe_m_tvalid && !pipe_m_tready)
                output_stalled_cycles <= output_stalled_cycles + 1'b1;

            if (write_commit) begin
                aw_pending <= 1'b0;
                w_pending <= 1'b0;
                s_axi_bvalid <= 1'b1;
                if (effective_awaddr == 32'd0 || effective_awaddr == BASE_ADDR) begin
                    s_axi_bresp <= 2'b00;
                    if (effective_wstrb[0]) drain_enable <= effective_wdata[0];
                end else begin
                    s_axi_bresp <= 2'b10;
                end
            end else begin
                if (aw_fire) begin
                    aw_pending <= 1'b1;
                    awaddr_hold <= s_axi_awaddr;
                end
                if (w_fire) begin
                    w_pending <= 1'b1;
                    wdata_hold <= s_axi_wdata;
                    wstrb_hold <= s_axi_wstrb;
                end
                if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 1'b0;
            end

            if (s_axi_arvalid && s_axi_arready) begin
                s_axi_rvalid <= 1'b1;
                if (((s_axi_araddr[31:8] != 0) &&
                     (s_axi_araddr[31:8] != BASE_ADDR[31:8])) ||
                    s_axi_araddr[1:0] != 0) begin
                    s_axi_rdata <= 32'd0;
                    s_axi_rresp <= 2'b10;
                end else begin
                    s_axi_rresp <= 2'b00;
                    case (s_axi_araddr[7:0])
                        8'h00: s_axi_rdata <= {31'd0,drain_enable};
                        8'h04: s_axi_rdata <= MAGIC;
                        8'h08: s_axi_rdata <= status_word;
                        8'h0c: s_axi_rdata <= {28'd0,pipe_error_code};
                        8'h10: s_axi_rdata <= pipe_frame_id;
                        8'h14: s_axi_rdata <= packet_done_count;
                        8'h18: s_axi_rdata <= packet_error_count;
                        8'h1c: s_axi_rdata <= input_accepted_bytes;
                        8'h20: s_axi_rdata <= input_beats;
                        8'h24: s_axi_rdata <= input_tlast_count;
                        8'h28: s_axi_rdata <= input_first_word;
                        8'h2c: s_axi_rdata <= input_last_word;
                        8'h30: s_axi_rdata <= input_byte_checksum;
                        8'h34: s_axi_rdata <= observed_pixels;
                        8'h38: s_axi_rdata <= observed_sofs;
                        8'h3c: s_axi_rdata <= observed_eols;
                        8'h40: s_axi_rdata <= observed_checksum;
                        8'h44: s_axi_rdata <= accepted_output_total;
                        8'h48: s_axi_rdata <= output_stalled_cycles;
                        default: begin
                            s_axi_rdata <= 32'd0;
                            s_axi_rresp <= 2'b10;
                        end
                    endcase
                end
            end else if (s_axi_rvalid && s_axi_rready) begin
                s_axi_rvalid <= 1'b0;
            end
        end
    end
endmodule
