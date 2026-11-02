`timescale 1ns/1ps
// Two mask banks. Mask producer never backpressures counting. A published bank
// remains immutable until the pixel-domain VSYNC swap has crossed back as ACK.
// Independent resets during use require a coordinated reset of all three domains.
// Status ports belong to the named domain; consumers must use a CDC snapshot.
module video_binary_overlay #(
  parameter integer WIDTH=640, HEIGHT=400, VIEW_X=384, VIEW_Y=146,
  parameter integer VIEW_WIDTH=WIDTH, VIEW_HEIGHT=HEIGHT,
  parameter integer MAX_BOXES=16, COUNT_ENABLE=1, COUNT_X=VIEW_X+VIEW_WIDTH-80,
  parameter integer COUNT_Y=(VIEW_Y>=12)?VIEW_Y-12:VIEW_Y
) (
  input wire mask_clk, mask_resetn,
  input wire tap_valid, tap_sof, tap_eol, tap_bit,
  input wire [31:0] tap_frame_id,
  input wire mask_accept,
  input wire [31:0] accepted_frame_id,
  input wire pixel_clk, pixel_resetn,
  input wire [23:0] vid_data24,
  input wire vid_active, vid_vsync, vid_hsync,
  input wire cfg_clk, cfg_resetn, cfg_commit,
  output wire cfg_ready,
  input wire cfg_enable,
  input wire [31:0] cfg_frame_id,
  input wire [4:0] cfg_box_count,
  input wire [31:0] cfg_total_count,
  input wire [MAX_BOXES*40-1:0] cfg_boxes,
  input wire [9:0] cfg_line_x,
  output reg [23:0] out_data24,
  output reg out_active, out_vsync, out_hsync,
  output reg [31:0] capture_count, drop_count, reject_count,
  output wire [31:0] captured_frame_id,
  output reg [31:0] displayed_frame_id,
  output wire capture_busy,
  output reg pending_valid,
  output wire completed_valid_status,
  output reg display_valid,
  output reg [31:0] mask_displayed_frame_id,
  output wire mask_display_valid
);
  localparam integer PIXELS=WIDTH*HEIGHT;
  localparam integer AW=(PIXELS<2)?1:$clog2(PIXELS);
  reg writing, write_bank, dropping;
  reg [AW-1:0] write_addr;
  reg [9:0] write_x;
  reg [31:0] writing_id, completed_id;
  reg completed_valid;
  reg publish_toggle, publish_bank;
  reg [31:0] publish_id;
  reg display_owned_mask, display_bank_mask;
  (* ASYNC_REG="TRUE" *) reg ack_sync1, ack_sync2;
  reg ack_seen;
  reg ack_toggle;
  assign captured_frame_id=completed_id;
  assign completed_valid_status=completed_valid;
  assign capture_busy=writing;
  assign mask_display_valid=display_owned_mask;
  wire free_bank=display_owned_mask ? ~display_bank_mask : 1'b0;
  // A single write-port template per bank is required for block-RAM inference.
  wire mem_start=tap_valid && tap_sof && !pending_valid && (tap_eol==(WIDTH==1));
  wire mem_continue=tap_valid && !tap_sof && writing && !dropping &&
       tap_frame_id==writing_id && (tap_eol==(write_x==WIDTH-1));
  wire mem_we=mask_resetn && (mem_start || mem_continue);
  wire mem_bank=mem_start ? free_bank : write_bank;
  wire [AW-1:0] mem_addr=mem_start ? {AW{1'b0}} : write_addr;
  // Each SOF starts a new capture. An unaccepted prior frame may be discarded;
  // software must accept after validation and before starting the next transfer.
  always @(posedge mask_clk) begin
    if(!mask_resetn) begin
      writing<=0; write_bank<=0; dropping<=0; write_addr<=0; write_x<=0;
      writing_id<=0; completed_id<=0; completed_valid<=0;
      publish_toggle<=0; publish_bank<=0; publish_id<=0; pending_valid<=0;
      display_owned_mask<=0; display_bank_mask<=0;mask_displayed_frame_id<=0;
      ack_sync1<=0; ack_sync2<=0; ack_seen<=0;
      capture_count<=0; drop_count<=0; reject_count<=0;
    end else begin
      ack_sync1<=ack_toggle; ack_sync2<=ack_sync1;
      if(ack_sync2!=ack_seen) begin
        ack_seen<=ack_sync2; display_owned_mask<=1;
        display_bank_mask<=publish_bank;mask_displayed_frame_id<=publish_id; pending_valid<=0;
      end
      if(tap_valid) begin
        if(tap_sof) begin
          completed_valid<=0; writing_id<=tap_frame_id;
          write_bank<=free_bank; write_addr<=1; write_x<=(WIDTH==1)?0:1;
          writing<=(PIXELS!=1); dropping<=pending_valid;
          if(writing && !dropping) reject_count<=reject_count+1'b1;
          if(pending_valid) drop_count<=drop_count+1'b1;
          else if(tap_eol!=(WIDTH==1)) begin
            dropping<=1; reject_count<=reject_count+1'b1;
          end else begin
            if(PIXELS==1) begin
              completed_valid<=1; completed_id<=tap_frame_id;
              capture_count<=capture_count+1'b1;
            end
          end
        end else if(writing) begin
          if(!dropping) begin
            if(tap_frame_id!=writing_id || tap_eol!=(write_x==WIDTH-1)) begin
              dropping<=1; reject_count<=reject_count+1'b1;
            end else begin
              if(write_addr==PIXELS-1) begin
                completed_valid<=1; completed_id<=writing_id;
                capture_count<=capture_count+1'b1;
              end
            end
          end
          write_x<=(write_x==WIDTH-1)?0:write_x+1'b1;
          if(write_addr==PIXELS-1) begin writing<=0; dropping<=0; end
          else write_addr<=write_addr+1'b1;
        end
      end
      if(mask_accept) begin
        if(!completed_valid || completed_id!=accepted_frame_id || writing ||
           pending_valid || (tap_valid && tap_sof)) reject_count<=reject_count+1'b1;
        else begin
          publish_bank<=write_bank; publish_id<=completed_id;
          publish_toggle<=~publish_toggle; pending_valid<=1; completed_valid<=0;
        end
      end
    end
  end

  function [9:0] project_box_x;
    input [9:0] source_coord;
    begin
      // The production 640-to-512 mapping is floor(source*4/5). Keep this
      // arithmetic in cfg_clk, where it runs once per configuration word.
      if(WIDTH*4==VIEW_WIDTH*5)
        project_box_x=({22'd0,source_coord}<<2)/5;
      else
        project_box_x=(source_coord*VIEW_WIDTH)/WIDTH;
    end
  endfunction
  function [9:0] project_box_y;
    input [9:0] source_coord;
    begin
      if(HEIGHT*4==VIEW_HEIGHT*5)
        project_box_y=({22'd0,source_coord}<<2)/5;
      else
        project_box_y=(source_coord*VIEW_HEIGHT)/HEIGHT;
    end
  endfunction

  // The raw PS/AXI ABI stores four 10-bit source coordinates per box. The
  // private mailbox stores four projected viewport coordinates plus one
  // validity bit per box (41 bits), so pixel_clk never scales coordinates.
  wire [MAX_BOXES-1:0] cfg_box_valid;
  genvar cfg_box_index;
  generate for(cfg_box_index=0;cfg_box_index<MAX_BOXES;cfg_box_index=cfg_box_index+1) begin: g_cfg_box_valid
    wire [9:0] src_x0,src_x1,src_y0,src_y1;
    assign src_x0=cfg_boxes[cfg_box_index*40 +: 10];
    assign src_x1=cfg_boxes[cfg_box_index*40+10 +: 10];
    assign src_y0=cfg_boxes[cfg_box_index*40+20 +: 10];
    assign src_y1=cfg_boxes[cfg_box_index*40+30 +: 10];
    assign cfg_box_valid[cfg_box_index]=(src_x0<=src_x1) && (src_y0<=src_y1) &&
                                         (src_x1<WIDTH) && (src_y1<HEIGHT);
  end endgenerate

  localparam integer CFG_SCALE_OFFSET_WIDTH=(MAX_BOXES*41<2)?1:$clog2(MAX_BOXES*41);
  localparam integer CFG_SCALE_BOX_WIDTH=(MAX_BOXES<2)?1:$clog2(MAX_BOXES);
  localparam integer CFG_BCD_LSB=0;
  localparam integer CFG_LINE_LSB=CFG_BCD_LSB+40;
  localparam integer CFG_BOXES_LSB=CFG_LINE_LSB+11;
  localparam integer CFG_COUNT_LSB=CFG_BOXES_LSB+MAX_BOXES*41;
  localparam integer CFG_ID_LSB=CFG_COUNT_LSB+5;
  localparam integer CFG_ENABLE_BIT=CFG_ID_LSB+32;
  localparam integer CFG_PAYLOAD_WIDTH=CFG_ENABLE_BIT+1;
  reg cfg_cdc_send;
  wire cfg_cdc_src_rcv;
  reg [CFG_PAYLOAD_WIDTH-1:0] cfg_cdc_src_data;
  wire [CFG_PAYLOAD_WIDTH-1:0] cfg_cdc_dest_data;
  wire cfg_cdc_dest_req;
  reg cfg_cdc_dest_ack;
  reg cfg_scale_busy, cfg_scale_finalize, cfg_scale_enable;
  reg [31:0] cfg_scale_id;
  reg [4:0] cfg_scale_count;
  reg [MAX_BOXES*40-1:0] cfg_scale_source_boxes;
  reg [9:0] cfg_scale_source_line;
  reg [MAX_BOXES-1:0] cfg_scale_box_valid;
  reg [CFG_SCALE_BOX_WIDTH-1:0] cfg_scale_box_index;
  reg [1:0] cfg_scale_axis;
  reg [CFG_SCALE_OFFSET_WIDTH-1:0] cfg_scale_offset;
  wire [9:0] cfg_scale_coord=cfg_scale_source_boxes[cfg_scale_offset +: 10];
  reg [MAX_BOXES*41-1:0] cfg_mb_boxes;
  reg cfg_bcd_busy;
  reg [31:0] cfg_bcd_source;
  reg [4:0] cfg_bcd_index;
  reg [39:0] cfg_bcd_shift, cfg_bcd_value;
  integer cfg_bcd_digit;
  reg [39:0] cfg_bcd_adjusted;
  wire [39:0] cfg_bcd_next={cfg_bcd_adjusted[38:0],cfg_bcd_source[31-cfg_bcd_index]};
  wire [39:0] cfg_dest_bcd=cfg_cdc_dest_data[CFG_BCD_LSB +: 40];
  wire [10:0] cfg_dest_line=cfg_cdc_dest_data[CFG_LINE_LSB +: 11];
  wire [MAX_BOXES*41-1:0] cfg_dest_boxes=cfg_cdc_dest_data[CFG_BOXES_LSB +: MAX_BOXES*41];
  wire [4:0] cfg_dest_count=cfg_cdc_dest_data[CFG_COUNT_LSB +: 5];
  wire [31:0] cfg_dest_id=cfg_cdc_dest_data[CFG_ID_LSB +: 32];
  wire cfg_dest_enable=cfg_cdc_dest_data[CFG_ENABLE_BIT];
  function [39:0] add3_to_bcd;
    input [39:0] value;
    integer n;
    reg [39:0] adjusted;
    begin
      adjusted=value;
      for(n=0;n<10;n=n+1)
        if(adjusted[n*4 +: 4]>=5) adjusted[n*4 +: 4]=adjusted[n*4 +: 4]+4'd3;
      add3_to_bcd=adjusted;
    end
  endfunction
  always @* cfg_bcd_adjusted=add3_to_bcd(cfg_bcd_shift);
  assign cfg_ready=cfg_resetn && !cfg_scale_busy && !cfg_scale_finalize && !cfg_bcd_busy &&
                   !cfg_cdc_send && !cfg_cdc_src_rcv;
  xpm_cdc_handshake #(
    .DEST_EXT_HSK(1), .DEST_SYNC_FF(4), .INIT_SYNC_FF(0),
    .SIM_ASSERT_CHK(0), .SRC_SYNC_FF(4), .WIDTH(CFG_PAYLOAD_WIDTH)
  ) cfg_payload_cdc (
    .src_clk(cfg_clk), .src_in(cfg_cdc_src_data), .src_send(cfg_cdc_send), .src_rcv(cfg_cdc_src_rcv),
    .dest_clk(pixel_clk), .dest_out(cfg_cdc_dest_data), .dest_req(cfg_cdc_dest_req),
    .dest_ack(cfg_cdc_dest_ack)
  );
  always @(posedge cfg_clk) begin
    if(!cfg_resetn) begin
      cfg_cdc_send<=0; cfg_cdc_src_data<=0;
      cfg_scale_busy<=0; cfg_scale_finalize<=0; cfg_scale_enable<=0; cfg_scale_id<=0; cfg_scale_count<=0;
      cfg_scale_source_boxes<=0; cfg_scale_source_line<=0; cfg_scale_box_valid<=0;
      cfg_scale_box_index<=0; cfg_scale_axis<=0; cfg_scale_offset<=0;
      cfg_mb_boxes<=0;
      cfg_bcd_busy<=0; cfg_bcd_source<=0; cfg_bcd_index<=0;
      cfg_bcd_shift<=0; cfg_bcd_value<=0;
    end else begin
      if(cfg_cdc_send && cfg_cdc_src_rcv) cfg_cdc_send<=0;
      if(cfg_commit && cfg_ready) begin
        cfg_scale_busy<=1;
        cfg_scale_enable<=cfg_enable; cfg_scale_id<=cfg_frame_id;
        cfg_scale_count<=cfg_box_count; cfg_scale_source_boxes<=cfg_boxes;
        cfg_scale_source_line<=cfg_line_x; cfg_scale_box_valid<=cfg_box_valid;
        cfg_scale_box_index<=0; cfg_scale_axis<=0; cfg_scale_offset<=0;
        cfg_bcd_busy<=1; cfg_bcd_source<=cfg_total_count;
        cfg_bcd_index<=0; cfg_bcd_shift<=0;
      end else if(cfg_scale_busy) begin
        if(!cfg_scale_axis[1])
          cfg_mb_boxes[cfg_scale_offset +: 10]<=project_box_x(cfg_scale_coord);
        else
          cfg_mb_boxes[cfg_scale_offset +: 10]<=project_box_y(cfg_scale_coord);

        if(cfg_scale_axis==3)
          cfg_mb_boxes[cfg_scale_offset+10]<=cfg_scale_box_valid[cfg_scale_box_index];

        if(cfg_scale_box_index==MAX_BOXES-1 && cfg_scale_axis==3) begin
          // Let the final validity bit settle before packing cfg_mb_boxes.
          // Nonblocking assignments in this cycle still expose its old value.
          cfg_scale_finalize<=1;
          cfg_scale_busy<=0;
        end else if(cfg_scale_axis==3) begin
          cfg_scale_box_index<=cfg_scale_box_index+1'b1;
          cfg_scale_axis<=0;
          cfg_scale_offset<=cfg_scale_offset+11;
        end else begin
          cfg_scale_axis<=cfg_scale_axis+1'b1;
          cfg_scale_offset<=cfg_scale_offset+10;
        end
      end
      if(cfg_bcd_busy) begin
        cfg_bcd_shift<=cfg_bcd_next;
        if(cfg_bcd_index==31) begin
          cfg_bcd_value<=cfg_bcd_next;
          cfg_bcd_busy<=0;
        end else cfg_bcd_index<=cfg_bcd_index+1'b1;
      end
      if(cfg_scale_finalize && !cfg_bcd_busy) begin
        cfg_cdc_src_data<={cfg_scale_enable,cfg_scale_id,cfg_scale_count,
                           cfg_mb_boxes,
                           {cfg_scale_source_line<WIDTH,project_box_x(cfg_scale_source_line)},
                           cfg_bcd_value};
        cfg_cdc_send<=1;
        cfg_scale_finalize<=0;
      end
    end
  end

  (* ASYNC_REG="TRUE" *) reg publish_sync1, publish_sync2;
  reg publish_seen, display_bank;
  // First pixel-clock capture of the bundled-data mailbox. These registers are
  // the only endpoints for cfg_clk payload paths; the active copy changes only
  // after a complete payload has been captured and a frame boundary arrives.
  reg enable_p;
  reg [31:0] id_p;
  reg [44:0] count_p;
  reg [MAX_BOXES*41-1:0] boxes_p;
  reg [10:0] line_p;
  reg cfg_mailbox_valid_p;
  reg enable_active_p;
  reg [31:0] id_active_p;
  reg [4:0] count_active_p;
  reg [MAX_BOXES*41-1:0] boxes_active_p;
  reg [10:0] line_active_p;
  reg [39:0] count_bcd_active_p;
  reg [11:0] x,y;
  reg active_prev,vsync_prev;
  wire frame_boundary=vid_vsync && !vsync_prev;
  wire in_view=vid_active && display_valid && enable_active_p &&
       x>=VIEW_X && x<VIEW_X+VIEW_WIDTH && y>=VIEW_Y && y<VIEW_Y+VIEW_HEIGHT;
  wire [9:0] local_x=x-VIEW_X,local_y=y-VIEW_Y;
  wire [9:0] source_x,source_y_next;
  wire [11:0] scan_next_y=y+12'd1;
  wire [11:0] next_local_y=scan_next_y-VIEW_Y;
  reg [AW-1:0] row_base;
  wire [AW-1:0] row_base_next=source_y_next*WIDTH;
  generate
    // The production preview maps 512x320 source pixels onto a 640x400
    // window. For this exact 5:4 scale, floor(v*5/4) == v + floor(v/4).
    // Keep the generic parameterized mapping for other viewports.
    if(WIDTH*4==VIEW_WIDTH*5) begin : g_source_x_5_4
      assign source_x=local_x+(local_x>>2);
    end else begin : g_source_x_generic
      assign source_x=(local_x*WIDTH)/VIEW_WIDTH;
    end
    if(HEIGHT*4==VIEW_HEIGHT*5) begin : g_source_y_5_4
      assign source_y_next=next_local_y+(next_local_y>>2);
    end else begin : g_source_y_generic
      assign source_y_next=(next_local_y*HEIGHT)/VIEW_HEIGHT;
    end
  endgenerate
  localparam integer BOX_GROUPS=(MAX_BOXES+3)/4;
  reg [MAX_BOXES-1:0] box_x_inside_s1,box_y_inside_s1;
  reg [MAX_BOXES-1:0] box_x_edge_s1,box_y_edge_s1,box_count_valid_s1;
  reg box_view_s1,line_hit_s1,line_hit_s2;
  reg [BOX_GROUPS-1:0] box_group_hit_s2;
  wire [MAX_BOXES-1:0] box_candidate_s1;
  assign box_candidate_s1=box_view_s1 ?
      (box_x_inside_s1 & box_y_inside_s1 &
       (box_x_edge_s1 | box_y_edge_s1) & box_count_valid_s1) : {MAX_BOXES{1'b0}};
  genvar box_index;
  generate for(box_index=0;box_index<MAX_BOXES;box_index=box_index+1) begin: g_box_compare_s1
    wire [9:0] bx0=boxes_active_p[box_index*41 +: 10];
    wire [9:0] bx1=boxes_active_p[box_index*41+10 +: 10];
    wire [9:0] by0=boxes_active_p[box_index*41+20 +: 10];
    wire [9:0] by1=boxes_active_p[box_index*41+30 +: 10];
    always @(posedge pixel_clk) begin
      if(!pixel_resetn) begin
        box_x_inside_s1[box_index]<=0;box_y_inside_s1[box_index]<=0;
        box_x_edge_s1[box_index]<=0;box_y_edge_s1[box_index]<=0;
        box_count_valid_s1[box_index]<=0;
      end else begin
        box_x_inside_s1[box_index]<=(local_x>=bx0) && (local_x<=bx1);
        box_y_inside_s1[box_index]<=(local_y>=by0) && (local_y<=by1);
        box_x_edge_s1[box_index]<=(local_x==bx0) || (local_x==bx1);
        box_y_edge_s1[box_index]<=(local_y==by0) || (local_y==by1);
        box_count_valid_s1[box_index]<=(box_index<count_active_p) &&
                                           boxes_active_p[box_index*41+40];
      end
    end
  end endgenerate
  genvar group_index;
  generate for(group_index=0;group_index<BOX_GROUPS;group_index=group_index+1) begin: g_box_reduce_s2
    localparam integer GROUP_START=group_index*4;
    localparam integer GROUP_WIDTH=(MAX_BOXES-GROUP_START>=4)?4:(MAX_BOXES-GROUP_START);
    wire [GROUP_WIDTH-1:0] group_candidate=box_candidate_s1[GROUP_START +: GROUP_WIDTH];
    always @(posedge pixel_clk) begin
      if(!pixel_resetn) box_group_hit_s2[group_index]<=0;
      else box_group_hit_s2[group_index]<=|group_candidate;
    end
  end endgenerate

  function [3:0] count_digit;
    input [39:0] bcd;
    input [3:0] index;
    begin
      case(index)
        0:count_digit=bcd[39:36]; 1:count_digit=bcd[35:32];
        2:count_digit=bcd[31:28]; 3:count_digit=bcd[27:24];
        4:count_digit=bcd[23:20]; 5:count_digit=bcd[19:16];
        6:count_digit=bcd[15:12]; 7:count_digit=bcd[11:8];
        8:count_digit=bcd[7:4]; default:count_digit=bcd[3:0];
      endcase
    end
  endfunction
  function [6:0] seven_segment;
    input [3:0] digit;
    begin
      case(digit)
        0:seven_segment=7'b1111110; 1:seven_segment=7'b0110000;
        2:seven_segment=7'b1101101; 3:seven_segment=7'b1111001;
        4:seven_segment=7'b0110011; 5:seven_segment=7'b1011011;
        6:seven_segment=7'b1011111; 7:seven_segment=7'b1110000;
        8:seven_segment=7'b1111111; 9:seven_segment=7'b1111011;
        default:seven_segment=7'b0000000;
      endcase
    end
  endfunction
  wire [11:0] count_x_offset=x-COUNT_X;
  wire [11:0] count_y_offset=y-COUNT_Y;
  wire [3:0] count_digit_index=count_x_offset[6:3];
  wire [2:0] count_glyph_x=count_x_offset[2:0];
  wire [3:0] count_glyph_y=count_y_offset[3:0];
  wire [6:0] count_segments=seven_segment(count_digit(count_bcd_active_p,count_digit_index));
  wire count_panel=(COUNT_ENABLE!=0) && vid_active && id_active_p==displayed_frame_id &&
      x>=COUNT_X && x<COUNT_X+80 && y>=COUNT_Y && y<COUNT_Y+11;
  wire count_glyph=count_panel && (
      (count_glyph_y==0 && count_glyph_x>=1 && count_glyph_x<=5 && count_segments[6]) ||
      (count_glyph_x==6 && count_glyph_y>=1 && count_glyph_y<=4 && count_segments[5]) ||
      (count_glyph_x==6 && count_glyph_y>=6 && count_glyph_y<=9 && count_segments[4]) ||
      (count_glyph_y==10 && count_glyph_x>=1 && count_glyph_x<=5 && count_segments[3]) ||
      (count_glyph_x==0 && count_glyph_y>=6 && count_glyph_y<=9 && count_segments[2]) ||
      (count_glyph_x==0 && count_glyph_y>=1 && count_glyph_y<=4 && count_segments[1]) ||
      (count_glyph_y==5 && count_glyph_x>=1 && count_glyph_x<=5 && count_segments[0]));
  // Advance the source row base during horizontal blanking. This removes the
  // viewport Y scaling and source-row multiply from the per-pixel BRAM address
  // path; x contributes only the within-row offset on each active pixel.
  wire [AW-1:0] pixel_addr=row_base+source_x;
  wire bank0_q,bank1_q;
  reg bank_q;
  wire mask_q=bank_q?bank1_q:bank0_q;
  binary_mask_ram #(.DEPTH(PIXELS),.AW(AW)) bank0_ram(
    .wr_clk(mask_clk),.we(mem_we&&!mem_bank),.wr_addr(mem_addr),.data(tap_bit),
    .rd_clk(pixel_clk),.re(in_view),.rd_addr(pixel_addr),.q(bank0_q));
  binary_mask_ram #(.DEPTH(PIXELS),.AW(AW)) bank1_ram(
    .wr_clk(mask_clk),.we(mem_we&&mem_bank),.wr_addr(mem_addr),.data(tap_bit),
    .rd_clk(pixel_clk),.re(in_view),.rd_addr(pixel_addr),.q(bank1_q));
  reg use_s1,use_s2,mask_s2;
  reg count_panel_s1,count_panel_s2,count_glyph_s1,count_glyph_s2;
  reg [23:0] base_s1,base_s2;
  reg active_s1,active_s2,vsync_s1,vsync_s2,hsync_s1,hsync_s2;
  // The box compare and four-box reduction use two pixel-clock stages. Carry
  // every other pixel field through the same two stages before the output
  // register; BRAM's synchronous read is captured into mask_s2 on stage two.
  always @(posedge pixel_clk) begin
    if(!pixel_resetn) begin
      publish_sync1<=0;publish_sync2<=0;publish_seen<=0;
      cfg_cdc_dest_ack<=0;cfg_mailbox_valid_p<=0;
      enable_p<=0;id_p<=0;count_p<=0;boxes_p<=0;line_p<=0;
      enable_active_p<=0;id_active_p<=0;count_active_p<=0;boxes_active_p<=0;
      line_active_p<=0;count_bcd_active_p<=0;
      display_bank<=0;display_valid<=0;displayed_frame_id<=0;ack_toggle<=0;
      x<=0;y<=0;active_prev<=0;vsync_prev<=0;
      bank_q<=0;box_view_s1<=0;line_hit_s1<=0;line_hit_s2<=0;
      use_s1<=0;use_s2<=0;mask_s2<=0;
      count_panel_s1<=0;count_panel_s2<=0;count_glyph_s1<=0;count_glyph_s2<=0;
      base_s1<=0;base_s2<=0;
      active_s1<=0;active_s2<=0;vsync_s1<=0;vsync_s2<=0;hsync_s1<=0;hsync_s2<=0;
      out_data24<=0;out_active<=0;out_vsync<=0;out_hsync<=0;
      row_base<=0;
    end else begin
      publish_sync1<=publish_toggle;publish_sync2<=publish_sync1;
      if(cfg_cdc_dest_req && !cfg_mailbox_valid_p && !cfg_cdc_dest_ack) begin
        enable_p<=cfg_dest_enable;id_p<=cfg_dest_id;
        count_p<={cfg_dest_bcd,cfg_dest_count};
        boxes_p<=cfg_dest_boxes;line_p<=cfg_dest_line;
        cfg_mailbox_valid_p<=1;
      end
      if(frame_boundary && cfg_mailbox_valid_p) begin
        enable_active_p<=enable_p;id_active_p<=id_p;count_active_p<=count_p[4:0];
        boxes_active_p<=boxes_p;line_active_p<=line_p;count_bcd_active_p<=count_p[44:5];
        cfg_mailbox_valid_p<=0;cfg_cdc_dest_ack<=1;
      end else if(!cfg_cdc_dest_req) cfg_cdc_dest_ack<=0;

      box_view_s1<=in_view && id_active_p==displayed_frame_id;
      line_hit_s1<=in_view && id_active_p==displayed_frame_id &&
                    line_active_p[10] && local_x==line_active_p[9:0];
      line_hit_s2<=line_hit_s1;
      active_prev<=vid_active;vsync_prev<=vid_vsync;
      if(frame_boundary) begin
        x<=0;y<=0;row_base<=0;
        if(publish_sync2!=publish_seen) begin
          display_bank<=publish_bank;displayed_frame_id<=publish_id;display_valid<=1;
          publish_seen<=publish_sync2;ack_toggle<=publish_sync2;
        end
      end else if(vid_active) x<=x+1'b1;
      else if(active_prev) begin
        x<=0;y<=y+1'b1;
        if(scan_next_y>VIEW_Y && scan_next_y<VIEW_Y+VIEW_HEIGHT)
          row_base<=row_base_next;
        else row_base<=0;
      end
      bank_q<=display_bank;
      use_s1<=in_view;count_panel_s1<=count_panel;count_glyph_s1<=count_glyph;
      base_s1<=vid_data24;active_s1<=vid_active;vsync_s1<=vid_vsync;hsync_s1<=vid_hsync;
      use_s2<=use_s1;mask_s2<=mask_q;
      count_panel_s2<=count_panel_s1;count_glyph_s2<=count_glyph_s1;base_s2<=base_s1;
      active_s2<=active_s1;vsync_s2<=vsync_s1;hsync_s2<=hsync_s1;
      if(count_panel_s2) out_data24<=count_glyph_s2?24'hffffff:24'h000000;
      else out_data24<=use_s2 ?
          ((mask_s2 || line_hit_s2 || (|box_group_hit_s2))?24'hffffff:24'h000000) : base_s2;
      out_active<=active_s2;out_vsync<=vsync_s2;out_hsync<=hsync_s2;
    end
  end
endmodule

// AMD UG901 simple dual-port RAM, independent read/write clocks.
module binary_mask_ram #(parameter DEPTH=256000, AW=18)(
 input wire wr_clk,we,input wire [AW-1:0] wr_addr,input wire data,
 input wire rd_clk,re,input wire [AW-1:0] rd_addr,output reg q
);
 (* ram_style="block" *) reg mem [0:DEPTH-1];
 always @(posedge wr_clk) if(we) mem[wr_addr]<=data;
 always @(posedge rd_clk) if(re) q<=mem[rd_addr];
endmodule
