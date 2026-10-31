`timescale 1ns/1ps
module tb_video_binary_overlay #(parameter W=8,H=4,VW=W,VH=H);
 localparam X0=3,Y0=2,SW=VW+5,SH=VH+4;
 reg mask_clk=0,pixel_clk=0,cfg_clk=0;
 always #5 mask_clk=~mask_clk;
 always #7 pixel_clk=~pixel_clk;
 always #11 cfg_clk=~cfg_clk;
 reg mask_resetn=0,pixel_resetn=0,cfg_resetn=0;
 reg tap_valid=0,tap_sof=0,tap_eol=0,tap_bit=0;
 reg [31:0] tap_frame_id=0,accepted_frame_id=0;
 reg mask_accept=0;
 reg [23:0] vid_data24=0;
 reg vid_active=0,vid_vsync=0,vid_hsync=0;
 reg cfg_commit=0,cfg_enable=0;
 reg [31:0] cfg_frame_id=0;
 reg [4:0] cfg_box_count=0;
 reg [639:0] cfg_boxes=0;
 reg [9:0] cfg_line_x=W;
 wire cfg_ready,out_active,out_vsync,out_hsync;
 wire [23:0] out_data24;
 wire [31:0] capture_count,drop_count,reject_count,displayed_frame_id,captured_frame_id;
 wire capture_busy,pending_valid,completed_valid_status,display_valid,mask_display_valid;
 wire [31:0] mask_displayed_frame_id;
 video_binary_overlay #(.WIDTH(W),.HEIGHT(H),.VIEW_X(X0),.VIEW_Y(Y0),.VIEW_WIDTH(VW),.VIEW_HEIGHT(VH)) dut(.*);
 integer checks=0;
 reg [23:0] expected_previous=0;
 reg expected_active=0,expected_vsync=0,expected_hsync=0;
 task automatic step(input [23:0] data,input bit active,input bit vs,input bit hs,input [23:0] expected);
  begin
   @(negedge pixel_clk);vid_data24=data;vid_active=active;vid_vsync=vs;vid_hsync=hs;
   @(posedge pixel_clk);#1;
   if(out_data24!==expected_previous || out_active!==expected_active || out_vsync!==expected_vsync || out_hsync!==expected_hsync)
    $fatal(1,"pixel mismatch check=%0d got=%06x expected=%06x active %b/%b",checks,out_data24,expected_previous,out_active,expected_active);
   expected_previous=expected;expected_active=active;expected_vsync=vs;expected_hsync=hs;checks=checks+1;
  end
 endtask
 task automatic boundary;
  begin
   repeat(6)step(24'h080808,0,0,0,24'h080808);
   step(24'h090909,0,1,1,24'h090909);
   repeat(5)step(24'h080808,0,0,0,24'h080808);
  end
 endtask
 task automatic commit_config(input bit en,input integer fid,input integer linepos,input bit box);
  begin
   wait(cfg_ready);@(negedge cfg_clk);
   cfg_enable=en;cfg_frame_id=fid;cfg_box_count=box;cfg_line_x=linepos;cfg_boxes=0;
   // low-to-high fields x0=1,x1=2,y0=2,y1=3
   cfg_boxes[0+:40]={10'd3,10'd2,10'd2,10'd1};cfg_commit=1;
   @(negedge cfg_clk);cfg_commit=0;
   repeat(5)@(posedge pixel_clk);
  end
 endtask
 task automatic send_frame(input integer fid,input integer ones_x,input bit gaps);
  integer xx,yy;
  begin
   for(yy=0;yy<H;yy=yy+1)for(xx=0;xx<W;xx=xx+1)begin
    if(gaps && (xx%3)==1)begin @(negedge mask_clk);tap_valid=0;tap_sof=0;tap_eol=0;end
    @(negedge mask_clk);tap_valid=1;tap_sof=(xx==0&&yy==0);tap_eol=(xx==W-1);
    tap_frame_id=fid;tap_bit=(xx==ones_x&&yy==1);
   end
   @(negedge mask_clk);tap_valid=0;tap_sof=0;tap_eol=0;
   repeat(2)@(posedge mask_clk);
  end
 endtask
 task automatic accept_frame(input integer fid);
  begin @(negedge mask_clk);accepted_frame_id=fid;mask_accept=1;
   @(negedge mask_clk);mask_accept=0;repeat(4)@(posedge mask_clk);end
 endtask
 task automatic scan(input bit en,input integer mask_x,input integer linepos,input bit box);
  integer xx,yy,lx,ly,sx,sy;
  reg [23:0] base,exp;
  reg white;
  begin
   for(yy=0;yy<SH;yy=yy+1)begin
    for(xx=0;xx<SW;xx=xx+1)begin
     base={8'(xx+1),8'(yy+1),8'h5a};exp=base;
     lx=xx-X0;ly=yy-Y0;
     if(en&&lx>=0&&lx<VW&&ly>=0&&ly<VH)begin
      sx=lx*W/VW;sy=ly*H/VH;
      white=(sx==mask_x&&sy==1)||(linepos<W&&lx==linepos*VW/W);
      if(box&&lx>=1*VW/W&&lx<=2*VW/W&&ly>=2*VH/H&&ly<=3*VH/H&&
        (lx==1*VW/W||lx==2*VW/W||ly==2*VH/H||ly==3*VH/H))white=1;
      exp=white?24'hffffff:24'h000000;
     end
     step(base,1,0,0,exp);
    end
    step(24'h111111,0,0,1,24'h111111);
    step(24'h121212,0,0,0,24'h121212);
   end
   step(24'h131313,0,0,0,24'h131313);
  end
 endtask
 always @(posedge mask_clk)if(mask_resetn&&tap_valid&&dut.writing&&!dut.dropping&&display_valid&&dut.write_bank==dut.display_bank)
  $fatal(1,"write/display bank conflict");
 initial begin
  repeat(5)@(negedge mask_clk);mask_resetn=1;
  @(negedge pixel_clk);pixel_resetn=1;
  @(negedge cfg_clk);cfg_resetn=1;
  // Input has accepted-pixel gaps and must not appear before PS acceptance.
  commit_config(1,7,W,0);send_frame(7,4,1);boundary();
  if(display_valid)$fatal(1,"unaccepted mask displayed");
  accept_frame(99);if(reject_count!=1)$fatal(1,"bad frame ID not rejected");
  accept_frame(7);if(!pending_valid)$fatal(1,"valid acceptance missing");
  boundary();if(!display_valid||displayed_frame_id!=7)$fatal(1,"VSYNC did not switch frame");
  scan(1,4,W,0);
  commit_config(1,7,3,1);boundary();scan(1,4,3,1);
  // Frame-ID mismatch must hide stale boxes, but valid mask remains visible.
  commit_config(1,99,W,1);boundary();scan(1,4,W,0);
  commit_config(0,7,W,0);boundary();scan(0,4,W,0);
  commit_config(1,8,W,0);
  send_frame(8,2,0);accept_frame(8);
  if(!pending_valid)$fatal(1,"frame 8 not pending");
  send_frame(9,6,1);if(drop_count!=1)$fatal(1,"busy bank frame not dropped");
  if(displayed_frame_id!=7)$fatal(1,"bank switched outside VSYNC");
  boundary();if(displayed_frame_id!=8)$fatal(1,"frame8 not displayed");scan(1,2,W,0);
  // Reuse of the previous display bank only after synchronized ACK.
  send_frame(10,6,1);accept_frame(10);boundary();scan(1,6,W,0);
  if(capture_count!=3)$fatal(1,"capture count %0d",capture_count);
  $display("BINARY_OVERLAY_PASS checks=%0d captures=%0d drops=%0d",checks,capture_count,drop_count);$finish;
 end
 initial begin #2000000;$fatal(1,"watchdog");end
endmodule


module tb_video_binary_overlay_scaled;
 tb_video_binary_overlay #(.W(10),.H(5),.VW(8),.VH(4)) test_scaled();
endmodule
