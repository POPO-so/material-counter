import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).parent
overlay = json.loads((ROOT / "overlay-only-result.json").read_text(encoding="utf-8"))
dma = json.loads((ROOT / "dma-single-result.json").read_text(encoding="utf-8"))
diag = json.loads((ROOT / "dma-single-diagnostics" / "step2_frame00_id0000000000_diagnostics.json").read_text(encoding="utf-8"))


def register_row(snapshot):
    r = snapshot["registers"]
    return {
        "FPGA0_CLK_CTRL": r["SLCR_FPGA0_CLK_CTRL"]["raw_hex"],
        "FPGA_RST_CTRL": r["SLCR_FPGA_RST_CTRL"]["raw_hex"],
        "AFI1_RDCHAN_CTRL": r["AFI1_RDCHAN_CTRL"]["raw_hex"],
        "AFI1_WRCHAN_CTRL": r["AFI1_WRCHAN_CTRL"]["raw_hex"],
        "AFI1_WRCHAN_CTRL_bit0": r["AFI1_WRCHAN_CTRL"]["width_bit0"],
    }


stage1 = {x["stage"]: register_row(x) for x in overlay["afi1_snapshots"]}
dma_context = dma["rx_diagnostics"]["context"]
stage2 = register_row(dma_context["afi1_after_dma"])
raw_files = {}
for name in ("rx_raw.bin", "tx_raw.bin", "expected_golden.bin"):
    data = (ROOT / name).read_bytes()
    raw_files[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

summary = {
    "boot_id": overlay["boot_id"],
    "manifest_sha256": overlay["manifest_sha256"],
    "overlay_only": {
        "status": overlay["status"],
        "board_accessed": overlay["board_accessed"],
        "pynq_imported": overlay["pynq_imported"],
        "snapshots": {
            "pre-overlay": stage1["before_overlay_load"],
            "post-overlay": stage1["after_overlay_load"],
        },
    },
    "dma_single": {
        "status": dma["status"],
        "board_accessed": dma["board_accessed"],
        "transfer_started": dma["transfer_started"],
        "dma_start_attempted": dma["dma_start_attempted"],
        "dma_may_be_active": dma["dma_may_be_active"],
        "error": dma["error"],
        "byte_mismatches": dma["byte_mismatches"],
        "first_mismatch": dma["first_mismatch"],
        "s2mm_dmasr": dma_context["s2mm_dmasr"],
        "mm2s_dmasr": dma_context["mm2s_dmasr"],
        "post-dma-registers": stage2,
        "parity": diag["parity"],
        "rx_diagnostics": dma["rx_diagnostics"]["capture_status"],
        "raw_files": raw_files,
    },
}
(ROOT / "board-evidence-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, indent=2))
