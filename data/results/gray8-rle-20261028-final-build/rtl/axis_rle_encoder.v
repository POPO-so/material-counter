// Bounded, lossless Binary8 raster -> RLE_PACKET_V1. TLAST ends the whole packet.
module axis_rle_encoder #(
    parameter integer WIDTH=640, HEIGHT=400, MAX_RUNS=16384
) (
    input wire aclk, aresetn, start,
    input wire [31:0] cfg_frame_id, cfg_params_version,
    input wire [63:0] cfg_timestamp,
    input wire [15:0] cfg_roi_x0,cfg_roi_y0,cfg_roi_x1,cfg_roi_y1,
    input wire [7:0] cfg_threshold,
    input wire [31:0] cfg_offset,
    input wire [1:0] cfg_morph,
    input wire [7:0] s_tdata,
    input wire s_tuser,s_tlast,s_tvalid, output wire s_tready,
    input wire packet_done,packet_error, input wire [3:0] input_error_code,
    output reg [31:0] m_tdata, output wire [3:0] m_tkeep,
    output reg m_tlast,m_tvalid, input wire m_tready,
    output reg busy,done, output reg [31:0] actual_bytes
);
    localparam integer PROJ_WORDS=(WIDTH+HEIGHT+1)/2;
    localparam [3:0] IDLE=0,CLEAR=1,HEADER=2,PIXELS=3,WAIT_PACKET=4,
                     PROJECTION=5,TRAILER=6,FINISH=7;
    reg [3:0] state;
    reg [31:0] frame_id,params_version,offset;
    reg [63:0] timestamp;
    reg [15:0] x0,y0,x1,y1;
    reg [7:0] threshold;
    reg [1:0] morph;
    reg [31:0] index,clear_index,x,y,run_start,row_fg;
    reg [31:0] total_fg,total_runs,emitted_runs,accepted_pixels,checksum,status,error;
    reg in_run,packet_seen,error_seen;
    (* ram_style="distributed" *) reg [15:0] rows[0:HEIGHT-1];
    (* ram_style="distributed" *) reg [15:0] columns[0:WIDTH-1];
    wire space=!m_tvalid || m_tready;
    wire fg=(s_tdata==8'hff) && x>=x0 && x<x1 && y>=y0 && y<y1;
    wire close_run=(in_run && !fg) || (fg && x==WIDTH-1);
    wire [31:0] close_start=in_run ? run_start : x;
    wire [31:0] close_end=fg ? x : x-1;
    wire [31:0] run_word=(y<<20)|(close_start<<10)|close_end;
    assign s_tready=aresetn && state==PIXELS && space && !error_seen && !packet_error;
    assign m_tkeep=4'hf;
    function [15:0] projection_value;
        input integer address;
        begin
            if(address<HEIGHT) projection_value=rows[address];
            else if(address<HEIGHT+WIDTH) projection_value=columns[address-HEIGHT];
            else projection_value=0;
        end
    endfunction
    reg [31:0] header_word,trailer_word,projection_word;
    always @* begin
        case(index)
            0: header_word=32'h31454c52;
            1: header_word=1;
            2: header_word=(HEIGHT<<16)|WIDTH;
            3: header_word=frame_id;
            4: header_word=params_version;
            5: header_word={y0,x0};
            6: header_word={y1,x1};
            7: header_word=timestamp[31:0];
            8: header_word=timestamp[63:32];
            9: header_word=1;
            10: header_word=PROJ_WORDS;
            11: header_word=MAX_RUNS;
            12: header_word={24'b0,threshold};
            13: header_word=offset;
            14: header_word={30'b0,morph};
            default: header_word=0;
        endcase
        projection_word={projection_value(index*2+1),projection_value(index*2)};
        case(index)
            0: trailer_word=32'h31444e45;
            1: trailer_word=frame_id;
            2: trailer_word=params_version;
            3: trailer_word=total_fg;
            4: trailer_word=total_runs;
            5: trailer_word=emitted_runs;
            6: trailer_word=status;
            7: trailer_word=actual_bytes;
            8: trailer_word=checksum;
            9: trailer_word=error;
            10: trailer_word=accepted_pixels;
            15: trailer_word=32'hc0dec0de;
            default: trailer_word=0;
        endcase
    end
    always @(posedge aclk) begin
        if(!aresetn) begin
            state<=IDLE;busy<=0;done<=0;m_tvalid<=0;m_tlast<=0;m_tdata<=0;
            actual_bytes<=0;index<=0;clear_index<=0;x<=0;y<=0;in_run<=0;
            row_fg<=0;run_start<=0;total_fg<=0;total_runs<=0;emitted_runs<=0;
            accepted_pixels<=0;checksum<=0;status<=0;error<=0;packet_seen<=0;error_seen<=0;
            frame_id<=0;params_version<=0;timestamp<=0;x0<=0;y0<=0;x1<=0;y1<=0;
            threshold<=0;offset<=0;morph<=0;
        end else begin
            done<=0;
            if(space) begin m_tvalid<=0;m_tlast<=0;end
            if(busy && packet_done) packet_seen<=1;
            if(busy && packet_error) begin
                error_seen<=1;status<=status|2;error<={28'b0,input_error_code};
            end
            case(state)
                IDLE: if(start) begin
                    busy<=1;frame_id<=cfg_frame_id;params_version<=cfg_params_version;
                    timestamp<=cfg_timestamp;x0<=cfg_roi_x0;y0<=cfg_roi_y0;
                    x1<=cfg_roi_x1;y1<=cfg_roi_y1;threshold<=cfg_threshold;
                    offset<=cfg_offset;morph<=cfg_morph;clear_index<=0;index<=0;
                    x<=0;y<=0;row_fg<=0;in_run<=0;total_fg<=0;total_runs<=0;
                    emitted_runs<=0;accepted_pixels<=0;checksum<=0;status<=0;error<=0;
                    actual_bytes<=0;packet_seen<=0;error_seen<=0;state<=CLEAR;
                end
                CLEAR: begin
                    if(clear_index<HEIGHT) rows[clear_index]<=0;
                    if(clear_index<WIDTH) columns[clear_index]<=0;
                    if(clear_index+1>=WIDTH && clear_index+1>=HEIGHT) begin
                        index<=0;state<=HEADER;
                    end else clear_index<=clear_index+1;
                end
                HEADER: if(space) begin
                    m_tdata<=header_word;m_tvalid<=1;checksum<=checksum+header_word;
                    if(index==15) begin index<=0;state<=PIXELS;end else index<=index+1;
                end
                PIXELS: begin
                    if(error_seen || packet_error) begin
                        if(y<HEIGHT) rows[y]<=row_fg;
                        in_run<=0;row_fg<=0;index<=0;state<=WAIT_PACKET;
                    end else if(s_tvalid && s_tready) begin
                        accepted_pixels<=accepted_pixels+1;
                        if(s_tuser!=(accepted_pixels==0) || s_tlast!=(x==WIDTH-1) ||
                           (s_tdata!=0 && s_tdata!=255)) begin
                            status<=status|2;error<=8;error_seen<=1;
                        end
                        if(fg) begin
                            total_fg<=total_fg+1;row_fg<=row_fg+1;
                            columns[x]<=columns[x]+1'b1;
                            if(!in_run) begin in_run<=1;run_start<=x;end
                        end
                        if(close_run) begin
                            in_run<=0;total_runs<=total_runs+1;
                            if(emitted_runs<MAX_RUNS) begin
                                m_tdata<=run_word;m_tvalid<=1;emitted_runs<=emitted_runs+1;
                                checksum<=checksum+run_word;
                            end else status<=status|1;
                        end
                        if(x==WIDTH-1) begin
                            rows[y]<=row_fg+(fg?1:0);row_fg<=0;x<=0;
                            if(y==HEIGHT-1) state<=WAIT_PACKET;else y<=y+1;
                        end else x<=x+1;
                    end
                end
                WAIT_PACKET: if(space && (packet_seen || error_seen || packet_error || packet_done)) begin
                    index<=0;actual_bytes<=(32+emitted_runs+PROJ_WORDS)*4;state<=PROJECTION;
                end
                PROJECTION: if(space) begin
                    m_tdata<=projection_word;m_tvalid<=1;checksum<=checksum+projection_word;
                    if(index==PROJ_WORDS-1) begin index<=0;state<=TRAILER;end else index<=index+1;
                end
                TRAILER: if(space) begin
                    m_tdata<=trailer_word;m_tvalid<=1;
                    if(index==15) begin m_tlast<=1;state<=FINISH;end else index<=index+1;
                end
                FINISH: if(space) begin busy<=0;done<=1;state<=IDLE;end
                default: state<=IDLE;
            endcase
        end
    end
    initial begin
        if(WIDTH<1 || WIDTH>1024 || HEIGHT<1 || HEIGHT>512 || MAX_RUNS<1)
            $fatal(1,"invalid RLE geometry/capacity");
    end
endmodule
