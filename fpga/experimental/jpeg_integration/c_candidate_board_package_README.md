# JPEG candidate board validation package (jj2026p)

Status: **ROUTED_NOT_BOARD_VERIFIED**. This is an offline package for review;
no board access, deployment, push, or merge was performed while preparing it.

## 1. Identity and binding

| Item | File | SHA-256 |
|---|---|---|
| BIT | `jpeg_candidate.bit` | `75577d743060a0908f5248e5a12062428bc1d6ad603c81fc5fdaedc9b15b230b` |
| HWH | `jpeg_candidate.hwh` | `06cf10cafc545d930fa210cd8fdd88d09981d808cb1c4197146183f822901380` |
| Routed DCP | `C:/w/jj2026p/joint-routed.dcp` | `c45429ef2f38c774480d6a30d22905795b800afd6ab091e18cde1b622c5caf21` |
| Route receipt | `C:/w/jj2026p/joint-route.tcldict` | `3443612f2b3d5942cc66390fd7bbf5455e6bb1796cae0622463e47ebd40b9b82` |
| Synth receipt | `C:/w/jj2026p/joint-synth.tcldict` | `f2ddc23b0c5c56e82610d33ecd0b818ebc724016c8d921baddd5590f127ebb4e` |
| Candidate manifest | `C:/w/jj2026p/joint_manifest.json` | `1bc1ffc9af42ece2e114cdb1f2e0786e8c1220456cff4be3bb70784863cf73b8` |
| Candidate XDC | `candidate_offload_cdc.xdc` in the staged snapshot | `a1a75ca572f7fd7154c7539fe7726173dd65164b3aff2f66987243cfe0f69b57` |

The BIT was written from the reviewed routed DCP (`c45429ef...`). The HWH was
taken from the same candidate project's `write_hwdef` archive / BD handoff;
the archive's `display.hwh` is byte-identical to the project's
`hw_handoff/display.hwh`. No old HWH is used.

Software versions: Python 3.12.10, Vivado 2026.1. The runtime entry and key
module SHAs are recorded in `artifact-manifest.json`.

## 2. Offline package checks (no hardware)

From the package directory:

```bash
sha256sum -c SHA256SUMS
```

From `software/` (offline admission; no PYNQ/MMIO access):

```bash
python3 - <<'PY'
import hashlib
from pathlib import Path
from scripts.experiments.run_jpeg_counter import verify_runtime_artifacts
bit = Path('../jpeg_candidate.bit')
hwh = Path('../jpeg_candidate.hwh')
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
print(verify_runtime_artifacts(bit, sha(bit), hwh, sha(hwh),
      status='ROUTED_NOT_BOARD_VERIFIED'))
PY
```

Expected contract: `xc7z020clg400-1`; DMA `0x40400000`, RLE `0x43C20000`,
overlay `0x43C50000`, JPEG `0x43C80000`; JPEG HLS control `s_axi_control`,
registers `CTRL=0x00`, `jpeg_size=0x10`, `frame_id=0x18`, `status=0x20`,
`status_ctrl=0x24`; FRG1 signature `0x46524731`.

The offline checks already passed for this package: `verify_runtime_artifacts`
= PASS, `scripts/experiments/test_run_jpeg_counter.py` = 5 passed,
`scripts/rle/test_jpeg_runtime.py` + `test_jpeg_transport.py` = 63 passed,
`joint_manifest.json` = `JOINT_SOURCE_HASHES_OK`.

## 3. Board prerequisites (separately authorized run only)

Copy the whole package to the board, e.g. `/home/xilinx/jpeg-candidate`, then:

```bash
cd /home/xilinx/jpeg-candidate
BOOT_ID="$(cat /proc/sys/kernel/random/boot_id)"
sha256sum -c SHA256SUMS
```

Requirements enforced by the runtime:

- root, `XILINX_XRT=/usr`, PYNQ venv;
- no other process owns `/dev/video0` (the runtime checks this and refuses a PL
  reload if an owner exists);
- PYNQ global state is empty before the overlay download; if it is not cold,
  reboot rather than forcing a load;
- the output path must not exist (one shot);
- use only the BIT/HWH in this package and only the SHAs above.

## 4. First board smoke command

This is the exact command form for the first, separately approved smoke run.
It is not executed by this package preparation.

```bash
sudo -n env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 \
  software/scripts/experiments/run_jpeg_counter.py \
  --execute \
  --bit jpeg_candidate.bit \
  --bit-sha256 75577d743060a0908f5248e5a12062428bc1d6ad603c81fc5fdaedc9b15b230b \
  --hwh jpeg_candidate.hwh \
  --hwh-sha256 06cf10cafc545d930fa210cd8fdd88d09981d808cb1c4197146183f822901380 \
  --build-status ROUTED_NOT_BOARD_VERIFIED \
  --boot-id "$BOOT_ID" \
  --frames 60 \
  --output runs/jpeg-smoke-001.json
```

The runtime is one-shot: if `runs/jpeg-smoke-001.json` exists, choose a new
output name only after the previous run has been reviewed; do not overwrite.

Before the first frame, the runtime sets FCLK0/1/2 to 50/100/142.857 MHz,
configures `hdmi_out` as `VideoMode(1280,720,8,60)` grayscale, starts the
HDMI/VTC/VDMA chain, and waits for the frame monitor's VSYNC scan counter to
advance. `publish_overlay` requires that VSYNC boundary. If display bring-up or
the VSYNC readback fails, no JPEG/RLE buffer is allocated and no transfer or
publish is attempted. On normal completion the runtime closes the display,
proves the VDMA halted, and only then frees the JPEG/RLE buffers.

Pre-load state policy (enforced before `Overlay.download`):

