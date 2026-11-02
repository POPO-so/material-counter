# C candidate — 10-frame smoke: first-frame RLE header rejected, FAIL_HOLD

Date: 2026-11-03
Board: PYNQ-Z2 at `192.168.2.99`.
Recovery: boot ID `6b0d11ee-d9cb-4513-afc8-fee3296448df` (HOLD PID 17659)
→ one controlled reboot → new boot ID `f97be97a-fd56-471e-be28-08c5429a1863`.
Package: `/home/xilinx/jpeg-candidate-20261103e`, revision package
`board-package-firstframe-fix-20261103b`.
Result: **FAIL_HOLD at the first frame, 0/10 frames.** The pre-load gate, strict
zero first-transfer proof, candidate load, display start, and transport
creation passed. The failure is the RLE packet header validation after DMA
completion. The HOLD process and buffers are retained. No retry, reset, reload,
kill, or further reboot was issued. `count_accuracy` remains `NOT_MEASURED`.

## 1. HOLD recovery and new boot

Before reboot, read-only identity matched the expected HOLD:

- boot ID `6b0d11ee-d9cb-4513-afc8-fee3296448df`;
- PID 17659, parent 17653, command
  `run_jpeg_counter.py ... --frames 60 --stop-base-trace-dmas --output run-20261103d/jpeg-smoke-003.json`;
- retained `/dev/mem`, `/dev/uio1`, `/dev/dri/renderD128`;
- report `FAIL_HOLD`, error `MM2S DMA is not idle`, frames 0, `buffers_held=true`;
- no other material/JPEG task.

A single `sudo -n reboot` was issued; no kill/reset/free/reload/retry before it.
The board returned with boot ID
`f97be97a-fd56-471e-be28-08c5429a1863`. No other task; FPGA manager `operating`,
flags `0`; boot bitstream `base.bin`; PYNQ global state absent
(`COLD_BOOT_NO_GLOBAL_STATE`); base.bit `3239e9cc...`, base.hwh `222898ef...`;
CMA 79,776 KiB free; no video owner. The new package directory was absent.

## 2. Field condition: monitor not powered

Timeline:

- Before/during the 10-frame run: the monitor was not powered. The observed
  black screen is explained by that field condition and is **not** attributed to
  the RLE packet header failure.
- After the user powered the monitor, the user observed a signal but no
  picture. This is a field observation, not a board validation; a valid
  physical picture is still **not accepted**.
- The HOLD has no new frame; powering the monitor after the fact does not
  retroactively validate the failed frame.

The run report's display evidence remains the PL display start call and
register readback only; no valid physical picture is claimed.

## 2. Package verification

`/home/xilinx/jpeg-candidate-20261103e` matched the revision package:

| Item | SHA-256 |
|---|---|
| SHA256SUMS | `58e06f0e719793c68aa6b65fcf10eace822647437833c4b5af94b09c16654f44` |
| manifest | `851fe94631fccce95a619fc05e6b9b07979eb8347ef55cf76ca7ddf312cf35b9` |
| BIT | `75577d743060a0908f5248e5a12062428bc1d6ad603c81fc5fdaedc9b15b230b` |
| HWH | `06cf10cafc545d930fa210cd8fdd88d09981d808cb1c4197146183f822901380` |
| `jpeg_transport.py` | `bbc29d27f04fc9128cb7bc90a98118623d89fbcb14f15d1d1a60bb2e6a9d23ea` |
| `test_jpeg_transport_integration.py` | `4e5245c597bd05c091af578031e550146f3083fabadf5c5ef45eec60a2de583d` |

`sha256sum -c SHA256SUMS` = 345/345 OK; manifest `package_root` =
`C:/w/jj2026p/board-package-firstframe-fix-20261103b`.

## 3. Run stages

