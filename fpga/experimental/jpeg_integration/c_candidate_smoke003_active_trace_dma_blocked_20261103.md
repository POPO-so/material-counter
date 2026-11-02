# C candidate — smoke-003 precheck stopped: active trace-analyzer DMA channels

Date: 2026-11-03
Board: PYNQ-Z2 at `192.168.2.99`, boot ID
`6b0d11ee-d9cb-4513-afc8-fee3296448df` (same boot; no reboot).
Package: `/home/xilinx/jpeg-candidate-20261103c`, SHA256SUMS
`2e05261a...`, manifest `152036de...`, BIT `75577d74...`, HWH `06cf10ca...`.
Result: **smoke-003 NOT STARTED — precheck stopped at the four-channel DMA
gate. The candidate was not loaded; no frames; no HOLD; no reset/release/
reload/retry.** `count_accuracy` remains `NOT_MEASURED`.

## 1. Base identity and full HWH

Re-read on the board and copied to
`data/results/jj2026p-board-smoke-003-20261103c/precheck/base.hwh`:

| Item | Value |
|---|---|
| base.bit SHA-256 / SHA-1 | `3239e9cc37ee4993c25f02ae6c97da0b3372f0e722e267d10073e20adc5d6717` / `39b937b490015ec0e1d29f18a9143292821f74ad` |
| base.hwh SHA-256 | `222898ef2c0ccc4a0c47608e99d8dc7c9c6cdfed5130964884ff35005827ef3a` |
| Device | `xc7z020clg400-1` |
| PYNQ state | `GLOBAL_STATE_PRESENT`, `active_name=Pynq-Z2`, `shutdown_ips={}` |
| Boot bitstream | `base.bin`; FPGA manager `operating`, flags `0` |

The complete DMA map matched the pinned map exactly:

| Instance | IP | Base | Channels |
|---|---|---|---|
| `video_axi_vdma` | `axi_vdma:6.3` | `0x43000000` | MM2S + S2MM |
| `trace_analyzer_pi_axi_dma_0` | `axi_dma:7.1` | `0x80430000` | S2MM only |
| `trace_analyzer_pmodb_axi_dma_0` | `axi_dma:7.1` | `0x80420000` | S2MM only |

## 2. Precheck state and camera

- No material/JPEG task; no camera owner; no FPGA owner findings.
- Three-state classification: **KNOWN_BASE** (identity and map verified).
- CMA: 37,748 KiB free before the precheck; 11,056 KiB during the
  PYNQ-imported precheck process; 37,048 KiB after it exited. The drop is
  transient PYNQ allocation in the precheck process, not evidence of a leak;
  the formal runtime reads CMA before importing PYNQ. No reboot/retry was
  derived from it.
- Camera raw MJPEG: 3 frames, all 640x400, 8-bit, baseline SOF0, 4:2:2,
  DRI=40, single scan, header 597 B — **PASS**.

## 3. Four-channel register snapshot (read-only, no writes)

Saved in `precheck/four-channel.json`.

| Master / channel | Control | Status | Gate |
|---|---|---|---|
| `video_axi_vdma` MM2S | `0x00010082` | `0x00010001` | SAFE |
| `video_axi_vdma` S2MM | `0x00010002` | `0x00010001` | SAFE |
| `trace_analyzer_pi_axi_dma_0` S2MM | `0x00011003` | `0x00000000` | **UNSAFE — running** |
| `trace_analyzer_pmodb_axi_dma_0` S2MM | `0x00011003` | `0x00000000` | **UNSAFE — running** |

### 3.1 Video VDMA (PG020 v6.3)

- MM2S control `0x00010082`: RS (bit 0) = 0; Circular_Park (bit 1) = 1;
  GenlockSrc (bit 7) = 1; IRQFrameCount (bits 23–16) = 1. No Reset.
- MM2S status `0x00010001`: Halted (bit 0) = 1; bit 1 Reserved = 0;
  error bits 4–7 = 0; reserved bits = 0; FrmCnt_Irq (bit 12) = 1;
  IRQFrameCntSts = 1.
- S2MM control `0x00010002`: RS = 0; Circular_Park = 1; IRQFrameCount = 1.
- S2MM status `0x00010001`: Halted = 1; bit 1 Reserved = 0; error bits
  4–8/11/15 = 0; reserved bits = 0; FrmCnt_Irq = 1.
- Verdict: halted, stopped, error-free → safe.

### 3.2 trace_analyzer AXI DMA (PG021 v7.1)

Both S2MM channels have the same values:

- Control `0x00011003`:
  - RS (bit 0) = **1 → run**;
  - bit 1 Reserved = 1 (documented always-1, normal);
  - Reset (bit 2) = 0;
  - IOC_IrqEn (bit 12) = 1;
  - IRQThreshold (bits 23–16) = 1;
  - Cyclic (bit 4) = 0, Keyhole (bit 3) = 0.
- Status `0x00000000`:
  - Halted (bit 0) = **0 → running**;
  - Idle (bit 1) = 0;
  - SGIncld (bit 3) = 0 (direct mode);
  - error bits 4–6 and 8–10 = 0;
  - reserved bits 2, 7, 11, 15 = 0.
- Verdict: **active bus masters, not halted**. No error bits, but they are
  actively running, so they are not safe to overwrite.

## 4. Decision

Per the authorization (“活动、错误或不确定即停止，不reset、不释放、不覆盖加载”):

- `Overlay.download` was **not** executed.
- No candidate DMA/overlay/JPEG MMIO was touched.
- No `run_jpeg_counter.py` process, console log, stage file, or 60-frame
  output was created; `run-20261103c/jpeg-smoke-003.json` does not exist.
- No reset, free, reload, or retry was issued; the two trace DMA channels were
  left running.

## 5. Evidence

Directory: `data/results/jj2026p-board-smoke-003-20261103c/`

| File | SHA-256 |
|---|---|
| `precheck/base.hwh` | `222898ef...` |
| `precheck/precheck.json` | `23b85d4ba71a020fa8b4248849ca2ad4ad39edf8ce044952436dad02d3f4eb49` |
| `precheck/four-channel.json` | `c737dc62580ef9a8787dc977e0fbc2720e5b074092f40f660e22e8d1f1780515` |
| `precheck/camera.json` | `01efd3c465c1b09f014d741c9dd242887cb17cd4e33e6a67ef391f35db0f9dad` |
| `precheck/camera-frame-0.jpg` | `720ff213...` |
| `precheck/camera-frame-1.jpg` | `ab887dcd...` |
| `precheck/camera-frame-2.jpg` | `54ae2b26...` |
| `postprecheck-state.txt` | `82eb13e0...` |

The package on the board matched SHA256SUMS `2e05261a...` (345/345), manifest
`152036de...`, BIT/HWH full reviewed SHAs.

## 6. Status and next step

- smoke-003 remains **not started**; this is a pre-load blocker, not a
  candidate failure or success.
- The policy worked as intended; no code change was made in this round.
- The open question is the base design's two `trace_analyzer` AXI DMA S2MM
  channels, which are running with IOC interrupts enabled at boot. Resolving
  this requires an explicit operator/policy decision (for example: evidence
  that they are intentionally free-running and do not touch the memory the
  candidate uses, an approved stop procedure, or a different verified base
  state). No whitelist or runtime behavior was changed on the spot.
- `count_accuracy` remains `NOT_MEASURED`; no physical HDMI or accuracy claim.
- No deployment, push, merge, or startup bitstream change.