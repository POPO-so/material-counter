# C candidate — smoke-003 precheck stopped: complete base DMA map mismatch (offline map extension)

Date: 2026-11-03
Worktree: `C:/fpga/wt/claude-jpeg-20261102`
Result: **smoke-003 NOT STARTED.** The read-only base.hwh check found two DMA
bus masters that were missing from the previously pinned map. Per the
no-on-the-spot-whitelist rule the run stopped before any MMIO, candidate load,
or transfer. The board was only read (SSH file/state reads); nothing was
uploaded, reset, freed, reloaded, or rebooted. `count_accuracy` remains
`NOT_MEASURED`.

## 1. Real base.hwh evidence

Read-only copy saved at:

`data/results/jj2026p-board-smoke-003-20261103b/precheck/base.hwh`

| Item | Value |
|---|---|
| Board path | `/usr/local/share/pynq-venv/lib/python3.10/site-packages/pynq/overlays/base/base.hwh` |
| Size | 4,529,854 bytes |
| SHA-256 | `222898ef2c0ccc4a0c47608e99d8dc7c9c6cdfed5130964884ff35005827ef3a` |
| Matches pinned base HWH hash | YES |
| Device / part | `xc7z020clg400-1` |
| base.bit SHA-256 / SHA-1 | `3239e9cc...` / `39b937b4...` (both match) |

Complete DMA bus-master table parsed from the original file:

| Instance | IP | Base range | Channels |
|---|---|---|---|
| `video_axi_vdma` | `xilinx.com:ip:axi_vdma:6.3` | `0x43000000`–`0x4300FFFF` | MM2S + S2MM |
| `trace_analyzer_pi_axi_dma_0` | `xilinx.com:ip:axi_dma:7.1` | `0x80430000`–`0x8043FFFF` | S2MM only |
| `trace_analyzer_pmodb_axi_dma_0` | `xilinx.com:ip:axi_dma:7.1` | `0x80420000`–`0x8042FFFF` | S2MM only |

Saved parse: `precheck/base-map.json`.

The prior pinned map contained only `video_axi_vdma`. The two
`trace_analyzer` AXI DMA S2MM masters are additional, so the complete table
did **not** match. The instruction says any mismatch stops and the whitelist
must not be expanded on the spot; the run stopped here.

## 2. Read-only precheck readings (before the stop)

Captured in `precheck/board-state.txt`:

| Item | Value |
|---|---|
| Boot ID | `6b0d11ee-d9cb-4513-afc8-fee3296448df` (same boot; no reboot) |
| Material/JPEG tasks | none |
| FPGA manager | `operating`, flags `0` |
| Boot bitstream | `base.bin` |
| PYNQ state | `GLOBAL_STATE_PRESENT`, `active_name=Pynq-Z2`, `shutdown_ips={}` |
| PYNQ loaded bit | base.bit, SHA-1/SHA-256 match the pinned base |
| Video owners | none |
| CMA | 131,072 KiB total / 37,748 KiB free |

Camera raw MJPEG format and the three-state VDMA readings were **not** taken:
camera/format checks were queued after the base-map gate, and step 3 explicitly
allows VDMACR/VDMASR reads only after base identity and mapping are confirmed.
No candidate or base MMIO was issued.

## 3. VDMA / AXI DMA criteria after the stop (offline)

Because the run stopped before MMIO, there are **no actual VDMACR/VDMASR
readings** for this attempt. Do not infer values.

Official definitions used for the offline map extension:

**AXI VDMA v6.3 (PG020)** — `VDMASR` offset `0x04` (MM2S) / `0x34` (S2MM):
- bit 0 `Halted` (1 = halted); bit 1 **Reserved, always 0 — not Idle**.
- MM2S error bits 4–7 (mask `0x00F0`); S2MM error bits 4–8, 11, 15 (mask
  `0x89F0`). Reserved bits must read 0.
- Safe state: `VDMACR.RS=0`, `VDMASR.Halted=1`, no error/reserved bits.

**AXI DMA v7.1 (PG021)** — `DMASR` offset `0x04` (MM2S) / `0x34` (S2MM):
- bit 0 `Halted`; bit 1 `Idle`, but Idle reads 0 while Halted=1, so it is not
  required for a halted channel.
