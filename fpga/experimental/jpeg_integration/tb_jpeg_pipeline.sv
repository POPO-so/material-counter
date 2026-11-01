`timescale 1ns/1ps

// Real HLS jpeg_axis_gray RTL -> pinned 640x400 RLE pipeline regression.
module tb_jpeg_pipeline;
  localparam integer WIDTH=640, HEIGHT=400, PIXELS=WIDTH*HEIGHT;
  reg clk=0, pixel_clk=0;
  always #10 clk=~clk;
  always #7 pixel_clk=~pixel_clk;
  reg resetn=0;

  reg [31:0] jpeg_data=0;
  reg [3:0] jpeg_keep=0;
  reg jpeg_last=0, jpeg_valid=0;
  wire jpeg_ready;
  wire [31:0] gray_data;
  wire [3:0] gray_keep;
  wire gray_last, gray_valid;
  wire gray_ready;

  reg [5:0] h_awaddr=0, h_araddr=0;
  reg [31:0] h_wdata=0;
  reg [3:0] h_wstrb=4'hf;
  reg h_awvalid=0,h_wvalid=0,h_bready=0,h_arvalid=0,h_rready=0;
  wire h_awready,h_wready,h_bvalid,h_arready,h_rvalid,interrupt;
  wire [1:0] h_bresp,h_rresp;
  wire [31:0] h_rdata;

  reg [31:0] p_awaddr=0,p_wdata=0,p_araddr=0;
  reg p_awvalid=0,p_wvalid=0,p_bready=0,p_arvalid=0,p_rready=0;
  wire p_awready,p_wready,p_bvalid,p_arready,p_rvalid;
  wire [1:0] p_bresp,p_rresp;
  wire [31:0] p_rdata;
  reg [31:0] o_awaddr=0,o_wdata=0,o_araddr=0;
  reg o_awvalid=0,o_wvalid=0,o_bready=0,o_arvalid=0,o_rready=0;
  wire o_awready,o_wready,o_bvalid,o_arready,o_rvalid;
  wire [1:0] o_bresp,o_rresp;
  wire [31:0] o_rdata;

  wire [31:0] rle_data;
  wire [3:0] rle_keep;
  wire rle_last,rle_valid;
  wire rle_ready=1'b1;
  wire preview_valid,preview_sof,preview_eol;
  wire [7:0] preview_bit;
  wire [31:0] preview_frame_id;
  wire [31:0] capture_count,drop_count,reject_count,captured_id,displayed_id;
  wire capture_busy,pending_valid,completed_valid,display_valid;
  reg vid_vsync=0;
  integer pixel_cycles=0;
  always @(negedge pixel_clk) begin
    pixel_cycles=pixel_cycles+1;
    vid_vsync=(pixel_cycles%257)<3;
  end

  jpeg_axis_gray jpeg_dut (
    .ap_clk(clk),.ap_rst_n(resetn),
    .jpeg_axis_in_TDATA(jpeg_data),.jpeg_axis_in_TVALID(jpeg_valid),
    .jpeg_axis_in_TREADY(jpeg_ready),.jpeg_axis_in_TKEEP(jpeg_keep),
    .jpeg_axis_in_TSTRB(jpeg_keep),.jpeg_axis_in_TLAST(jpeg_last),
    .gray_axis_out_TDATA(gray_data),.gray_axis_out_TVALID(gray_valid),
    .gray_axis_out_TREADY(gray_ready),.gray_axis_out_TKEEP(gray_keep),
    .gray_axis_out_TSTRB(),.gray_axis_out_TLAST(gray_last),
    .s_axi_control_AWVALID(h_awvalid),.s_axi_control_AWREADY(h_awready),.s_axi_control_AWADDR(h_awaddr),
    .s_axi_control_WVALID(h_wvalid),.s_axi_control_WREADY(h_wready),.s_axi_control_WDATA(h_wdata),
    .s_axi_control_WSTRB(h_wstrb),.s_axi_control_ARVALID(h_arvalid),.s_axi_control_ARREADY(h_arready),
    .s_axi_control_ARADDR(h_araddr),.s_axi_control_RVALID(h_rvalid),.s_axi_control_RREADY(h_rready),
    .s_axi_control_RDATA(h_rdata),.s_axi_control_RRESP(h_rresp),.s_axi_control_BVALID(h_bvalid),
    .s_axi_control_BREADY(h_bready),.s_axi_control_BRESP(h_bresp),.interrupt(interrupt)
  );

  axis_dma_rle_pipeline_bd image_pipeline (
    .aclk(clk),.aresetn(resetn),
    .s_axi_awaddr(p_awaddr),.s_axi_awvalid(p_awvalid),.s_axi_awready(p_awready),
    .s_axi_wdata(p_wdata),.s_axi_wstrb(4'hf),.s_axi_wvalid(p_wvalid),.s_axi_wready(p_wready),
    .s_axi_bresp(p_bresp),.s_axi_bvalid(p_bvalid),.s_axi_bready(p_bready),
    .s_axi_araddr(p_araddr),.s_axi_arvalid(p_arvalid),.s_axi_arready(p_arready),
    .s_axi_rdata(p_rdata),.s_axi_rresp(p_rresp),.s_axi_rvalid(p_rvalid),.s_axi_rready(p_rready),
    .s_axis_tdata(gray_data),.s_axis_tkeep(gray_keep),.s_axis_tlast(gray_last),
    .s_axis_tvalid(gray_valid),.s_axis_tready(gray_ready),
    .m_axis_tdata(rle_data),.m_axis_tkeep(rle_keep),.m_axis_tlast(rle_last),
    .m_axis_tvalid(rle_valid),.m_axis_tready(rle_ready),
    .preview_valid(preview_valid),.preview_sof(preview_sof),.preview_eol(preview_eol),
    .preview_bit(preview_bit),.preview_frame_id(preview_frame_id)
  );

  video_binary_overlay_bd binary_overlay (
    .s_axi_aclk(clk),.s_axi_aresetn(resetn),
    .s_axi_awaddr(o_awaddr),.s_axi_awvalid(o_awvalid),.s_axi_awready(o_awready),
    .s_axi_wdata(o_wdata),.s_axi_wstrb(4'hf),.s_axi_wvalid(o_wvalid),.s_axi_wready(o_wready),
    .s_axi_bresp(o_bresp),.s_axi_bvalid(o_bvalid),.s_axi_bready(o_bready),
    .s_axi_araddr(o_araddr),.s_axi_arvalid(o_arvalid),.s_axi_arready(o_arready),
    .s_axi_rdata(o_rdata),.s_axi_rresp(o_rresp),.s_axi_rvalid(o_rvalid),.s_axi_rready(o_rready),
    .mask_clk(clk),.mask_resetn(resetn),
    .tap_valid(preview_valid),.tap_sof(preview_sof),.tap_eol(preview_eol),
    .tap_data(preview_bit),.tap_frame_id(preview_frame_id),
    .pixel_clk(pixel_clk),.pixel_resetn(resetn),.vid_data24(24'd0),.vid_active(1'b0),
    .vid_vsync(vid_vsync),.vid_hsync(1'b0),.out_data24(),.out_active(),.out_vsync(),.out_hsync(),
    .capture_count(capture_count),.drop_count(drop_count),.reject_count(reject_count),
    .capture_busy(capture_busy),.pending_valid(pending_valid),.completed_valid_status(completed_valid),
    .captured_frame_id(captured_id),.mask_displayed_frame_id(displayed_id),.mask_display_valid(display_valid)
  );

  reg [7:0] jpeg_bytes[0:511999];
  reg [7:0] golden_y[0:255999];
  reg [31:0] rle_words_mem[0:20000], expected_rle_mem[0:20000];
  reg [31:0] expected_frg_mem[0:4095];
  reg [31:0] expected_id;
  string jpeg_file,golden_file,rle_log_file,rle_expected_file,frg_expected_file;
  integer jpeg_size, jpeg_beats, gray_beats, pixel_count, rle_words,expected_rle_words;
  integer expected_frg_records,expected_fg_pixels;
  integer cycles=0, watchdog=0, i, x, y, lane, frg_wait;
  reg [31:0] checksum=0, observed, expected_rle_word, tslo=0,tshi=0;
  reg [31:0] frg_flags, frg_id, frg_records, frg_fg;
  reg [31:0] pipeline_flags, parser_code, rle_status, rle_error;
  reg [31:0] last_accepted_id=0;
  reg have_accepted=0, output_done=0, checking=0, explicit_accept_window=0;
  reg stalled=0, held_last;
  reg [31:0] held_data;
  reg [3:0] held_keep;
  integer case_file, rle_stalls, preview_stalls, input_stalls;

  task automatic hwrite(input [5:0] addr,input [31:0] value);
    begin
      @(negedge clk);h_awaddr=addr;h_wdata=value;h_awvalid=1;h_wvalid=1;
      fork
        begin do @(posedge clk); while(!h_awready); @(negedge clk);h_awvalid=0;end
        begin do @(posedge clk); while(!h_wready); @(negedge clk);h_wvalid=0;end
      join
      while(!h_bvalid) @(negedge clk);
      if(h_bresp!==0) $fatal(1,"HLS AXI write response addr=%h resp=%h",addr,h_bresp);
      h_bready=1;@(posedge clk);@(negedge clk);h_bready=0;
    end
  endtask
  task automatic hread(input [5:0] addr,output [31:0] value);
    begin
      @(negedge clk);h_araddr=addr;h_arvalid=1;
      do @(posedge clk); while(!h_arready);
      @(negedge clk);h_arvalid=0;
      while(!h_rvalid) @(negedge clk);
      if(h_rresp!==0) $fatal(1,"HLS AXI read response addr=%h resp=%h",addr,h_rresp);
      value=h_rdata;h_rready=1;@(posedge clk);@(negedge clk);h_rready=0;
    end
  endtask
  task automatic pwrite(input [31:0] addr,input [31:0] value);
    begin
      @(negedge clk);p_awaddr=addr;p_wdata=value;p_awvalid=1;p_wvalid=1;
      fork
        begin do @(posedge clk); while(!p_awready); @(negedge clk);p_awvalid=0;end
        begin do @(posedge clk); while(!p_wready); @(negedge clk);p_wvalid=0;end
      join
      while(!p_bvalid) @(negedge clk);
      if(p_bresp!==0) $fatal(1,"pipeline AXI write rejected addr=%h",addr);
      p_bready=1;@(posedge clk);@(negedge clk);p_bready=0;
    end
  endtask
  task automatic pread(input [31:0] addr,output [31:0] value);
    begin
      @(negedge clk);p_araddr=addr;p_arvalid=1;
      do @(posedge clk); while(!p_arready);
      @(negedge clk);p_arvalid=0;while(!p_rvalid) @(negedge clk);
      if(p_rresp!==0) $fatal(1,"pipeline AXI read rejected addr=%h",addr);
      value=p_rdata;p_rready=1;@(posedge clk);@(negedge clk);p_rready=0;
    end
  endtask
  task automatic owrite(input [31:0] addr,input [31:0] value);
    begin
      @(negedge clk);o_awaddr=addr;o_wdata=value;o_awvalid=1;o_wvalid=1;
      fork
        begin do @(posedge clk); while(!o_awready); @(negedge clk);o_awvalid=0;end
        begin do @(posedge clk); while(!o_wready); @(negedge clk);o_wvalid=0;end
      join
      while(!o_bvalid) @(negedge clk);
      if(o_bresp!==0) $fatal(1,"overlay AXI write rejected addr=%h",addr);
      o_bready=1;@(posedge clk);@(negedge clk);o_bready=0;
    end
  endtask
  task automatic oread(input [31:0] addr,output [31:0] value);
    begin
      @(negedge clk);o_araddr=addr;o_arvalid=1;
      do @(posedge clk); while(!o_arready);
      @(negedge clk);o_arvalid=0;while(!o_rvalid) @(negedge clk);
      if(o_rresp!==0) $fatal(1,"overlay AXI read rejected addr=%h",addr);
      value=o_rdata;o_rready=1;@(posedge clk);@(negedge clk);o_rready=0;
    end
  endtask

  always @(posedge clk) if(resetn) begin
    cycles=cycles+1;
    if(cycles>2000000) $fatal(1,"JPEG_PIPELINE watchdog cycles=%0d gray=%0d rle=%0d preview=%0d",cycles,gray_beats,rle_words,pixel_count);
    if(binary_overlay.mask_accept && !explicit_accept_window) $fatal(1,"overlay accepted without explicit software commit");
    if(checking) begin
      if(gray_valid && gray_ready) begin
        if(gray_keep!==4'hf) $fatal(1,"Gray8 TKEEP invalid beat=%0d keep=%h",gray_beats,gray_keep);
        if(gray_last!==(gray_beats==64007)) $fatal(1,"Gray8 TLAST framing error beat=%0d",gray_beats);
        if(gray_beats==0 && gray_data!==32'h47525938) $fatal(1,"Gray8 packet magic mismatch");
        if(gray_beats==1 && gray_data!=={16'd400,16'd640}) $fatal(1,"Gray8 geometry mismatch");
        if(gray_beats==2 && gray_data!==PIXELS) $fatal(1,"Gray8 pixel count mismatch");
        if(gray_beats==3 && gray_data!==expected_id) $fatal(1,"Gray8 header frame ID mismatch");
        if(gray_beats>=4 && gray_beats<64004) begin
          for(lane=0;lane<4;lane=lane+1) begin
            if(gray_data[lane*8 +: 8]!==golden_y[(gray_beats-4)*4+lane])
              $fatal(1,"Gray8 Y golden mismatch pixel=%0d got=%02x expected=%02x",
                     (gray_beats-4)*4+lane,gray_data[lane*8 +: 8],golden_y[(gray_beats-4)*4+lane]);
          end
          checksum=checksum+gray_data[7:0]+gray_data[15:8]+gray_data[23:16]+gray_data[31:24];
        end
        if(gray_beats==64004 && gray_data!==32'h454e4438) $fatal(1,"Gray8 trailer magic mismatch");
        if(gray_beats==64005 && gray_data!==expected_id) $fatal(1,"Gray8 trailer frame ID mismatch");
        if(gray_beats==64006 && gray_data!==checksum) $fatal(1,"Gray8 checksum mismatch got=%h expected=%h",gray_data,checksum);
        if(gray_beats==64007 && gray_data!==32'hc0dec018) $fatal(1,"Gray8 trailer guard mismatch");
        gray_beats=gray_beats+1;
      end
      if(preview_valid) begin
        x=pixel_count%WIDTH;y=pixel_count/WIDTH;
        if(pixel_count>=PIXELS || preview_frame_id!==expected_id ||
           preview_sof!==(pixel_count==0) || preview_eol!==(x==WIDTH-1) ||
           preview_bit!==((golden_y[pixel_count]>=128)?8'hff:8'h00))
          $fatal(1,"preview/golden/identity mismatch pixel=%0d fid=%h",pixel_count,preview_frame_id);
        pixel_count=pixel_count+1;
      end
      if(rle_valid && rle_ready) begin
        if(rle_keep!==4'hf) $fatal(1,"RLE TKEEP mismatch word=%0d",rle_words);
        if(rle_words>=expected_rle_words) $fatal(1,"RLE stream exceeded expected word count");
        if(rle_words==7) tslo=rle_data;
        if(rle_words==8) tshi=rle_data;
        expected_rle_word=expected_rle_mem[rle_words];
        if(rle_words==expected_rle_words-8) expected_rle_word=expected_rle_word+tslo+tshi;
        if(rle_words!=7 && rle_words!=8 && rle_data!==expected_rle_word)
          $fatal(1,"RLE raster oracle mismatch word=%0d got=%h expected=%h",rle_words,rle_data,expected_rle_word);
        if(rle_last!==(rle_words==expected_rle_words-1))
          $fatal(1,"RLE TLAST mismatch word=%0d expected_words=%0d",rle_words,expected_rle_words);
        if(rle_words==3 && rle_data!==expected_id) $fatal(1,"RLE frame identity mismatch");
        if(rle_words>=20000) $fatal(1,"RLE stream exceeded scoreboard capacity");
        rle_words_mem[rle_words]=rle_data;
        $fdisplay(case_file,"%08x",rle_data);
        rle_words=rle_words+1;
        if(rle_last) output_done=1;
      end
    end
  end

  initial begin
    if(!$value$plusargs("JPEG=%s",jpeg_file) || !$value$plusargs("GOLDEN_Y=%s",golden_file) ||
       !$value$plusargs("RLE_LOG=%s",rle_log_file) || !$value$plusargs("RLE_EXPECTED=%s",rle_expected_file) ||
       !$value$plusargs("FRG_EXPECTED=%s",frg_expected_file) || !$value$plusargs("RLE_WORDS=%d",expected_rle_words) ||
       !$value$plusargs("FRG_RECORDS=%d",expected_frg_records) || !$value$plusargs("FG_PIXELS=%d",expected_fg_pixels) ||
       !$value$plusargs("JPEG_BYTES=%d",jpeg_size) || !$value$plusargs("FRAME_ID=%d",expected_id))
      $fatal(1,"JPEG/golden/RLE/FRG/JPEG_BYTES/FRAME_ID plusargs required");
    if(jpeg_size<=0 || jpeg_size>512000) $fatal(1,"JPEG size outside HLS bound: %0d",jpeg_size);
    if(expected_rle_words<16 || expected_rle_words>20000 || expected_frg_records<0 || expected_frg_records>512 ||
       expected_fg_pixels<0 || expected_fg_pixels>PIXELS) $fatal(1,"oracle count outside hardware bounds");
    $readmemh(jpeg_file,jpeg_bytes,0,jpeg_size-1);
    $readmemh(golden_file,golden_y,0,PIXELS-1);
    $readmemh(rle_expected_file,expected_rle_mem,0,expected_rle_words-1);
    if(expected_frg_records!=0) $readmemh(frg_expected_file,expected_frg_mem,0,expected_frg_records*8-1);
    jpeg_beats=(jpeg_size+3)/4;
    gray_beats=0;pixel_count=0;rle_words=0;checksum=0;
    repeat(8) @(negedge clk);resetn=1;
    pwrite('h74,0);pwrite('h10,expected_id);pwrite('h14,3);
    pwrite('h18,0);pwrite('h1c,{16'd400,16'd640});pwrite('h20,128);pwrite('h24,0);pwrite('h28,0);pwrite(0,1);
    case_file=$fopen(rle_log_file,"w");if(!case_file) $fatal(1,"cannot open observed RLE log");
    checking=1;
    hwrite('h10,jpeg_size);hwrite('h18,expected_id);hwrite(0,1);
    for(i=0;i<jpeg_beats;i=i+1) begin
      @(negedge clk);
      jpeg_data={jpeg_bytes[i*4+3],jpeg_bytes[i*4+2],jpeg_bytes[i*4+1],jpeg_bytes[i*4]};
      jpeg_keep=(jpeg_size-i*4>=4)?4'hf:((1<< (jpeg_size-i*4))-1);
      jpeg_last=(i==jpeg_beats-1);jpeg_valid=1;
      do @(posedge clk); while(!jpeg_ready);
      @(negedge clk);jpeg_valid=0;jpeg_last=0;jpeg_keep=0;
    end
    watchdog=0;while(!output_done) begin @(negedge clk);watchdog=watchdog+1;if(watchdog>1000000) $fatal(1,"RLE completion watchdog");end
    $fclose(case_file);
    if(gray_beats!=64008 || pixel_count!=PIXELS) $fatal(1,"full frame counts wrong gray=%0d preview=%0d",gray_beats,pixel_count);
    if(rle_words!=expected_rle_words || rle_words_mem[3]!==expected_id || rle_words_mem[rle_words-10]!==0 ||
       rle_words_mem[rle_words-7]!==0) $fatal(1,"RLE frame/status/error mismatch fid=%h status=%h error=%h",
       rle_words_mem[3],rle_words_mem[rle_words-10],rle_words_mem[rle_words-7]);
    hread('h00,observed);if(!observed[1] || !observed[2]) $fatal(1,"HLS ap_done/ap_idle not asserted ctrl=%h",observed);
    hread('h18,observed);if(observed!==expected_id) $fatal(1,"HLS frame_id register mismatch=%h",observed);
    hread('h24,observed);if(!observed[0]) $fatal(1,"JPEG decoder status_ap_vld missing=%h",observed);
    hread('h20,observed);if(observed!==0) $fatal(1,"JPEG decoder status nonzero=%h",observed);
    pread('h08,pipeline_flags);pread('h38,parser_code);
    if(pipeline_flags[2] || pipeline_flags[1:0]!==2'b10 || parser_code!==0) $fatal(1,"pipeline rejected Gray8 input flags=%h parser=%h",pipeline_flags,parser_code);
    pread('h0c,observed);if(observed!==rle_words*4) $fatal(1,"RLE byte count mismatch");
    frg_wait=0;frg_flags=0;
    while(!frg_flags[0]) begin oread('h0204,frg_flags);frg_wait=frg_wait+1;if(frg_wait>10000) $fatal(1,"FRG done watchdog");end
    oread('h0200,observed);if(observed!==32'h46524731) $fatal(1,"FRG signature mismatch");
    oread('h0208,frg_id);oread('h020c,frg_records);oread('h0210,frg_fg);oread('h0214,observed);
    if(frg_flags[2:0]!==3'b001 || frg_id!==expected_id || frg_records!==expected_frg_records ||
       frg_fg!==expected_fg_pixels || observed!==0) $fatal(1,"FRG oracle/state/frame identity mismatch");
    for(i=0;i<expected_frg_records*8;i=i+1) begin
      oread('h1000+i*4,observed);
      if(observed!==expected_frg_mem[i]) $fatal(1,"FRG record mismatch word=%0d got=%h expected=%h",i,observed,expected_frg_mem[i]);
    end
    repeat(600) @(negedge pixel_clk);
    if(pending_valid || drop_count!==0 || reject_count!==0 || capture_count!==1 || captured_id!==expected_id || !completed_valid)
      $fatal(1,"overlay capture/frame bookkeeping mismatch");
    if(display_valid!==have_accepted || (have_accepted && displayed_id!==last_accepted_id))
      $fatal(1,"overlay changed displayed frame without explicit accept");
    explicit_accept_window=1;owrite('h0020,expected_id);owrite('h0024,1);repeat(3) @(negedge clk);explicit_accept_window=0;
    watchdog=0;while(!display_valid || displayed_id!==expected_id || pending_valid) begin @(negedge clk);watchdog=watchdog+1;if(watchdog>3000) $fatal(1,"preview publication watchdog");end
    have_accepted=1;last_accepted_id=expected_id;
    checking=0;
    $display("JPEG_PIPELINE_PASS frame_id=%0d jpeg_bytes=%0d gray_beats=%0d preview_pixels=%0d rle_words=%0d frg_records=%0d displayed_id=%0d",expected_id,jpeg_size,gray_beats,pixel_count,rle_words,frg_records,displayed_id);
    $finish;
  end
  initial begin #200000000; $fatal(1,"JPEG_PIPELINE absolute watchdog");end
endmodule
