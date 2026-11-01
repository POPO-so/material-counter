`timescale 1ns/1ps

// Synchronize the PYNQ-Z2 slide switch before exposing it through AXI GPIO.
// The switch is a slowly changing asynchronous input; the two marked flops
// are the only asynchronous boundary in this path.
module sw0_synchronizer (
    input  wire clk,
    input  wire resetn,
    input  wire switch_async,
    output wire switch_sync
);
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg switch_meta_reg;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg switch_sync_reg;

    always @(posedge clk or negedge resetn) begin
        if (!resetn) begin
            switch_meta_reg <= 1'b0;
            switch_sync_reg <= 1'b0;
        end else begin
            switch_meta_reg <= switch_async;
            switch_sync_reg <= switch_meta_reg;
        end
    end

    assign switch_sync = switch_sync_reg;
endmodule
