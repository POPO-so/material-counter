# JPEG hard-decode — phase 2 status (2026-11-02, updated)

Isolated worktree `C:/fpga/wt/claude-jpeg-20261102`, branch `codex/claude-jpeg-20261102`, base `3d466c6c`. Factual working note, not a design freeze. Software port phase is a separate branch `codex/m8-selective-port-20261102` (`58b95273`, `8aa4f6f4`).

## Toolchain
Vivado/Vitis **are installed** and working: `F:\AMDDesignTools\2026.1\Vivado\bin\vivado.bat` reports `vivado v2026.1 (64-bit)` (license limit 2026.06), with `Vitis/bin/vitis-run.bat` and `v++.bat` present. No Vivado/ROSS MCP is connected in this Claude session; batch entry points are the verified fallback. Project `.venv` (`C:\fpga\material-counter\.venv`) has pytest 9.1.1 / numpy 2.5.3 / cv2 5.0.0 and is used for tests.

## Done (commits on this branch)
- `52d524f9` — imported JPEG PS candidate (transport/runtime/runner + tests); removed the 0x44 total_count write; named FRG1 v FRG2 in errors.
- `7765851d` — Gap 2: `process_jpeg_frame` now validates FRG identity then counts via `tracker.update_rle` (run-based CCL + candidate 2-4 way split), and the runner uses the field-v1 counter. End-to-end touching-pair split regression yields 2 detections/boxes.
- (uncommitted) Gap 6: `preflight_jpeg_header` parses baseline/640x400/4:2:2/single-scan/DRI=40/header≤4096 as a PS pre-check at the camera boundary; PL stays authoritative.

## Gap status
| # | Gap | State |
|---|---|---|
| 1 | 0x44 total_count register absent in RTL | Fixed in PS (write removed). **This is not digit display**: current display capability is mask/line/boxes only; a running count digit is a separate, not-yet-scoped RTL task |
| 2 | JPEG runtime bypassed touching-nut split | **Fixed** — counts via `update_rle`; FRG identity/status/geometry still validated |
| 3 | FRG version | **Fixed candidate = c5a8680, all FRG1.** `video_binary_overlay_bd.v` (dfc48722…) returns `0x46524731`; `rle_feature_tap.v` (493bc04f…) is instantiated by that FRG1 wrapper — neither is FRG2. FRG2 (`0x46524732`) exists only in the *latest* worktree `overlay_bd`; it is a separate later upgrade, not part of this candidate. Pinned set: see `data/results/jpeg-csynth-probe-20261102/receipt.json`. The D695 wrapper (`d695aac…`) and the 19439 BRAM region (`19439a2c…`, recorded replacement) are NOT at c5a8680 — they come from later branches. |
| 4 | Artifact admission (file/hash/status-string only) | **Open** — HWH part/IP parse, source-manifest and route binding (offline-doable, not yet done) |
| 5 | JPEG format restrictions | SOI/EOI/size + PS header preflight (baseline, 640x400, 4:2:2, DRI=40, single-scan, header≤4096); PL wrapper remains final authority |
| 6 | DRI/DQT "not PS-checkable" claim | **Corrected** — DRI/SOF/DQT presence are PS-parseable; `preflight_jpeg_header` does this |

## Dependencies (verified on this machine — no PYNQ blocker)
- PYNQ_SOURCE = `C:/fpga/ext/pynq-v3.1` (HEAD 19ed17d4…; `verify_sources.py` 32/32 ok, 0 errors).
- VIDEO_IP_BUILT = `C:/fpga/video-ip-20261101` (`color_convert` + `pixel_unpack` `solution1/impl/ip` present).
- board repo = `C:/fpga/vivado_board_repos` (Pynq-Z2 def at `tul.com.tw/pynq-z2/A.0`).
- ipcfg = in-repo `.agents/skills/vivado-ip-configurator/lib/ipcfg.tcl`.

## Done this round
- HLS CSim / CSynth / C/RTL co-sim (smoke + faults×7 + entropy-cut, random_stall) + independent Vivado OOC all PASS; threshold-boundary CSim (46k risk px) 0 flips.
- 19439 region verification (sim reference_diff/limits + OOC BRAM mapping) `REGION_RUN_PASS` (`C:/fpga/wt/rgn2026/region-…`).
- `prepare_snapshot.py` extracted fixed c5a8680 FRG1 sources + per-file manifest.

## Remaining (serial heavy, in order)
1. `create_g2_integration.tcl` (single entry, `OFFLOAD_CANDIDATE=1`) → FRG1 baseline XPR/BD + validate/save + clock/CDC/pin readback (env: PYNQ_SOURCE, VIDEO_IP_BUILT, G2_OUT/DISPLAY_OUT/DISPLAY_NATIVE_LOG).
2. `joint_prepare --verify-gates --stage` (HLS + region green receipts bound).
3. `joint_connect.tcl` → `joint_build.tcl` (joint synth) → clock/CDC/mailbox/DRC/timing review → route → matched BIT/HWH export (per-input/report SHA).

## Unclosed / later
- HLS timing estimate 15.96 ns vs 14.6 ns budget (OOC nominal WNS 4.964 ns) — reaffirm at joint synthesis; keep the warning open.
- vendorY bit-exact reference — later, non-blocking.
- FRG2 remains a separate future upgrade (fixed candidate = FRG1).