# C candidate — smoke-002: FAIL_HOLD after candidate load, frames 0

Date: 2026-11-03
Board: PYNQ-Z2 at `192.168.2.99` (`xilinx`), boot ID
`b8034c58-663f-4f86-b68c-37e4bf7cdca2`.
Package: `/home/xilinx/jpeg-candidate-20261103b`, SHA256SUMS
`112c0edc...`, manifest `22071968...`, BIT `75577d74...`, HWH `06cf10ca...`.
Result: **FAIL_HOLD — candidate temporarily loaded, display started, JPEG/RLE
frames 0, no camera open, no candidate transfer.** The HOLD process is still
alive; no retry, reset, reload, reboot, or kill was performed.

## 1. Preconditions and launch

- Package `sha256sum -c SHA256SUMS` PASS; BIT/HWH/manifest/SHA256SUMS matched
  the reviewed values.
- Pre-load classification: **COLD_BASE** (PYNQ cold, boot `base.bin`, FPGA
  manager `operating`/`flags=0`, no FPGA/camera owner).
- Camera raw MJPEG: 3 consecutive frames, 640x400, 8-bit, baseline, 4:2:2,
  DRI=40, single scan, header 597 bytes.
- Smoke launched once with the formal entry; real Python PID **14240**
  (wrapper PID 14234), stdout/stderr to
  `run-20261103b/jpeg-smoke-002.console.log`, stdin `/dev/null`, new output
  directory.

Stages recorded before the failure:

```text
JPEG_STAGE preflight
JPEG_STAGE overlay_download
JPEG_STAGE overlay_loaded
JPEG_STAGE post_load_identity
JPEG_STAGE candidate_dma
JPEG_STAGE display_prepare
JPEG_STAGE display_prepared
JPEG_STAGE display_start
JPEG_STAGE display_started
```

`dmesg` confirms `fpga_manager fpga0: writing jpeg_candidate.bin` and the
candidate zocl/xclbin load (`70f4910d...`). No external abort occurred.

## 2. Failure

Immediately after `display_started`, transport creation raised:

```text
NameError: name 'overlay' is not defined
```

`frames=0`, `total_count=0`, `count_accuracy=NOT_MEASURED`. No camera source
was opened and no candidate JPEG/RLE transfer started.

## 3. Root cause

The pre-load refactor moved the `Overlay` object inside
`run_candidate_sequence`. The `make_transport` callback in `main` still
referenced a main-scope `overlay` variable that no longer exists:

```python
make_transport=lambda tx, rx: JpegRleDmaTransport(
    overlay.axi_dma_0.sendchannel, ...)
```

The candidate load, post-load identity, candidate DMA check, and display
start all completed; the `NameError` occurred when the sequence called
`make_transport`, after TX/RX allocation. The existing fake tests used
`make_transport=lambda tx, rx: ...` and therefore did not exercise the real
callback signature/closure.

This is a software-only integration bug. The candidate BIT/HWH, RTL, and
constraints are not implicated.

## 4. HOLD state

The runtime's HOLD path worked as designed:

- `status: FAIL_HOLD`, `buffers_held: true`
- `display.started: true`, `display.closed: false`,
  `display.vdma_uncertain: true`
- process PID 14240 alive, sleeping in `do_select`
- open FDs include `/dev/mem`, `/dev/uio1`, `/dev/dri/renderD128`; the display
  VDMA and TX/RX allocations are retained
- no camera FD is open (frame loop was never reached)
- no kill, Ctrl+C, reset, reload, or reboot was issued

Recovery requires an operator-authorized reboot. The HOLD process must remain
alive until that recovery; do not start another smoke on this boot.

## 5. Evidence

Directory: `data/results/jj2026p-board-smoke-002-20261103/`

| File | SHA-256 |
|---|---|
| `jpeg-smoke-002.json` | `50efbb31...` |
| `jpeg-smoke-002.json.hold.json` | `b83190c7...` |
| `jpeg-smoke-002.json.stage.json` | `3d0a2c93...` |
| `jpeg-smoke-002.console.log` | `8d454fcb...` |
| `jpeg-smoke-002.python.pid` | `d8800127...` |
| `dmesg-tail.txt` | `00774199...` |
| `journal-tail.txt` | `423fc0ed...` |
| `state.txt` | `a8b46e66...` |
| `precheck/precheck.json` | `7941632d...` |

The evidence records the exact package SHAs; BIT/HWH remain
`75577d74...` / `06cf10ca...`.

## 6. Recommended offline fix (not applied)

- Change `run_candidate_sequence` to call
  `make_transport(tx, rx, overlay)` (or pass a transport factory that closes
  over the sequence's Overlay).
- Update the main lambda to accept the overlay argument; update the fake tests
  to use the same three-argument callback.
- Add a regression test that exercises the real callback signature after the
  display start, so this closure error cannot recur.
- Repackage with a new SHA and request separate board authorization for a
  fresh smoke. Do not retry on this HOLD; reboot is separately authorized.

## 7. Limitations

- `count_accuracy` remains `NOT_MEASURED`; no physical HDMI or real counting
  accuracy claim is made.
- The candidate BIT was loaded for this authorized smoke, but no candidate
  transfer/frame ran.
- No RTL/BIT/HWH/constraint change; no deployment, push, or merge.
- The board remains in HOLD with the candidate BIT loaded; the boot base.bin
  is no longer the active bitstream. Operator-authorized reboot is required.

## 8. Follow-up (2026-11-03, offline only)

The transport factory scope bug is fixed in the updated package:

- package manifest `9a50d9914cdc5b07257376a0cbbb2fa59120598756810abbf3a9a8c47dc712f9`
- package SHA256SUMS `1a6bf853a9e969813f69e3cb2a3edf6360cf089d5278565f02558586df4896dd`
- `run_jpeg_counter.py` `9859fcbf...`
- `test_jpeg_transport_integration.py` `5042cbed...`

The production callback is now `make_transport(tx, rx, overlay, preview)`;
`run_candidate_sequence` creates the preview after display start, passes the
same Overlay/preview objects to the factory, and returns the preview to the
frame loop. `assemble_candidate_callbacks` is the exact main callback assembly.
95 JPEG tests pass, including 3 integration tests for the normal factory path
and the factory-failure HOLD path (no camera, no transfer, no close/free/reset/
reload).

Smoke-002 status remains unchanged: candidate temporarily loaded, display
started, JPEG/RLE frames 0; the board HOLD process was not touched. No board
retry/reset/reload/reboot was performed. smoke-003 requires a separately
authorized reboot and a fresh board directory; see
`c_candidate_transport_factory_fix_20261103.md`.