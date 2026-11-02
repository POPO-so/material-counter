# C candidate — smoke-003 stopped at the AXI VDMA register precheck (offline fix)

Date: 2026-11-03
Worktree: `C:/fpga/wt/claude-jpeg-20261102`
Result: **smoke-003 NOT STARTED. No board access, no upload, no candidate
load, no MMIO, no transfer, no reboot.** The register-definition check failed
before any board work, so the authorized board test was stopped as required.
`count_accuracy` remains `NOT_MEASURED`.

## 1. Gate outcome

The previous KNOWN_BASE probe reused the AXI DMA status mask and required
`(VDMASR & 0x3) == 0x3`. That is wrong for AXI VDMA v6.3:

- `VDMASR` bit 1 is **Reserved, always 0** in AXI VDMA; it is not an Idle bit.
  Requiring it to be 1 would make every real VDMA read fail the gate.
- The AXI DMA mask `0x770` (bits 4,5,6,8,9,10) does not match either VDMA
  channel's error layout.

Per the authorization (“若发现代码需要修改，停止本次板测，离线修复、测试、重新打包后交审”),
the board test was stopped. Step 1 (read-only base.hwh retrieval) was not
performed because the step-2 gate failed first; the board remains in the PYNQ
base overlay, untouched by this round.

## 2. Official AXI VDMA v6.3 register definitions (PG020)

`MM2S_VDMASR` offset `0x04`, `S2MM_VDMASR` offset `0x34`:

| Bit | MM2S v6.3 | S2MM v6.3 |
|---:|---|---|
| 0 | Halted (RO) | Halted (RO) |
| 1 | **Reserved, reads 0** | **Reserved, reads 0** |
| 2–3 | Reserved | Reserved |
| 4 | VDMAIntErr (R/WC) | VDMAIntErr (R/WC) |
| 5 | VDMASlvErr (RO) | VDMASlvErr (RO) |
| 6 | VDMADecErr (RO) | VDMADecErr (RO) |
| 7 | SOFEarlyErr (R/WC) | SOFEarlyErr (R/WC) |
| 8 | Reserved | EOLEarlyErr (R/WC) |
| 9–10 | Reserved | Reserved |
| 11 | Reserved | SOFLateErr (R/WC) |
| 12 | FrmCnt_Irq | FrmCnt_Irq |
| 13 | DlyCnt_Irq | DlyCnt_Irq |
| 14 | Err_Irq | Err_Irq |
| 15 | Reserved | EOLLateErr (R/WC) |
| 31–16 | IRQDelayCntSts / IRQFrameCntSts | IRQDelayCntSts / IRQFrameCntSts |

Meanings used by the gate:

- **Halted (bit 0)**: 0 = running, 1 = halted. Set when `VDMACR.RS = 0`, with a
  possible lag. The gate therefore also reads `VDMACR.RS` (offset `0x00`
  MM2S, `0x30` S2MM) and requires it to be 0.
- **No Idle bit exists.** The AXI DMA-style `Idle` assumption must not be used.
- **MM2S error mask `0x00F0`**: bits 4–7.
- **S2MM error mask `0x89F0`**: bits 4–8, 11, 15.
- Bits 12–14 are interrupt flags, not transfer/error state; they are reported
  but not gated.
- Documented reserved status bits must read 0; a non-zero reserved bit is
  treated as an unexpected design state and stops the run.

