# JPEG hard-decode — phase 2 status checkpoint (2026-11-02)

Isolated worktree `C:/fpga/wt/claude-jpeg-20261102`, branch `codex/claude-jpeg-20261102`, base `3d466c6c`. This is a factual working note, not a design freeze. The software selective-port phase is a separate branch `codex/m8-selective-port-20261102` (`58b95273`); no JPEG dependency was pulled from it.

## Done this session
- Imported the teammate JPEG PS candidate files and tests into this worktree (isolated, not merged into the counting path):
  `scripts/rle/jpeg_transport.py` + `test_jpeg_transport.py`, `scripts/rle/jpeg_runtime.py` + `test_jpeg_runtime.py`, `scripts/experiments/run_jpeg_counter.py` + `test_run_jpeg_counter.py`.
- Host regression passes against the local M8 codebase: **58 JPEG PS tests passed**.
- **Gap 1 fixed (software):** removed `publish_overlay`'s `0x44` total_count write + readback. Neither the fixed c5a8680 overlay map nor `video_binary_overlay_bd.v` implements a total_count digit register, so `0x44` writes return SLVERR. Contract documented in the method docstring (digit display is a pending RTL addition).
- **Gap 3 (partial, fail-closed):** mismatch errors now name the read signature and FRG1 (`0x46524731`) vs FRG2 (`0x46524732`) status; FRG2 remains unsupported by the transport record layout.

## Gap status and evidence
| # | Gap | State |
|---|---|---|
| 1 | 0x44 total_count register absent in RTL | Fixed in PS (write removed); count-digit RTL register still required for digit display |
| 2 | JPEG runtime bypasses touching-nut split (`update_detections` only) | **Open** — must route FRG detections through the split-capable counter path (mask reconstruct → `MaskCounter.update`), not silently under-count |
| 3 | FRG1 (transport, fixed c5a8680) vs FRG2 (latest `overlay_bd`) | Fail-closed; version selection and FRG2 record layout still to be decided |
| 4 | Artifact admission is file/hash/status-string only | **Open** — need HWH part/IP parse and source-manifest/route binding so fake byte files fail |
| 5 | JPEG format restrictions | Partially covered (SOI/EOI, 640x400, ≤512000 B); DRI/DQT baseline correctness is a PL-decoder constraint, not PS-checkable |

## Blocked — no hardware toolchain here
- No Vivado/Vitis under any install path and no `vivado`/`vitis_hls` on PATH; no Vivado/ROSS MCP connected in this session. Therefore HLS CSim/CSynth, joint synthesis, place/route, BIT/HWH export, and clock/CDC/mailbox/pixel-path review **cannot run in this environment**.
- The teammate HLS logs, exported components, and `.runtime/j9` joint reports were not uploaded to the repo (audit §8); only source + receipts are present.

## Next steps (ordered)
1. Gap 2: wire `process_jpeg_frame` to reconstruct the RLE mask and run the split-capable counter; add touching/4-way regression. Deepest remaining software item; must preserve 5feafe63 / 3d466c6c split + local_mask(bounds=) semantics.
2. Gap 4: parse HWH (part + expected IP/register map) and bind BIT/HWH to route receipt + source manifest.
3. Gap 3: decide FRG1 (fixed c5a8680, has JPEG receipt) vs FRG2 (latest root, unvalidated record layout); keep them as separate candidates.
4. Hardware: on a machine with the toolchain, run HLS (explicit cfg + isolated output dir), joint synth/PnR, and clock/CDC/mailbox review; then BIT/HWH export with source/manifest/route binding.