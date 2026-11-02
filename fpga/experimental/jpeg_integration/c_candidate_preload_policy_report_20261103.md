# C candidate — pre-load MMIO policy and stage persistence (offline)

Date: 2026-11-03
Worktree: `C:/fpga/wt/claude-jpeg-20261102`
Scope: offline software fix, fake tests, and repackaging only. No board
access this round; no retry, reset, reload, or reboot; no change to reviewed
RTL, BIT/HWH, or constraints.

## 1. What changed

`scripts/experiments/run_jpeg_counter.py` now classifies the PL state before
loading the candidate and never probes an unknown address:

- **COLD_BASE**: PYNQ `COLD_BOOT_NO_GLOBAL_STATE` + FPGA manager
  `operating` with `flags=0` + the last boot bitstream from `dmesg` is
  `base.bin` + no FPGA owner. No candidate DMA/overlay MMIO is issued before
  `Overlay.download`.
- **KNOWN_OLD**: the PYNQ loaded BIT SHA matches a known table entry
  (`jpeg_candidate` `75577d74...` or pinned `rle_previous`
  `ca55911e...`); only that entry's confirmed control/DMA addresses are read,
  and the old-DMA active/faulty ownership check remains.
- **UNKNOWN**: stop before `Overlay.download`; never read unknown MMIO to
  identify a design.

After a successful load: post-load BIT identity and HWH address contract,
candidate DMA idle/error check, display bring-up/VSYNC, then TX/RX allocation
and transport creation. `DmaOwnershipUncertain` enters the existing
`FAIL_HOLD` path without close/free/reset/reload.

Stage persistence:

- `output.stage.json` is written/fsynced and `JPEG_STAGE <name>` is printed
  with flush before each hardware step (`preflight`, `known_old_probe`,
  `overlay_download`, `post_load_identity`, `candidate_dma`, `display_start`,
  frames, and finalize).
- If an uncatchable MMIO fault kills the process, the last stage file and
  console line remain as evidence.

`scripts/standalone/display_bringup.py` is unchanged from the previous round.

## 2. Test results

`python -m pytest -q scripts/experiments/test_jpeg_preload_policy.py
scripts/experiments/test_jpeg_display_and_hold.py
scripts/experiments/test_run_jpeg_counter.py
scripts/rle/test_jpeg_runtime.py scripts/rle/test_jpeg_transport.py`

**95 passed**:

| Suite | Result |
|---|---|
| admission (`test_run_jpeg_counter.py`) | 5 passed |
| display/HOLD (`test_jpeg_display_and_hold.py`) | 13 passed |
| pre-load policy (`test_jpeg_preload_policy.py`) | 11 passed |
| transport integration (`test_jpeg_transport_integration.py`) | 3 passed |
| runtime/transport | 63 passed |

The 11 pre-load tests prove:

1. cold base has no candidate MMIO before `Overlay.download`;
2. unknown state stops before download with no MMIO/overlay;
3. unknown loaded-BIT identity is refused;
4. known old overlay reads only its confirmed control/DMA addresses;
5. known old overlay with active/faulty DMA is refused before overlay;
6. overlay load failure skips post-load identity/DMA/display/allocation;
7. post-load identity failure skips DMA/display/allocation;
8. normal path still runs post-load DMA and display checks;
9. cold state without `base.bin` boot evidence is UNKNOWN;
10. an FPGA owner makes the state UNKNOWN;
11. `write_stage` persists the boot record/stage JSON.

## 3. Updated package SHAs

