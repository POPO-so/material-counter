# C candidate — RLE raw input mode fix and bounded RX capture (offline)

Date: 2026-11-03
Worktree: `C:/fpga/wt/claude-jpeg-20261102`
Baseline: `9f43738d`
Scope: offline code/test changes and a new independent package. No board
access, no contact with HOLD PID 1358 or its buffers, no reboot/retry/hotfix,
no RTL/BIT/HWH/constraint or startup-bitstream change, no push/merge.

Status: **offline revision awaiting audit**; `count_accuracy` remains
`NOT_MEASURED`.

## 1. Confirmed configuration omission: RLE raw input mode

The candidate build manifest pins `axis_dma_rle_pipeline_bd.v`
SHA-256 `7cfbdde5f0ec6fea7005e53fadcb51473fe7b9bf45d9013720c65274ed50f261`
(snapshot). The worktree copy is `2addeae7...` and lacks the raw adapter/mode
logic. The pinned wrapper has:

| Register | Meaning |
|---|---|
| `0x70` | RAW1 capability, `0x52415731` |
| `0x74` | `0` = packetized gray adapter (reset default); `1` = raw gray adapter |

The candidate HWH connects `jpeg_axis_gray_0/gray_axis_out` to
`image_pipeline/s_axis` and `image_pipeline/m_axis` to
`axi_dma_0/S_AXIS_S2MM`. The JPEG output is a raw gray stream, but the runtime
did not write `0x74`; the wrapper therefore stayed in packetized mode.

The JPEG `JpegRleDmaTransport` now, after the existing preflight and **before**
TX write, RLE/JPEG start, or DMA submit:

1. reads `0x70` and requires `0x52415731`;
2. writes `0x74=1` once, reads it back, and requires `1`;
3. on later frames reads `0x74` and requires `1`.

Missing capability or readback mismatch raises before TX write, pipeline start,
or DMA submit. The packetized input path is unchanged. This is a **confirmed
configuration omission**; it is **not** claimed as proven root cause of the
RLE magic/version failure.

## 2. Bounded RX capture (implemented, not yet executed on board)

After `sync_from_device()` and before `decode_packet()`, the transport takes
one immutable `bytes` snapshot of exactly `dma_bytes` from the RX buffer (first
valid frame only; no invalid tail). The same object is passed to
`decode_packet()`, so the saved content is the decoder input.

The production callback writes:

- `output.rx-frame-<id>.bin` — exact valid RX bytes;
- `output.rx-frame-<id>.json` — frame ID, length, SHA-256, first/last 64 bytes,
  header words `h[0..15]`, `dma_bytes`/`rle_bytes`, RX
  `physical_address`/`device_address`/`nbytes`, hardware S2MM DA/DA_MSB/
  length/status, `input_mode`/RAW1, and completion state.

It does not read read-to-clear registers (for example JPEG `status_ap_vld`).
Capture errors are recorded in `rx_capture_error` and never replace the
decode/transport exception or weaken HOLD. The capture provides the actual
decoder input but cannot by itself guarantee a causal distinction between a PL
packet issue and a CPU sync/mapping issue.

## 3. Tests

Old code `9f43738d` with the new tests:
`old_9f43738d_tests.txt` (5 failed, 70 deselected):

- raw mode write missing (`('rle','write',0x74,1)` not in list);
- missing capability did not raise;
- readback failure did not raise;
- capture tests rejected the absent `rx_capture` constructor argument
  (`TypeError`).

Current tree:

| Suite | Result |
|---|---|
| pre-load policy | 45 |
| display/HOLD | 13 |
| admission | 5 |
| transport integration | 12 |
| runtime/transport | 83 |
| **full** | **158 passed** |

Targeted raw/capture tests: 5 passed; integration: 12 passed; full: 158 passed,
EXIT=0. Production assembly receives a callable capture callback; a production
capture test writes exact bytes/metadata; production guard + real `transfer()`
covers raw mode and normal first/second frames.

## 4. New revision package

Path: `C:/w/jj2026p/board-package-firstframe-fix-20261103c`
(existing packages `board-package`, `...-20261103`,
`...-20261103b` and all failure evidence are preserved).

| Item | SHA-256 |
|---|---|
| manifest | `280971324f9fff23f1b98f19bdf44446da878cc8af90ebdd6564a9d0698ac16c` |
| SHA256SUMS | `ed252984501c1ff3a4c89b9be2c651dcacb1e08989ebf58b1d6f45f839e9619c` |
| README | `6dbd24c093ea98567a9b374cea23c5e87882b92d9ccbb85855754f51fca2f263` |
| BIT (unchanged) | `75577d743060a0908f5248e5a12062428bc1d6ad603c81fc5fdaedc9b15b230b` |
| HWH (unchanged) | `06cf10cafc545d930fa210cd8fdd88d09981d808cb1c4197146183f822901380` |
| `jpeg_transport.py` | `c646e45606e1014b33943b2b1b2cd34b36837723aa39df3ba5604256c30cb177` |
| `test_jpeg_transport.py` | `42c06a5147a92d025021a3730e999bdbede77182eb88f4a35165fd5503761bfa` |
| `run_jpeg_counter.py` | `8bf5c7ab6e5ad1e341537c53ecc69714615ea4ece903eb4419b5f11a9c0984a4` |
| `test_jpeg_transport_integration.py` | `17562142f99996e2459241f7c56355ead2b76a00dd334e450d6986e862b1971c` |

`package_root` is `C:/w/jj2026p/board-package-firstframe-fix-20261103c`.
`sha256sum -c SHA256SUMS` = 345/345 OK; manifest source check has no
mismatches; package regression = 158 passed; BIT/HWH unchanged.

## 5. Monitor supply timeline (corrected)

- Before/during the 10-frame run: monitor unpowered; black screen explained by
  that condition, not attributed to the RLE failure.
- After the user powered the monitor: user observed a signal but no picture.
  This is a field observation, not a board validation.
- A valid physical picture is still **not accepted**. The HOLD has no new
  frame; powering the monitor later does not validate the failed frame.

## 6. Status

**Offline revision awaiting audit.** No board access; PID 1358 and its buffers
untouched; no kill/reset/reload/retry/reboot; no decoder change; no push/merge.
`count_accuracy` remains `NOT_MEASURED`.