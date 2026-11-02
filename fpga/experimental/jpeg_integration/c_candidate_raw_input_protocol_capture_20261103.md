# C candidate — raw input protocol analysis and error-frame capture path (offline)

Date: 2026-11-03
Worktree: `C:/fpga/wt/claude-jpeg-20261102`
Scope: offline source/artifact analysis and a bounded transport change. No board
access, no HLS/synthesis/route, no RTL/BIT/HWH change, no hotfix, no reboot,
no retry, no push/merge. `count_accuracy` remains `NOT_MEASURED`.

Status: **offline revision awaiting audit**.

## 1. Frozen sources

The candidate manifest `C:/w/jj2026p/joint_manifest.json` pins:

| Source | SHA-256 |
|---|---|
| `fpga/experimental/jpeg_axis/jpeg_axis_gray.cpp` | `d695aac91915f3bf8b4d4357d7b2ca539929197720b2f2390cfb5a2472108f7d` |
| `fpga/experimental/gray8/axis_raw_gray_adapter.v` | `da12e1392d2c4d168277441a4e916d098d9222332aa408e7b30e36e3fc293060` |
| `fpga/experimental/gray8/axis_dma_gray_adapter.v` | `1d6f303af3ecbbd2fa72d9ff1921d23bce22f2e33bcfc90c03b4ff8a829137d4` |
| `fpga/experimental/rle/axis_dma_rle_pipeline_bd.v` | `7cfbdde5f0ec6fea7005e53fadcb51473fe7b9bf45d9013720c65274ed50f261` |
| `fpga/experimental/rle/axis_rle_encoder.v` | `2606fc95105671a0f0864f5af01207ae2a2415fa76c9f45523c5e06103ed1bb7` |

### 1.1 Frozen JPEG output is a GRAY1 packet, not a raw pixel stream

`jpeg_axis_gray.cpp` `write_axis()` emits 32-bit words with `keep=0xF` and
`last` only on the final word. `render_gray()` writes:

1. four header words: `0x47525938`, `(400<<16)|640` = `0x01900280`,
   `640*400` = `0x0003E800`, `frame_id`;
2. 64,000 payload words, four pixels per word, `keep=0xF`, `last=0`;
3. four trailer words: `0x454E4438`, `frame_id`, checksum,
   `0xC0DEC018` with `last=1`.

Total: **64,008 AXIS beats** (4 + 64,000 + 4). Pixels: 256,000.
Header/trailer references: `jpeg_axis_gray.cpp:550-553`, `:581-587`,
`:595-598`. Constants: `jpeg_axis_gray.h:8-13`.

### 1.2 Raw adapter contract

`axis_raw_gray_adapter.v` expects a raw stream only:

- `BEATS=(WIDTH*HEIGHT)/4` = **64,000 input beats** (`:17`);
- `final_beat=(beat_count==BEATS-1)`, i.e. beat index 63,999 (`:31`);
- every beat must have `s_tkeep==4'hf` (`:73`, `:83`);
- `s_tlast` must equal `final_beat` (`:84`); a mismatch rejects with code 4;
- each beat is treated as four pixels; the module does not parse header or
  trailer words;
- `frame_id` is copied from `cfg_frame_id` (`:78`), not from the input stream;
- output `m_tlast` is asserted at `column==WIDTH-1` (`:59`), i.e. per row for
  the downstream RLE encoder.

### 1.3 Packetized adapter contract

`axis_dma_gray_adapter.v` matches the frozen JPEG output exactly:

- H0 `0x47525938` (`:96-101`), H1 geometry (`:102-107`), H2 payload bytes
  `0x0003E800` (`:108-113`), H3 `frame_id` (`:114-122`);
- 64,000 payload words with `keep=0xF` and `last=0`;
- T0 `0x454E4438`, T1 `frame_id`, T2 payload checksum, T3 `0xC0DEC018`
  with `last=1` (`:127-135`);
- `packet_done` at T3; `frame_id` comes from H3 and is cross-checked at T1;
- total expected beats: **64,008**.

### 1.4 Wrapper selection and error registers

