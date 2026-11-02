# C candidate — offline BIT/HWH export and board validation package (jj2026p)

Date: 2026-11-03
Worktree: `C:/fpga/wt/claude-jpeg-20261102`, branch `codex/claude-jpeg-20261102`
Stage root: `C:/w/jj2026p`
Package: `C:/w/jj2026p/board-package`
Status: **ROUTED_NOT_BOARD_VERIFIED**; offline export and package preparation only.
No board access, deployment, push, or merge was performed.

## 1. Export provenance

- BIT source: `C:/w/jj2026p/joint-routed.dcp`, sha256
  `c45429ef2f38c774480d6a30d22905795b800afd6ab091e18cde1b622c5caf21`.
- `write_bitstream` from that checkpoint completed with **0 errors, 0 warnings,
  0 critical warnings**.
- HWH source: the same candidate project. `write_hwdef` produced a ZIP whose
  `display.hwh` is byte-identical to
  `display.gen/sources_1/bd/display/hw_handoff/display.hwh`
  (`06cf10ca...`). That standard PYNQ HWH is used; no old HWH is mixed in.
- BIT and HWH share the stem `jpeg_candidate` and are adjacent in the package.

## 2. Artifact and input SHA binding

| Item | Path | SHA-256 |
|---|---|---|
| BIT | `board-package/jpeg_candidate.bit` | `75577d743060a0908f5248e5a12062428bc1d6ad603c81fc5fdaedc9b15b230b` |
| HWH | `board-package/jpeg_candidate.hwh` | `06cf10cafc545d930fa210cd8fdd88d09981d808cb1c4197146183f822901380` |
| Routed DCP | `C:/w/jj2026p/joint-routed.dcp` | `c45429ef...` |
| Route receipt | `C:/w/jj2026p/joint-route.tcldict` | `3443612f...` |
| Synth receipt | `C:/w/jj2026p/joint-synth.tcldict` | `f2ddc23b...` |
| Candidate manifest | `C:/w/jj2026p/joint_manifest.json` | `1bc1ffc9...` |
| Candidate mailbox XDC | `.../constraints/candidate_offload_cdc.xdc` | `a1a75ca5...` |
| Overlay RTL | `video_binary_overlay.v` | `811db981...` |
| Overlay BD RTL | `video_binary_overlay_bd.v` | `dfc48722...` |
| FRG/RLE tap RTL | `rle_feature_tap.v` | `493bc04f...` |
| Region RTL | `rle_region_features.v` | `19439a2c...` |
| Display timing XDC | `display_timing.xdc` | `4ace486d...` |
| Frozen offload XDC | `offload_cdc.xdc` | `3a8be351...` |
| Monitor XDC | `joint_monitor_cdc.xdc` | `31ed0a71...` |
| Package manifest | `board-package/artifact-manifest.json` | `b1d7ba32...` |
| Package sums | `board-package/SHA256SUMS` | `6461fa9d...` |

Software: Python 3.12.10; Vivado 2026.1. The package records the exact runtime
script SHAs in `artifact-manifest.json`; the runtime entry
`software/scripts/experiments/run_jpeg_counter.py` is `50617018...` and the
display bring-up module `software/scripts/standalone/display_bringup.py` is
`81c11447...`.

## 3. Offline IP/address/JPEG/FRG contract

Parsed from the exported HWH and the reviewed sources:

