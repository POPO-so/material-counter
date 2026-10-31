// Configurable full-resolution Gray8 -> bounded RLE DMA endpoint.
module axis_dma_rle_pipeline_bd #(
    parameter integer WIDTH = 640,
    parameter integer HEIGHT = 400,
    parameter integer MAX_RUNS = 16384,
    parameter [31:0] BASE_ADDR = 32'h43c20000
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK",
       X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aclk, ASSOCIATED_BUSIF s_axi:s_axis:m_axis, ASSOCIATED_RESET aresetn" *)
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
    output wire        s_axis_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 m_axis TDATA",
       X_INTERFACE_PARAMETER = "XIL_INTERFACENAME m_axis, TDATA_NUM_BYTES 4, HAS_TKEEP 1, HAS_TLAST 1, HAS_TREADY 1, HAS_TSTRB 0, HAS_TUSER 0, HAS_TID 0, HAS_TDEST 0" *)
    output wire [31:0] m_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 m_axis TKEEP" *)
    output wire [3:0] m_axis_tkeep,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 m_axis TLAST" *)
    output wire m_axis_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 m_axis TVALID" *)
    output wire m_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 m_axis TREADY" *)
    input wire m_axis_tready,
    output wire preview_valid, output wire preview_sof, output wire preview_eol,
    output wire [7:0] preview_bit, output wire [31:0] preview_frame_id
);
    reg [31:0] frame_id,params_version,roi_start,roi_end,threshold,offset,morph;
    reg [31:0] awaddr_hold,wdata_hold;
    reg [3:0] wstrb_hold;
    reg aw_pending,w_pending,start_pulse,done_seen,input_window,input_mode;
    reg [63:0] clock_ticks;
    // Candidate monitor ABI: cumulative modulo-2^64 counters in the RLE aclk
    // domain. Only aresetn clears them; per-frame start does not. AXI-Lite
    // writes of 1 to 0x3c latch one atomic snapshot even while busy. 0x3c
    // reads its modulo-2^32 sequence; 0x40..0x6c read low/high pairs. These
    // diagnostics do not change the packet protocol or stream handshakes.
    reg [63:0] active_ticks,input_beats,output_beats,input_stalls,output_stalls;
    reg [63:0] snap_ticks,snap_active,snap_input,snap_output,snap_in_stalls,snap_out_stalls;
    reg [31:0] snapshot_seq;
    wire aw_fire=s_axi_awvalid&&s_axi_awready;
    wire w_fire=s_axi_wvalid&&s_axi_wready;
    wire [31:0] wa=aw_pending?awaddr_hold:s_axi_awaddr;
    wire [31:0] wd=w_pending?wdata_hold:s_axi_wdata;
    wire [3:0] ws=w_pending?wstrb_hold:s_axi_wstrb;
    wire commit=!s_axi_bvalid&&(aw_pending||aw_fire)&&(w_pending||w_fire);
    wire valid_write_addr=((wa[31:8]==0)||(wa[31:8]==BASE_ADDR[31:8]))&&wa[1:0]==0;
    wire valid_read_addr=((s_axi_araddr[31:8]==0)||(s_axi_araddr[31:8]==BASE_ADDR[31:8]))&&s_axi_araddr[1:0]==0;
    wire busy,done;wire [31:0] actual_bytes;
    wire pipeline_reset=aresetn&&!start_pulse;
    wire [7:0] gray;wire gray_sof,gray_eol,gray_valid,gray_ready,adapter_ready;
    wire [7:0] raw_gray;wire raw_sof,raw_eol,raw_valid,raw_ready,raw_adapter_ready;
    wire packet_done,packet_error;wire [3:0] packet_code;wire [31:0] input_frame_id;
    wire raw_done,raw_error;wire [3:0] raw_code;wire [31:0] raw_frame_id;
    wire selected_packet_done=input_mode?raw_done:packet_done;
    wire selected_packet_error=input_mode?raw_error:packet_error;
    wire [3:0] selected_packet_code=input_mode?raw_code:packet_code;
    wire [31:0] selected_frame_id=input_mode?raw_frame_id:input_frame_id;
    wire [7:0] source_gray=input_mode?raw_gray:gray;
    wire source_sof=input_mode?raw_sof:gray_sof;
    wire source_eol=input_mode?raw_eol:gray_eol;
    wire source_valid=input_mode?raw_valid:gray_valid;
    reg input_error_latched,good_packet_seen;
    wire frame_mismatch=selected_packet_done && selected_frame_id!=frame_id;
    wire error_event=selected_packet_error||frame_mismatch;
    reg [31:0] x,y;
    wire in_roi=x>=roi_start[15:0]&&x<roi_end[15:0]&&y>=roi_start[31:16]&&y<roi_end[31:16];
    wire signed [32:0] shifted=$signed({1'b0,24'b0,source_gray})+$signed({offset[31],offset});
    wire [7:0] mapped=(shifted<0)?8'd0:((shifted>255)?8'd255:shifted[7:0]);
    wire [7:0] binary=(in_roi&&mapped>=threshold[7:0])?8'hff:8'h00;
    wire [7:0] erode_data,dilate_data;wire erode_sof,erode_eol,erode_valid,erode_ready;
    wire dilate_sof,dilate_eol,dilate_valid,dilate_ready;
    wire encoder_ready;
    wire [7:0] selected_data=(morph==1)?erode_data:((morph==2)?dilate_data:binary);
    wire selected_sof=(morph==1)?erode_sof:((morph==2)?dilate_sof:source_sof);
    wire selected_eol=(morph==1)?erode_eol:((morph==2)?dilate_eol:source_eol);
    wire selected_valid=(morph==1)?erode_valid:((morph==2)?dilate_valid:source_valid);
    assign gray_ready=(morph==1)?erode_ready:((morph==2)?dilate_ready:encoder_ready);
    assign raw_ready=(morph==1)?erode_ready:((morph==2)?dilate_ready:encoder_ready);
    assign s_axis_tready=(input_mode?raw_adapter_ready:adapter_ready)&&input_window;
    assign preview_valid=selected_valid&&encoder_ready;
    assign preview_sof=selected_sof;assign preview_eol=selected_eol;
    assign preview_bit=selected_data;assign preview_frame_id=frame_id;
    assign s_axi_awready=aresetn&&!aw_pending&&!s_axi_bvalid;
    assign s_axi_wready=aresetn&&!w_pending&&!s_axi_bvalid;
    assign s_axi_arready=aresetn&&!s_axi_rvalid;
    axis_dma_gray_adapter #(.WIDTH(WIDTH),.HEIGHT(HEIGHT)) adapter(
      .aclk(aclk),.aresetn(pipeline_reset),.s_tdata(s_axis_tdata),.s_tkeep(s_axis_tkeep),
      .s_tlast(s_axis_tlast),.s_tvalid(s_axis_tvalid&&input_window),.s_tready(adapter_ready),
      .m_tdata(gray),.m_tuser(gray_sof),.m_tlast(gray_eol),.m_tvalid(gray_valid),.m_tready(gray_ready),
      .packet_done(packet_done),.packet_error(packet_error),.error_code(packet_code),.frame_id(input_frame_id));
    axis_raw_gray_adapter #(.WIDTH(WIDTH),.HEIGHT(HEIGHT)) raw_adapter(
      .aclk(aclk),.aresetn(pipeline_reset),.s_tdata(s_axis_tdata),.s_tkeep(s_axis_tkeep),
      .s_tlast(s_axis_tlast),.s_tvalid(s_axis_tvalid&&input_window),.s_tready(raw_adapter_ready),
      .m_tdata(raw_gray),.m_tuser(raw_sof),.m_tlast(raw_eol),.m_tvalid(raw_valid),.m_tready(raw_ready),
      .cfg_frame_id(frame_id),.packet_done(raw_done),.packet_error(raw_error),
      .error_code(raw_code),.frame_id(raw_frame_id));
    axis_morphology3x3 #(.FRAME_WIDTH(WIDTH),.FRAME_HEIGHT(HEIGHT),.MODE(1)) erode(
      .aclk(aclk),.aresetn(pipeline_reset&&!input_error_latched),.s_tdata(binary),.s_tuser(source_sof),
      .s_tlast(source_eol),.s_tvalid(source_valid&&morph==1),.s_tready(erode_ready),
      .m_tdata(erode_data),.m_tuser(erode_sof),.m_tlast(erode_eol),.m_tvalid(erode_valid),
      .m_tready(encoder_ready&&morph==1));
    axis_morphology3x3 #(.FRAME_WIDTH(WIDTH),.FRAME_HEIGHT(HEIGHT),.MODE(2)) dilate(
      .aclk(aclk),.aresetn(pipeline_reset&&!input_error_latched),.s_tdata(binary),.s_tuser(source_sof),
      .s_tlast(source_eol),.s_tvalid(source_valid&&morph==2),.s_tready(dilate_ready),
      .m_tdata(dilate_data),.m_tuser(dilate_sof),.m_tlast(dilate_eol),.m_tvalid(dilate_valid),
      .m_tready(encoder_ready&&morph==2));
    axis_rle_encoder #(.WIDTH(WIDTH),.HEIGHT(HEIGHT),.MAX_RUNS(MAX_RUNS)) encoder(
      .aclk(aclk),.aresetn(aresetn),.start(start_pulse),.cfg_frame_id(frame_id),
      .cfg_params_version(params_version),.cfg_timestamp(clock_ticks),
      .cfg_roi_x0(roi_start[15:0]),.cfg_roi_y0(roi_start[31:16]),
      .cfg_roi_x1(roi_end[15:0]),.cfg_roi_y1(roi_end[31:16]),
      .cfg_threshold(threshold[7:0]),.cfg_offset(offset),.cfg_morph(morph[1:0]),
      .s_tdata(selected_data),.s_tuser(selected_sof),.s_tlast(selected_eol),
      .s_tvalid(selected_valid),.s_tready(encoder_ready),
      .packet_done(selected_packet_done&&!frame_mismatch),.packet_error(error_event),
      .input_error_code(frame_mismatch?4'd7:selected_packet_code),
      .m_tdata(m_axis_tdata),.m_tkeep(m_axis_tkeep),.m_tlast(m_axis_tlast),
      .m_tvalid(m_axis_tvalid),.m_tready(m_axis_tready),.busy(busy),.done(done),.actual_bytes(actual_bytes));
    always @(posedge aclk) begin
      if(!aresetn) begin
        frame_id<=0;params_version<=0;roi_start<=0;roi_end<=(HEIGHT<<16)|WIDTH;input_mode<=0;
        threshold<=128;offset<=0;morph<=0;clock_ticks<=0;x<=0;y<=0;
        aw_pending<=0;w_pending<=0;start_pulse<=0;done_seen<=0;input_window<=0;
        awaddr_hold<=0;wdata_hold<=0;wstrb_hold<=0;
        s_axi_bvalid<=0;s_axi_bresp<=0;s_axi_rvalid<=0;s_axi_rresp<=0;s_axi_rdata<=0;
        input_error_latched<=0;good_packet_seen<=0;
        active_ticks<=0;input_beats<=0;output_beats<=0;input_stalls<=0;output_stalls<=0;
        snap_ticks<=0;snap_active<=0;snap_input<=0;snap_output<=0;snap_in_stalls<=0;snap_out_stalls<=0;
        snapshot_seq<=0;
      end else begin
        clock_ticks<=clock_ticks+1;start_pulse<=0;
        if(busy||input_window)active_ticks<=active_ticks+1;
        if(s_axis_tvalid&&s_axis_tready)input_beats<=input_beats+1;
        if(m_axis_tvalid&&m_axis_tready)output_beats<=output_beats+1;
        if(s_axis_tvalid&&!s_axis_tready)input_stalls<=input_stalls+1;
        if(m_axis_tvalid&&!m_axis_tready)output_stalls<=output_stalls+1;
        if(done)done_seen<=1;
        // The parser drains rejected packets through DMA TLAST. Closing on an
        // early checksum/header error would strand MM2S with unread tail beats.
        if(s_axis_tvalid&&s_axis_tready&&s_axis_tlast)input_window<=0;
        if(selected_packet_done&&!frame_mismatch)good_packet_seen<=1;
        if(error_event)input_error_latched<=1;
        if(source_valid&&(input_mode?raw_ready:gray_ready))begin
          if(x==WIDTH-1)begin x<=0;y<=y+1;end else x<=x+1;
        end
        if(commit)begin
          aw_pending<=0;w_pending<=0;s_axi_bvalid<=1;s_axi_bresp<=0;
          if(!valid_write_addr||ws!=15)s_axi_bresp<=2;
          else if(wa[7:0]==8'h3c)begin
            if(wd!=1)s_axi_bresp<=2;
            else begin
              snap_ticks<=clock_ticks;snap_active<=active_ticks;
              snap_input<=input_beats;snap_output<=output_beats;
              snap_in_stalls<=input_stalls;snap_out_stalls<=output_stalls;
              snapshot_seq<=snapshot_seq+1;
            end
          end
          else if(busy||start_pulse||input_window)s_axi_bresp<=2;
          else if(wa[7:0]==8'h74) begin
            if(wd>1)s_axi_bresp<=2;else input_mode<=wd[0];
          end
          else case(wa[7:0])
            8'h00: if(wd!=1||roi_start[15:0]>=roi_end[15:0]||roi_start[31:16]>=roi_end[31:16]||
                      roi_end[15:0]>WIDTH||roi_end[31:16]>HEIGHT||threshold>255||
                      $signed(offset)<-255||$signed(offset)>255||morph>2)s_axi_bresp<=2;
                    else begin start_pulse<=1;input_window<=1;done_seen<=0;x<=0;y<=0;
                      input_error_latched<=0;good_packet_seen<=0;end
            8'h10:frame_id<=wd;
            8'h14:params_version<=wd;
            8'h18:roi_start<=wd;
            8'h1c:roi_end<=wd;
            8'h20:threshold<=wd;
            8'h24:offset<=wd;
            8'h28:morph<=wd;
            default:s_axi_bresp<=2;
          endcase
        end else begin
          if(aw_fire)begin aw_pending<=1;awaddr_hold<=s_axi_awaddr;end
          if(w_fire)begin w_pending<=1;wdata_hold<=s_axi_wdata;wstrb_hold<=s_axi_wstrb;end
          if(s_axi_bvalid&&s_axi_bready)s_axi_bvalid<=0;
        end
        if(s_axi_arvalid&&s_axi_arready)begin
          s_axi_rvalid<=1;s_axi_rresp<=0;
          if(!valid_read_addr)begin s_axi_rdata<=0;s_axi_rresp<=2;end
          else case(s_axi_araddr[7:0])
            8'h04:s_axi_rdata<=32'h31454c52;
            8'h08:s_axi_rdata<={29'd0,input_error_latched,done_seen,busy};
            8'h0c:s_axi_rdata<=actual_bytes;
            8'h10:s_axi_rdata<=frame_id;
            8'h14:s_axi_rdata<=params_version;
            8'h18:s_axi_rdata<=roi_start;
            8'h1c:s_axi_rdata<=roi_end;
            8'h20:s_axi_rdata<=threshold;
            8'h24:s_axi_rdata<=offset;
            8'h28:s_axi_rdata<=morph;
            8'h2c:s_axi_rdata<=(HEIGHT<<16)|WIDTH;
            8'h30:s_axi_rdata<=MAX_RUNS;
            8'h38:s_axi_rdata<={28'd0,selected_packet_code};
            8'h70:s_axi_rdata<=32'h52415731;
            8'h74:s_axi_rdata<={31'd0,input_mode};
            8'h3c:s_axi_rdata<=snapshot_seq;
            8'h40:s_axi_rdata<=snap_ticks[31:0];
            8'h44:s_axi_rdata<=snap_ticks[63:32];
            8'h48:s_axi_rdata<=snap_active[31:0];
            8'h4c:s_axi_rdata<=snap_active[63:32];
            8'h50:s_axi_rdata<=snap_input[31:0];
            8'h54:s_axi_rdata<=snap_input[63:32];
            8'h58:s_axi_rdata<=snap_output[31:0];
            8'h5c:s_axi_rdata<=snap_output[63:32];
            8'h60:s_axi_rdata<=snap_in_stalls[31:0];
            8'h64:s_axi_rdata<=snap_in_stalls[63:32];
            8'h68:s_axi_rdata<=snap_out_stalls[31:0];
            8'h6c:s_axi_rdata<=snap_out_stalls[63:32];
            default:begin s_axi_rdata<=0;s_axi_rresp<=2;end
          endcase
        end else if(s_axi_rvalid&&s_axi_rready)s_axi_rvalid<=0;
      end
    end
endmodule
