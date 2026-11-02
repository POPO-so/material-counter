# C candidate — trace DMA cause and bounded normal-stop plan (offline)

Date: 2026-11-03
Worktree: `C:/fpga/wt/claude-jpeg-20261102`
Scope: read-only board investigation, offline implementation and fake tests.
No board register write, no candidate load, no smoke. BIT/HWH/RTL/constraints
unchanged; no push or merge.

## 1. Why the two trace S2MM channels are running

The installed PYNQ base overlay driver is a component that programs
these exact DMA addresses; the observed register value matches its
initialization exactly. No other component is identified in the retained
evidence, but the historical caller is not established (see section 7).

- `pynq/overlays/base/base.py`: `BaseOverlay.__init__` constructs
  `TraceAnalyzer` objects (`trace_rpi`, `trace_pmoda`, `trace_pmodb`).
- `pynq/lib/logictools/trace_analyzer.py`: `_PSTraceAnalyzer.__init__` builds a
  `DMA(trace_dma_info)`.
- `pynq/lib/dma.py`: `DMA.set_up_rx_channel()` creates `_SDMAChannel`, whose
  `__init__` calls `start()`, writing `S2MM_DMACR = 0x1001` (RS=1, IOC_IrqEn=1)
  and waiting for running.

The observed `S2MM_DMACR = 0x00011003` is exactly that value read back with the
documented always-1 Reserved bit 1 and IRQThreshold=1. `S2MM_DMASR = 0`, so
`Halted=0`; `S2MM_DA = 0` and `S2MM_LENGTH = 0`, so **no transfer is armed at
the time of sampling**. This is the “started, waiting for data” state. The
registers do not establish whether an earlier transfer completed before this
sample (the completion value could have been overwritten or reset), so the
stop plan does not assume a clean history.

No boot service, startup script, crontab, or rc.local references
`BaseOverlay`/`TraceAnalyzer`, and no current process owns them. The observed
register value exactly matches the driver's initialization write. Which
process or session constructed `BaseOverlay` and left the channels running is
**not established** by the available logs; that historical caller remains
unknown. The Jupyter service is running, but its log shows no kernel or trace
activity, so it cannot be attributed. The verified mechanism is the PYNQ
driver initialization itself; the candidate runtime and a boot service are
ruled out.

The formal runtime uses `pynq.Overlay(candidate)`, which only downloads and
parses the candidate bitstream; it does **not** instantiate `BaseOverlay` or
`TraceAnalyzer`. It therefore does not restart these channels. Any other
process that constructs `BaseOverlay` would start them again, which is why the
stop is placed immediately before `Overlay.download` and followed by a
stability re-read.

## 2. Read-only register evidence

Saved in `data/results/jj2026p-board-smoke-003-20261103c/precheck/four-channel-detail.json`.

| Master / channel | control | status | DA | LENGTH | State |
|---|---|---|---|---|---|
| `video_axi_vdma` MM2S | `0x00010082` | `0x00010001` | — | — | halted, safe |
| `video_axi_vdma` S2MM | `0x00010002` | `0x00010001` | — | — | halted, safe |
| `trace_analyzer_pi_axi_dma_0` S2MM | `0x00011003` | `0x00000000` | `0` | `0` | RS=1, no transfer armed |
| `trace_analyzer_pmodb_axi_dma_0` S2MM | `0x00011003` | `0x00000000` | `0` | `0` | RS=1, no transfer armed |

AXI DMA v7.1 bit interpretation for `0x00011003` / `0x00000000`:
- Control: RS(0)=1; Reserved(1)=1 (always 1); Reset(2)=0; Keyhole(3)=0;
  Cyclic(4)=0; IOC_IrqEn(12)=1; IRQThreshold(23:16)=1.
- Status: Halted(0)=0; Idle(1)=0; SGIncld(3)=0; error bits 4–6, 8–10=0;
  reserved bits 2,7,11,15=0.
- `S2MM_DA`/`LENGTH` = 0: the channel has not been given a destination or
  length, so no write burst is pending. The stop does not need to reason about
  an unknown buffer.

## 3. Bounded normal-stop plan

New CLI flag: `--stop-base-trace-dmas` (default off). It is opt-in; without it
the previous fail-closed behavior is unchanged.

Function: `stop_base_trace_dmas()` in `scripts/experiments/run_jpeg_counter.py`.

Preconditions (before any write):
- classification is `KNOWN_BASE` and the base is the pinned `pynq_base`;
- the HWH was already verified (hash + complete map + channel set);
- the `axi_dma` set is exactly the two pinned instances:
  `trace_analyzer_pi_axi_dma_0` @ `0x80430000` and
  `trace_analyzer_pmodb_axi_dma_0` @ `0x80420000`, each S2MM-only.
  Any extra/missing/changed instance aborts before a write.

Allowed write (only):
- `S2MM_DMACR` at `0x80430000 + 0x30` and `0x80420000 + 0x30`;
- value = old value with bit 0 (RS) cleared, all other bits preserved;
- for the observed value: `0x00011003` → `0x00011002`.

Sequence per channel:
1. read `DMACR`/`DMASR` (and record before values);
2. if RS=1, write `old & ~1`;
3. poll `RS=0` and `Halted=1` for up to 1.0 s;
4. wait a 0.2 s stability window, re-read control/status;
5. recheck error bits (`0x0770`) and reserved bits (`0x8884`), without clearing
   them.

Never:
- write `S2MM_DMASR` (no error clear, no interrupt clear);
- set Reset (bit 2) or any other control bit;
- free a buffer, reset the DMA, reload, or reboot;
- auto-retry on timeout.

