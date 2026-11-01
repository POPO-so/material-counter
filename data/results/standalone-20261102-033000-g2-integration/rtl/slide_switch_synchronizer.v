// Synchronize the PYNQ-Z2 slide switches before exposing them through AXI GPIO.
// The two marked stages are the only asynchronous boundary in this path.
module slide_switch_synchronizer (
    input  wire       clk,
    input  wire       resetn,
    input  wire [1:0] switch_async,
    output wire [1:0] switch_sync
);
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] switch_meta_reg;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] switch_sync_reg;

    always @(posedge clk or negedge resetn) begin
        if (!resetn) begin
            switch_meta_reg <= 2'b00;
            switch_sync_reg <= 2'b00;
        end else begin
            switch_meta_reg <= switch_async;
            switch_sync_reg <= switch_meta_reg;
        end
    end

    assign switch_sync = switch_sync_reg;
endmodule