`axis_dma_rle_pipeline_bd.v` selects the adapter by `0x74 input_mode`
(`:103-110`, `:208-209`):

- `0x74=0` → packetized adapter (reset default);
- `0x74=1` → raw adapter.

`0x08` is `{29'd0, input_error_latched, done_seen, busy}` (`:191`,
`:245-246`). `0x38` returns `{28'd0, selected_packet_code}` (`:247`), i.e.
the **currently selected adapter's 4-bit code**, not a unified latch of all
error sources. `frame_mismatch` contributes to `error_event` but does not
change `selected_packet_code`.

## 2. Raw input protocol finding

Selecting `0x74=1` for this frozen JPEG output is a protocol error:

- the stream has 64,008 beats; the raw adapter expects exactly 64,000;
- beats 0-3 are the GRAY1 header, beats 4-64,003 are pixel words, beats
  64,004-64,007 are the trailer;
- at beat index 63,999 the raw adapter asserts `final_beat=1` but the actual
  `s_tlast=0` (TLAST is on beat 64,007), so `reject_frame(4,0)` fires
  (`axis_raw_gray_adapter.v:84`);
- the adapter has already emitted the four header words as 16 gray pixels;
  after rejection it drops the remaining beats until TLAST, so the pixel
  stream is shifted and the last payload bytes are never emitted;
- `TKEEP` is `0xF` on every frozen JPEG word, so the rejection is the
  beat-count/TLAST check (code 4), not a TKEEP check (code 3);
- the raw adapter ignores the stream `frame_id`; the wrapper's
  `frame_mismatch` comparison uses `raw_frame_id=cfg_frame_id`, so it cannot
  detect a frame-ID mismatch in raw mode.

This matches the board observation: `RLE 0x08=0x6` (done_seen + input-error
latched), `0x74=1`, DMA Idle, S2MM length 2,220 equal to RLE actual length
2,220. The transport then timed out because `_wait_for_completion()` requires
`rle_state & 0x2` and `not (rle_state & 0x5)`.

Backpressure is not the cause of this failure. Both adapters gate output with
`output_space`/`after_emit`, hold `m_tvalid` and data stable while `m_tready`
is low, and advance counters only on accepted handshakes. No drop or duplicate
beat follows from the source-level handshake logic. The raw adapter fails on
the beat-count/TLAST contract before backpressure can matter.

**Conclusion:** the frozen JPEG output is GRAY1 packetized; only the
packetized adapter matches its beat count, header/trailer, TKEEP, TLAST and
frame-ID contract. Raw mode (`0x74=1`) is the wrong selection. This does not
by itself explain the earlier packetized-mode `RLE header magic or version is
invalid` failure; that remains a separate open item. No source or
configuration change is made in this round; the evidence points to a
configuration decision (`0x74=0`), not an RTL change. If raw mode must be
supported, the raw adapter would need a GRAY1 parser (duplicating the
packetized adapter) or the JPEG core would need to emit a raw stream; both
are interface changes requiring separate evidence and scope approval.

### 2.1 `0x38` evidence gap

The board snapshot did not record `0x38`. No board access is permitted in this
round, so the gap is not filled. `0x38` returns `selected_packet_code` for the
currently selected adapter; it must not be treated as a unified latched error
code for all sources. In particular, the raw adapter's expected code 4 is an
inference from the frozen source and the observed rejection timing, not a
recorded `0x38` value.

## 3. Error-frame diagnostic capture

### 3.1 Safety conditions

RX is read only when all of the following hold after a completion failure:

1. MM2S and S2MM status have no error bits;
2. MM2S and S2MM both report Idle (the write side has stopped);
3. RLE `0x08` busy bit is clear and `packet_done` (done_seen) is observed;
4. S2MM length and RLE actual length are equal;
5. the length is 4-byte aligned and within
   `MIN_RLE_PACKET_BYTES..RX_CAPACITY`;
6. the length is inside the RX buffer `nbytes`.

If any condition fails, only the observed state and a reason are recorded; no
`sync_from_device()` and no RX read occur. The conditions are evaluated from
registers only, before touching the buffer.

