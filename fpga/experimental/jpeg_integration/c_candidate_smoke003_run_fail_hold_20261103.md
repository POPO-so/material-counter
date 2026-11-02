# C candidate — smoke-003 run: bounded stop passed, FAIL_HOLD at first frame

Date: 2026-11-03
Board: PYNQ-Z2 at `192.168.2.99`, boot ID
`6b0d11ee-d9cb-4513-afc8-fee3296448df`.
Package: `/home/xilinx/jpeg-candidate-20261103d`, SHA256SUMS
`3ca1049c...`, manifest `b15215b4...`, BIT `75577d74...`, HWH `06cf10ca...`.
Result: **bounded stop PASS; four-channel recheck PASS; candidate loaded;
display started; FAIL_HOLD at the first frame with 0 frames, 0 count.**
The HOLD process is still alive and its buffers/display are retained. No
retry, reset, reload, kill, or reboot was issued. `count_accuracy` remains
`NOT_MEASURED`.

## 1. Prechecks and bounded stop

Base identity and map (read-only, before stop):

- base.bit SHA-256 `3239e9cc...`, SHA-1 `39b937b4...`;
- base.hwh SHA-256 `222898ef...`, device `xc7z020clg400-1`;
- three masters: video VDMA `0x43000000` (MM2S+S2MM), trace pi
  `0x80430000` (S2MM), trace pmodb `0x80420000` (S2MM);
- boot ID unchanged; no other material/FPGA task; no camera owner;
- camera: 3 frames, 640x400, 8-bit, baseline, 4:2:2, DRI=40, header 597 B;
- CMA before stop: 39,736 KiB free;
- three-state: KNOWN_BASE.

Before-stop trace registers (both channels identical):

| Item | Value |
|---|---|
| control 0x30 | `0x00011003` (RS=1, Reserved bit1=1, IOC_IrqEn=1, IRQThreshold=1, Reset=0) |
| status 0x34 | `0x00000000` (Halted=0, Idle=0, no error/reserved) |
| S2MM_DA / DA_MSB | `0x00000000` / `0x00000000` |
| S2MM_LENGTH | `0x00000000` (no transfer armed at sampling) |

Actual writes (only these two, both at offset `0x30`):

```text
0x80430000 + 0x30 = 0x00011002
0x80420000 + 0x30 = 0x00011002
```

No DMASR write, no Reset, no buffer free, no other channel.

After-stop and 0.2 s stability recheck:

| Channel | control | status | Verdict |
|---|---|---|---|
| video VDMA MM2S | `0x00010082` | `0x00010001` | safe |
| video VDMA S2MM | `0x00010002` | `0x00010001` | safe |
| trace pi S2MM | `0x00011002` | `0x00000001` | RS=0, Halted=1, no error/reserved |
| trace pmodb S2MM | `0x00011002` | `0x00000001` | RS=0, Halted=1, no error/reserved |

All four channels passed; DA/LENGTH remained 0.

## 2. Formal entry

Stages recorded (console and stage file):

```text
preflight
known_base_probe
known_base_dma_stop
known_base_dma_stop_done
known_base_probe_done
overlay_download
overlay_loaded
post_load_identity
candidate_dma
display_prepare
display_prepared
display_start
display_started
overlay_mmio
overlay_mmio_ready
transport_ready
frames_start
```

The formal entry's stop step was idempotent: both trace channels were already
`RS=0/Halted=1`, so `wrote_rs_clear=false` and no second write occurred. The
loaded candidate BIT identity matched `75577d74...`; display clocks read
50/100/142.857 MHz; the display start call returned (VSYNC snapshot `unique=0,
scans=1, invalid=1, sequence=79`). This is a completed start call, **not** a
claim that a valid physical picture was verified. The production transport
factory completed (`transport_ready`).

## 3. Failure

On the first frame, `process_jpeg_frame` → `JpegRleDmaTransport._preflight`
raised:

```text
RuntimeError: MM2S DMA is not idle
```

`frames=0`, `total_count=0`. The camera was opened for the first frame
(`next(capture_source)`), but no JPEG/RLE transfer was accepted.

Root cause: PYNQ's DMA driver starts both channels when
`overlay.axi_dma_0.sendchannel/recvchannel` are first accessed
(`_SDMAChannel.__init__` writes `S2MM_DMACR=0x1001` / `0x0001`). In AXI DMA
v7.1 direct mode, a started channel with no transfer yet reads
`Halted=0, Idle=0` (`status=0x0`). `_preflight` requires
`status & (DMA_IDLE | DMA_HALTED)` to be non-zero, so it rejects the valid
pre-first-transfer state. `candidate_dma_after_load` had read `0x1` (halted)
before the transport was created; the PYNQ driver then changed it to
`0x10003/0x0` for both channels.

Read-only state during HOLD:

