`timescale 1ns/1ps

// Digital-video RTL continuity only: no PHY, TMDS or board timing model.
module tb_joint_video_continuity;
  localparam integer H_SYNC=40,H_BACK=220,H_ACTIVE=1280,H_FRONT=110,H_TOTAL=1650;
  localparam integer V_SYNC=5,V_BACK=20,V_ACTIVE=720,V_FRONT=5,V_TOTAL=750;
  localparam integer H_START=H_SYNC+H_BACK,V_START=V_SYNC+V_BACK;
  localparam [31:0] ID_A=32'h3101,ID_BAD=32'h3102,ID_C=32'h3103;
  reg mask_clk=0,pixel_clk=0,resetn=0;
  always #10 mask_clk=~mask_clk;
  always #6.734 pixel_clk=~pixel_clk;

  reg tap_valid=0,tap_sof=0,tap_eol=0;
  reg [7:0] tap_data=0;
  reg [31:0] tap_id=0;
  reg [31:0] awaddr=0,wdata=0,araddr=0;
  reg awvalid=0,wvalid=0,bready=0,arvalid=0,rready=0;
  wire awready,wready,bvalid,arready,rvalid;
  wire [1:0] bresp,rresp;
  wire [31:0] rdata;
  reg [23:0] vid_data=0;
  reg vid_active=0,vid_hsync=0,vid_vsync=0;
  wire [23:0] out_data;
  wire out_active,out_hsync,out_vsync;
  wire [31:0] capture_count,drop_count,reject_count,captured_id,mask_displayed_id;
  wire capture_busy,pending_valid,completed_valid,mask_display_valid;
  video_binary_overlay_bd dut (
    .s_axi_aclk(mask_clk),.s_axi_aresetn(resetn),
    .s_axi_awaddr(awaddr),.s_axi_awvalid(awvalid),.s_axi_awready(awready),
    .s_axi_wdata(wdata),.s_axi_wstrb(4'hf),.s_axi_wvalid(wvalid),.s_axi_wready(wready),
    .s_axi_bresp(bresp),.s_axi_bvalid(bvalid),.s_axi_bready(bready),
    .s_axi_araddr(araddr),.s_axi_arvalid(arvalid),.s_axi_arready(arready),
    .s_axi_rdata(rdata),.s_axi_rresp(rresp),.s_axi_rvalid(rvalid),.s_axi_rready(rready),
    .mask_clk(mask_clk),.mask_resetn(resetn),.tap_valid(tap_valid),.tap_sof(tap_sof),
    .tap_eol(tap_eol),.tap_data(tap_data),.tap_frame_id(tap_id),
    .pixel_clk(pixel_clk),.pixel_resetn(resetn),
    .vid_data24(vid_data),.vid_active(vid_active),.vid_vsync(vid_vsync),.vid_hsync(vid_hsync),
    .out_data24(out_data),.out_active(out_active),.out_vsync(out_vsync),.out_hsync(out_hsync),
    .capture_count(capture_count),.drop_count(drop_count),.reject_count(reject_count),
    .capture_busy(capture_busy),.pending_valid(pending_valid),.completed_valid_status(completed_valid),
    .captured_frame_id(captured_id),.mask_displayed_frame_id(mask_displayed_id),.mask_display_valid(mask_display_valid)
  );

  integer raster_x=0,raster_y=0,video_sequence=0;
  integer driven_x=0,driven_y=0,driven_frame=0;
  integer active_x,active_y;
  reg video_started=0;
  function automatic [23:0] background(input integer x,y,f);
    background={8'(x^f),8'(y),8'h5a};
  endfunction
  always @(negedge pixel_clk) begin
    if(!resetn) begin
      raster_x=0;raster_y=0;video_sequence=0;video_started=0;
      vid_active=0;vid_hsync=0;vid_vsync=0;vid_data=0;
    end else begin
      video_started=1;driven_x=raster_x;driven_y=raster_y;driven_frame=video_sequence;
      vid_hsync=(raster_x<H_SYNC);vid_vsync=(raster_y<V_SYNC);
      vid_active=(raster_x>=H_START && raster_x<H_START+H_ACTIVE &&
                  raster_y>=V_START && raster_y<V_START+V_ACTIVE);
      active_x=raster_x-H_START;active_y=raster_y-V_START;
      vid_data=vid_active?background(active_x,active_y,video_sequence):24'h123456;
      if(raster_x==H_TOTAL-1) begin
        raster_x=0;
        if(raster_y==V_TOTAL-1) begin raster_y=0;video_sequence=video_sequence+1;end
        else raster_y=raster_y+1;
      end else raster_x=raster_x+1;
    end
  end

  task automatic write_reg(input [31:0] address,data);
    begin
      @(negedge mask_clk);awaddr=address;wdata=data;awvalid=1;wvalid=1;
      fork
        begin do @(posedge mask_clk);while(!awready);@(negedge mask_clk);awvalid=0;end
        begin do @(posedge mask_clk);while(!wready);@(negedge mask_clk);wvalid=0;end
      join
      while(!bvalid) @(negedge mask_clk);
      if(bresp!==0) $fatal(1,"continuity AXI write rejected addr=%h resp=%h",address,bresp);
      bready=1;@(posedge mask_clk);@(negedge mask_clk);bready=0;
    end
  endtask
  task automatic read_reg(input [31:0] address,output [31:0] value);
    begin
      @(negedge mask_clk);araddr=address;arvalid=1;
      do @(posedge mask_clk);while(!arready);
      @(negedge mask_clk);arvalid=0;
      while(!rvalid) @(negedge mask_clk);
      if(rresp!==0) $fatal(1,"continuity AXI read rejected addr=%h",address);
      value=rdata;rready=1;@(posedge mask_clk);@(negedge mask_clk);rready=0;
    end
  endtask

  function automatic bit mask_value(input [31:0] id,input integer x,y);
    begin
      if(id==ID_A) mask_value=(x>=40 && x<50 && y>=40 && y<50);
      else mask_value=(x>=400 && x<410 && y>=200 && y<210);
    end
  endfunction

  integer completed_video_frames=0,mask_cycles=0,mask_beats=0,producer_gap_cycles=0;
  integer producer_pause_frames=0,producer_pause_active_pixels=0,invalid_retention_pixels=0;
  reg producer_paused=0,bad_retention_phase=0;
  reg explicit_accept_window=0;
  reg [31:0] next_requested_id=0;
  integer accept_pulses=0;
  always @(posedge mask_clk) if(resetn) begin
    mask_cycles=mask_cycles+1;
    if(mask_cycles>6000000) $fatal(1,"mask-domain continuity watchdog");
    if(tap_valid) mask_beats=mask_beats+1;
    if(producer_paused) producer_gap_cycles=producer_gap_cycles+1;
    if(dut.mask_accept) begin
      if(!explicit_accept_window) $fatal(1,"uncommanded mask_accept");
      accept_pulses=accept_pulses+1;
    end
    if(mask_display_valid && mask_displayed_id==ID_BAD) $fatal(1,"bad mask became displayed");
  end

  // Input pixel k is sampled into *_q at edge k; output uses it at edge k+1.
  // Queue metadata independently of DUT coordinate and memory registers.
  reg have_queued=0,queue_active=0,queue_hsync=0,queue_vsync=0;
  reg queue_probe=0;
  reg [23:0] queue_rgb=0;
  integer queue_x=0,queue_y=0,queue_frame=0;
  reg last_input_vsync=0,last_display_valid=0;
  reg [31:0] last_pixel_id=0;
  reg edge_boundary,sample_active,sample_hsync,sample_vsync,sample_probe;
  reg [23:0] sample_rgb;
  integer sx,sy,lx,ly,mx,my;
  integer timing_checks=0,active_checks=0,blanking_checks=0,rgb_checks=0;
  integer rgb_white_a=0,rgb_white_c=0,rgb_black=0,rgb_background=0;
  integer frame_pixels=0,frame_lines=0,line_pixels=0,frame_hsync=0,frame_vsync=0;
  integer pixel_id_swaps=0,pause_old_id_checks=0,bad_old_id_checks=0;
  integer trace;
  reg continuity_done=0;
  initial trace=$fopen("video_continuity_frames.log","w");
  always @(posedge pixel_clk) begin
    if(!resetn) begin
      have_queued=0;last_input_vsync=0;last_pixel_id=0;last_display_valid=0;
    end else if(video_started && !continuity_done) begin
      edge_boundary=vid_vsync&&!last_input_vsync;
      sample_active=vid_active;sample_hsync=vid_hsync;sample_vsync=vid_vsync;
      sx=driven_x-H_START;sy=driven_y-V_START;
      sample_probe=vid_active && ((sx==100&&sy==100) || (sx==416&&sy==178) ||
                                  (sx==704&&sy==306) || (sx==484&&sy==246));
      sample_rgb=vid_data;
      if(sample_probe && dut.core.display_valid && sx>=384&&sx<896 && sy>=146&&sy<466) begin
        lx=sx-384;ly=sy-146;mx=(lx*640)/512;my=(ly*400)/320;
        sample_rgb=mask_value(dut.core.displayed_frame_id,mx,my)?24'hffffff:24'h000000;
      end
      #1;
      if((dut.core.displayed_frame_id!==last_pixel_id) || (dut.core.display_valid!==last_display_valid)) begin
        if(!edge_boundary || !dut.core.display_valid || dut.core.displayed_frame_id!==next_requested_id ||
           dut.core.displayed_frame_id==ID_BAD) $fatal(1,"pixel-domain publication was not a commanded frame-boundary switch");
        pixel_id_swaps=pixel_id_swaps+1;
        $fdisplay(trace,"SWAP video_frame=%0d raster=%0d/%0d id=%h",driven_frame,driven_x,driven_y,dut.core.displayed_frame_id);
      end
      last_pixel_id=dut.core.displayed_frame_id;last_display_valid=dut.core.display_valid;
      if(producer_paused && dut.core.display_valid) begin
        if(dut.core.displayed_frame_id!==ID_A) $fatal(1,"old mask identity lost during producer pause");
        pause_old_id_checks=pause_old_id_checks+1;
      end
      if(bad_retention_phase) begin
        if(!dut.core.display_valid || dut.core.displayed_frame_id!==ID_A) $fatal(1,"bad/unaccepted mask replaced old display");
        bad_old_id_checks=bad_old_id_checks+1;
      end
      if(have_queued) begin
        if({out_active,out_hsync,out_vsync} !== {queue_active,queue_hsync,queue_vsync})
          $fatal(1,"video continuity mismatch frame=%0d raster=%0d/%0d got=%b%b%b expected=%b%b%b",
                 queue_frame,queue_x,queue_y,out_active,out_hsync,out_vsync,queue_active,queue_hsync,queue_vsync);
        timing_checks=timing_checks+1;
        if(out_active) begin
          active_checks=active_checks+1;frame_pixels=frame_pixels+1;line_pixels=line_pixels+1;
          if(producer_paused) producer_pause_active_pixels=producer_pause_active_pixels+1;
          if(bad_retention_phase) invalid_retention_pixels=invalid_retention_pixels+1;
        end else blanking_checks=blanking_checks+1;
        if(out_hsync) frame_hsync=frame_hsync+1;
        if(out_vsync) frame_vsync=frame_vsync+1;
        if(queue_probe) begin
          rgb_checks=rgb_checks+1;
          if(out_data!==queue_rgb) $fatal(1,"RGB probe mismatch frame=%0d raster=%0d/%0d got=%h expected=%h",
                                        queue_frame,queue_x,queue_y,out_data,queue_rgb);
          if(queue_x-H_START==100) rgb_background=rgb_background+1;
          else if(queue_rgb==0) rgb_black=rgb_black+1;
          else if(queue_rgb==24'hffffff && queue_x-H_START==416) rgb_white_a=rgb_white_a+1;
          else if(queue_rgb==24'hffffff && queue_x-H_START==704) rgb_white_c=rgb_white_c+1;
        end
        if(queue_x==H_TOTAL-1 && queue_y>=V_START && queue_y<V_START+V_ACTIVE) begin
          if(line_pixels!=H_ACTIVE) $fatal(1,"active line interrupted pixels=%0d",line_pixels);
          frame_lines=frame_lines+1;line_pixels=0;
        end
        if(queue_x==H_TOTAL-1 && queue_y==V_TOTAL-1) begin
          if(frame_pixels!=1280*720 || frame_lines!=720 || frame_hsync!=40*750 || frame_vsync!=5*1650)
            $fatal(1,"video frame geometry/sync count mismatch frame=%0d pixels=%0d lines=%0d hs=%0d vs=%0d",
                   queue_frame,frame_pixels,frame_lines,frame_hsync,frame_vsync);
          $fdisplay(trace,"FRAME_PASS frame=%0d pixels=%0d lines=%0d hs=%0d vs=%0d displayed_id=%h producer_paused=%b",
                    queue_frame,frame_pixels,frame_lines,frame_hsync,frame_vsync,dut.core.displayed_frame_id,producer_paused);
          completed_video_frames=completed_video_frames+1;
          if(producer_paused) producer_pause_frames=producer_pause_frames+1;
          frame_pixels=0;frame_lines=0;frame_hsync=0;frame_vsync=0;
          if(completed_video_frames==5) continuity_done=1;
        end
      end
      have_queued=1;queue_active=sample_active;queue_hsync=sample_hsync;queue_vsync=sample_vsync;
      queue_x=driven_x;queue_y=driven_y;queue_frame=driven_frame;
      queue_probe=sample_probe;queue_rgb=sample_rgb;last_input_vsync=sample_vsync;
    end
  end

  task automatic produce_mask(input [31:0] id,input bit malformed,input bit pause_midframe);
    integer i,x,y;
    begin
      for(i=0;i<256000;i=i+1) begin
        @(negedge mask_clk);
        if(pause_midframe && i==128000) begin
          tap_valid=0;tap_sof=0;tap_eol=0;producer_paused=1;
          while(completed_video_frames<3) @(negedge mask_clk);
          producer_paused=0;
        end
        if(i%23==0) begin tap_valid=0;tap_sof=0;tap_eol=0;@(negedge mask_clk);end
        x=i%640;y=i/640;
        tap_valid=1;tap_sof=(i==0);tap_eol=(x==639);
        tap_id=(malformed && i==85333)?id+1:id;
        tap_data=mask_value(id,x,y)?8'hff:0;
        @(posedge mask_clk);
      end
      @(negedge mask_clk);tap_valid=0;tap_sof=0;tap_eol=0;
    end
  endtask

  task automatic accept_and_wait(input [31:0] id);
    integer t;reg [31:0] value;
    begin
      // Place the AXI request well within active video, away from VSYNC.
      while(!vid_active) @(negedge mask_clk);
      next_requested_id=id;explicit_accept_window=1;
      write_reg('h20,id);write_reg('h24,1);
      repeat(3) @(negedge mask_clk);explicit_accept_window=0;
      t=0;while(!mask_display_valid || mask_displayed_id!=id || pending_valid) begin
        @(negedge mask_clk);t=t+1;if(t>1000000) $fatal(1,"display publication timeout");
      end
      read_reg('h1c,value);if(value!==id) $fatal(1,"mask-domain displayed identity readback mismatch");
    end
  endtask

  reg [31:0] value;
  integer t;
  initial begin
    repeat(6) @(negedge mask_clk);resetn=1;
    write_reg('h30,1);write_reg('h34,ID_A);write_reg('h38,0);write_reg('h3c,639);write_reg('h40,1);
    produce_mask(ID_A,0,0);
    if(!completed_valid || captured_id!==ID_A || capture_count!==1) $fatal(1,"mask A capture failed");
    if(mask_display_valid || dut.core.display_valid || pending_valid || accept_pulses!=0) $fatal(1,"mask A auto-accepted");
    accept_and_wait(ID_A);
    $display("VIDEO_PHASE_PASS accepted_A id=%h",ID_A);

    bad_retention_phase=1;
    produce_mask(ID_BAD,1,1);
    repeat(12) @(negedge mask_clk);
    if(completed_valid || captured_id!==ID_A || reject_count!==1 || pending_valid || accept_pulses!=1)
      $fatal(1,"malformed unaccepted mask bookkeeping mismatch");
    read_reg('h204,value);if(value[1:0]!==2'b11) $fatal(1,"malformed FRG was not marked overflow/done");
    $display("VIDEO_PHASE_PASS rejected_bad id=%h old_id=%h rejects=%0d",ID_BAD,mask_displayed_id,reject_count);

    produce_mask(ID_C,0,0);
    if(!completed_valid || captured_id!==ID_C || capture_count!==2 || mask_displayed_id!==ID_A)
      $fatal(1,"mask C capture changed old displayed mask");
    bad_retention_phase=0;
    accept_and_wait(ID_C);
    $display("VIDEO_PHASE_PASS accepted_C id=%h",ID_C);
    while(!continuity_done) @(negedge mask_clk);
    if(completed_video_frames!=5 || timing_checks!=5*1650*750 || active_checks!=5*1280*720 ||
       accept_pulses!=2 || pixel_id_swaps!=2 || producer_pause_frames<1 ||
       producer_pause_active_pixels==0 || invalid_retention_pixels==0 ||
       pause_old_id_checks==0 || bad_old_id_checks==0 ||
       rgb_white_a==0 || rgb_white_c==0 || rgb_black==0 || rgb_background!=5 || rgb_checks!=20 ||
       mask_beats!=3*256000 || drop_count!=0 || reject_count!=1)
      $fatal(1,"continuity coverage/count gate failed frames=%0d timing=%0d active=%0d swaps=%0d rgb=%0d/%0d/%0d/%0d",
             completed_video_frames,timing_checks,active_checks,pixel_id_swaps,rgb_white_a,rgb_white_c,rgb_black,rgb_background);
    $fclose(trace);
    $display("JOINT_VIDEO_CONTINUITY_PASS frames=%0d timing_checks=%0d active_pixels=%0d blanking_pixels=%0d rgb_checks=%0d white_A=%0d white_C=%0d black=%0d background=%0d accept_pulses=%0d boundary_swaps=%0d producer_pause_cycles=%0d pause_frames=%0d pause_active_pixels=%0d invalid_retention_pixels=%0d mask_beats=%0d rejects=%0d",
             completed_video_frames,timing_checks,active_checks,blanking_checks,rgb_checks,rgb_white_a,rgb_white_c,rgb_black,rgb_background,
             accept_pulses,pixel_id_swaps,producer_gap_cycles,producer_pause_frames,producer_pause_active_pixels,invalid_retention_pixels,mask_beats,reject_count);
    $finish;
  end
  initial begin #120000000; $fatal(1,"video continuity absolute watchdog");end
endmodule