1. **COLD_BASE** — PYNQ `COLD_BOOT_NO_GLOBAL_STATE`, FPGA manager `operating`
   with `flags=0`, last boot bitstream `base.bin`, and no FPGA owner. No
   candidate DMA/overlay MMIO is issued before `Overlay.download`.
2. **KNOWN_OLD** — PYNQ loaded BIT SHA is in the known candidate/RLE table.
   Only the confirmed control/DMA addresses for that overlay are read; the
   ownership/active check is retained.
3. **UNKNOWN** — stop before `Overlay.download`; never probe unknown MMIO to
   identify a design.

After a successful candidate load, the runtime verifies the loaded BIT
identity/HWH address contract, checks the candidate DMA idle/error status, and
then performs display bring-up/VSYNC before allocating TX/RX and creating the
transport.

The production transport factory now receives the sequence-owned objects
explicitly:

```text
make_transport(tx, rx, overlay, preview)
```

The same `overlay` and `preview` objects are passed to the factory and returned
to the frame loop; no main-scope `overlay`/`preview` closure is used. A factory
failure after display start enters `FAIL_HOLD` without opening the camera,
starting a transfer, or closing/freeing/resetting/reloading the display or
buffers.

Before every hardware step the runtime writes and fsyncs `output.stage.json`
and prints `JPEG_STAGE <name>` with flush. If an uncatchable fault terminates
the process, collect that stage file and the console log; do not retry or
reload.

Smoke-002 status (2026-11-03): the candidate was temporarily loaded and the
display started, but transport creation raised `NameError: name 'overlay' is
not defined`; JPEG/RLE frames = 0. The board is still in the HOLD state and
must not be reused for another run. This package fixes that factory scope bug;
smoke-003 requires an operator-authorized reboot and a fresh board directory.

## 5. Acceptance items

### 5.1 Smoke acceptance (first run)

- Package integrity and offline admission PASS with the exact SHAs above.
- Pre-load classification is COLD_BASE (or a verified KNOWN_OLD); the log shows
  no candidate MMIO before `overlay_download`.
- Display bring-up succeeded: FCLK readback, HDMI/VTC/VDMA start, and a frame
  monitor scan advance proving VSYNC before the first frame. If bring-up fails,
  no frame transfer or overlay publish is attempted.
- `output.stage.json` exists and its last stage is `frames_done` (or the first
  failing stage on a controlled failure).
- The production transport factory received the sequence-owned Overlay and
  preview; a factory failure would enter HOLD without camera/transfer.
- Report status: `JPEG_PL_RUN_COMPLETED_NOT_BOARD_VALIDATED`.
- `frames` equals the requested 60; no `FAIL_HOLD`; no DMA active/faulty
  pre-load error; no camera-owner error.
- Overlay loaded; PYNQ loaded-BIT path and SHA match this package.
- Each frame: FRG1 signature reads `0x46524731`; FRG frame ID matches the
  requested frame; FRG status is complete; record count is 0..512; JPEG status
  valid; RLE packet decodes; `displayed_frame_id` advances.
- `total_count` is monotonic; per-frame `timing_ms` and `throughput_fps` are
  recorded.
- HDMI evidence is the PL `displayed_frame_id` register only; no physical
  monitor observation is claimed.

### 5.2 Accuracy acceptance (separate, known sample)

The smoke run reports `count_accuracy: NOT_MEASURED`. A separate accuracy
acceptance must use a known sample (for example the reviewed 10-nut sample),
record the operator denominator and per-frame `total_count`, and require the
agreed exact count for the first static sample. Do not treat the smoke status
as an accuracy PASS.

### 5.3 Telemetry / non-gates

- `throughput_fps`, frame interval, JPEG size, and per-frame detection count are
  recorded telemetry; the first smoke run does not gate on a specific FPS.
- No new timing waiver, `false_path`, or clock relaxation is part of this
  package.

## 6. Rollback and failure handling

1. **`FAIL_HOLD`**: the runtime retains DMA-owned CMA buffers/display and loops.
   Keep the HOLD process alive. Do not Ctrl+C/kill it, retry, reset DMA, reload
   the overlay, or free buffers. Collect `runs/jpeg-smoke-001.json` and the live
   `runs/jpeg-smoke-001.json.hold.json` through an independent connection, then
   recover by rebooting the board with operator authorization. SIGINT and
   SIGTERM are consumed during HOLD; SIGKILL cannot be intercepted, so no Python
   cleanup or buffer-release guarantee exists if the process is killed with
   SIGKILL — reboot is still required.
2. **Overlay/PYNQ failure before or during download**: do not substitute another
   BIT or HWH. Preserve the report; reboot if PYNQ global state is uncertain.
3. **Ordinary frame/JPEG/FRG failure**: the runtime closes the transport and
   frees buffers only when DMA is not held. Preserve the report; do not
   auto-retry; review the first failing frame before another authorized run.
4. **Artifact/admission failure**: stop before loading; correct the package; do
   not bypass the SHA or part checks.
5. **Uncatchable MMIO fault** (external abort): collect `output.stage.json` and
   the console log, preserve the board state, and recover by an operator-
   authorized reboot if needed. Do not retry, reset, reload, or substitute a
   BIT/HWH.
6. No bitstream deployment, board script push, or merge is part of this
   package.

## 7. Files

- `jpeg_candidate.bit`, `jpeg_candidate.hwh` — reviewed export pair.
- `artifact-manifest.json` — SHA binding, contract, software versions.
- `software/scripts/...` — runtime sources copied from the reviewed worktree,
  including `scripts/standalone/display_bringup.py`.
- `SHA256SUMS` — integrity list for the package (excluding itself).
- `README.md` — this document.