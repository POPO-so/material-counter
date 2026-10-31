`timescale 1ns/1ps

// Per-key synchronizer, debounce filter and sticky event/fault status.
// button_in is active high. resetn asserts asynchronously; reset release is
// held for two clk edges before the sampled inputs and state can advance.
module button_debounce_irq #(
    parameter integer KEY_COUNT = 4,
    parameter integer DEBOUNCE_CYCLES = 3000000
)(
    input wire clk,
    input wire resetn,
    input wire [KEY_COUNT-1:0] button_in,
    input wire [KEY_COUNT-1:0] event_clear,
    input wire [KEY_COUNT-1:0] fault_in,
    input wire [KEY_COUNT-1:0] fault_clear,
    output reg [KEY_COUNT-1:0] stable_state,
    output reg [KEY_COUNT-1:0] press_pulse,
    output reg [KEY_COUNT-1:0] release_pulse,
    output reg [KEY_COUNT-1:0] press_event,
    output reg [KEY_COUNT-1:0] fault_sticky
);
    localparam integer COUNT_W = (DEBOUNCE_CYCLES < 2) ? 1 : $clog2(DEBOUNCE_CYCLES);

    (* ASYNC_REG = "TRUE" *) reg reset_release_meta;
    (* ASYNC_REG = "TRUE" *) reg reset_release_sync;
    (* ASYNC_REG = "TRUE" *) reg [KEY_COUNT-1:0] button_meta;
    (* ASYNC_REG = "TRUE" *) reg [KEY_COUNT-1:0] button_sync;
    reg [COUNT_W-1:0] debounce_count [0:KEY_COUNT-1];
    integer i;

    always @(posedge clk or negedge resetn) begin
        if (!resetn) begin
            reset_release_meta <= 1'b0;
            reset_release_sync <= 1'b0;
        end else begin
            reset_release_meta <= 1'b1;
            reset_release_sync <= reset_release_meta;
        end
    end

    always @(posedge clk or negedge resetn) begin
        if (!resetn) begin
            button_meta <= {KEY_COUNT{1'b0}};
            button_sync <= {KEY_COUNT{1'b0}};
            stable_state <= {KEY_COUNT{1'b0}};
            press_pulse <= {KEY_COUNT{1'b0}};
            release_pulse <= {KEY_COUNT{1'b0}};
            press_event <= {KEY_COUNT{1'b0}};
            fault_sticky <= {KEY_COUNT{1'b0}};
            for (i=0; i<KEY_COUNT; i=i+1) debounce_count[i] <= {COUNT_W{1'b0}};
        end else if (!reset_release_sync) begin
            button_meta <= {KEY_COUNT{1'b0}};
            button_sync <= {KEY_COUNT{1'b0}};
            stable_state <= {KEY_COUNT{1'b0}};
            press_pulse <= {KEY_COUNT{1'b0}};
            release_pulse <= {KEY_COUNT{1'b0}};
            press_event <= {KEY_COUNT{1'b0}};
            fault_sticky <= {KEY_COUNT{1'b0}};
            for (i=0; i<KEY_COUNT; i=i+1) debounce_count[i] <= {COUNT_W{1'b0}};
        end else begin
            button_meta <= button_in;
            button_sync <= button_meta;
            press_pulse <= {KEY_COUNT{1'b0}};
            release_pulse <= {KEY_COUNT{1'b0}};
            for (i=0; i<KEY_COUNT; i=i+1) begin
                if (event_clear[i]) press_event[i] <= 1'b0;
                if (fault_clear[i]) fault_sticky[i] <= 1'b0;
                if (fault_in[i]) fault_sticky[i] <= 1'b1;

                if (button_sync[i] == stable_state[i]) begin
                    debounce_count[i] <= {COUNT_W{1'b0}};
                end else if (DEBOUNCE_CYCLES <= 1 || debounce_count[i] >= DEBOUNCE_CYCLES-1) begin
                    stable_state[i] <= button_sync[i];
                    debounce_count[i] <= {COUNT_W{1'b0}};
                    if (button_sync[i]) begin
                        press_pulse[i] <= 1'b1;
                        press_event[i] <= 1'b1;
                    end else begin
                        release_pulse[i] <= 1'b1;
                    end
                end else begin
                    debounce_count[i] <= debounce_count[i] + 1'b1;
                end
            end
        end
    end
endmodule
