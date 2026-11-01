`timescale 1ns/1ps

// Protocol-boundary acceptance: emitted Gray8 packet -> pinned image pipeline
// -> RLE stream and FRG1 register interface. No JPEG decoder is modeled here.
module tb_joint_packet;
  localparam integer WIDTH=640, HEIGHT=400, PIXELS=WIDTH*HEIGHT;
  reg clk=0, pixel_clk=0;
  always #10 clk=~clk;
  always #7 pixel_clk=~pixel_clk;
  reg resetn=0;
  reg [31:0] sd=0;
  reg sv=0, sl=0, mr=0;
  wire sr, mv, ml;
  wire [31:0] md;
  wire [3:0] mk;
  wire preview_valid, preview_sof, preview_eol;
  wire [7:0] preview_bit;
  wire [31:0] preview_frame_id;

  // One verification AXI-Lite master, selected between the two real endpoints.
  reg overlay_select=0;
  reg [31:0] awaddr=0, wd=0, araddr=0;
  reg awv=0, wv=0, br=0, arv=0, rr=0;
  wire p_awr,p_wr,p_bv,p_arr,p_rv,o_awr,o_wr,o_bv,o_arr,o_rv;
  wire [1:0] p_bp,p_rp,o_bp,o_rp;
  wire [31:0] p_rd,o_rd;
  wire awr=overlay_select?o_awr:p_awr;
  wire wr=overlay_select?o_wr:p_wr;
  wire bv=overlay_select?o_bv:p_bv;
  wire arr=overlay_select?o_arr:p_arr;
  wire rv=overlay_select?o_rv:p_rv;
  wire [1:0] bp=overlay_select?o_bp:p_bp;
  wire [1:0] rp=overlay_select?o_rp:p_rp;
  wire [31:0] rd=overlay_select?o_rd:p_rd;

  axis_dma_rle_pipeline_bd image_pipeline (
    .aclk(clk),.aresetn(resetn),
    .s_axi_awaddr(awaddr),.s_axi_awvalid(awv&&!overlay_select),.s_axi_awready(p_awr),
    .s_axi_wdata(wd),.s_axi_wstrb(4'hf),.s_axi_wvalid(wv&&!overlay_select),.s_axi_wready(p_wr),
    .s_axi_bresp(p_bp),.s_axi_bvalid(p_bv),.s_axi_bready(br&&!overlay_select),
    .s_axi_araddr(araddr),.s_axi_arvalid(arv&&!overlay_select),.s_axi_arready(p_arr),
    .s_axi_rdata(p_rd),.s_axi_rresp(p_rp),.s_axi_rvalid(p_rv),.s_axi_rready(rr&&!overlay_select),
    .s_axis_tdata(sd),.s_axis_tkeep(4'hf),.s_axis_tlast(sl),.s_axis_tvalid(sv),.s_axis_tready(sr),
    .m_axis_tdata(md),.m_axis_tkeep(mk),.m_axis_tlast(ml),.m_axis_tvalid(mv),.m_axis_tready(mr),
    .preview_valid(preview_valid),.preview_sof(preview_sof),.preview_eol(preview_eol),
    .preview_bit(preview_bit),.preview_frame_id(preview_frame_id)
  );

  reg vid_vsync=0;
  integer pixel_cycles=0;
  always @(negedge pixel_clk) begin
    pixel_cycles=pixel_cycles+1;
    vid_vsync=(pixel_cycles%257)<3;
  end
  wire [31:0] capture_count,drop_count,reject_count,captured_id,displayed_id;
  wire capture_busy,pending_valid,completed_valid,display_valid;
  video_binary_overlay_bd binary_overlay (
    .s_axi_aclk(clk),.s_axi_aresetn(resetn),
    .s_axi_awaddr(awaddr),.s_axi_awvalid(awv&&overlay_select),.s_axi_awready(o_awr),
    .s_axi_wdata(wd),.s_axi_wstrb(4'hf),.s_axi_wvalid(wv&&overlay_select),.s_axi_wready(o_wr),
    .s_axi_bresp(o_bp),.s_axi_bvalid(o_bv),.s_axi_bready(br&&overlay_select),
    .s_axi_araddr(araddr),.s_axi_arvalid(arv&&overlay_select),.s_axi_arready(o_arr),
    .s_axi_rdata(o_rd),.s_axi_rresp(o_rp),.s_axi_rvalid(o_rv),.s_axi_rready(rr&&overlay_select),
    .mask_clk(clk),.mask_resetn(resetn),
    .tap_valid(preview_valid),.tap_sof(preview_sof),.tap_eol(preview_eol),
    .tap_data(preview_bit),.tap_frame_id(preview_frame_id),
    .pixel_clk(pixel_clk),.pixel_resetn(resetn),
    .vid_data24(24'd0),.vid_active(1'b0),.vid_vsync(vid_vsync),.vid_hsync(1'b0),
    .out_data24(),.out_active(),.out_vsync(),.out_hsync(),
    .capture_count(capture_count),.drop_count(drop_count),.reject_count(reject_count),
    .capture_busy(capture_busy),.pending_valid(pending_valid),.completed_valid_status(completed_valid),
    .captured_frame_id(captured_id),.mask_displayed_frame_id(displayed_id),.mask_display_valid(display_valid)
  );

  reg [31:0] packet[0:64007],expected_rle[0:20000],expected_frg[0:4095],meta[0:15];
  string vector_dir;
  integer cases=0, case_index=0, n=0, preview_count=0, input_count=0;
  integer cycles=0, input_stalls=0, output_stalls=0, accept_pulses=0;
  integer expected_words=0,expected_records=0,case_file=0,frg_file=0;
  reg [31:0] current_id=0,expected_status=0,expected_error=0,expected_parser_code=0;
  reg [31:0] tslo=0,tshi=0,observed,expected_word;
  reg [31:0] rle_id=0,rle_status=0,rle_error=0;
  reg [31:0] frg_flags,frg_id,frg_records,frg_fg,pipeline_flags,parser_code;
  reg [31:0] last_accepted_id=0;
  reg have_accepted=0,output_done=0,checking=0,explicit_accept_window=0;
  reg stalled=0;
  reg [31:0] held_data;
  reg held_last;
  reg [3:0] held_keep;
  integer px,py;
  reg [7:0] gray_value,binary_value;

  task automatic write_reg(input bit to_overlay,input [31:0] addr,data);
    begin
      @(negedge clk); overlay_select=to_overlay;awaddr=addr;wd=data;awv=1;wv=1;
      fork
        begin do @(posedge clk); while(!awr); @(negedge clk);awv=0;end
        begin do @(posedge clk); while(!wr); @(negedge clk);wv=0;end
      join
      while(!bv) @(negedge clk);
      if(bp!==0) $fatal(1,"AXI write rejected overlay=%b addr=%h resp=%h",to_overlay,addr,bp);
      br=1;@(posedge clk);@(negedge clk);br=0;
    end
  endtask
  task automatic read_reg(input bit from_overlay,input [31:0] addr,output [31:0] value);
    begin
      @(negedge clk);overlay_select=from_overlay;araddr=addr;arv=1;
      do @(posedge clk); while(!arr);
      @(negedge clk);arv=0;
      while(!rv) @(negedge clk);
      if(rp!==0) $fatal(1,"AXI read rejected overlay=%b addr=%h resp=%h",from_overlay,addr,rp);
      value=rd;rr=1;@(posedge clk);@(negedge clk);rr=0;
    end
  endtask
  task automatic send_packet;
    integer i;
    begin
      for(i=0;i<64008;i=i+1) begin
        @(negedge clk);sd=packet[i];sl=(i==64007);sv=1;
        do @(posedge clk); while(!sr);
        @(negedge clk);sv=0;sl=0;
        if(i%17==0) @(negedge clk);
      end
    end
  endtask

  always @(negedge clk) mr=resetn&&(cycles%13<7);
  always @(posedge clk) if(resetn) begin
    cycles=cycles+1;
    if(cycles>10000000) $fatal(1,"JOINT_PACKET watchdog case=%0d n=%0d preview=%0d",case_index,n,preview_count);
    if(binary_overlay.mask_accept) begin
      if(!explicit_accept_window) $fatal(1,"mask_accept occurred without explicit AXI write");
      accept_pulses=accept_pulses+1;
    end
    if(checking) begin
      if(sv&&sr) input_count=input_count+1;
      if(sv&&!sr) input_stalls=input_stalls+1;
      if(mv&&!mr) output_stalls=output_stalls+1;
      if(stalled && (!mv || md!==held_data || ml!==held_last || mk!==held_keep))
        $fatal(1,"RLE changed under backpressure case=%0d word=%0d",case_index,n);
      stalled=mv&&!mr;held_data=md;held_last=ml;held_keep=mk;
      if(preview_valid) begin
        if(preview_count>=PIXELS) $fatal(1,"too many accepted preview pixels");
        px=preview_count%WIDTH;py=preview_count/WIDTH;
        gray_value=packet[4+(preview_count/4)] >> ((preview_count%4)*8);
        binary_value=(px>=meta[5]&&px<meta[7]&&py>=meta[6]&&py<meta[8]&&gray_value>=128)?8'hff:8'h00;
        if(preview_frame_id!==current_id || preview_sof!==(preview_count==0) ||
           preview_eol!==(px==639) || preview_bit!==binary_value)
          $fatal(1,"accepted preview mismatch index=%0d frame=%h",preview_count,preview_frame_id);
        preview_count=preview_count+1;
      end
      if(mv&&mr) begin
        if(mk!==4'hf || n>=expected_words) $fatal(1,"RLE beat count/KEEP mismatch n=%0d",n);
        if(n==7) tslo=md;
        if(n==8) tshi=md;
        if(n==3) rle_id=md;
        if(n==expected_words-10) rle_status=md;
        if(n==expected_words-7) rle_error=md;
        expected_word=expected_rle[n];
        if(n==expected_words-8) expected_word=expected_word+tslo+tshi;
        if(n!=7 && n!=8 && md!==expected_word)
          $fatal(1,"RLE mismatch case=%0d word=%0d got=%h expected=%h",case_index,n,md,expected_word);
        if(ml!==(n==expected_words-1)) $fatal(1,"RLE TLAST mismatch word=%0d",n);
        $fdisplay(case_file,"%08x",md);n=n+1;
        if(ml) begin $fclose(case_file);output_done=1;end
      end
    end
  end

  task automatic run_case(input integer ci);
    integer i,t,pulses_before;
    bit valid_for_accept;
    begin
      $readmemh($sformatf("%s/case%0d_meta.hex",vector_dir,ci),meta,0,15);
      current_id=meta[0];expected_status=meta[1];expected_error=meta[2];
      expected_words=meta[3];expected_records=meta[4];expected_parser_code=meta[9];
      $readmemh($sformatf("%s/case%0d_packet.hex",vector_dir,ci),packet,0,64007);
      $readmemh($sformatf("%s/case%0d_rle.hex",vector_dir,ci),expected_rle,0,expected_words-1);
      if(expected_records!=0)
        $readmemh($sformatf("%s/case%0d_frg.hex",vector_dir,ci),expected_frg,0,expected_records*8-1);
      @(negedge clk);
      n=0;preview_count=0;input_count=0;input_stalls=0;output_stalls=0;tslo=0;tshi=0;
      output_done=0;stalled=0;checking=1;pulses_before=accept_pulses;
      case_file=$fopen($sformatf("observed_rle_%0d.hex",ci),"w");
      if(!case_file) $fatal(1,"cannot open observed RLE file");
      write_reg(0,'h74,0); // GRAY8_PACKET_V1, not raw mode.
      write_reg(0,'h10,current_id);write_reg(0,'h14,3);
      write_reg(0,'h18,(meta[6]<<16)|meta[5]);write_reg(0,'h1c,(meta[8]<<16)|meta[7]);
      write_reg(0,'h20,128);write_reg(0,'h24,0);write_reg(0,'h28,0);write_reg(0,0,1);
      send_packet();
      t=0;while(!output_done) begin @(negedge clk);t=t+1;if(t>100000) $fatal(1,"RLE completion timeout");end
      if(input_count!=64008 || preview_count!=256000 || n!=expected_words)
        $fatal(1,"full-size count mismatch packet=%0d pixels=%0d RLE=%0d",input_count,preview_count,n);
      if(input_stalls==0 || output_stalls==0) $fatal(1,"backpressure coverage missing");
      repeat(10) @(negedge clk);
      read_reg(0,'h08,pipeline_flags);read_reg(0,'h38,parser_code);
      if(pipeline_flags[2] !== (expected_status!=0) || pipeline_flags[1:0]!==2'b10 ||
         parser_code!==expected_parser_code) $fatal(1,"pipeline invalid/done/error-code mismatch");
      read_reg(0,'h0c,observed);if(observed!==expected_words*4) $fatal(1,"actual_bytes mismatch");
      t=0;frg_flags=0;
      while(!frg_flags[0]) begin
        read_reg(1,'h0204,frg_flags);t=t+1;if(t>10000) $fatal(1,"FRG completion timeout");
      end
      read_reg(1,'h0200,observed);if(observed!==32'h46524731) $fatal(1,"FRG1 signature mismatch");
      read_reg(1,'h0208,frg_id);read_reg(1,'h020c,frg_records);read_reg(1,'h0210,frg_fg);
      read_reg(1,'h0214,observed);if(observed!==0) $fatal(1,"unexpected FRG dropped frame");
      if(frg_flags[2:0]!==3'b001 || frg_id!==current_id || frg_records!==expected_records || frg_fg!==meta[10])
        $fatal(1,"FRG status/identity/count mismatch flags=%h fid=%h count=%0d fg=%0d",frg_flags,frg_id,frg_records,frg_fg);
      frg_file=$fopen($sformatf("observed_frg_%0d.hex",ci),"w");
      for(i=0;i<expected_records*8;i=i+1) begin
        read_reg(1,'h1000+i*4,observed);
        if(observed!==expected_frg[i]) $fatal(1,"FRG record mismatch case=%0d word=%0d got=%h expected=%h",ci,i,observed,expected_frg[i]);
        $fdisplay(frg_file,"%08x",observed);
      end
      $fclose(frg_file);
      // The existing FRG tap is speculative: it has no packet-invalid input.
      // A consumer may accept only after checking the completed RLE packet.
      valid_for_accept=(rle_status==0) && (rle_error==0) && !pipeline_flags[2] &&
                       frg_flags[0]&&!frg_flags[1]&&(frg_id==rle_id);
      if(valid_for_accept!==(expected_status==0)) $fatal(1,"consumer invalid gate mismatch");
      repeat(600) @(negedge clk); // several pixel-domain VSYNC boundaries
      if(accept_pulses!=pulses_before || pending_valid ||
         display_valid!==have_accepted || (have_accepted && displayed_id!==last_accepted_id))
        $fatal(1,"capture auto-published or changed displayed identity");
      if(capture_count!==ci+1 || drop_count!==0 || reject_count!==0 || captured_id!==current_id || !completed_valid)
        $fatal(1,"overlay capture bookkeeping mismatch");
      if(valid_for_accept) begin
        explicit_accept_window=1;
        write_reg(1,'h0020,current_id);write_reg(1,'h0024,1);
        repeat(3) @(negedge clk);explicit_accept_window=0;
        if(accept_pulses!=pulses_before+1) $fatal(1,"explicit mask acceptance pulse count mismatch");
        t=0;while(!display_valid || displayed_id!=current_id || pending_valid) begin
          @(negedge clk);t=t+1;if(t>3000) $fatal(1,"explicit publication CDC timeout");
        end
        have_accepted=1;last_accepted_id=current_id;
      end else if(accept_pulses!=pulses_before) $fatal(1,"invalid frame was accepted");
      checking=0;
      $display("JOINT_PACKET_CASE_PASS case=%0d fid=%h invalid=%b error=%0d pixels=%0d rle_words=%0d frg_records=%0d input_stalls=%0d output_stalls=%0d accept=%b",
               ci,current_id,expected_status!=0,expected_error,preview_count,n,expected_records,input_stalls,output_stalls,valid_for_accept);
    end
  endtask

  initial begin
    if(!$value$plusargs("VECTORS=%s",vector_dir) || !$value$plusargs("CASES=%d",cases))
      $fatal(1,"VECTORS and CASES arguments are required");
    repeat(6) @(negedge clk);resetn=1;
    for(case_index=0;case_index<cases;case_index=case_index+1) run_case(case_index);
    $display("JOINT_PACKET_PASS cases=%0d width=640 height=400 explicit_accepts=%0d",cases,accept_pulses);
    $finish;
  end
  initial begin #200000000; $fatal(1,"JOINT_PACKET absolute watchdog");end
endmodule