| Item | SHA-256 |
|---|---|
| BIT (unchanged) | `75577d743060a0908f5248e5a12062428bc1d6ad603c81fc5fdaedc9b15b230b` |
| HWH (unchanged) | `06cf10cafc545d930fa210cd8fdd88d09981d808cb1c4197146183f822901380` |
| Package manifest | `9a50d9914cdc5b07257376a0cbbb2fa59120598756810abbf3a9a8c47dc712f9` |
| Package SHA256SUMS | `1a6bf853a9e969813f69e3cb2a3edf6360cf089d5278565f02558586df4896dd` |
| Package README | `1b9dc13dfa4e99c41000a09c2a07d615a8a4f51756532f3bfdfade7d238e1f32` |
| `run_jpeg_counter.py` | `9859fcbf41c242403d043a50741daaa0f6098daacb0096383f0bc9f5d7a588d1` |
| `display_bringup.py` | `81c11447bf7cf730abeb4bf1aaed8585d9aebc179cc45464c0689d6cd8f57d3a` |
| pre-load test | `fc1520c2a32a4e8e0715f108f06f17096c628e0890fd81ee32919f0746579107` |
| transport integration test | `5042cbed2ef130b948ea6ef698619b5c5c1bad9139da38d2fb243b5b304f6bab` |

The package admission check still passes on the unchanged BIT/HWH
(`verify_runtime_artifacts`), and `sha256sum -c SHA256SUMS` passes.

## 4. Pending command (not executed)

The board still contains the **old** package directory
`/home/xilinx/jpeg-candidate-20261103`; do not reuse it for the next attempt.
The new package is `C:/w/jj2026p/board-package` (SHA256SUMS `1a6bf853...`).
After a separately authorized board attempt, copy it to a new directory:

```bash
scp -r C:/w/jj2026p/board-package xilinx@192.168.2.99:/home/xilinx/jpeg-candidate-20261103b
ssh xilinx@192.168.2.99 'cd /home/xilinx/jpeg-candidate-20261103b && sha256sum -c SHA256SUMS'
```

Then repeat the camera/PYNQ/owner precheck and run the same formal entry with
a new boot ID and output directory:

```bash
cd /home/xilinx/jpeg-candidate-20261103b
BOOT_ID="$(cat /proc/sys/kernel/random/boot_id)"
sudo -n env XILINX_XRT=/usr nohup setsid \
  /usr/local/share/pynq-venv/bin/python3 software/scripts/experiments/run_jpeg_counter.py \
  --execute \
  --bit jpeg_candidate.bit --bit-sha256 75577d74... \
  --hwh jpeg_candidate.hwh --hwh-sha256 06cf10ca... \
  --build-status ROUTED_NOT_BOARD_VERIFIED \
  --boot-id "$BOOT_ID" --frames 60 \
  --output run-20261103b/jpeg-smoke-002.json
```

Collect `run-20261103b/jpeg-smoke-002.json`, its console log, and
`run-20261103b/jpeg-smoke-002.json.stage.json` through an independent
connection. Do not retry, reset, reload, or reboot automatically.

## 5. Failure-report correction

`c_candidate_board_smoke_failure_20261103.md` now states that the board was
accessed read-only for identity/PYNQ/camera checks, the package was copied, and
the formal smoke entry was launched once. The earlier "未访问板卡" wording is
withdrawn. The candidate BIT was not loaded and no candidate MMIO was issued
before that fault.

## 6. Remaining limitations

1. No board retry was performed in this round; the new package is not yet on
   the board.
2. The cold-base classification depends on the board's boot evidence
   (`base.bin` in dmesg, FPGA manager `operating`/`flags=0`) and the absence of
   an FPGA owner. If those differ, the runtime stops as UNKNOWN before load.
3. The known-old table covers the reviewed candidate SHA and the pinned RLE
   SHA `ca55911e...`; other overlays are UNKNOWN.
4. The stage file is crash evidence, not a substitute for the final report.
5. `count_accuracy` remains `NOT_MEASURED`; no physical HDMI or accuracy claim
   is made.
6. No RTL/BIT/HWH/constraint change; no deployment, push, or merge.

## 7. Follow-up (2026-11-03)

The smoke-002 attempt used the previous package and hit the transport factory
`NameError: name 'overlay' is not defined` after the candidate load/display
start (frames 0); see `c_candidate_board_smoke_002_failure_20261103.md`. This
package adds the explicit `make_transport(tx, rx, overlay, preview)` production
callback and three transport integration tests. smoke-003 is pending after a
separately authorized reboot; no board retry was performed in this round.