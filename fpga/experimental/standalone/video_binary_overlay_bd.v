`timescale 1ns/1ps
// AXI-Lite and video integration shell for the experimental binary overlay.
// s_axi_aclk, mask_clk and core cfg_clk share PS FCLK0 at 50 MHz; do not clock
// the AXI/configuration interface independently from the mask mailbox domain.
module video_binary_overlay_bd (
  (* X_INTERFACE_INFO="xilinx.com:signal:clock:1.0 s_axi_aclk CLK", X_INTERFACE_PARAMETER="XIL_INTERFACENAME s_axi_aclk, ASSOCIATED_BUSIF S_AXI, ASSOCIATED_RESET s_axi_aresetn, FREQ_HZ 50000000" *) input wire s_axi_aclk,
  (* X_INTERFACE_INFO="xilinx.com:signal:reset:1.0 s_axi_aresetn RST", X_INTERFACE_PARAMETER="XIL_INTERFACENAME s_axi_aresetn, POLARITY ACTIVE_LOW" *) input wire s_axi_aresetn,
  (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI AWADDR", X_INTERFACE_PARAMETER="XIL_INTERFACENAME S_AXI, PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 32" *) input wire [31:0] s_axi_awaddr,
  (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *) input wire s_axi_awvalid,
  (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *) output wire s_axi_awready,
  (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI WDATA" *) input wire [31:0] s_axi_wdata,
  (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *) input wire [3:0] s_axi_wstrb,
  (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI WVALID" *) input wire s_axi_wvalid,
  (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI WREADY" *) output wire s_axi_wready,
  (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI BRESP" *) output reg [1:0] s_axi_bresp,
  (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI BVALID" *) output reg s_axi_bvalid,
  (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI BREADY" *) input wire s_axi_bready,
  (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *) input wire [31:0] s_axi_araddr,
  (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *) input wire s_axi_arvalid,
  (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *) output wire s_axi_arready,
  (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI RDATA" *) output reg [31:0] s_axi_rdata,
  (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI RRESP" *) output reg [1:0] s_axi_rresp,
  (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI RVALID" *) output reg s_axi_rvalid,
  (* X_INTERFACE_INFO="xilinx.com:interface:aximm:1.0 S_AXI RREADY" *) input wire s_axi_rready,
  input wire mask_clk, mask_resetn,
  input wire tap_valid, tap_sof, tap_eol, input wire [7:0] tap_data, input wire [31:0] tap_frame_id,
  input wire pixel_clk, pixel_resetn,
  input wire [23:0] vid_data24, input wire vid_active, vid_vsync, vid_hsync,
  output wire [23:0] out_data24, output wire out_active, out_vsync, out_hsync,
  output wire [31:0] capture_count, drop_count, reject_count,
  output wire capture_busy, pending_valid, completed_valid_status,
  output wire [31:0] captured_frame_id, mask_displayed_frame_id,
  output wire mask_display_valid,
  input wire [3:0] button_state, button_events, button_fault,
  output reg [3:0] event_clear, fault_clear
);
  localparam [1:0] OKAY=2'b00, SLVERR=2'b10;
  reg [31:0] wr_addr;
  reg [31:0] wr_data;
  reg [3:0] wr_strb;
  reg aw_full, w_full;
  reg [31:0] accepted_id, cfg_frame_id, cfg_box_words [0:31];
  reg [23:0] cfg_max_area;
  reg [15:0] cfg_max_w, cfg_max_h;
  reg [7:0] cfg_aspect_x16;
  reg cfg_enable;
  reg [4:0] cfg_count;
  reg [9:0] cfg_line_x;
  reg cfg_commit;
  wire cfg_ready;
  wire [639:0] cfg_boxes;
  genvar gi;
  generate for(gi=0;gi<16;gi=gi+1) begin: pack_boxes
    assign cfg_boxes[gi*40 +: 10]=cfg_box_words[gi*2][9:0];
    assign cfg_boxes[gi*40+10 +: 10]=cfg_box_words[gi*2][25:16];
    assign cfg_boxes[gi*40+20 +: 10]=cfg_box_words[gi*2+1][9:0];
    assign cfg_boxes[gi*40+30 +: 10]=cfg_box_words[gi*2+1][25:16];
  end endgenerate
  assign s_axi_awready=!aw_full && !s_axi_bvalid;
  assign s_axi_wready=!w_full && !s_axi_bvalid;
  reg [1:0] feature_read_wait;
  reg [11:0] feature_read_address;
  reg feature_read_enable;
  assign s_axi_arready=!s_axi_rvalid && feature_read_wait==0;
  wire write_fire=aw_full && w_full && !s_axi_bvalid;
  wire [31:0] wa=wr_addr;
  wire [31:0] wd=wr_data;
  wire [31:0] id_read;
  wire display_valid_core;
  wire [31:0] displayed_id_core;
  reg mask_accept;
  wire [31:0] feature_word, feature_frame_id, feature_total_fg, feature_dropped;
  wire feature_done, feature_overflow, feature_busy;
  wire [9:0] feature_count;
  // The AXI and accepted-pixel clocks are the same FCLK0 domain.
  rle_feature_tap features (
    .cfg_max_area(cfg_max_area),.cfg_max_w(cfg_max_w),.cfg_max_h(cfg_max_h),.cfg_aspect_x16(cfg_aspect_x16),
    .tap_clk(mask_clk),.resetn(mask_resetn),
    .tap_valid(tap_valid),.tap_sof(tap_sof),.tap_eol(tap_eol),
    .tap_bit(tap_data[0]),.tap_frame_id(tap_frame_id),
    .record_word_address(feature_read_address),.read_enable(feature_read_enable),
    .record_word_data(feature_word),.frame_id(feature_frame_id),
    .done(feature_done),.overflow(feature_overflow),.busy(feature_busy),
    .record_count(feature_count),.total_fg(feature_total_fg),.dropped_frames(feature_dropped)
  );

  video_binary_overlay #(.WIDTH(640),.HEIGHT(400),.VIEW_X(384),.VIEW_Y(146),.VIEW_WIDTH(512),.VIEW_HEIGHT(320),.MAX_BOXES(16)) core (
    .mask_clk(mask_clk),.mask_resetn(mask_resetn),
    .tap_valid(tap_valid),.tap_sof(tap_sof),.tap_eol(tap_eol),.tap_bit(tap_data[0]),.tap_frame_id(tap_frame_id),
    .mask_accept(mask_accept),.accepted_frame_id(accepted_id),
    .pixel_clk(pixel_clk),.pixel_resetn(pixel_resetn),
    .vid_data24(vid_data24),.vid_active(vid_active),.vid_vsync(vid_vsync),.vid_hsync(vid_hsync),
    .cfg_clk(mask_clk),.cfg_resetn(mask_resetn),.cfg_commit(cfg_commit),.cfg_ready(cfg_ready),
    .cfg_enable(cfg_enable),.cfg_frame_id(cfg_frame_id),.cfg_box_count(cfg_count),.cfg_boxes(cfg_boxes),.cfg_line_x(cfg_line_x),
    .out_data24(out_data24),.out_active(out_active),.out_vsync(out_vsync),.out_hsync(out_hsync),
    .capture_count(capture_count),.drop_count(drop_count),.reject_count(reject_count),
    .captured_frame_id(captured_frame_id),.displayed_frame_id(displayed_id_core),
    .capture_busy(capture_busy),.pending_valid(pending_valid),
    .completed_valid_status(completed_valid_status),.display_valid(display_valid_core),
    .mask_displayed_frame_id(mask_displayed_frame_id),.mask_display_valid(mask_display_valid)
  );

  integer ri;
  reg [31:0] read_value;
  reg read_error;
  always @* begin
    read_value=0; read_error=0;
    if(s_axi_araddr[1:0]!=0 || (s_axi_araddr[31:16]!=0 && s_axi_araddr[31:16]!=16'h43c5)) read_error=1;
    else case(s_axi_araddr[15:0])
      16'h0000: read_value=32'h50424f31;
      16'h0004: read_value=32'h01900280;
      16'h0008: read_value={27'b0,mask_display_valid,completed_valid_status,pending_valid,capture_busy,cfg_ready};
      16'h000c: read_value=capture_count;
      16'h0010: read_value=drop_count;
      16'h0014: read_value=reject_count;
      16'h0018: read_value=captured_frame_id;
      16'h001c: read_value=mask_displayed_frame_id;
      16'h0030: read_value={31'b0,cfg_enable};
      16'h0034: read_value=cfg_frame_id;
      16'h0038: read_value={27'b0,cfg_count};
      16'h003c: read_value={22'b0,cfg_line_x};
      16'h0080: read_value=32'h42544e31;
      16'h0084: read_value={28'd0,button_state};
      16'h0088: read_value={28'd0,button_events};
      16'h008c: read_value={28'd0,button_fault};
      16'h0200: read_value=32'h46524732;
      16'h0204: read_value={29'b0,feature_busy,feature_overflow,feature_done};
      16'h0208: read_value=feature_frame_id;
      16'h020c: read_value={22'b0,feature_count};
      16'h0210: read_value=feature_total_fg;
      16'h0214: read_value=feature_dropped;
      16'h0218: read_value=32'd512;
      16'h0220: read_value={8'd0,cfg_max_area};
      16'h0224: read_value={16'd0,cfg_max_w};
      16'h0228: read_value={16'd0,cfg_max_h};
      16'h022c: read_value={24'd0,cfg_aspect_x16};
      default: begin
        if(s_axi_araddr[15:0]>=16'h0100 && s_axi_araddr[15:0]<16'h0180)
          read_value=cfg_box_words[(s_axi_araddr[15:0]-16'h0100)>>2];
        else if(s_axi_araddr[15:0]>=16'h1000 && s_axi_araddr[15:0]<16'h5000)
          read_value=0; // RAM response is delayed until its synchronous read completes.
        else read_error=1;
      end
    endcase
  end
  always @(posedge s_axi_aclk) begin
    if(!s_axi_aresetn) begin
      wr_addr<=0;wr_data<=0;wr_strb<=0;aw_full<=0;w_full<=0;
      s_axi_bresp<=OKAY;s_axi_bvalid<=0;s_axi_rdata<=0;s_axi_rresp<=OKAY;s_axi_rvalid<=0;
      feature_read_wait<=0;feature_read_address<=0;feature_read_enable<=0;
      accepted_id<=0;cfg_enable<=0;cfg_frame_id<=0;cfg_count<=0;cfg_line_x<=640;cfg_commit<=0;mask_accept<=0;
      cfg_max_area<=0;cfg_max_w<=0;cfg_max_h<=0;cfg_aspect_x16<=0;event_clear<=0;fault_clear<=0;
      for(ri=0;ri<32;ri=ri+1) cfg_box_words[ri]<=0;
    end else begin
      cfg_commit<=0;mask_accept<=0;event_clear<=0;fault_clear<=0;
      feature_read_enable<=0;
      if(s_axi_awvalid&&s_axi_awready) begin wr_addr<=s_axi_awaddr;aw_full<=1;end
      if(s_axi_wvalid&&s_axi_wready) begin wr_data<=s_axi_wdata;wr_strb<=s_axi_wstrb;w_full<=1;end
      if(s_axi_bvalid&&s_axi_bready) s_axi_bvalid<=0;
      if(write_fire) begin
        aw_full<=0;w_full<=0;s_axi_bvalid<=1;s_axi_bresp<=OKAY;
        if(wr_strb!=4'hf || wa[1:0]!=0 || (wa[31:16]!=0 && wa[31:16]!=16'h43c5)) s_axi_bresp<=SLVERR;
        else case(wa[15:0])
          16'h0000,16'h0004,16'h0008,16'h000c,16'h0010,16'h0014,16'h0018,16'h001c,
          16'h0080,16'h0084,16'h0200,16'h0204,16'h0208,16'h020c,
          16'h0210,16'h0214,16'h0218:
            s_axi_bresp<=SLVERR;
          16'h0088: if(wd[31:4]!=0) s_axi_bresp<=SLVERR; else event_clear<=wd[3:0];
          16'h008c: if(wd[31:4]!=0) s_axi_bresp<=SLVERR; else fault_clear<=wd[3:0];
          16'h0220: if(feature_busy || wd[31:24]!=0) s_axi_bresp<=SLVERR; else cfg_max_area<=wd[23:0];
          16'h0224: if(feature_busy || wd[31:16]!=0) s_axi_bresp<=SLVERR; else cfg_max_w<=wd[15:0];
          16'h0228: if(feature_busy || wd[31:16]!=0) s_axi_bresp<=SLVERR; else cfg_max_h<=wd[15:0];
          16'h022c: if(feature_busy || wd[31:8]!=0) s_axi_bresp<=SLVERR; else cfg_aspect_x16<=wd[7:0];
          16'h0020: accepted_id<=wd;
          16'h0024: if(wd==1) mask_accept<=1; else s_axi_bresp<=SLVERR;
          16'h0030: if(wd[31:1]!=0) s_axi_bresp<=SLVERR; else cfg_enable<=wd[0];
          16'h0034: cfg_frame_id<=wd;
          16'h0038: if(wd>16) s_axi_bresp<=SLVERR; else cfg_count<=wd[4:0];
          16'h003c: if(wd>=640) s_axi_bresp<=SLVERR; else cfg_line_x<=wd[9:0];
          16'h0040: if(wd!=1 || !cfg_ready) s_axi_bresp<=SLVERR; else cfg_commit<=1;
          default: begin
            if(wa[15:0]>=16'h0100 && wa[15:0]<16'h0180) cfg_box_words[(wa[15:0]-16'h0100)>>2]<=wd;
            else s_axi_bresp<=SLVERR;
          end
        endcase
      end
      if(s_axi_rvalid&&s_axi_rready) s_axi_rvalid<=0;
      if(s_axi_arvalid&&s_axi_arready) begin
        if(!read_error && s_axi_araddr[15:0]>=16'h1000 && s_axi_araddr[15:0]<16'h5000) begin
          feature_read_address<=(s_axi_araddr[15:0]-16'h1000)>>2;
          feature_read_enable<=1;feature_read_wait<=2;
        end else begin
          s_axi_rvalid<=1;s_axi_rdata<=read_value;s_axi_rresp<=read_error?SLVERR:OKAY;
        end
      end
      if(feature_read_wait!=0) begin
        feature_read_wait<=feature_read_wait-1'b1;
        if(feature_read_wait==1) begin
          s_axi_rvalid<=1;s_axi_rdata<=feature_word;s_axi_rresp<=OKAY;
        end
      end
    end
  end
endmodule
