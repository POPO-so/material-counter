`timescale 1ns/1ps
module tb_button_debounce_irq;
  reg clk=0; always #5 clk=~clk;
  reg resetn=0;
  reg [1:0] button_in=0, event_clear=0, fault_in=0, fault_clear=0;
  wire [1:0] stable_state, press_pulse, release_pulse, press_event, fault_sticky;
  integer press_count=0, release_count=0;

  button_debounce_irq #(.KEY_COUNT(2), .DEBOUNCE_CYCLES(4)) dut (
    .clk(clk), .resetn(resetn), .button_in(button_in),
    .event_clear(event_clear), .fault_in(fault_in), .fault_clear(fault_clear),
    .stable_state(stable_state), .press_pulse(press_pulse),
    .release_pulse(release_pulse), .press_event(press_event),
    .fault_sticky(fault_sticky)
  );

  always @(posedge clk) begin
    if (resetn) begin
      if (press_pulse[0]) press_count=press_count+1;
      if (release_pulse[0]) release_count=release_count+1;
    end
  end

  task automatic clocks(input integer n);
    repeat (n) @(negedge clk);
  endtask

  initial begin
    $display("CASE reset");
    clocks(3); resetn=1; clocks(4);
    if (stable_state !== 2'b00 || press_event !== 2'b00 || fault_sticky !== 2'b00)
      $fatal(1,"DEBOUNCE_FAIL reset outputs stable=%b event=%b fault=%b",stable_state,press_event,fault_sticky);

    $display("CASE bounce filtered");
    button_in[0]=1; clocks(2); button_in[0]=0; clocks(6);
    if (stable_state[0] !== 1'b0 || press_count != 0 || release_count != 0)
      $fatal(1,"DEBOUNCE_FAIL bounce was not filtered");

    $display("CASE stable press and long hold");
    button_in[0]=1; clocks(12);
    if (stable_state[0] !== 1'b1 || press_count != 1 || release_count != 0 || press_event[0] !== 1'b1)
      $fatal(1,"DEBOUNCE_FAIL stable press stable=%b press_count=%0d release_count=%0d event=%b",stable_state[0],press_count,release_count,press_event[0]);
    clocks(12);
    if (press_count != 1) $fatal(1,"DEBOUNCE_FAIL long hold repeated press count=%0d",press_count);

    $display("CASE sticky event clear");
    event_clear[0]=1; clocks(1); event_clear[0]=0; clocks(1);
    if (press_event[0] !== 1'b0 || stable_state[0] !== 1'b1)
      $fatal(1,"DEBOUNCE_FAIL event clear did not preserve stable state");

    $display("CASE release pulse");
    button_in[0]=0; clocks(12);
    if (stable_state[0] !== 1'b0 || release_count != 1 || press_count != 1)
      $fatal(1,"DEBOUNCE_FAIL release stable=%b press_count=%0d release_count=%0d",stable_state[0],press_count,release_count);

    $display("CASE fault latch and clear");
    fault_in[1]=1; clocks(1); fault_in[1]=0; clocks(1);
    if (fault_sticky[1] !== 1'b1) $fatal(1,"DEBOUNCE_FAIL fault did not latch");
    fault_clear[1]=1; clocks(1); fault_clear[1]=0; clocks(1);
    if (fault_sticky[1] !== 1'b0) $fatal(1,"DEBOUNCE_FAIL fault clear failed");

    $display("CASE asynchronous reset assertion");
    fault_in[0]=1; clocks(1); fault_in[0]=0; resetn=0; #1;
    if (stable_state !== 2'b00 || press_event !== 2'b00 || fault_sticky !== 2'b00 ||
        press_pulse !== 2'b00 || release_pulse !== 2'b00)
      $fatal(1,"DEBOUNCE_FAIL asynchronous reset did not clear outputs");
    clocks(2); resetn=1; clocks(4);
    if (stable_state !== 2'b00 || press_event !== 2'b00 || fault_sticky !== 2'b00)
      $fatal(1,"DEBOUNCE_FAIL reset release state");

    $display("DEBOUNCE_PASS cases=7");
    $finish;
  end

  initial begin #10000; $fatal(1,"DEBOUNCE_FAIL watchdog timeout"); end
endmodule
