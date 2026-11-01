`timescale 1ns/1ps
module tb_rle_feature_tap;
    reg clk=0; always #10 clk=~clk;
    reg resetn=0;
    reg tap_valid=0, tap_sof=0, tap_eol=0, tap_bit=0;
    reg [31:0] tap_frame_id=0;
    reg [23:0] cfg_max_area=0;
    reg [15:0] cfg_max_w=0,cfg_max_h=0;
    reg [7:0] cfg_aspect_x16=0;
    reg [11:0] record_word_address=0;
    reg read_enable=0;
    wire [31:0] record_word_data, frame_id, total_fg, dropped_frames;
    wire done, overflow, busy;
    wire [9:0] record_count;
    rle_feature_tap #(.WIDTH(8),.HEIGHT(4),.FIFO_DEPTH(32)) dut (
        .tap_clk(clk),.resetn(resetn),.tap_valid(tap_valid),.tap_sof(tap_sof),.tap_eol(tap_eol),
        .tap_bit(tap_bit),.tap_frame_id(tap_frame_id),.cfg_max_area(cfg_max_area),
        .cfg_max_w(cfg_max_w),.cfg_max_h(cfg_max_h),.cfg_aspect_x16(cfg_aspect_x16),
        .record_word_address(record_word_address),
        .read_enable(read_enable),.record_word_data(record_word_data),.frame_id(frame_id),
        .done(done),.overflow(overflow),.busy(busy),.record_count(record_count),
        .total_fg(total_fg),.dropped_frames(dropped_frames)
    );

    reg ov_valid=0, ov_sof=0, ov_eol=0, ov_bit=0, ov_resetn=0;
    reg [31:0] ov_id=0;
    wire [31:0] ov_frame, ov_fg, ov_dropped;
    wire ov_done, ov_overflow, ov_busy;
    wire [9:0] ov_records;
    wire [31:0] ov_ram_data;
    rle_feature_tap #(.WIDTH(16),.HEIGHT(2),.FIFO_DEPTH(2)) overrun_dut (
        .tap_clk(clk),.resetn(ov_resetn),.tap_valid(ov_valid),.tap_sof(ov_sof),.tap_eol(ov_eol),
        .tap_bit(ov_bit),.tap_frame_id(ov_id),.cfg_max_area(24'd0),.cfg_max_w(16'd0),
        .cfg_max_h(16'd0),.cfg_aspect_x16(8'd0),.record_word_address(12'd0),.read_enable(1'b0),
        .record_word_data(ov_ram_data),.frame_id(ov_frame),.done(ov_done),.overflow(ov_overflow),
        .busy(ov_busy),.record_count(ov_records),.total_fg(ov_fg),.dropped_frames(ov_dropped)
    );

    integer i, j, fg_sum;
    reg pix[0:3][0:7];
    reg [31:0] rd[0:7];
    task tick; begin @(posedge clk); #1; end endtask
    task send_pixel(input integer x,input integer y,input bit value,input bit first);
        begin
            @(negedge clk);
            if ((x+y)%3==1) begin tap_valid=0; tap_sof=0; tick(); @(negedge clk); end
            tap_valid=1; tap_sof=first; tap_bit=value; tap_eol=(x==7);
            tap_frame_id=tap_frame_id;
            tick();
            @(negedge clk); tap_valid=0; tap_sof=0;
        end
    endtask
    task send_frame(input [31:0] id);
        integer x,y;
        begin
            tap_frame_id=id;
            for (y=0;y<4;y=y+1)
                for (x=0;x<8;x=x+1) send_pixel(x,y,pix[y][x],(x==0&&y==0));
        end
    endtask
    task wait_done;
        integer t;
        begin
            t=0; while (!done && t<4000) begin tick(); t=t+1; end
            if (!done) $fatal(1,"timeout waiting for frame completion");
        end
    endtask
    task read_word(input integer word_index,output reg [31:0] value);
        begin
            @(negedge clk); record_word_address=word_index; read_enable=1;
            @(posedge clk); #1; value=record_word_data;
            @(negedge clk); read_enable=0;
        end
    endtask
    task check_record(input integer n,input [31:0] area,sx,sy,
                      input [9:0] x0,y0,x1,y1,input [31:0] id);
        begin
            for (j=0;j<8;j=j+1) read_word(n*8+j,rd[j]);
            if (rd[0]!==area || rd[1]!==sx || rd[2]!==sy ||
                rd[3]!==(({6'd0,x1,6'd0,x0})) || rd[4]!==(({6'd0,y1,6'd0,y0})) ||
                rd[5]!==id || rd[7]!==0)
                $fatal(1,"record %0d mismatch: %h %h %h %h %h %h %h %h",n,
                       rd[0],rd[1],rd[2],rd[3],rd[4],rd[5],rd[6],rd[7]);
        end
    endtask
    task clear_image;
        integer x,y; begin for(y=0;y<4;y=y+1) for(x=0;x<8;x=x+1) pix[y][x]=0; end
    endtask
    task start_case(input [31:0] id);
        begin
            tap_frame_id=id;
            @(negedge clk); tap_valid=1; tap_sof=1; tap_bit=pix[0][0]; tap_eol=0;
            tick(); @(negedge clk); tap_valid=0; tap_sof=0;
            for (i=1;i<32;i=i+1) begin
                j=i; send_pixel(j%8,j/8,pix[j/8][j%8],0);
            end
            wait_done();
            if (overflow) $fatal(1,"unexpected overflow frame %0d",id);
            fg_sum=0; for(i=0;i<32;i=i+1) if(pix[i/8][i%8]) fg_sum=fg_sum+1;
            if (total_fg!==fg_sum) $fatal(1,"fg total mismatch frame %0d: %0d expected %0d",id,total_fg,fg_sum);
        end
    endtask

    initial begin
        repeat(4) tick(); resetn=1; ov_resetn=1; repeat(3) tick();

        clear_image(); start_case(32'h101); // empty
        if (record_count!=0 || total_fg!=0) $fatal(1,"empty frame mismatch");

        clear_image(); pix[1][2]=1; pix[1][3]=1; pix[2][2]=1; pix[2][3]=1;
        start_case(32'h102);
        if(record_count!=1) $fatal(1,"single component count");
        check_record(0,4,10,6,2,1,3,2,32'h102);

        // Thresholds latch at SOF; FRG2 word 6 carries reason bits and flag.
        clear_image(); pix[0][1]=1; pix[0][2]=1; pix[0][3]=1;
        cfg_max_area=2;
        start_case(32'h10a);
        read_word(6,rd[6]);
        if(rd[6]!==32'h3) $fatal(1,"candidate reason/flag encoding mismatch: %h",rd[6]);
        cfg_max_area=0;

        clear_image(); pix[0][1]=1; pix[1][2]=1; pix[2][3]=1;
        start_case(32'h103);
        if(record_count!=1) $fatal(1,"diagonal component count");
        check_record(0,3,6,3,1,0,3,2,32'h103);

        clear_image(); pix[0][1]=1; pix[0][5]=1;
        pix[1][2]=1; pix[1][3]=1; pix[1][4]=1;
        start_case(32'h104);
        if(record_count!=1) $fatal(1,"bridge merge did not produce one region");
        check_record(0,5,15,3,1,0,5,1,32'h104);

        clear_image(); pix[0][0]=1; pix[3][7]=1;
        start_case(32'h105);
        if(record_count!=2) $fatal(1,"broken-row-gap should remain disconnected");
        check_record(0,1,0,0,0,0,0,0,32'h105);
        check_record(1,1,7,3,7,3,7,3,32'h105);

        // Start frame 106, then abort it with SOF for 107; only 107 may be reported.
        @(negedge clk); tap_valid=1; tap_sof=1; tap_eol=0; tap_bit=1; tap_frame_id=32'h106; tick();
        @(negedge clk); tap_valid=0; tap_sof=0;
        send_pixel(1,0,1,0);
        clear_image(); pix[2][6]=1;
        tap_frame_id=32'h107;
        @(negedge clk); tap_valid=1; tap_sof=1; tap_eol=0; tap_bit=0; tick();
        @(negedge clk); tap_valid=0; tap_sof=0;
        for(i=1;i<32;i=i+1) begin j=i; send_pixel(j%8,j/8,pix[j/8][j%8],0); end
        wait_done();
        if(frame_id!=32'h107 || overflow || record_count!=1 || dropped_frames<1)
            $fatal(1,"SOF abort/restart status mismatch");
        check_record(0,1,6,2,6,2,6,2,32'h107);

        // Malformed eol must invalidate the frame and expose no records.
        @(negedge clk); tap_valid=1; tap_sof=1; tap_eol=0; tap_bit=0; tap_frame_id=32'h108; tick();
        @(negedge clk); tap_valid=1; tap_sof=0; tap_eol=1; tap_bit=0; tick();
        @(negedge clk); tap_valid=0; tap_eol=0; tick();
        if(!done || !overflow || record_count!=0) $fatal(1,"bad eol was not rejected atomically");

        // No producer ready exists. A burst of maximal runs into a depth-2 FIFO must drop frame.
        @(negedge clk); ov_valid=1; ov_sof=1; ov_eol=0; ov_bit=0; ov_id=32'h201; tick();
        @(negedge clk); ov_sof=0;
        for(i=1;i<32;i=i+1) begin
            ov_valid=1; ov_bit=(i%2==1); ov_eol=((i%16)==15);
            tick(); @(negedge clk);
        end
        ov_valid=0; ov_eol=0;
        j=0; while(!ov_done && j<1000) begin tick(); j=j+1; end
        if(!ov_done || !ov_overflow || ov_records!=0) $fatal(1,"FIFO overrun not rejected atomically");

        $display("RLE_FEATURE_TAP_PASS FRG2=PASS thresholds=PASS");
        $finish;
    end
    initial begin #2000000; $fatal(1,"testbench watchdog"); end
endmodule