| Check | Result |
|---|---|
| Silicon part | `xc7z020clg400-1` (`DEVICE=7z020`, `PACKAGE=clg400`, `SPEEDGRADE=-1`) |
| DMA control base | `0x40400000` |
| RLE/image-pipeline base | `0x43C20000` |
| Overlay base | `0x43C50000` |
| JPEG control base | `0x43C80000` (range `0x10000`) |
| Control interconnect | `NUM_MI = 13`; M12 is the JPEG AXI-Lite port |
| FCLK0 | `PCW_ACT_FPGA0_PERIPHERAL_FREQMHZ = 50.000000` |
| JPEG reset | `reset_rle_peripheral_aresetn` |
| DMA | simple mode, MM2S+S2MM, no SG, no micro-DMA, length width 19, DRE 0, memory widths 64/32, stream widths 32/32 |
| RLE pipeline | `image_pipeline` MODTYPE `axis_dma_rle_pipeline_bd`, `WIDTH=640`, `HEIGHT=400`, `MAX_RUNS=16384` |
| Overlay | `binary_overlay` MODTYPE `video_binary_overlay_bd` |
| JPEG IP | `jpeg_axis_gray_0`, VLNV `xilinx.com:hls:jpeg_axis_gray:1.0`, control interface `s_axi_control` range 65536 |
| JPEG interfaces | `s_axi_control`, `jpeg_axis_in`, `gray_axis_out`; ports include `ap_clk`, `ap_rst_n` |
| JPEG registers | `CTRL=0x00`, `GIER=0x04`, `IP_IER=0x08`, `IP_ISR=0x0C`, `jpeg_size=0x10`, `frame_id=0x18`, `status=0x20`, `status_ctrl=0x24` |
| FRG1 | runtime `FRG_SIGNATURE=0x46524731`; RTL `video_binary_overlay_bd.v:116` returns `32'h46524731` |

The runtime constants (`scripts/rle/jpeg_transport.py`) match the HWH:
`JPEG_AP_CTRL=0x00`, `JPEG_SIZE=0x10`, `JPEG_FRAME_ID=0x18`, `JPEG_STATUS=0x20`,
`JPEG_STATUS_VALID=0x24`, `FRG_SIGNATURE=0x46524731`.

## 4. Artifact admission and tests

| Check | Result |
|---|---|
| `verify_runtime_artifacts` on the exported BIT/HWH | **PASS** (part, SHA, bases) |
| Same check from the packaged `software/` layout | **PASS** |
| `scripts/experiments/test_run_jpeg_counter.py` | 5 passed |
| `scripts/experiments/test_jpeg_display_and_hold.py` | 13 passed |
| `scripts/rle/test_jpeg_runtime.py` + `test_jpeg_transport.py` | 63 passed |
| `joint_prepare.py --verify-manifest C:/w/jj2026p/joint_manifest.json` | `JOINT_SOURCE_HASHES_OK` |
| Package `sha256sum -c SHA256SUMS` | PASS |

Total JPEG offline tests: **81 passed** (5 admission + 13 display/HOLD + 63
runtime/transport).

One software admission fix was required and tested: the HWH names the JPEG HLS
control base `C_S_AXI_CONTROL_BASEADDR` (not `C_BASEADDR`), so
`run_jpeg_counter._parse_hwh` now accepts `*_BASEADDR` parameters and
`MEMRANGE BASEVALUE`. This is a software-only fix; the reviewed BIT/HWH, RTL and
constraints were not changed.

## 4b. First-smoke software fixes (display bring-up and FAIL_HOLD)

### 4b.1 Display timing initialization

The runtime previously loaded the Overlay but never configured/started the
HDMI/VTC/VDMA chain; `publish_overlay` waits for a VSYNC-latched `cfg_ready`, so
the first cold-boot smoke could block or publish nothing. New
`scripts/standalone/display_bringup.py` reuses the relevant parts of
`g1_run.setup_hardware`/`snapshot` (clocks, `hdmi_out` + `axi_vdma`, frame
monitor mailbox) and deliberately excludes the G1/WO-0015 bitstream-transition
logic.

Order now enforced:

1. set/read back FCLK0/1/2 = 50/100/142.857 MHz;
2. configure `hdmi_out` as `VideoMode(1280,720,8,60)`, `PIXEL_GRAY`;
3. `hdmi.start()`;
4. wait for the frame monitor's `scans` counter to advance, proving VSYNC;
5. only then allocate TX/RX, create the JPEG/RLE transport, and enter the frame
   loop.