### 3.2 Minimal implementation

- `JpegRleDmaTransport._diagnostic_capture_facts()` reads statuses, RLE state
  and lengths, and returns either a safe fact set or a refusal reason.
- `_capture_rx_diagnostic()` is invoked only from the `except Exception`
  around `_wait_for_completion()` in `transfer()`. On a safe endpoint it
  calls `sync_from_device()`, takes one immutable `bytes` snapshot of exactly
  the S2MM length, and invokes the existing capture callback with
  `diagnostic=true`, `frame_accepted=false`, the original error, and the
  safety facts. It is one attempt per transport and never masks the original
  exception.
- The original completion exception is re-raised immediately after the
  capture attempt. The frame is not decoded, accepted, counted, published,
  retried or freed; `dma_may_be_active` remains true, so the existing
  FAIL_HOLD path is unchanged.
- `rx_capture_decision` records `attempted/captured/diagnostic/reason` plus
  the observed statuses and lengths; `run_jpeg_counter.py` adds it to the run
  report.
- The production callback writes diagnostic evidence as
  `output.rx-error-frame-<id>.bin/.json`; the valid path keeps
  `output.rx-frame-<id>.bin/.json`.

`_wait_for_completion()` and `decode_packet()` are unchanged. Input-error
states still fail; the diagnostic path never turns an error frame into a
success.

### 3.3 Offline tests

`scripts/rle/test_jpeg_transport.py`:

- `test_diagnostic_capture_runs_only_after_error_frame_is_rejected`:
  RLE error + idle DMA + equal in-range lengths → one diagnostic capture,
  original `TimeoutError` re-raised, decode not called, frames not completed,
  `dma_may_be_active=true`, no preview accept.
- `test_diagnostic_capture_skips_rx_read_when_dma_not_idle`: DMA not Idle →
  no `sync_from_device`, no callback, state recorded.
- `test_diagnostic_capture_skips_rx_read_when_packet_done_missing`: RLE not
  done → no RX read.
- `test_diagnostic_capture_skips_rx_read_when_lengths_disagree`: S2MM/RLE
  lengths differ → no RX read.
- `test_diagnostic_capture_failure_preserves_original_timeout`: capture
  raises `OSError` → original `TimeoutError` still propagates,
  `rx_capture_error` records the capture failure.

`scripts/experiments/test_jpeg_transport_integration.py`:
`test_make_rx_capture_writes_error_named_files_for_diagnostic_frame` verifies
the production `rx-error-frame-<id>` naming and `diagnostic`/
`frame_accepted` metadata.

Full JPEG suite after the change: **168 passed** (previous 162 + 6 new).

## 4. Corrections to the previous evidence statements

- MM2S JPEG length and S2MM RLE length are not required to be equal. In the
  failed run, MM2S length was 5,812 (JPEG input) and S2MM length was 2,220;
  only S2MM length equaled RLE `actual_length` (2,220).
- The direct `/dev/mem` snapshot's trailer magic at word 538 and END magic at
  word 554 are 16 words apart. A legal 16-word trailer has its magic at
  index 0 and END at index 15 (15 words apart). The snapshot therefore does
  not have a legal trailer layout, and its fields (including the word
  previously read as status 2) must not be treated as validated protocol
  data. The direct read is raw-word evidence only, bypasses PYNQ cache
  invalidation, and is non-authoritative.
- `rx_capture_error=null` does not mean capture succeeded. It means no
  capture exception was recorded. In the failed run, the runtime capture did
  not execute because `_wait_for_completion()` raised before
  `sync_from_device()`/`decode_packet()`. No `output.rx-frame-*` file was
  produced.

## 5. Status

- No board access in this round; HOLD PID 1115 and its buffers/display were
  not touched.
- No HLS, synthesis, route, RTL/BIT/HWH or constraint change.
- No source hotfix on the board; no reboot, retry, push or merge.
- The frozen JPEG output / raw adapter / wrapper analysis is source-level.
  No local Vivado/XSim entry is available; XSim was not run.
- `count_accuracy` remains `NOT_MEASURED`.
- Offline revision awaiting audit.