- error bits 4–6 and 8–10 (mask `0x0770`); reserved bits 2, 7, 11, 15
  (mask `0x8884`).
- Safe state: `DMACR.RS=0`, `DMASR.Halted=1`, no error/reserved bits.

The `0x770` mask is correct for AXI DMA and was wrongly reused for VDMA. The
earlier wording “0x770 misses bit 8” was wrong and is corrected in
`c_candidate_smoke003_vdma_register_blocker_20261103.md`: `0x770` includes
bit 8; the defect is that bit 8 is Reserved on MM2S and EOLEarlyErr on S2MM,
and the mask omits bits 7, 11, and 15.

## 4. Offline fix in this package

- The pinned base map now has all three confirmed masters with their exact
  addresses, IP types, and `C_INCLUDE_MM2S`/`C_INCLUDE_S2MM` channel sets.
- The HWH parser derives each master's type from VLNV and requires the complete
  `(vlnv, type, base, channels)` set to equal the pinned set.
- The probe dispatches per type: AXI VDMA v6.3 criteria for `video_axi_vdma`;
  AXI DMA v7.1 criteria for the two `trace_analyzer` S2MM channels.
- Only confirmed base addresses are read; no candidate address is touched
  before `Overlay.download`. Any mismatch, active/faulty master, or reserved
  bit stops as `PreloadUnsafe` with no reset/release/reload/retry.

## 5. Tests

Full JPEG regression: **121 passed**

| Suite | Result |
|---|---|
| admission | 5 |
| display/HOLD | 13 |
| pre-load policy | **37** |
| transport integration | 3 |
| runtime/transport | 63 |

New coverage includes the two trace AXI DMA S2MM masters (safe halted with
Idle=0 accepted; DMAIntErr/SGIntErr, RS=1, and reserved-bit cases refused),
the exact three-master map from the real HWH, and the proof that pre-load MMIO
is limited to `{0x43000000, 0x80430000, 0x80420000}`. The packaged runtime was
also run against the saved real base.hwh: the pinned map matches.

## 6. New package

| Item | SHA-256 |
|---|---|
| BIT (unchanged) | `75577d743060a0908f5248e5a12062428bc1d6ad603c81fc5fdaedc9b15b230b` |
| HWH (unchanged) | `06cf10cafc545d930fa210cd8fdd88d09981d808cb1c4197146183f822901380` |
| Package manifest | `152036decba1fdeddc2f5110d5835bdc6406b6b17f4adce970a94e7f40396e6f` |
| Package SHA256SUMS | `2e05261ae23b30f42e4ac2c371bd114fc859bd2db466189656b3f975c90be15c` |
| Package README | `e217df9777e156f1a5a7170797675a2ebac402405a3c1f05d3d99c982bad05e9` |
| `run_jpeg_counter.py` | `e23a49be41aa92051595cb562840465bbadfbc4a0a2124af9292e4ce8d1674f9` |
| pre-load test | `627ae0ea5eee87cd871519bb32fc8c7f7e4e47bf35c6b42325dc3e8ef3173517` |

`sha256sum -c SHA256SUMS` = 345/345 OK; package `verify_runtime_artifacts` =
PASS; BIT/HWH unchanged.

## 7. Pending re-authorization

smoke-003 remains **not started**. On the next authorization the same order
applies: re-read and save base.hwh (expected SHA-256 `222898ef...`), confirm
the three-master map, then read only the confirmed VDMA/AXI DMA control and
status registers and apply the criteria above. Only if all pass may the
candidate be loaded and one 60-frame run started. Any mismatch stops again
without reset/release/overwrite.

## 8. Limitations

- No actual VDMA/AXI DMA register values, run report, or logs exist for this
  attempt; none are claimed.
- The board was only read over SSH (state and HWH); no MMIO, no upload, no
  candidate load, no transfer, no reboot.
- `count_accuracy` remains `NOT_MEASURED`; no physical HDMI or accuracy claim.
- No deployment, push, merge, or startup bitstream change.