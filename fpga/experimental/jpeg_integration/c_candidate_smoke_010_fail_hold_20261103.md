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

## 6. Status and next step

- 10-frame smoke: **FAIL_HOLD at first frame**, 0/10; no second frame, no
  completion, no normal cleanup.
- DMA errors: none; both channels Idle. The failure is RLE packet header
  validation after a completed transfer.
- The strict-zero first-transfer fix worked; this is a downstream data-format
  issue.
- HOLD must remain alive; do not Ctrl+C/kill/reset/reload/retry. Another reboot
  requires separate authorization.
- Offline follow-up should inspect the PL RLE packet header/layout produced by
  the JPEG path against `protocol.decode_packet` (magic/version word) and the
  previous RLE path, without touching the HOLD.
- `count_accuracy` remains `NOT_MEASURED`; no physical picture or accuracy
  claim.
- No source/hardware change, no push/merge.