```text
preflight
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

Pre-load classification was **COLD_BASE** (PYNQ state absent, boot `base.bin`,
FPGA manager `operating`/flags 0, no owner), `candidate_mmio_before_load=false`.
`base_dma_stop_result` is null because the KNOWN_BASE stop path was not needed.
The strict-zero first-transfer proof passed; the previous
`MM2S DMA is not idle` failure did not recur. Candidate identity matched
`75577d74...`; display clocks 50/100/142.857 MHz; display start call returned.

## 4. Failure

At the first frame, after DMA completion and `sync_from_device`, the transport
called `decode_packet()` and raised:

```text
ValueError: RLE header magic or version is invalid
```

`frames=0`, `total_count=0`; the stage file's last stage is `frames_start`.
This is the RLE packet header check (`HEADER_MAGIC=0x31454C52`, `VERSION=1`),
not the pre-load gate, DMA preflight, timeout, or DMA ownership check.

Read-only register snapshot during HOLD (`hold-registers.txt`):

| Block | Values |
|---|---|
| DMA MM2S | control `0x00010003`, status `0x00001002` (Idle, no error bits), address `0x15880000`, length `0x00003d38` (15672) |
| DMA S2MM | control `0x00010003`, status `0x00001002` (Idle, no error bits), address `0x15860000`, length `0x000014c8` (5320) |
| RLE | signature `0x31454c52`, state `2`, actual length `0x000014c8` (5320), frame_id `0`, geometry `0x01900280` (640×400), max_runs `0x00004000` |
| JPEG | CTRL `4` (idle), size `0x00003d38` (15672), frame_id `0`, status `0` |
| FRG | signature `0x46524731`, status `1`, frame ID `0` |

The MM2S JPEG length (15672), S2MM RLE length (5320), and RLE actual length
(5320) agree; both DMA channels report Idle and no error. This is therefore a
packet-content/format rejection, not a partial submission, timeout, or DMA
error. An independent `/dev/mem` read of the S2MM buffer did not show the
expected magic/version at word 0, but that read bypasses the PYNQ buffer cache
invalidation and is corroborating only; the runtime's post-`sync_from_device`
`decode_packet` rejection is authoritative.

The process entered `FAIL_HOLD` because `dma_may_be_active` was set before
arming and remains set. PID 1358 (parent 1352) is alive, sleeping, with
`/dev/mem`, `/dev/uio1`, `/dev/dri/renderD128` open. Display `started=true`,
`closed=false`, `vdma_uncertain=true`, `buffers_held=true`. CMA before 36,824
KiB, after 15,420 KiB (retained resources; not a leak inference).

### 4.1 Offline byte/contract comparison

`compare_packet_contract.py` / `offline_packet_compare.txt` compare the
captured S2MM buffer words with **this run's actual** RLE_PACKET_V1 header.
Actual parameters: frame_id 0, params_version 0, full ROI (0,0,640,400),
threshold 128, offset 0, morph 0; timestamp words are dynamic. The historical
vector `baseline-expected.hex` is a format reference only (it has frame_id
0x11/params_version 3, not this run).

Fixed-field result (timestamp words 7/8 excluded): 6/14 match. Magic/version/
geometry/ROI end/flags/projection/max_runs/threshold do not match; frame_id/
params/ROI start/offset/morph/reserved do. Captured word 0/1 are
`0x00000001`/`0x00000000`.

Build-source trace (from `C:/w/jj2026p/joint_manifest.json`):

- pinned `axis_rle_encoder.v` = `2606fc95...` (same as the worktree); its
  header state machine writes magic/version at index 0/1 before pixels;
- pinned wrapper `axis_dma_rle_pipeline_bd.v` = `7cfbdde5...` (snapshot); the
  worktree copy is `2addeae7...`. The pinned wrapper adds
  `axis_raw_gray_adapter`, `input_mode` at register `0x74`, and preview ports;
- HWH connections: `jpeg_axis_gray_0/gray_axis_out` → `image_pipeline/s_axis`;
  `image_pipeline/m_axis` → `axi_dma_0/S_AXIS_S2MM`;
- the runtime writes RLE `0x00` start and config `0x10`–`0x28`, but does **not**
  write `0x74 input_mode`. Default `input_mode=0` selects the packetized gray
  adapter, while the JPEG output is a raw gray stream. This is a concrete
  configuration/connection discrepancy to review; it is not yet proven to be
  the cause of the header failure.

Software buffer path: `_buffer_span` verifies RX physical==device, exactly
`RX_CAPACITY`, DDR range and 4-byte alignment; `recv_channel.transfer` writes
the physical address/length; `_wait_for_completion` matches DMA S2MM length and
RLE actual length (5320); `send/recv wait`, `sync_from_device`, then
`memoryview(rx_buffer)[:dma_bytes]`, then `decode_packet`. The byte view starts
at offset 0 and the length is within the buffer. The generic PYNQ
`pynq/lib/dma.py` sync implementation is not hash-verified from retained
materials; that remains an unverified boundary.

**Caveat:** the independent `/dev/mem` read bypasses the PYNQ buffer cache
invalidation used by the runtime, so the captured words are corroborating
only. The authoritative observation is the runtime's post-`sync_from_device`
`decode_packet` rejection at h[0]/h[1]. The current evidence cannot distinguish
(a) a PL RLE packet/header/configuration issue from (b) a software
buffer-sync/mapping issue. Do not patch the decoder to bypass the check.

## 5. Evidence

Directory: `data/results/jj2026p-board-smoke-10frame-20261103/`

| File | SHA-256 |
|---|---|
| `jpeg-smoke-010.json` | `9317ec97e61d0309157971bff4f0bc71dfe46ea26336cb2ac5c611799e516dd9` |
| `jpeg-smoke-010.json.hold.json` | `e41933fe9dce3e8cf49b3310bcaa34717d7f7e9efdc35cd3a525ff86de9ed080` |
| `jpeg-smoke-010.json.stage.json` | `6223e16822be455343e506d802d8a7778c085a8d4f414eda30545da44d282df0` |
| `jpeg-smoke-010.console.log` | `0876f4578055d065b66f0498e5f192a63d923d15eec7f00a8a5c4d677f827560` |
| `jpeg-smoke-010.python.pid` | `53ee0258ae5308cd63fe2ce806596f62e173803ca1af6bdd120e519f1e1b5f9c` |
| `hold-state.txt` | `f7b31805ab2c2eea7bab7a36aa1bcffe6cdfffa6bed7a30b00a1933b201e7e22` |
| `hold-registers.txt` | `65a9d52ef57af725ee5e2b5f3406b7007325af7f46df3a3f7a24cee339b43dae` |
| `offline_packet_compare.txt` | `8ef2dadc5c0733a884a58eaa8c7b6562197fdcf17462a46e2cf181446867b37d` |
| `compare_packet_contract.py` | `82027a1d18bd33cae0436ac44d1b578214f0e7135b75ebcd8d0ca448e5114088` |

## 6. Status and next step

- 10-frame smoke: **FAIL_HOLD at first frame**, 0/10; no second frame, no
  completion, no normal cleanup.
- DMA errors: none; both channels Idle. The failure is RLE packet header
  validation after a completed transfer.
- The strict-zero first-transfer fix worked; this is a downstream data-format
  issue. The actual reason is not yet resolved offline: the independent buffer
  read is not cache-authoritative, and the runtime rejection is the only
  authoritative observation.
- HOLD must remain alive; do not Ctrl+C/kill/reset/reload/retry. Another reboot
  requires separate authorization.
- Offline follow-up should inspect the PL RLE packet header/layout produced by
  the JPEG path against `protocol.decode_packet` (magic/version word) and the
  previous RLE path, without touching the HOLD.
- `count_accuracy` remains `NOT_MEASURED`; no physical picture or accuracy
  claim.
- No source/hardware change, no push/merge.

### 6.1 Next-run minimal capture plan (not executed)

Do not inject into the current HOLD. On a separately authorized run, after the
existing `sync_from_device()` and **before** `decode_packet()`:

1. Write a bounded evidence file next to the run output (only when the packet
   is about to be validated; flush before decoding):
   - `dma_bytes`, `rle_bytes`;
   - RX buffer `physical_address`, `device_address`, `nbytes`, and the
     `memoryview` offset/length used for the packet;
   - first 64 and last 64 bytes as hex, plus SHA-256 of the full `dma_bytes`;
   - header words `h[0..15]` as hex/decoded;
   - DMA S2MM DA/length/status, RLE actual length and `0x74 input_mode`
     readback, RLE config, JPEG size/frame_id/status.
2. Leave `decode_packet()` unchanged. If it raises, preserve the original
   exception and enter the existing FAIL_HOLD path; no close/free/reset/reload/
   retry; the evidence file must already be flushed.
3. Keep the capture bounded; no full RX buffer dump unless separately
   authorized. The head/tail words plus the digest are enough to distinguish
   a header/format mismatch from a sync/mapping mismatch.
4. Also read back the pinned wrapper's `0x74 input_mode` in that run. If the
   reviewed contract requires raw mode, make a separate offline source/test
   change and review it; do not hotfix the running process.

Acceptance of the capture: it must show whether the synced buffer starts with
`0x31454c52/1`, whether the fixed fields match this run's actual parameters,
and what `input_mode` was selected. That distinguishes (a) PL RLE
header/configuration from (b) software buffer sync/mapping.