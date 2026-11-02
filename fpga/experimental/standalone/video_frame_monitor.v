`timescale 1ns/1ps

module video_frame_monitor #(
  parameter integer WIDTH = 1280,
  parameter integer HEIGHT = 720,
  parameter integer MARKER_X = 640
) (
  (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 pixel_clk CLK",
     X_INTERFACE_PARAMETER = "XIL_INTERFACENAME pixel_clk, ASSOCIATED_RESET pixel_resetn" *)
  input wire pixel_clk,
  (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 pixel_resetn RST",
     X_INTERFACE_PARAMETER = "XIL_INTERFACENAME pixel_resetn, POLARITY ACTIVE_LOW" *)
  input wire pixel_resetn,
  input wire [23:0] vid_data,
  input wire vid_active,
  input wire vid_vsync,

  (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 s_axi_aclk CLK",
     X_INTERFACE_PARAMETER = "XIL_INTERFACENAME s_axi_aclk, ASSOCIATED_BUSIF S_AXI, ASSOCIATED_RESET s_axi_aresetn, FREQ_HZ 100000000" *)
  input wire s_axi_aclk,
  (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 s_axi_aresetn RST",
     X_INTERFACE_PARAMETER = "XIL_INTERFACENAME s_axi_aresetn, POLARITY ACTIVE_LOW" *)
  input wire s_axi_aresetn,

  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR",
     X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXI, PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 8" *) input wire [7:0] s_axi_awaddr,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *) input wire s_axi_awvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *) output wire s_axi_awready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *) input wire [31:0] s_axi_wdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *) input wire [3:0] s_axi_wstrb,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *) input wire s_axi_wvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *) output wire s_axi_wready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *) output reg [1:0] s_axi_bresp,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *) output reg s_axi_bvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *) input wire s_axi_bready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *) input wire [7:0] s_axi_araddr,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *) input wire s_axi_arvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *) output wire s_axi_arready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *) output reg [31:0] s_axi_rdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *) output reg [1:0] s_axi_rresp,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *) output reg s_axi_rvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *) input wire s_axi_rready
);
  localparam [31:0] TOTAL_PIXELS = WIDTH * HEIGHT;
  localparam [1:0] AXI_OKAY = 2'b00;
  localparam [1:0] AXI_SLVERR = 2'b10;

  reg vsync_d;
  reg vid_active_d;
  reg frame_armed;
  reg [31:0] active_pixels;
  reg geometry_overflow;
  reg geometry_line_error;
  reg [31:0] line_pixels;
  reg [31:0] line_count;
  reg gray_error;
  reg marker_pixel_error;
  reg [223:0] top_marker, bottom_marker;
  reg top_marker_valid_latched, bottom_marker_valid_latched;
  reg marker_pair_equal_latched;
  reg [31:0] last_diag_flags;
  reg [223:0] last_top_marker, last_bottom_marker;
  reg [63:0] unique_count, scan_count, repeat_count, invalid_count, partial_count;
  reg [127:0] last_session;
  reg [63:0] last_frame;
  reg previous_identity_valid;
  reg [127:0] previous_session;
  reg [63:0] previous_frame;

  function [15:0] marker_crc(input [207:0] payload);
    integer i;
    reg [15:0] crc;
    begin
      crc = 16'hffff;
      for (i = 207; i >= 0; i = i - 1) begin
        if (crc[15] ^ payload[i]) crc = {crc[14:0],1'b0} ^ 16'h1021;
        else crc = {crc[14:0],1'b0};
      end
      marker_crc = crc;
    end
  endfunction

  function marker_is_valid(input [223:0] marker);
    begin
      marker_is_valid = (marker[223:208] == 16'h4d43) &&
                        (marker[15:0] == marker_crc(marker[223:16]));
    end
  endfunction

  wire vsync_rise = vid_vsync && !vsync_d;
  wire marker_pair_valid = marker_pair_equal_latched &&
                           top_marker_valid_latched && bottom_marker_valid_latched;
  wire [31:0] scan_diag_flags = {27'b0, !bottom_marker_valid_latched,
                                 !top_marker_valid_latched, !marker_pair_equal_latched,
                                 marker_pixel_error, gray_error};

  always @(posedge pixel_clk) begin
    if (!pixel_resetn) begin
      vsync_d <= 1'b0;
      vid_active_d <= 1'b0;
      frame_armed <= 1'b0;
      active_pixels <= 0;
      geometry_overflow <= 1'b0;
      geometry_line_error <= 1'b0;
      line_pixels <= 0;
      line_count <= 0;
      gray_error <= 1'b0;
      marker_pixel_error <= 1'b0;
      top_marker <= 0;
      bottom_marker <= 0;
      top_marker_valid_latched <= 1'b0;
      bottom_marker_valid_latched <= 1'b0;
      marker_pair_equal_latched <= 1'b0;
      last_diag_flags <= 0;
      last_top_marker <= 0;
      last_bottom_marker <= 0;
      unique_count <= 0;
      scan_count <= 0;
      repeat_count <= 0;
      invalid_count <= 0;
      partial_count <= 0;
      last_session <= 0;
      last_frame <= 0;
      previous_identity_valid <= 1'b0;
      previous_session <= 0;
      previous_frame <= 0;
    end else begin
      vsync_d <= vid_vsync;
      vid_active_d <= vid_active;

      if (vid_active && frame_armed) begin
        if (active_pixels >= TOTAL_PIXELS) geometry_overflow <= 1'b1;
        if ((vid_data[7:0] != vid_data[15:8]) || (vid_data[15:8] != vid_data[23:16]))
          gray_error <= 1'b1;
        if ((line_count == 0) || (line_count == HEIGHT-1)) begin
          if (line_pixels >= MARKER_X && line_pixels < MARKER_X+224) begin
            if (!((vid_data == 24'h000000) || (vid_data == 24'hffffff)))
              marker_pixel_error <= 1'b1;
            if (line_count == 0)
              top_marker[223-(line_pixels-MARKER_X)] <= (vid_data == 24'hffffff);
            else
              bottom_marker[223-(line_pixels-MARKER_X)] <= (vid_data == 24'hffffff);
          end
        end
        // The marker payloads are complete on the pixel immediately after
        // each 224-bit region. Register their validation before VSYNC so the
        // frame-boundary decision does not traverse the CRC and identity logic.
        if (line_count == 0 && line_pixels == MARKER_X+224)
          top_marker_valid_latched <= marker_is_valid(top_marker);
        if (line_count == HEIGHT-1 && line_pixels == MARKER_X+224) begin
          bottom_marker_valid_latched <= marker_is_valid(bottom_marker);
          marker_pair_equal_latched <= (top_marker == bottom_marker);
        end
        active_pixels <= active_pixels + 1'b1;
        line_pixels <= line_pixels + 1'b1;
      end
      if (!vid_active && vid_active_d && frame_armed) begin
        if (line_pixels != WIDTH) geometry_line_error <= 1'b1;
        line_count <= line_count + 1'b1;
        line_pixels <= 0;
      end

      if (vsync_rise) begin
        if (!frame_armed) begin
          frame_armed <= 1'b1;
          scan_count <= 0;
          active_pixels <= 0;
          geometry_overflow <= 1'b0;
          geometry_line_error <= 1'b0;
          line_pixels <= 0;
          line_count <= 0;
          gray_error <= 1'b0;
          marker_pixel_error <= 1'b0;
          top_marker <= 0;
          bottom_marker <= 0;
          top_marker_valid_latched <= 1'b0;
          bottom_marker_valid_latched <= 1'b0;
          marker_pair_equal_latched <= 1'b0;
          previous_identity_valid <= 1'b0;
        end else begin
          scan_count <= scan_count + 1'b1;
          last_diag_flags <= scan_diag_flags;
          last_top_marker <= top_marker;
          last_bottom_marker <= bottom_marker;
          if (active_pixels != TOTAL_PIXELS || geometry_overflow || geometry_line_error ||
              line_pixels != 0 || line_count != HEIGHT) begin
            partial_count <= partial_count + 1'b1;
            invalid_count <= invalid_count + 1'b1;
          end else if (gray_error || marker_pixel_error || !marker_pair_valid) begin
            invalid_count <= invalid_count + 1'b1;
          end else begin
            if (previous_identity_valid && previous_session == top_marker[207:80]) begin
              if (previous_frame == top_marker[79:16]) begin
                repeat_count <= repeat_count + 1'b1;
              end else if (top_marker[79:16] > previous_frame) begin
                unique_count <= unique_count + 1'b1;
                last_session <= top_marker[207:80];
                last_frame <= top_marker[79:16];
                previous_frame <= top_marker[79:16];
              end else begin
                // frameID is monotonic within a session; wrap requires a new session.
                invalid_count <= invalid_count + 1'b1;
              end
            end else begin
              unique_count <= unique_count + 1'b1;
              last_session <= top_marker[207:80];
              last_frame <= top_marker[79:16];
              previous_session <= top_marker[207:80];
              previous_frame <= top_marker[79:16];
            end
            previous_identity_valid <= 1'b1;
          end
          active_pixels <= 0;
          geometry_overflow <= 1'b0;
          geometry_line_error <= 1'b0;
          line_pixels <= 0;
          line_count <= 0;
          gray_error <= 1'b0;
          marker_pixel_error <= 1'b0;
          top_marker <= 0;
          bottom_marker <= 0;
          top_marker_valid_latched <= 1'b0;
          bottom_marker_valid_latched <= 1'b0;
          marker_pair_equal_latched <= 1'b0;
        end
      end
    end
  end

  reg request_toggle;
  reg [31:0] pending_sequence;
  reg snapshot_busy, snapshot_valid;
  reg [31:0] snapshot_sequence;
  reg [63:0] snap_unique, snap_scans, snap_repeat, snap_invalid, snap_partial;
  reg [127:0] snap_session;
  reg [63:0] snap_frame;
  reg [31:0] snap_diag_flags;
  reg [223:0] snap_top_marker, snap_bottom_marker;
  reg [63:0] mailbox_unique, mailbox_scans, mailbox_repeat, mailbox_invalid, mailbox_partial;
  reg [127:0] mailbox_session;
  reg [63:0] mailbox_frame;
  reg [31:0] mailbox_diag_flags;
  reg [223:0] mailbox_top_marker, mailbox_bottom_marker;
  reg acknowledge_toggle;
  (* ASYNC_REG = "TRUE" *) reg request_sync1, request_sync2;
  reg request_seen;
  (* ASYNC_REG = "TRUE" *) reg acknowledge_sync1, acknowledge_sync2;
  reg acknowledge_seen;

  always @(posedge pixel_clk) begin
    if (!pixel_resetn) begin
      request_sync1 <= 0;
      request_sync2 <= 0;
      request_seen <= 0;
      acknowledge_toggle <= 0;
      mailbox_unique <= 0;
      mailbox_scans <= 0;
      mailbox_repeat <= 0;
      mailbox_invalid <= 0;
      mailbox_partial <= 0;
      mailbox_session <= 0;
      mailbox_frame <= 0;
      mailbox_diag_flags <= 0;
      mailbox_top_marker <= 0;
      mailbox_bottom_marker <= 0;
    end else begin
      request_sync1 <= request_toggle;
      request_sync2 <= request_sync1;
      if (request_sync2 != request_seen) begin
        mailbox_unique <= unique_count;
        mailbox_scans <= scan_count;
        mailbox_repeat <= repeat_count;
        mailbox_invalid <= invalid_count;
        mailbox_partial <= partial_count;
        mailbox_session <= last_session;
        mailbox_frame <= last_frame;
        mailbox_diag_flags <= last_diag_flags;
        mailbox_top_marker <= last_top_marker;
        mailbox_bottom_marker <= last_bottom_marker;
        request_seen <= request_sync2;
        acknowledge_toggle <= request_sync2;
      end
    end
  end

  (* ASYNC_REG = "TRUE" *) reg axi_reset_sync1, axi_reset_sync2;
  reg [3:0] cdc_warmup;
  wire cdc_ready = &cdc_warmup;
  always @(posedge s_axi_aclk) begin
    if (!s_axi_aresetn) begin
      axi_reset_sync1 <= 0;
      axi_reset_sync2 <= 0;
    end else begin
      axi_reset_sync1 <= pixel_resetn;
      axi_reset_sync2 <= axi_reset_sync1;
    end
  end

  reg aw_pending, w_pending;
  reg [7:0] awaddr_saved;
  reg [31:0] wdata_saved;
  reg [3:0] wstrb_saved;
  reg aw_take, w_take;
  reg [7:0] write_addr;
  reg [31:0] write_data;
  reg [3:0] write_strb;
  assign s_axi_awready = s_axi_aresetn && cdc_ready && !aw_pending && !s_axi_bvalid;
  assign s_axi_wready  = s_axi_aresetn && cdc_ready && !w_pending && !s_axi_bvalid;
  assign s_axi_arready = s_axi_aresetn && cdc_ready && !s_axi_rvalid;

  always @* begin
    aw_take = s_axi_awvalid && s_axi_awready;
    w_take = s_axi_wvalid && s_axi_wready;
    write_addr = aw_pending ? awaddr_saved : s_axi_awaddr;
    write_data = w_pending ? wdata_saved : s_axi_wdata;
    write_strb = w_pending ? wstrb_saved : s_axi_wstrb;
  end

  function [31:0] read_word(input [7:0] addr);
    begin
      case (addr)
        8'h00: read_word = 32'h4d435631;
        8'h08: read_word = {30'b0,snapshot_valid,snapshot_busy};
        8'h0c: read_word = snapshot_sequence;
        8'h10: read_word = snap_unique[31:0];
        8'h14: read_word = snap_unique[63:32];
        8'h18: read_word = snap_scans[31:0];
        8'h1c: read_word = snap_scans[63:32];
        8'h20: read_word = snap_repeat[31:0];
        8'h24: read_word = snap_repeat[63:32];
        8'h28: read_word = snap_invalid[31:0];
        8'h2c: read_word = snap_invalid[63:32];
        8'h30: read_word = snap_partial[31:0];
        8'h34: read_word = snap_partial[63:32];
        8'h38: read_word = snap_session[31:0];
        8'h3c: read_word = snap_session[63:32];
        8'h40: read_word = snap_session[95:64];
        8'h44: read_word = snap_session[127:96];
        8'h48: read_word = snap_frame[31:0];
        8'h4c: read_word = snap_frame[63:32];
        8'h50: read_word = snap_diag_flags;
        8'h60: read_word = snap_top_marker[31:0];
        8'h64: read_word = snap_top_marker[63:32];
        8'h68: read_word = snap_top_marker[95:64];
        8'h6c: read_word = snap_top_marker[127:96];
        8'h70: read_word = snap_top_marker[159:128];
        8'h74: read_word = snap_top_marker[191:160];
        8'h78: read_word = snap_top_marker[223:192];
        8'h80: read_word = snap_bottom_marker[31:0];
        8'h84: read_word = snap_bottom_marker[63:32];
        8'h88: read_word = snap_bottom_marker[95:64];
        8'h8c: read_word = snap_bottom_marker[127:96];
        8'h90: read_word = snap_bottom_marker[159:128];
        8'h94: read_word = snap_bottom_marker[191:160];
        8'h98: read_word = snap_bottom_marker[223:192];
        default: read_word = 0;
      endcase
    end
  endfunction

  always @(posedge s_axi_aclk) begin
    if (!s_axi_aresetn || !axi_reset_sync2) begin
      cdc_warmup <= 0;
      aw_pending <= 0; w_pending <= 0;
      awaddr_saved <= 0; wdata_saved <= 0; wstrb_saved <= 0;
      s_axi_bresp <= AXI_OKAY; s_axi_bvalid <= 0;
      s_axi_rdata <= 0; s_axi_rresp <= AXI_OKAY; s_axi_rvalid <= 0;
      request_toggle <= 0; acknowledge_sync1 <= 0; acknowledge_sync2 <= 0;
      acknowledge_seen <= 0; snapshot_busy <= 0; snapshot_valid <= 0;
      snapshot_sequence <= 0; pending_sequence <= 0;
      snap_unique <= 0; snap_scans <= 0; snap_repeat <= 0;
      snap_invalid <= 0; snap_partial <= 0; snap_session <= 0; snap_frame <= 0;
      snap_diag_flags <= 0; snap_top_marker <= 0; snap_bottom_marker <= 0;
    end else begin
      acknowledge_sync1 <= acknowledge_toggle;
      acknowledge_sync2 <= acknowledge_sync1;
      if (!cdc_ready) begin
        cdc_warmup <= {cdc_warmup[2:0],1'b1};
        acknowledge_seen <= acknowledge_sync2;
        request_toggle <= acknowledge_sync2;
        snapshot_busy <= 0;
        snapshot_valid <= 0;
        snap_diag_flags <= 0;
        snap_top_marker <= 0;
        snap_bottom_marker <= 0;
      end else if (acknowledge_sync2 != acknowledge_seen) begin
        // Mailbox data is stable from pixel-domain capture through this atomic copy.
        snap_unique <= mailbox_unique;
        snap_scans <= mailbox_scans;
        snap_repeat <= mailbox_repeat;
        snap_invalid <= mailbox_invalid;
        snap_partial <= mailbox_partial;
        snap_session <= mailbox_session;
        snap_frame <= mailbox_frame;
        snap_diag_flags <= mailbox_diag_flags;
        snap_top_marker <= mailbox_top_marker;
        snap_bottom_marker <= mailbox_bottom_marker;
        acknowledge_seen <= acknowledge_sync2;
        snapshot_sequence <= pending_sequence;
        snapshot_busy <= 0;
        snapshot_valid <= 1;
      end

      if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 0;
      if (aw_take) begin aw_pending <= 1; awaddr_saved <= s_axi_awaddr; end
      if (w_take) begin w_pending <= 1; wdata_saved <= s_axi_wdata; wstrb_saved <= s_axi_wstrb; end
      if (!s_axi_bvalid && (aw_pending || aw_take) && (w_pending || w_take) && cdc_ready) begin
        aw_pending <= 0; w_pending <= 0;
        s_axi_bvalid <= 1;
        s_axi_bresp <= AXI_OKAY;
        if (write_addr == 8'h04 && write_strb[0] && write_data[0]) begin
          if (snapshot_busy) s_axi_bresp <= AXI_SLVERR;
          else begin
            request_toggle <= ~request_toggle;
            pending_sequence <= snapshot_sequence + 1'b1;
            snapshot_busy <= 1;
          end
        end else if (write_addr != 8'h04) begin
          s_axi_bresp <= AXI_SLVERR;
        end
      end

      if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 0;
      if (s_axi_arvalid && s_axi_arready) begin
        s_axi_rdata <= read_word(s_axi_araddr);
        s_axi_rresp <= AXI_OKAY;
        s_axi_rvalid <= 1;
      end
    end
  end
endmodule
