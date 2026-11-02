# C candidate — first bounded board smoke: blocked before candidate load

Date: 2026-11-03
Board: PYNQ-Z2 at `192.168.2.99` (`xilinx`), boot ID
`b8034c58-663f-4f86-b68c-37e4bf7cdca2`, up 28–33 min.
Package: `/home/xilinx/jpeg-candidate-20261103` (copied from
`C:/w/jj2026p/board-package`, package SHA-256 `6461fa9d...`).
Result: **BLOCKED — no 60-frame smoke result; candidate BIT was not loaded.**
No retry, no reload, no reboot, no other task stopped.

## 1. Preconditions that passed

- Board identity: PynqLinux 3.0 (Ubuntu 22.04), Python 3.10.4, PYNQ venv,
  numpy 1.21.5, cv2 4.5.4; `sudo -n` works.
- Camera: HSK USB Camera serial `20210616103`, `/dev/video0`; MJPG 640x400
  at 60 fps supported; no camera owner in `/proc/*/fd`.
- PYNQ global state file absent:
  `COLD_BOOT_NO_GLOBAL_STATE`, `shutdown_ips = {}`.
- FPGA manager: `operating`; the boot PL is `base.bin` (loaded at dmesg
  t=150 s), not the candidate.
- CMA: 131,072 KiB total / 83,132 KiB free.
- Package integrity on the board:
  `sha256sum -c SHA256SUMS` PASS; BIT `75577d74...`, HWH `06cf10ca...`,
  manifest `b1d7ba32...`, SHA256SUMS `6461fa9d...`.
- Camera raw MJPEG contract (before any candidate load): 3 consecutive frames,
  all 640x400, 8-bit, baseline SOF0, 4:2:2, DRI=40, single scan,
  header 597 bytes (<4096). Evidence in
  `data/results/jj2026p-board-smoke-20261103/precheck/`.

## 2. Smoke launch and failure

The package's formal entry was launched once, detached, with a new output
directory:

```bash
cd /home/xilinx/jpeg-candidate-20261103
sudo -n env XILINX_XRT=/usr nohup setsid \
  /usr/local/share/pynq-venv/bin/python3 software/scripts/experiments/run_jpeg_counter.py \
  --execute \
  --bit jpeg_candidate.bit --bit-sha256 75577d74... \
  --hwh jpeg_candidate.hwh --hwh-sha256 06cf10ca... \
  --build-status ROUTED_NOT_BOARD_VERIFIED \
  --boot-id b8034c58-663f-4f86-b68c-37e4bf7cdca2 \
  --frames 60 --output run-20261103/jpeg-smoke-001.json
```

The process (`pid 7168`) exited before writing any report. The console log is
**0 bytes** and the output report/HOLD file do not exist. `dmesg` records:

```text
[1935.527896] 8<--- cut here ---
[1935.529670] Unhandled fault: external abort on non-linefetch (0x018) at 0xb6f30004
[1935.535954] [b6f30004] *pgd=02fdf831, *pte=40400703, *ppte=40400e33
```

The fault address corresponds to the pre-load DMA register probe at
`0x40400004`. The process died in the kernel-level external abort before
`Overlay(download=True)`, before display/JPEG/RLE buffer allocation, and before
the runtime's report/HOLD path. The candidate BIT was **not** loaded; the boot
`base.bin` remains in the FPGA manager. No camera owner remains, no candidate
DMA/VDMA buffer is held, and no reboot was performed.

## 3. Root cause

`run_jpeg_counter.py` performs this sequence before loading the candidate:

1. `inspect_pynq_global_state(...)` → correctly reports
   `COLD_BOOT_NO_GLOBAL_STATE` and empty `shutdown_ips`;
2. `MMIO(DMA_BASE, 0x10000)` and reads `0x04`/`0x34` as a pre-load DMA-owner
   check;
3. only then `Overlay(candidate.bit, download=True)`.

On this board the PL is running the boot `base.bin`, where
`0x40400000` is not the candidate's AXI DMA. The PS AXI read returns an
external abort (SIGBUS-like, `external abort on non-linefetch`), which is not
catchable as a Python exception. The process therefore dies before the runtime
can write a report or enter `FAIL_HOLD`. The pre-load DMA probe is only
meaningful when a known prior overlay (candidate/RLE) is loaded; on a cold
state with `base.bin`, it must not touch `0x40400000` before the candidate
load.

This is a **runtime precondition bug**, not a BIT/HWH/RTL/constraint or route
failure. The reviewed candidate artifacts were not modified and the candidate
BIT was never downloaded.

## 4. Evidence

| File | SHA-256 |
|---|---|
| `console.log` (0 bytes) | `e3b0c442...` (empty) |
| `dmesg-tail.txt` | `8519304f...` |
| `journal-tail.txt` | `035951fd...` |
| `state.txt` | `dab82b7d...` |
| `precheck/camera-precheck.json` | `bb7e012b...` |
| `precheck/camera-frame-0.jpg` | `0c5a8497...` |
| `precheck/camera-frame-1.jpg` | `f824cb5c...` |
| `precheck/camera-frame-2.jpg` | `4f48c24c...` |

Board state at evidence collection: boot ID unchanged, FPGA manager
`operating` (base.bin), PYNQ global state absent, no `run_jpeg_counter.py`
process, no camera owner, package hashes unchanged.

## 5. Impact and limitations

- No 60-frame smoke result; `count_accuracy` remains `NOT_MEASURED`.
- No physical HDMI picture or real counting accuracy is claimed.
- The candidate BIT/HWH/RTL/constraints/route artifacts are unchanged.
- The board remains on the boot `base.bin`; no candidate load, no HOLD, no
  reboot, no other task stopped.
- The runtime's normal/FAIL_HOLD reporting did not run because the external
  abort is uncatchable at the MMIO instruction. This is not a case where the
  process could retain buffers; it died before allocating any candidate
  resource.

## 6. Recommended next fix (not applied)

Before another authorized board attempt, fix the cold-start probe in
`run_jpeg_counter.py`:

- if `pynq_state_before["pl_state_status"] == "COLD_BOOT_NO_GLOBAL_STATE"`
  (or the loaded BIT is not a known candidate/RLE overlay), skip the pre-load
  `MMIO(DMA_BASE)` probe; load the candidate with `Overlay(download=True)`
  first, then use the post-load `inspect_pynq_global_state` and DMA/display
  checks;
- keep the pre-load probe only for a known prior overlay where the DMA address
  is actually mapped;
- add a fake/unit test for the cold-state path (no pre-load MMIO) and retain
  the existing known-overlay path.

This change is software-only. A new package SHA and a separately authorized
60-frame attempt would be required; no automatic retry or bypass is proposed
here.