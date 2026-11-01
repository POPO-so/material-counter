// Candidate raw Gray8 DMA adapter: one 640x400 frame, four little-endian
// pixels per full AXIS beat, and TLAST only on the final beat.
module axis_raw_gray_adapter #(
    parameter integer WIDTH=640,
    parameter integer HEIGHT=400
) (
    input wire aclk, input wire aresetn,
    input wire [31:0] s_tdata, input wire [3:0] s_tkeep,
    input wire s_tlast, input wire s_tvalid, output wire s_tready,
    output reg [7:0] m_tdata, output reg m_tuser,
    output reg m_tlast, output reg m_tvalid, input wire m_tready,
    input wire [31:0] cfg_frame_id,
    output reg packet_done, output reg packet_error,
    output reg [3:0] error_code, output reg [31:0] frame_id
);
    localparam [1:0] IDLE=0, DATA=1, DROP=2, LAST=3;
    localparam [31:0] BEATS=(WIDTH*HEIGHT)/4;
    reg [1:0] state;
    reg [31:0] beat_count;
    reg [31:0] pixel_count;
    reg [31:0] column;
    reg [31:0] row;
    reg [31:0] lanes;
    reg [2:0] lane_index;
    reg [2:0] lanes_left;
    wire output_space=!m_tvalid || m_tready;
    assign s_tready=aresetn && (state==DROP || (state==IDLE) ||
                      (state==DATA && lanes_left==0));
    wire take=s_tvalid && s_tready;
    wire emit_pixel=((state==DATA)||(state==LAST)) && lanes_left!=0 && output_space;
    wire final_beat=(beat_count==BEATS-1);
    task reject_frame;
        input [3:0] code;
        input ended;
        begin
            packet_error<=1'b1;
            error_code<=code;
            lanes_left<=0;
            state<=ended ? IDLE : DROP;
        end
    endtask
    always @(posedge aclk) begin
        if (!aresetn) begin
            state<=IDLE; beat_count<=0; pixel_count<=0; column<=0; row<=0;
            lanes<=0; lane_index<=0; lanes_left<=0;
            m_tdata<=0; m_tuser<=0; m_tlast<=0; m_tvalid<=0;
            packet_done<=0; packet_error<=0; error_code<=0; frame_id<=0;
        end else begin
            packet_done<=0; packet_error<=0;
            if (output_space) m_tvalid<=0;
            if (emit_pixel) begin
                case(lane_index)
                    0:m_tdata<=lanes[7:0];
                    1:m_tdata<=lanes[15:8];
                    2:m_tdata<=lanes[23:16];
                    default:m_tdata<=lanes[31:24];
                endcase
                m_tuser<=(pixel_count==0);
                m_tlast<=(column==WIDTH-1);
                m_tvalid<=1;
                lane_index<=lane_index+1'b1;
                lanes_left<=lanes_left-1'b1;
                pixel_count<=pixel_count+1'b1;
                if(column==WIDTH-1)begin column<=0;row<=row+1'b1;end
                else column<=column+1'b1;
                if(state==LAST && lanes_left==1)begin
                    state<=IDLE;beat_count<=0;packet_done<=1'b1;
                end
            end
            if(take) begin
                case(state)
                    IDLE: begin
                        if(s_tkeep!=4'hf) reject_frame(3,s_tlast);
                        else if(s_tlast) reject_frame(4,1'b1);
                        else begin
                            state<=DATA; beat_count<=1; pixel_count<=0;
                            column<=0;row<=0;lanes<=s_tdata;lane_index<=0;
                            lanes_left<=4;frame_id<=cfg_frame_id;error_code<=0;
                            if(final_beat) reject_frame(4,1'b0);
                        end
                    end
                    DATA: begin
                        if(s_tkeep!=4'hf) reject_frame(3,s_tlast);
                        else if(s_tlast!=final_beat) reject_frame(4,s_tlast);
                        else if(!final_beat) begin
                            beat_count<=beat_count+1'b1;
                            lanes<=s_tdata;lane_index<=0;lanes_left<=4;
                        end else begin
                            lanes<=s_tdata;lane_index<=0;lanes_left<=4;state<=LAST;
                        end
                    end
                    DROP: if(s_tlast) state<=IDLE;
                    default: reject_frame(1,s_tlast);
                endcase
            end
        end
    end
endmodule
