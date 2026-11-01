// Full-size Binary8 morphology with explicit zero padding and row-phase flow.
module axis_morphology3x3 #(
    parameter integer FRAME_WIDTH = 320,
    parameter integer FRAME_HEIGHT = 240,
    parameter integer MODE = 0 // 0 bypass, 1 erosion, 2 dilation
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
    localparam integer X_BITS = (FRAME_WIDTH < 2) ? 1 : $clog2(FRAME_WIDTH + 1);
    localparam integer Y_BITS = (FRAME_HEIGHT < 2) ? 1 : $clog2(FRAME_HEIGHT);
    localparam [X_BITS-1:0] LAST_X = FRAME_WIDTH - 1;
    localparam [X_BITS-1:0] PAD_X = FRAME_WIDTH;
    localparam [Y_BITS-1:0] LAST_Y = FRAME_HEIGHT - 1;

    // Separate read/write addresses infer small distributed line RAMs. Do not
    // reset the arrays: invalid rows are masked until real data has filled them.
    (* ram_style = "distributed" *) reg line0 [0:FRAME_WIDTH-1];
    (* ram_style = "distributed" *) reg line1 [0:FRAME_WIDTH-1];
    (* ram_style = "distributed" *) reg line2 [0:FRAME_WIDTH-1];
    reg emitting;
    reg synced;
    reg bottom_pad;
    reg [X_BITS-1:0] input_x;
    reg [X_BITS-1:0] scan_x;
    reg [Y_BITS-1:0] input_y;
    reg [Y_BITS-1:0] output_y;
    reg [1:0] write_bank;
    reg [1:0] top_bank;
    reg [1:0] middle_bank;
    reg [1:0] bottom_bank;
    reg [1:0] top_taps;
    reg [1:0] middle_taps;
    reg [1:0] bottom_taps;

    wire [1:0] next_write_bank = (write_bank == 2'd2) ? 2'd0 : write_bank + 2'd1;
    wire [1:0] previous_write_bank = (write_bank == 2'd0) ? 2'd2 : write_bank - 2'd1;

    assign s_tready = !emitting;
    wire accept_pixel = aresetn && s_tvalid && s_tready && (synced || s_tuser[0]);
    wire foreground = (s_tdata == 8'hff);
    wire output_space = !m_tvalid || m_tready;
    // PAD_X is an autonomous virtual column; it never reads outside a line.
    wire read0 = (scan_x == PAD_X) ? 1'b0 : line0[scan_x];
    wire read1 = (scan_x == PAD_X) ? 1'b0 : line1[scan_x];
    wire read2 = (scan_x == PAD_X) ? 1'b0 : line2[scan_x];
    wire top_pixel = (output_y == 0) ? 1'b0 :
                     ((top_bank == 0) ? read0 : ((top_bank == 1) ? read1 : read2));
    wire middle_pixel = (middle_bank == 0) ? read0 :
                        ((middle_bank == 1) ? read1 : read2);
    wire bottom_pixel = bottom_pad ? 1'b0 :
                        ((bottom_bank == 0) ? read0 : ((bottom_bank == 1) ? read1 : read2));
    wire [8:0] window_bits = {top_taps, top_pixel, middle_taps, middle_pixel,
                              bottom_taps, bottom_pixel};
    wire result_bit = (MODE == 1) ? (&window_bits) :
                      ((MODE == 2) ? (|window_bits) : middle_taps[0]);

    always @(posedge aclk) begin
        if (accept_pixel) begin
            case (write_bank)
                2'd0: line0[input_x] <= foreground;
                2'd1: line1[input_x] <= foreground;
                default: line2[input_x] <= foreground;
            endcase
        end
    end

    always @(posedge aclk) begin
        if (!aresetn) begin
            emitting <= 1'b0;
            synced <= 1'b0;
            bottom_pad <= 1'b0;
            input_x <= 0;
            scan_x <= 0;
            input_y <= 0;
            output_y <= 0;
            write_bank <= 0;
            top_bank <= 0;
            middle_bank <= 0;
            bottom_bank <= 0;
            top_taps <= 0;
            middle_taps <= 0;
            bottom_taps <= 0;
            m_tvalid <= 1'b0;
        end else begin
            if (output_space)
                m_tvalid <= 1'b0;

            if (accept_pixel) begin
                synced <= 1'b1;
                if (s_tlast) begin
                    input_x <= 0;
                    if (input_y == 0 && FRAME_HEIGHT > 1) begin
                        // A centered first row needs the next real row first.
                        input_y <= input_y + 1'b1;
                        write_bank <= next_write_bank;
                    end else begin
                        emitting <= 1'b1;
                        output_y <= (FRAME_HEIGHT == 1) ? 0 : input_y - 1'b1;
                        top_bank <= next_write_bank;
                        middle_bank <= (FRAME_HEIGHT == 1) ? write_bank : previous_write_bank;
                        bottom_bank <= write_bank;
                        bottom_pad <= (FRAME_HEIGHT == 1);
                        scan_x <= 0;
                        top_taps <= 0;
                        middle_taps <= 0;
                        bottom_taps <= 0;
                    end
                end else begin
                    input_x <= input_x + 1'b1;
                end
            end

            if (emitting && output_space) begin
                top_taps <= {top_taps[0], top_pixel};
                middle_taps <= {middle_taps[0], middle_pixel};
                bottom_taps <= {bottom_taps[0], bottom_pixel};
                if (scan_x != 0) begin
                    // scan_x supplies the right neighbor of output x=scan_x-1.
                    m_tdata <= result_bit ? 8'hff : 8'h00;
                    m_tuser <= (output_y == 0 && scan_x == 1);
                    m_tlast <= (scan_x == PAD_X);
                    m_tvalid <= 1'b1;
                end
                if (scan_x == PAD_X) begin
                    scan_x <= 0;
                    top_taps <= 0;
                    middle_taps <= 0;
                    bottom_taps <= 0;
                    if (output_y == LAST_Y) begin
                        // Last pixel may remain stalled in the elastic register.
                        // A new frame may capture rows, but cannot overwrite it.
                        emitting <= 1'b0;
                        synced <= 1'b0;
                        input_x <= 0;
                        input_y <= 0;
                        write_bank <= 0;
                        bottom_pad <= 1'b0;
                    end else if (input_y == LAST_Y) begin
                        // Drain the final row using a virtual zero bottom row.
                        output_y <= LAST_Y;
                        top_bank <= previous_write_bank;
                        middle_bank <= write_bank;
                        bottom_pad <= 1'b1;
                    end else begin
                        emitting <= 1'b0;
                        input_y <= input_y + 1'b1;
                        write_bank <= next_write_bank;
                    end
                end else begin
                    scan_x <= scan_x + 1'b1;
                end
            end
        end
    end

    generate
        if (FRAME_WIDTH < 1 || FRAME_HEIGHT < 1 || MODE < 0 || MODE > 2) begin : g_invalid
            initial $fatal(1, "morphology parameters require W,H>=1 and MODE in [0,2]");
        end
    endgenerate

`ifndef SYNTHESIS
    // The block processes well-formed raster frames; it does not repair markers.
    always @(posedge aclk) begin
        if (accept_pixel) begin
            if (s_tdata !== 8'h00 && s_tdata !== 8'hff)
                $fatal(1, "morphology input must be Binary8 00/FF");
            if (s_tuser[0] !== (input_x == 0 && input_y == 0))
                $fatal(1, "morphology unexpected SOF at x=%0d y=%0d", input_x, input_y);
            if (s_tlast !== (input_x == LAST_X))
                $fatal(1, "morphology unexpected EOL at x=%0d y=%0d", input_x, input_y);
        end
    end
`endif
endmodule