Timeout, re-arm (RS returns to 1), error bits, reserved bits, or an unexpected
target set all stop the boot attempt before `Overlay.download`.

This does not reuse PYNQ `_SDMAChannel.stop()`: that writes `DMACR=0x0000`
(clears other control bits) and busy-waits without a timeout. The bounded
read-modify-write above is narrower and cannot reset or free anything.

## 4. Integration with the formal entry

`run_candidate_sequence` for `KNOWN_BASE` now performs:

```text
verify HWH identity/map
  -> [if flag] known_base_dma_stop
  -> [if flag] known_base_dma_stop_done
  -> four-channel recheck (must now be halted/error-free)
  -> overlay_download
```

The runtime then loads the candidate with `pynq.Overlay(candidate)`, which does
not construct `BaseOverlay`; the candidate HWH has no trace analyzers, so the
formal entry does not restart them. If another process constructs
`BaseOverlay` during the stability window, the re-arm check fails and the load
does not happen. This is a residual race only if another process starts after
the check and before the download; the runtime's preflight already requires no
other task/owner, and the window is the milliseconds between stop and load.

## 5. Tests

Full JPEG regression: **129 passed**

| Suite | Result |
|---|---|
| admission | 5 |
| display/HOLD | 13 |
| pre-load policy | **45** |
| transport integration | 3 |
| runtime/transport | 63 |

New fake tests in `test_jpeg_preload_policy.py`:

1. stop writes only `0x30` with `old & ~1` on the two confirmed channels;
2. already-halted channels are left alone (no write);
3. timeout (`Halted` never asserts) raises `DmaStopTimeout`, writes only the
   RS clear, no `0x34` write, no Reset bit;
4. error-after-stop raises and does not clear the error;
5. re-arm during the stability window raises;
6. an unexpected `axi_dma` set aborts before any write;
7. the sequence stops before the four-channel recheck and `Overlay.download`;
8. without the flag, active trace DMA still stops with `PreloadUnsafe` and no
   write.

The packaged copy passes the same 45 tests.

## 6. Package and pending command

| Item | SHA-256 |
|---|---|
| BIT (unchanged) | `75577d743060a0908f5248e5a12062428bc1d6ad603c81fc5fdaedc9b15b230b` |
| HWH (unchanged) | `06cf10cafc545d930fa210cd8fdd88d09981d808cb1c4197146183f822901380` |
| Manifest | `b15215b4ba65cdcf90fe1a4aebccbef2268a38bdf811f7fede5b2e2c3c546407` |
| SHA256SUMS | `3ca1049c0064512c9534cd65490d196c38d35c414821b0d50daa9731dcb8a08f` |
| README | `0c584199a6b33f7f205c41cefca4641856a88504de20509d3615fd3abeaaca8a` |
| `run_jpeg_counter.py` | `e148ea6ad1dcbaa7834bf15a016e8ad3360ea7235979ed55c010bb869a110c4c` |
| pre-load test | `a8ffcdb7a3e9a2a49f26d2a969a2d31278b16c9c5588f26d7e558e140d425f63` |

`sha256sum -c SHA256SUMS` = 345/345 OK; package `verify_runtime_artifacts` =
PASS.

Pending (only after review and separate authorization; use a fresh directory,
not `jpeg-candidate-20261103c`):

```bash
scp -r C:/w/jj2026p/board-package xilinx@192.168.2.99:/home/xilinx/jpeg-candidate-20261103d
cd /home/xilinx/jpeg-candidate-20261103d
sha256sum -c SHA256SUMS
test "$(sha256sum SHA256SUMS | cut -d' ' -f1)" = "3ca1049c0064512c9534cd65490d196c38d35c414821b0d50daa9731dcb8a08f"
test "$(sha256sum artifact-manifest.json | cut -d' ' -f1)" = "b15215b4ba65cdcf90fe1a4aebccbef2268a38bdf811f7fede5b2e2c3c546407"
BOOT_ID="$(cat /proc/sys/kernel/random/boot_id)"
mkdir -p run-20261103d
sudo -n env XILINX_XRT=/usr nohup setsid \
  /usr/local/share/pynq-venv/bin/python3 software/scripts/experiments/run_jpeg_counter.py \
  --execute \
  --bit jpeg_candidate.bit \
  --bit-sha256 75577d743060a0908f5248e5a12062428bc1d6ad603c81fc5fdaedc9b15b230b \
  --hwh jpeg_candidate.hwh \
  --hwh-sha256 06cf10cafc545d930fa210cd8fdd88d09981d808cb1c4197146183f822901380 \
  --build-status ROUTED_NOT_BOARD_VERIFIED \
  --boot-id "$BOOT_ID" \
  --frames 60 \
  --stop-base-trace-dmas \
  --output run-20261103d/jpeg-smoke-003.json
```

Expected before the load: both trace channels read `0x00011003`/`0`, are
written once with `0x00011002`, reach `RS=0, Halted=1`, and recheck clean;
stage order includes `known_base_dma_stop` before `overlay_download`.

## 7. Correction and limitations

- Corrected report wording: `0x00010001` has **bit 12 = 0** (FrmCnt_Irq not
  set) and **bit 16 = 1** (IRQFrameCntSts); the earlier “FrmCnt_Irq=1” was
  wrong. Fixed in `c_candidate_smoke003_active_trace_dma_blocked_20261103.md`.
- No board register was written this round; no stop was executed; no candidate
  was loaded; no smoke ran.
- `count_accuracy` remains `NOT_MEASURED`; no physical HDMI or accuracy claim.
- The historical caller that constructed `BaseOverlay` and left the DMA started
  is not established; the verified mechanism is the PYNQ
  `BaseOverlay`/`TraceAnalyzer`/`DMA` initialization.