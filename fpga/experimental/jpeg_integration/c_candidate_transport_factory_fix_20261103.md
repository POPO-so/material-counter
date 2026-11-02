# C candidate — transport factory scope fix (offline)

Date: 2026-11-03
Worktree: `C:/fpga/wt/claude-jpeg-20261102`
Scope: offline software fix, integration tests, and repackaging only. The board
is still in the smoke-002 HOLD; this round did not access the board, kill/reset/
reload/reboot it, or rerun any smoke.

## 1. Root cause and fix

Smoke-002 loaded the candidate and started the display, then raised:

```text
NameError: name 'overlay' is not defined
```

at transport creation (`frames 0`). The pre-load refactor moved the `Overlay`
object into `run_candidate_sequence`; main's `make_transport` lambda still
closed over a removed main-scope `overlay`. The same closure review found the
frame-loop `preview` reference, which was also no longer defined in main.

The production boundary is now explicit:

```text
make_transport(tx, rx, overlay, preview)
```

- `run_candidate_sequence` creates the preview MMIO after display start, passes
  the same `overlay` and `preview` objects to the factory, and returns the
  preview to the frame loop.
- `build_transport_callback` is the production transport callback; it receives
  the Overlay/preview explicitly and extracts the AXI DMA channels.
- `assemble_candidate_callbacks` is the exact callback set used by main. It
  builds the transport callback, candidate-DMA check, preview factory, display
  prepare, identity check, allocations, and stage writer. Tests inject fake
  hardware dependencies into this same assembly.
- No main-scope `overlay` or `preview` closure remains.

## 2. Integration test coverage

`scripts/experiments/test_jpeg_transport_integration.py` (3 tests) drives the
production `assemble_candidate_callbacks` / `build_transport_callback` with
fake Overlay, MMIO, display, allocator, and transport factory:

1. **Production factory receives the same Overlay and preview** — the fake
   transport factory receives the fake Overlay's `sendchannel`/`recvchannel`
   identities and the exact preview object returned to the frame loop; display
   start occurs before transport creation.
2. **Main-assembled callbacks normal path** — production assembly is called
   directly; display start -> preview MMIO -> TX/RX -> factory -> transport
   success; the stage sequence includes `display_start` and `transport_ready`.
3. **Factory failure HOLD path** — factory raises after display start; no
   camera open, no `transport_ready`, no frame processing; display and TX/RX
   buffers remain; `finalize_run(held=True)` performs no close/free/reset/
   reload. The exception carries the same display and allocated buffers.

Full JPEG regression: **95 passed**:

| Suite | Result |
|---|---|
| admission | 5 passed |
| display/HOLD | 13 passed |
| pre-load policy | 11 passed |
| transport integration | 3 passed |
| runtime/transport | 63 passed |

## 3. New package SHA

| Item | SHA-256 |
|---|---|
| BIT (unchanged) | `75577d743060a0908f5248e5a12062428bc1d6ad603c81fc5fdaedc9b15b230b` |
| HWH (unchanged) | `06cf10cafc545d930fa210cd8fdd88d09981d808cb1c4197146183f822901380` |
| Package manifest | `9a50d9914cdc5b07257376a0cbbb2fa59120598756810abbf3a9a8c47dc712f9` |
| Package SHA256SUMS | `1a6bf853a9e969813f69e3cb2a3edf6360cf089d5278565f02558586df4896dd` |
| Package README | `a9cedbb9d415dc56ff43889f3953dc38500aec182ebe4b1d7b26e7a2cc508b50` |
| `run_jpeg_counter.py` | `9859fcbf41c242403d043a50741daaa0f6098daacb0096383f0bc9f5d7a588d1` |
| `display_bringup.py` | `81c11447bf7cf730abeb4bf1aaed8585d9aebc179cc45464c0689d6cd8f57d3a` |
| pre-load test | `fc1520c2a32a4e8e0715f108f06f17096c628e0890fd81ee32919f0746579107` |
| integration test | `5042cbed2ef130b948ea6ef698619b5c5c1bad9139da38d2fb243b5b304f6bab` |

`sha256sum -c SHA256SUMS` passes; package `verify_runtime_artifacts` passes;
BIT/HWH full SHAs are unchanged.

## 4. Smoke-002 status (unchanged)

- Candidate **temporarily loaded**; display started.
- **JPEG/RLE frames 0**; no camera open, no candidate transfer.
- `FAIL_HOLD` active; PID 14240 still alive; display/DMA resources retained.
- Board HOLD was not touched this round; no kill/reset/reload/reboot; no board
  access at all in this offline round.
- `count_accuracy` remains `NOT_MEASURED`; no physical HDMI or accuracy claim.

## 5. Smoke-003 pending command (not executed)

The current boot is in HOLD; smoke-003 requires an operator-authorized reboot
and a fresh board directory. After that authorization:

```bash
# Windows host -> board, new directory
scp -r C:/w/jj2026p/board-package xilinx@192.168.2.99:/home/xilinx/jpeg-candidate-20261103c

# Board shell
cd /home/xilinx/jpeg-candidate-20261103c
sha256sum -c SHA256SUMS
test "$(sha256sum SHA256SUMS | cut -d' ' -f1)" = "1a6bf853a9e969813f69e3cb2a3edf6360cf089d5278565f02558586df4896dd"
test "$(sha256sum artifact-manifest.json | cut -d' ' -f1)" = "9a50d9914cdc5b07257376a0cbbb2fa59120598756810abbf3a9a8c47dc712f9"
BOOT_ID="$(cat /proc/sys/kernel/random/boot_id)"
mkdir -p run-20261103c
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
  --output run-20261103c/jpeg-smoke-003.json
```

Do not run smoke-003 until the reboot is separately authorized and the new
boot ID is captured. On success collect the 60-frame report, console, and
`run-20261103c/jpeg-smoke-003.json.stage.json`; on `FAIL_HOLD`, keep the process
and buffers alive and collect over an independent connection.

## 6. Limitations

- No board access this round; smoke-003 has not run.
- The board remains in HOLD with the candidate BIT loaded; reboot is required
  before any new smoke.
- `count_accuracy` remains `NOT_MEASURED`.
- No RTL/BIT/HWH/constraint change; no deployment, push, or merge.