Sources: AMD PG020 v6.3
[MM2S_VDMASR](https://docs.amd.com/r/en-US/pg020_axi_vdma/MM2S_VDMASR-MM2S-VDMA-Status-Register-Offset-04h)
and
[S2MM_VDMASR](https://docs.amd.com/r/en-US/pg020_axi_vdma/S2MM_VDMASR-S2MM-VDMA-Status-Register-Offset-34h).

## 3. Offline fix

`scripts/experiments/run_jpeg_counter.py`:

1. `VDMA_CHANNELS` now holds the per-channel control/status offsets, error
   masks/fields, and reserved masks for AXI VDMA v6.3.
2. The KNOWN_BASE probe no longer uses `0x770` or bit 1. For every confirmed
   channel it reads `VDMACR` and `VDMASR` and requires:
   - `VDMACR.RS == 0`;
   - `VDMASR.Halted == 1`;
   - no defined error bit;
   - no reserved-status bit set.
   It records the raw control/status values, RS, Halted, the reserved-bit value,
   the error mask, and the named error bits.
3. The base HWH parser now reads `C_INCLUDE_MM2S` and `C_INCLUDE_S2MM` and
   requires the channel set to equal the pinned set (both channels for the PYNQ
   base `video_axi_vdma`). A missing flag or channel mismatch stops before any
   MMIO.
4. No candidate address is read before `Overlay.download`; no reset, free,
   reload, or retry is performed on failure.

The existing `scripts/standalone/vdma_diag.py` already documented the MM2S
error bits 4–7 and warned against reusing `0x770`; the policy code is now
consistent with it. `vdma_diag.py` itself is unchanged.

## 4. Tests

Full JPEG regression: **116 passed**

| Suite | Result |
|---|---|
| admission | 5 passed |
| display/HOLD | 13 passed |
| pre-load policy | **32 passed** |
| transport integration | 3 passed |
| runtime/transport | 63 passed |

New pre-load tests cover:

- VDMA safe state with bit 1 = 0, `RS=0`, `Halted=1`, no errors, no reserved
  bits → accepted;
- MM2S SOFEarlyErr (bit 7) → refused;
- S2MM EOLLateErr (bit 15) → refused;
- `VDMACR.RS=1` → refused;
- reserved status bit set → refused;
- error masks are `0x00F0` / `0x89F0` and are never `0x770`;
- HWH channel flags are required and must match `mm2s`+`s2mm`;
- the KNOWN_BASE pre-load MMIO set remains exactly `{0x43000000}`.

The same 32 tests pass against the packaged copy.

## 5. New package

| Item | SHA-256 |
|---|---|
| BIT (unchanged) | `75577d743060a0908f5248e5a12062428bc1d6ad603c81fc5fdaedc9b15b230b` |
| HWH (unchanged) | `06cf10cafc545d930fa210cd8fdd88d09981d808cb1c4197146183f822901380` |
| Package manifest | `77d4f7bb0e6d086d698d5d3b814640cfa2bb7fe3187ec984df4f7429f385a2da` |
| Package SHA256SUMS | `b962a4ad04d67ebb88a8b063db812d0ccc32f1f6cdf193e34a4e87aa2ed480ec` |
| Package README | `ca99fb591e3c9a210f8db582f28ad4fb1e40f137b3afe37d9890074d74921578` |
| `run_jpeg_counter.py` | `48e65f7ba002a4136a4e016fd1740cab9677a8fda476b75a35b7d66b8b2dbb62` |
| pre-load test | `2321bc844cc27bb414788e18b5a621e0739ae360336b3415390d8c9d7581cb58` |

`sha256sum -c SHA256SUMS` = 345/345 OK; package `verify_runtime_artifacts` =
PASS; BIT/HWH unchanged.

## 6. Pending re-authorization

smoke-003 remains **not started**. On the next authorization, the prechecks
must begin with the read-only retrieval of the actual `base.hwh` adjacent to
`base.bit`, saving the original file, its SHA-256, and the parsed DMA
bus-master/channel table, then repeat boot ID/ownership/camera/CMA/three-state
checks. No board access was made this round; nothing was reset or reloaded.

## 7. Limitations

- No board access, no actual VDMA register values were read, and no run report
  or logs exist for smoke-003.
- The fix follows the AMD PG020 v6.3 definitions above; the PYNQ base HWH
  channel set is required to match `C_INCLUDE_MM2S=1` / `C_INCLUDE_S2MM=1`
  from the captured evidence.
- `count_accuracy` remains `NOT_MEASURED`; no physical HDMI or accuracy claim.
- No deployment, push, merge, or startup bitstream change.