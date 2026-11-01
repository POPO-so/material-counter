`timescale 1ns/1ps
// Two mask banks. Mask producer never backpressures counting. A published bank
// remains immutable until the pixel-domain VSYNC swap has crossed back as ACK.
// Independent resets during use require a coordinated reset of all three domains.
// Status ports belong to the named domain; consumers must use a CDC snapshot.
module video_binary_overlay #(
  parameter integer WIDTH=640, HEIGHT=400, VIEW_X=384, VIEW_Y=146,
  parameter integer VIEW_WIDTH=WIDTH, VIEW_HEIGHT=HEIGHT,
  parameter integer MAX_BOXES=16
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

  reg cfg_toggle, cfg_ack;
  (* ASYNC_REG="TRUE" *) reg cfg_ack_s1, cfg_ack_s2;
  localparam integer CFG_IDLE=0, CFG_LINE=1, CFG_BOX_READ=2,
                     CFG_BOX_SCALE=3, CFG_BOX_WRITE=4, CFG_PUBLISH=5;
  localparam integer CFG_COORDS=MAX_BOXES*4;
  localparam integer CFG_INDEX_W=(CFG_COORDS<2)?1:$clog2(CFG_COORDS);
  reg [2:0] cfg_state;
  reg [CFG_INDEX_W-1:0] cfg_box_idx;
  reg [1:0] cfg_coord_sel;
  reg cfg_raw_enable;
  reg [31:0] cfg_raw_id;
  reg [4:0] cfg_raw_count;
  reg [MAX_BOXES*40-1:0] cfg_raw_boxes, cfg_work_boxes;
  reg [MAX_BOXES-1:0] cfg_work_box_valid;
  reg [9:0] cfg_raw_line, cfg_work_line;
  reg cfg_work_line_valid;
  reg [9:0] cfg_coord_raw, cfg_coord_scaled_reg;
  integer cfgb;
  wire [9:0] cfg_coord_scaled=(cfg_coord_sel<2)?
      ((cfg_coord_raw*VIEW_WIDTH)/WIDTH):
      ((cfg_coord_raw*VIEW_HEIGHT)/HEIGHT);
  reg cfg_mb_enable;
  reg [31:0] cfg_mb_id;
  reg [4:0] cfg_mb_count;
  reg [MAX_BOXES*40-1:0] cfg_mb_boxes;
  reg [9:0] cfg_mb_line;
  reg [MAX_BOXES-1:0] cfg_mb_box_valid;
  reg cfg_mb_line_valid;
  assign cfg_ready=cfg_resetn && cfg_state==CFG_IDLE && cfg_ack_s2==cfg_toggle;
  always @(posedge cfg_clk) begin
    if(!cfg_resetn) begin
      cfg_toggle<=0; cfg_ack_s1<=0; cfg_ack_s2<=0;
      cfg_mb_enable<=0; cfg_mb_id<=0; cfg_mb_count<=0;
      cfg_mb_boxes<=0; cfg_mb_line<=0; cfg_mb_box_valid<=0; cfg_mb_line_valid<=0;
      cfg_state<=CFG_IDLE; cfg_box_idx<=0; cfg_coord_sel<=0;
      cfg_raw_enable<=0; cfg_raw_id<=0; cfg_raw_count<=0;
      cfg_raw_boxes<=0; cfg_work_boxes<=0; cfg_work_box_valid<=0;
      cfg_raw_line<=0; cfg_work_line<=0; cfg_work_line_valid<=0;
      cfg_coord_raw<=0; cfg_coord_scaled_reg<=0;
    end else begin
      cfg_ack_s1<=cfg_ack; cfg_ack_s2<=cfg_ack_s1;
      case(cfg_state)
        CFG_IDLE: if(cfg_commit && cfg_ready) begin
          cfg_raw_enable<=cfg_enable; cfg_raw_id<=cfg_frame_id;
          cfg_raw_count<=cfg_box_count; cfg_raw_boxes<=cfg_boxes;
          cfg_raw_line<=cfg_line_x; cfg_work_line_valid<=cfg_line_x<WIDTH;
          for(cfgb=0;cfgb<MAX_BOXES;cfgb=cfgb+1) begin
            cfg_work_box_valid[cfgb]<=
              cfg_boxes[cfgb*40+0 +: 10]<=cfg_boxes[cfgb*40+10 +: 10] &&
              cfg_boxes[cfgb*40+20 +: 10]<=cfg_boxes[cfgb*40+30 +: 10] &&
              cfg_boxes[cfgb*40+10 +: 10]<WIDTH &&
              cfg_boxes[cfgb*40+30 +: 10]<HEIGHT;
          end
          cfg_state<=CFG_LINE;
        end
        CFG_LINE: begin
          cfg_work_line<=(cfg_raw_line*VIEW_WIDTH)/WIDTH;
          cfg_box_idx<=0; cfg_coord_sel<=0; cfg_state<=CFG_BOX_READ;
        end
        CFG_BOX_READ: begin
          cfg_coord_raw<=cfg_raw_boxes[cfg_box_idx*40+cfg_coord_sel*10 +: 10];
          cfg_state<=CFG_BOX_SCALE;
        end
        CFG_BOX_SCALE: begin
          cfg_coord_scaled_reg<=cfg_coord_scaled;
          cfg_state<=CFG_BOX_WRITE;
        end
        CFG_BOX_WRITE: begin
          cfg_work_boxes[cfg_box_idx*40+cfg_coord_sel*10 +: 10]<=cfg_coord_scaled_reg;
          if(cfg_coord_sel==3) begin
            cfg_coord_sel<=0;
            if(cfg_box_idx==MAX_BOXES-1) cfg_state<=CFG_PUBLISH;
            else begin cfg_box_idx<=cfg_box_idx+1'b1;cfg_state<=CFG_BOX_READ;end
          end else begin cfg_coord_sel<=cfg_coord_sel+1'b1;cfg_state<=CFG_BOX_READ;end
        end
        CFG_PUBLISH: begin
          cfg_mb_enable<=cfg_raw_enable; cfg_mb_id<=cfg_raw_id;
          cfg_mb_count<=cfg_raw_count; cfg_mb_boxes<=cfg_work_boxes;
          cfg_mb_box_valid<=cfg_work_box_valid;
          cfg_mb_line<=cfg_work_line; cfg_mb_line_valid<=cfg_work_line_valid;
          cfg_toggle<=~cfg_toggle; cfg_state<=CFG_IDLE;
        end
        default: cfg_state<=CFG_IDLE;
      endcase
    end
  end

  (* ASYNC_REG="TRUE" *) reg publish_sync1, publish_sync2;
  (* ASYNC_REG="TRUE" *) reg cfg_req_s1, cfg_req_s2;
  reg publish_seen, cfg_seen, display_bank;
  reg enable_p;
  reg [31:0] id_p;
  reg [4:0] count_p;
  reg [MAX_BOXES*40-1:0] boxes_p;
  reg [MAX_BOXES-1:0] box_valid_p;
  reg [9:0] line_p;
  reg line_valid_p;
  reg [11:0] x,y;
  reg active_prev,vsync_prev;
  wire frame_boundary=vid_vsync && !vsync_prev;
  wire in_view=vid_active && display_valid && enable_p &&
       x>=VIEW_X && x<VIEW_X+VIEW_WIDTH && y>=VIEW_Y && y<VIEW_Y+VIEW_HEIGHT;
  wire [9:0] local_x=x-VIEW_X,local_y=y-VIEW_Y;
  wire [9:0] source_x,source_y;
  reg [AW-1:0] row_base_addr;
  reg [1:0] row_scale_phase;
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
      assign source_y=local_y+(local_y>>2);
    end else begin : g_source_y_generic
      assign source_y=(local_y*HEIGHT)/VIEW_HEIGHT;
    end
  endgenerate
  reg line_or_box;
  integer b;
  reg [9:0] bx0,bx1,by0,by1;
  always @* begin
    line_or_box=in_view && line_valid_p && local_x==line_p;
    bx0=0;bx1=0;by0=0;by1=0;
    for(b=0;b<MAX_BOXES;b=b+1) begin
      bx0=boxes_p[b*40 +: 10]; bx1=boxes_p[b*40+10 +: 10];
      by0=boxes_p[b*40+20 +: 10]; by1=boxes_p[b*40+30 +: 10];
      if(in_view && id_p==displayed_frame_id && b<count_p &&
         box_valid_p[b] && bx0<=bx1 && by0<=by1 &&
         local_x>=bx0 && local_x<=bx1 && local_y>=by0 && local_y<=by1 &&
         (local_x==bx0 || local_x==bx1 || local_y==by0 || local_y==by1))
        line_or_box=1;
    end
  end
  wire [AW-1:0] pixel_addr;
  generate
    // For the exact 5:4 vertical map, advance the source row base by 1,1,1,2
    // rows per output line. This removes source_y*WIDTH from the pixel path.
    if(HEIGHT*4==VIEW_HEIGHT*5) begin : g_row_base_5_4
      assign pixel_addr=row_base_addr+source_x;
    end else begin : g_row_base_generic
      assign pixel_addr=source_y*WIDTH+source_x;
    end
  endgenerate
  wire bank0_q,bank1_q;
  reg bank_q;
  wire mask_q=bank_q?bank1_q:bank0_q;
  binary_mask_ram #(.DEPTH(PIXELS),.AW(AW)) bank0_ram(
    .wr_clk(mask_clk),.we(mem_we&&!mem_bank),.wr_addr(mem_addr),.data(tap_bit),
    .rd_clk(pixel_clk),.re(in_view),.rd_addr(pixel_addr),.q(bank0_q));
  binary_mask_ram #(.DEPTH(PIXELS),.AW(AW)) bank1_ram(
    .wr_clk(mask_clk),.we(mem_we&&mem_bank),.wr_addr(mem_addr),.data(tap_bit),
    .rd_clk(pixel_clk),.re(in_view),.rd_addr(pixel_addr),.q(bank1_q));
  reg use_q,overlay_q;
  reg [23:0] base_q;
  reg active_q,vsync_q,hsync_q;
  // BRAM synchronous read and the base-video metadata take the same one-pixel
  // delay. A zero mask pixel is black (not the old background pixel).
  always @(posedge pixel_clk) begin
    if(!pixel_resetn) begin
      publish_sync1<=0;publish_sync2<=0;publish_seen<=0;
      cfg_req_s1<=0;cfg_req_s2<=0;cfg_seen<=0;cfg_ack<=0;
      enable_p<=0;id_p<=0;count_p<=0;boxes_p<=0;box_valid_p<=0;line_p<=0;line_valid_p<=0;
      display_bank<=0;display_valid<=0;displayed_frame_id<=0;ack_toggle<=0;
      x<=0;y<=0;active_prev<=0;vsync_prev<=0;
      row_base_addr<=0;row_scale_phase<=0;
      bank_q<=0;use_q<=0;overlay_q<=0;base_q<=0;
      active_q<=0;vsync_q<=0;hsync_q<=0;
      out_data24<=0;out_active<=0;out_vsync<=0;out_hsync<=0;
    end else begin
      publish_sync1<=publish_toggle;publish_sync2<=publish_sync1;
      cfg_req_s1<=cfg_toggle;cfg_req_s2<=cfg_req_s1;
      active_prev<=vid_active;vsync_prev<=vid_vsync;
      if(frame_boundary) begin
        x<=0;y<=0;row_base_addr<=0;row_scale_phase<=0;
        if(publish_sync2!=publish_seen) begin
          display_bank<=publish_bank;displayed_frame_id<=publish_id;display_valid<=1;
          publish_seen<=publish_sync2;ack_toggle<=publish_sync2;
        end
        if(cfg_req_s2!=cfg_seen) begin
          enable_p<=cfg_mb_enable;id_p<=cfg_mb_id;count_p<=cfg_mb_count;
          boxes_p<=cfg_mb_boxes;box_valid_p<=cfg_mb_box_valid;
          line_p<=cfg_mb_line;line_valid_p<=cfg_mb_line_valid;
          cfg_seen<=cfg_req_s2;cfg_ack<=cfg_req_s2;
        end
      end else if(vid_active) x<=x+1'b1;
      else if(active_prev) begin
        x<=0;y<=y+1'b1;
        if(HEIGHT*4==VIEW_HEIGHT*5) begin
          if(y==VIEW_Y-1) begin row_base_addr<=0;row_scale_phase<=0;end
          else if(y>=VIEW_Y && y<VIEW_Y+VIEW_HEIGHT-1) begin
            if(row_scale_phase==3) begin
              row_base_addr<=row_base_addr+2*WIDTH;row_scale_phase<=0;
            end else begin
              row_base_addr<=row_base_addr+WIDTH;row_scale_phase<=row_scale_phase+1'b1;
            end
          end
        end
      end
      bank_q<=display_bank;
      use_q<=in_view;overlay_q<=line_or_box;base_q<=vid_data24;
      active_q<=vid_active;vsync_q<=vid_vsync;hsync_q<=vid_hsync;
      out_data24<=use_q ? ((mask_q || overlay_q)?24'hffffff:24'h000000) : base_q;
      out_active<=active_q;out_vsync<=vsync_q;out_hsync<=hsync_q;
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
