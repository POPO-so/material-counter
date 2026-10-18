// Binary8 video input to a single fixed-size DMA packet. TLAST is EOF here.
module axis_s2mm_frame_packer #(
    parameter integer WIDTH = 320,
    parameter integer HEIGHT = 240
) (
    input wire aclk, input wire aresetn, input wire arm,
    input wire [7:0] s_tdata, input wire [0:0] s_tuser,
    input wire s_tlast, input wire s_tvalid, output wire s_tready,
    input wire packet_done, input wire packet_error,
    output reg [31:0] m_tdata, output wire [3:0] m_tkeep,
    output reg m_tlast, output reg m_tvalid, input wire m_tready,
    output reg busy, output reg done, output reg fault,
    output reg [7:0] error_code,
    output wire [31:0] accepted_pixels, output wire [31:0] sent_beats
);
    localparam integer PIXELS = WIDTH * HEIGHT;
    localparam integer BEATS = PIXELS / 4;
    localparam integer PB = $clog2(PIXELS+1);
    localparam integer BB = $clog2(BEATS+1);
    localparam integer CB = WIDTH > 1 ? $clog2(WIDTH) : 1;
    reg [PB-1:0] pixel_count;
    reg [BB-1:0] beat_count;
    reg [CB-1:0] column;
    reg [1:0] lane;
    reg [23:0] partial_word;
    reg [31:0] final_word;
    reg final_pending;
    reg packet_ok;
    wire out_space = !m_tvalid || m_tready;
    wire in_fire = s_tvalid && s_tready;
    wire out_fire = m_tvalid && m_tready;
    wire bad_sof = s_tuser[0] != (pixel_count == 0);
    wire bad_eol = s_tlast != (column == WIDTH-1);
    wire bad_value = s_tdata != 8'h00 && s_tdata != 8'hff;
    assign s_tready = aresetn && busy && !fault && !packet_error &&
                      pixel_count < PIXELS && out_space;
    assign m_tkeep = 4'hf;
    assign accepted_pixels = pixel_count;
    assign sent_beats = beat_count;
    generate
        if (WIDTH < 1 || HEIGHT < 1 || PIXELS < 4 || PIXELS % 4 != 0) begin : g_bad_geometry
            initial $fatal(1, "Packer requires positive geometry and full 32-bit words");
        end
    endgenerate

    always @(posedge aclk) begin
        if (!aresetn) begin
            pixel_count <= 0; beat_count <= 0; column <= 0; lane <= 0;
            partial_word <= 0; final_word <= 0; final_pending <= 0;
            packet_ok <= 0; m_tdata <= 0; m_tlast <= 0; m_tvalid <= 0;
            busy <= 0; done <= 0; fault <= 0; error_code <= 0;
        end else begin
            // Never retract an offered beat on fault: AXIS stability still holds.
            if (out_fire) begin
                m_tvalid <= 0;
                beat_count <= beat_count + 1'b1;
                if (m_tlast) begin busy <= 0; done <= 1; end
            end
            if (arm) begin
                if (busy || m_tvalid || fault) begin
                    fault <= 1; error_code <= 8'd1;
                end else begin
                    busy <= 1; done <= 0; pixel_count <= 0; beat_count <= 0;
                    column <= 0; lane <= 0; partial_word <= 0;
                    final_pending <= 0; packet_ok <= 0;
                end
            end
            if (busy && !fault) begin
                if (packet_done) packet_ok <= 1;
                if (packet_error) begin fault <= 1; error_code <= 8'd2; end
                else if (in_fire) begin
                    if (bad_sof || bad_eol || bad_value) begin
                        fault <= 1;
                        error_code <= bad_sof ? 8'd3 : bad_eol ? 8'd4 : 8'd5;
                    end else begin
                        pixel_count <= pixel_count + 1'b1;
                        column <= (column == WIDTH-1) ? 0 : column + 1'b1;
                        lane <= lane + 1'b1;
                        case (lane)
                            0: partial_word[7:0] <= s_tdata;
                            1: partial_word[15:8] <= s_tdata;
                            2: partial_word[23:16] <= s_tdata;
                            3: begin
                                if (pixel_count == PIXELS-1) begin
                                    final_word <= {s_tdata,partial_word};
                                    final_pending <= 1;
                                end else begin
                                    m_tdata <= {s_tdata,partial_word};
                                    m_tlast <= 0; m_tvalid <= 1;
                                end
                            end
                        endcase
                    end
                end
                // Keep the final word private until the input trailer is valid.
                // Adapter trailer acceptance is independent of this output slot.
                if (!packet_error && final_pending && (packet_ok || packet_done) && out_space) begin
                    if (pixel_count != PIXELS || lane != 0 || beat_count != BEATS-1) begin
                        fault <= 1; error_code <= 8'd6;
                    end else begin
                        m_tdata <= final_word; m_tlast <= 1; m_tvalid <= 1;
                        final_pending <= 0;
                    end
                end
            end
        end
    end
endmodule
