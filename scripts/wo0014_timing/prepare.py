"""Prepare isolated offline runners and cycle comparison against the committed baseline."""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "ca6a68c7e454d87aeda414c65579271ffa948362"

out = ROOT / "data/results/wo0014-timing-20261014"
generated = out / "generated"
generated.mkdir(parents=True, exist_ok=True)
(out / "reports").mkdir(exist_ok=True)
baseline = subprocess.check_output([
    "git", "show", f"{BASE}:fpga/experimental/wo0014/axis_dma_rgb_adapter.v"], cwd=ROOT).decode()
(generated / "baseline_adapter.v").write_text(baseline.replace(
    "module axis_dma_rgb_adapter", "module wo14_baseline_adapter", 1), encoding="utf-8")
tb = (ROOT / "fpga/sim/wo0014/tb_axis_dma_image_pipeline.sv").read_text()
comparison = r'''
    wire ref_ready, ref_valid, ref_sof, ref_eol, ref_done, ref_error;
    wire [23:0] ref_data;
    wire [3:0] ref_code;
    wire [31:0] ref_id;
    wo14_baseline_adapter #(.WIDTH(WIDTH), .HEIGHT(HEIGHT)) reference_adapter (
        .aclk(aclk), .aresetn(aresetn), .s_tdata(s_tdata), .s_tkeep(s_tkeep),
        .s_tlast(s_tlast), .s_tvalid(s_tvalid), .s_tready(ref_ready),
        .m_tdata(ref_data), .m_tuser(ref_sof), .m_tlast(ref_eol),
        .m_tvalid(ref_valid), .m_tready(dut.rgb_ready),
        .packet_done(ref_done), .packet_error(ref_error),
        .error_code(ref_code), .frame_id(ref_id));
    // Compare after sequential updates on every cycle, including reset and invalid cycles.
    always @(posedge aclk) begin
        #1;
        if ({ref_ready,ref_valid,ref_data,ref_sof,ref_eol,ref_done,ref_error,ref_code,ref_id}
            !== {s_tready,dut.rgb_valid,dut.rgb_data,dut.rgb_sof,dut.rgb_eol,
                 packet_done,packet_error,error_code,frame_id})
            $fatal(1, "Baseline adapter cycle equivalence failed");
    end
'''
(generated / "tb_cycle_equivalence.sv").write_text(
    tb.replace("endmodule", comparison + "\nendmodule"), encoding="utf-8")
old_result = "C:/fpga/material-counter/data/results/wo0014-offline-20261014"
for name in ("run_full", "run_directed", "run_negative", "run_legacy"):
    source = (ROOT / f"scripts/wo0014/{name}.tcl").read_text()
    source = source.replace("data results wo0014-offline-20261014", "data results " + out.name)
    source = source.replace(old_result, out.as_posix())
    source = source.replace(out.as_posix() + "/vectors", old_result + "/vectors")
    source = source.replace("$result/vectors", old_result + "/vectors")
    source = source.replace("$result/legacy-binary-vectors", old_result + "/legacy-binary-vectors")
    source = source.replace("[file join $root scripts wo0014 run_negative.tcl]",
                            "{" + (generated / "run_negative.tcl").as_posix() + "}")
    source = source.replace("[file join $root scripts wo0014 run_directed.tcl]",
                            "{" + (generated / "run_directed.tcl").as_posix() + "}")
    source = source.replace("[file join $root fpga sim wo0014 tb_axis_dma_image_pipeline.sv]",
                            "{" + (generated / "tb_cycle_equivalence.sv").as_posix() + "}")
    source = source.replace("C:/fpga/material-counter/fpga/sim/wo0014/tb_axis_dma_image_pipeline.sv",
                            (generated / "tb_cycle_equivalence.sv").as_posix())
    if name == "run_full":
        source = source.replace("[file join $root fpga experimental wo0014 axis_dma_rgb_adapter.v]",
            "[file join $root fpga experimental wo0014 axis_dma_rgb_adapter.v] {" +
            (generated / "baseline_adapter.v").as_posix() + "}")
    (generated / f"{name}.tcl").write_text(source, encoding="utf-8")
paths = [ROOT / "fpga/experimental/wo0014/axis_dma_rgb_adapter.v", Path(__file__).resolve()]
(out / "input-hashes.json").write_text(json.dumps({
    "baseline_commit": BASE,
    "sources": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
}, indent=2), encoding="utf-8")
print(out)
