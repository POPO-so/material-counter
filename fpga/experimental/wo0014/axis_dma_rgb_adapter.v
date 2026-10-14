// Independent WO0014 RGB packet parser; DMA TLAST is packet EOF, not video EOL.
module axis_dma_rgb_adapter #(
    parameter integer WIDTH=320,
    parameter integer HEIGHT=240
) (
    input wire aclk, input wire aresetn,
    input wire [31:0] s_tdata, input wire [3:0] s_tkeep,
    input wire s_tlast, input wire s_tvalid, output wire s_tready,
    output reg [23:0] m_tdata, output reg [0:0] m_tuser,
    output reg m_tlast, output reg m_tvalid, input wire m_tready,
    output reg packet_done, output reg packet_error,
    output reg [3:0] error_code, output reg [31:0] frame_id
);
    localparam [3:0] H0=0,H1=1,H2=2,H3=3,PAY=4,T0=5,T1=6,T2=7,T3=8,DROP=9;
    localparam [31:0] PIXELS=WIDTH*HEIGHT;
    localparam [31:0] PAYLOAD_BYTES=WIDTH*HEIGHT*3;
    localparam [31:0] GEOMETRY=(HEIGHT<<16)|WIDTH;
    reg [3:0] state;
    reg [63:0] reservoir;
    reg [3:0] buffered_bytes;
    reg [31:0] received_bytes, emitted_pixels, column;
    reg [31:0] payload_sum;
    wire output_space=!m_tvalid || m_tready;
    wire emit_pixel=(state==PAY) && buffered_bytes>=4'd3 && output_space;
    wire [3:0] after_emit=buffered_bytes-(emit_pixel ? 4'd3 : 4'd0);
    // Accepted payload words advance by four bytes until the constant tail.
    // Avoid a wide subtract feeding the checksum lane selects.
    localparam [3:0] TAIL_BYTES=(PAYLOAD_BYTES % 4 == 0) ? 4 : PAYLOAD_BYTES % 4;
    localparam [31:0] FULL_BYTES=PAYLOAD_BYTES-TAIL_BYTES;
    wire [3:0] incoming_bytes=(received_bytes<FULL_BYTES) ? 4'd4 : TAIL_BYTES;
    wire [3:0] expected_keep=(incoming_bytes==4) ? 4'hf :
                               (incoming_bytes==3) ? 4'h7 :
                               (incoming_bytes==2) ? 4'h3 : 4'h1;
    assign s_tready=aresetn && ((state!=PAY) ||
                      (received_bytes<PAYLOAD_BYTES && after_emit<=4'd4));
    wire take=s_tvalid && s_tready;
    wire payload_take=take && state==PAY && s_tkeep==expected_keep && !s_tlast;
    reg [63:0] next_reservoir;
    reg [3:0] next_buffered;
    wire [7:0] lane1=(incoming_bytes>=2) ? s_tdata[15:8] : 8'd0;
    wire [7:0] lane2=(incoming_bytes>=3) ? s_tdata[23:16] : 8'd0;
    wire [7:0] lane3=(incoming_bytes>=4) ? s_tdata[31:24] : 8'd0;
    wire [8:0] sum01={1'b0,s_tdata[7:0]}+{1'b0,lane1};
    wire [8:0] sum23={1'b0,lane2}+{1'b0,lane3};
    wire [9:0] incoming_sum={1'b0,sum01}+{1'b0,sum23};
    always @* begin
        next_reservoir=emit_pixel ? (reservoir>>24) : reservoir;
        next_buffered=after_emit;
        if (payload_take) begin
            // Ignore padding lanes in the final partial payload word.
            case (incoming_bytes)
                1: next_reservoir=next_reservoir | ({56'b0,s_tdata[7:0]} << (after_emit*8));
                2: next_reservoir=next_reservoir | ({48'b0,s_tdata[15:0]} << (after_emit*8));
                3: next_reservoir=next_reservoir | ({40'b0,s_tdata[23:0]} << (after_emit*8));
                default: next_reservoir=next_reservoir | ({32'b0,s_tdata} << (after_emit*8));
            endcase
            next_buffered=after_emit+incoming_bytes;
        end
    end
    task reject_packet;
        input [3:0] code;
        input ended;
        begin
            packet_error<=1'b1; error_code<=code;
            state<=ended ? H0 : DROP;
            reservoir<=64'b0; buffered_bytes<=4'b0;
            // An already offered pixel remains valid/stable until consumed.
        end
    endtask
    always @(posedge aclk) begin
        if (!aresetn) begin
            state<=H0; reservoir<=0; buffered_bytes<=0;
            received_bytes<=0; emitted_pixels<=0; column<=0; payload_sum<=0;
            m_tvalid<=0; m_tdata<=0; m_tuser<=0; m_tlast<=0;
            packet_done<=0; packet_error<=0; error_code<=0; frame_id<=0;
        end else begin
            packet_done<=0; packet_error<=0;
            if (output_space) m_tvalid<=0;
            if (state==PAY) begin
                reservoir<=next_reservoir; buffered_bytes<=next_buffered;
                if (payload_take) begin
                    received_bytes<=received_bytes+{28'b0,incoming_bytes};
                    payload_sum<=payload_sum+{22'b0,incoming_sum};
                end
                if (emit_pixel) begin
                    m_tdata<={reservoir[7:0],reservoir[15:8],reservoir[23:16]};
                    m_tuser<=(emitted_pixels==0);
                    m_tlast<=(column==WIDTH-1); m_tvalid<=1;
                    emitted_pixels<=emitted_pixels+1;
                    if (column==WIDTH-1) column<=0; else column<=column+1;
                    if (emitted_pixels==PIXELS-1) state<=T0;
                end
            end
            if (take) begin
                case (state)
                    H0: begin
                        if (s_tkeep!=4'hf) reject_packet(3,s_tlast);
                        else if (s_tlast) reject_packet(4,1'b1);
                        else if (s_tdata!=32'h574f3134) reject_packet(1,1'b0);
                        else begin state<=H1;error_code<=0;end
                    end
                    H1: begin
                        if (s_tkeep!=4'hf) reject_packet(3,s_tlast);
                        else if (s_tlast) reject_packet(4,1'b1);
                        else if (s_tdata!=GEOMETRY) reject_packet(1,1'b0);
                        else state<=H2;
                    end
                    H2: begin
                        if (s_tkeep!=4'hf) reject_packet(3,s_tlast);
                        else if (s_tlast) reject_packet(4,1'b1);
                        else if (s_tdata!=PAYLOAD_BYTES) reject_packet(2,1'b0);
                        else state<=H3;
                    end
                    H3: begin
                        if (s_tkeep!=4'hf) reject_packet(3,s_tlast);
                        else if (s_tlast) reject_packet(4,1'b1);
                        else begin
                            frame_id<=s_tdata;state<=PAY;received_bytes<=0;
                            emitted_pixels<=0;column<=0;payload_sum<=0;
                            reservoir<=0;buffered_bytes<=0;
                        end
                    end
                    PAY: begin
                        if (s_tkeep!=expected_keep) reject_packet(3,s_tlast);
                        else if (s_tlast) reject_packet(4,1'b1);
                    end
                    T0,T1,T2,T3: begin
                        if (s_tkeep!=4'hf) reject_packet(3,s_tlast);
                        else if (s_tlast!=(state==T3)) reject_packet(4,s_tlast);
                        else if ((state==T0 && s_tdata!=32'h454e4434) ||
                                 (state==T1 && s_tdata!=frame_id) ||
                                 (state==T2 && s_tdata!=payload_sum) ||
                                 (state==T3 && s_tdata!=32'hc0dec014)) reject_packet(5,s_tlast);
                        else if (state==T3) begin state<=H0;packet_done<=1;end
                        else state<=state+1'b1;
                    end
                    DROP: if (s_tlast) state<=H0;
                    default: reject_packet(1,s_tlast);
                endcase
            end
        end
    end
endmodule