| Register | Value |
|---|---|
| MM2S DMACR 0x00 | `0x00010003` |
| MM2S DMASR 0x04 | `0x00000000` |
| S2MM DMACR 0x30 | `0x00010003` |
| S2MM DMASR 0x34 | `0x00000000` |

No error bits; both channels are running (RS=1, Halted=0), consistent with
the PYNQ driver start. MM2S and S2MM control registers are distinct: MM2S
uses control `0x00`, status `0x04`, address `0x18`, length `0x28`; S2MM uses
control `0x30`, status `0x34`, address `0x48`, length `0x58`. The error
message named MM2S because it is checked first; both channels were in the
same started/unarmed state.

This is a transport preflight issue, not the trace-DMA stop plan. No code was
changed and the HOLD process was not touched.

## 4. HOLD state

- PID 17659 (`python3`), parent 17653 (`sudo`), state `S`, 1 thread.
- Open FDs include `/dev/mem` (multiple), `/dev/uio1`,
  `/dev/dri/renderD128`.
- `display.started=true`, `closed=false`, `vdma_uncertain=true`;
  `buffers_held=true`.
- `FAIL_HOLD` report and live `.hold.json` written; no close/free/reset/
  reload/retry; no reboot.
- CMA: 35,808 KiB before the run; 7,804 KiB after the failure (display and
  DMA buffers retained). This is a retained-resource snapshot, not a leak
  inference.

A first shell launch attempt failed before Python started because the
pre-created run directory was root-owned and the xilinx shell could not open
the console log; the directory ownership was corrected and exactly one actual
Python run was then started. No outputs from the failed shell attempt existed.

## 5. Evidence

Directory: `data/results/jj2026p-board-smoke-003-20261103d/`

| File | SHA-256 |
|---|---|
| `stop/stop.json` | `d2fdf4a552a2b56de839a72ae3696bb03ec1d4d09b624d07671aa62f70790f8b` |
| `jpeg-smoke-003.json` | `c12780d0bdd29f77ecb2115b5af1bbc3bfab3715f60891387d5809415ddc8e6a` |
| `jpeg-smoke-003.json.hold.json` | `2bfb9d81e610d569fa6d2db1df047ed80f1e11fbe5995e1b0eaa39141b70e7ed` |
| `jpeg-smoke-003.json.stage.json` | `c6c7abf248feab6787cfef055c02747349eaf1b511567c1a874cd6ecf6d1cff5` |
| `jpeg-smoke-003.console.log` | `54e82c94c646bac7e74229159e597580cc0bdc1f990768384dd3e06206dc2d38` |
| `jpeg-smoke-003.python.pid` | `5481ba7822e404eb2e80e5453b56730be87742ba58c41f6f063e1a2befe13447` |
| `hold-state.txt` | `7efe3629a117c7c477b78281a2073d18638e4b36662e4fceefc98279abc5b21e` |
| `hold-candidate-dma.txt` | `da281cfc3c18e81a7c5397b3a65b9514042f87d3788f099e5edf712b09578178` |

Camera frames and stop camera copies are in `stop/camera-frame-*.jpg`.

## 6. Corrections

- The historical caller that constructed `BaseOverlay` and left the trace
  channels running is **not established**; only the PYNQ
  `BaseOverlay`/`TraceAnalyzer`/`DMA` initialization mechanism is verified.
  The candidate runtime and boot service are ruled out.
- The trace registers show **no transfer armed at the time of sampling**
  (`DA=0`, `LENGTH=0`). This does not prove no earlier transfer ever occurred;
  the earlier wording is corrected in
  `c_candidate_smoke003_trace_dma_stop_plan_20261103.md`.
- `0x00010001` bit 12 is 0 and bit 16 is 1 (FrmCnt_Irq not set); the earlier
  report wording was corrected.

## 7. Status and next step

- The trace-DMA stop plan itself passed: exact two writes, RS cleared, Halted
  asserted, stability and four-channel recheck clean.
- The candidate load and display start passed; the run failed at the first
  frame in the transport preflight, so there is **no 60-frame report** and
  `count_accuracy` remains `NOT_MEASURED`.
- The HOLD process must remain alive; collect evidence through an independent
  connection. Do not Ctrl+C/kill/reset/reload/retry. Reboot requires separate
  authorization.
- The next step is an offline fix and review of the transport's AXI DMA
  preflight criterion for the valid started/no-transfer state, with fake
  tests, then a separately authorized reboot and rerun. No such code change
  was made in this round.
- No RTL/BIT/HWH/constraint or startup bitstream change; no push or merge.

## 8. Follow-up (offline only)

The first-frame preflight was fixed offline in
`c_candidate_smoke003_first_frame_fix_20261103.md`. That work did not access
the board, the HOLD process, or its buffers; the HOLD remains as described
here until an authorized reboot.