If display prepare/start/VSYNC fails, `bring_up_display_and_dma` raises before
any allocation/transport/publish; the handle is attached to the exception for
ownership decisions. On normal completion the runtime calls `hdmi.close()`,
proves the VDMA halted (status bit 0), and only then frees the JPEG/RLE buffers.
No unconditional reset, reload, or free is introduced.

### 4b.2 FAIL_HOLD lifecycle

`finalize_run` now retains both DMA owners when ownership is uncertain:

- `FAIL_HOLD` does not call `hdmi.close`, `transport.close`, `freebuffer`, DMA
  reset, or Overlay reload;
- the camera is not DMA-owned and may be closed;
- SIGINT/SIGTERM are consumed while the HOLD process is alive and recorded in
  `output.hold.json`; a second Ctrl+C/SIGTERM cannot unwind the process and run
  buffer destructors;
- the primary report and a live `.hold.json` can be collected over an
  independent connection; recovery is an operator-authorized reboot;
- SIGKILL cannot be intercepted; if used, no Python cleanup/buffer-release
  guarantee exists and reboot is still required.

Normal completion still stops the display, proves VDMA halt, closes the
transport, frees TX/RX, and writes the normal report. If display stop or
transport close fails, the run escalates to `FAIL_HOLD` rather than freeing.

Tests in `scripts/experiments/test_jpeg_display_and_hold.py` (13) use fakes for:
display start before allocation/transport; start/prepare failure blocks
transfer; VSYNC timeout; VDMA uncertain/halt readback; signal handler
consume/raise; HOLD never close/free/reset/reload; normal stop/free; stop/close
failure escalation; no-transport buffer cleanup.

## 5. Board validation package

`C:/w/jj2026p/board-package/`:

- `jpeg_candidate.bit` (4,045,770 B), `jpeg_candidate.hwh` (870,630 B)
- `artifact-manifest.json` (SHA binding, contract, software versions)
- `README.md` (commands, acceptance, rollback)
- `SHA256SUMS`
- `software/scripts/...` (runtime sources copied from the reviewed worktree,
  including `scripts/standalone/display_bringup.py`)

First-run command form (not executed here):

```bash
sudo -n env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 \
  software/scripts/experiments/run_jpeg_counter.py \
  --execute \
  --bit jpeg_candidate.bit \
  --bit-sha256 75577d74... \
  --hwh jpeg_candidate.hwh \
  --hwh-sha256 06cf10ca... \
  --build-status ROUTED_NOT_BOARD_VERIFIED \
  --boot-id "$BOOT_ID" \
  --frames 60 \
  --output runs/jpeg-smoke-001.json
```

Acceptance: package/admission PASS; status
`JPEG_PL_RUN_COMPLETED_NOT_BOARD_VALIDATED`; 60 frames; no `FAIL_HOLD`; FRG1
signature/frame ID/complete/record bounds; JPEG status/RLE/display-frame-ID
evidence; monotonic count; timing/throughput recorded. Smoke accuracy is
`NOT_MEASURED`; a separate known-sample accuracy run is required.

Rollback: on `FAIL_HOLD`, do not retry/reset/reload; preserve report/console
and reboot with operator approval; on overlay/PYNQ failure, do not substitute a
BIT/HWH; on ordinary failure, preserve the report and do not auto-retry.

## 6. Open items and restrictions

1. No board run was executed; the package is for review and separate board
   authorization.
2. Accuracy is not established by the smoke plan; a known-sample acceptance is
   still required.
3. Route capture adaptation into `joint_build.tcl` remains a follow-up; current
   reviewed artifacts were not changed for flow cleanup.
4. No bitstream deployment, board access, push, or merge was performed.
5. No timing waiver, `false_path`, or clock relaxation was added in export or
   package preparation.
6. Display bring-up and VSYNC readback are PYNQ/board-side; offline tests use
   fakes. Actual HDMI/VTC/VDMA startup and physical VSYNC remain unverified
   until the separately authorized board run.
7. SIGKILL cannot be intercepted; the HOLD signal guarantee covers SIGINT and
   SIGTERM only, and a SIGKILL still requires reboot.