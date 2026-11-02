# C candidate — first-frame AXI DMA preflight fix (offline, for Codex audit)

Date: 2026-11-03
Worktree: `C:/fpga/wt/claude-jpeg-20261102`
Baseline: `4e2c0513`
Scope: offline code, tests, and a new independent local package only. No board
access, no contact with HOLD PID 17659 or its buffers, no reset/reload/retry/
reboot, no RTL/BIT/HWH/constraint or startup-bitstream change, no push/merge.

Status: **offline fix awaiting Codex audit**; smoke-003 remains FAIL_HOLD.

## 1. Root cause

PYNQ's DMA driver starts both Direct-mode channels when
`overlay.axi_dma_0.sendchannel/recvchannel` are first accessed
(`_SDMAChannel.__init__` writes `DMACR=0x1001`/`0x0001`). Before the first
submitted transfer the channel reads:

```text
DMACR = 0x00010003 (RS=1, Reserved bit1=1, IOC_IrqEn=1, IRQThreshold=1)
DMASR = 0x00000000 (Halted=0, Idle=0)
```

The old `_preflight` required `DMASR & (IDLE|HALTED)` to be non-zero, so a valid
started-but-unarmed channel was rejected as `MM2S DMA is not idle`. The first
smoke-003 run hit this before any frame (`frames=0`).

## 2. Fix: explicit ownership + unarmed proof

The first exception is allowed only when all of the following hold:

1. **Transport has not submitted**: `_submitted_frames == 0`. The counter is
   incremented immediately before the first `recv_channel.transfer()` arming
   call. This is not `_completed_frames`, not a new-transport assumption, and
   not the driver's `_first_transfer` flag.
2. **Ownership proof**: the production `first_transfer_guard` verifies
   `channel is overlay.axi_dma_0.sendchannel/recvchannel` and
   `dma_regs is` the exact MMIO object passed to the transport. A different
   channel or MMIO object yields no proof.
3. **Hardware unarmed proof**: the guard reads the channel's control, status,
   address and length:
   - `DMACR.RS=1`, `DMACR.Reset=0`;
   - `DMASR` has no error bits (`0x770`) and no reserved bits (`0x8884`);
   - the observed status is still `0x0`;
   - `MM2S_SA=0` and `MM2S_LENGTH=0`, or `S2MM_DA=0` and
     `S2MM_LENGTH=0`.

Any nonzero address/length (committed or partially submitted), a state change
during the proof, an error/reserved bit, missing ownership, or a status that is
not `0x0` after the proof is rejected **before** the TX buffer write, RLE/JPEG
start, or DMA submission.

MM2S and S2MM are distinct: MM2S uses control `0x00`, status `0x04`, address
`0x18`, length `0x28`; S2MM uses control `0x30`, status `0x34`, address `0x48`,
length `0x58`.

### Invalidation conditions

- Once the first transfer is submitted, `_submitted_frames > 0` permanently
  disables the exception for that transport.
- A new transport on an already started/committed channel is rejected because
  its guard sees a nonzero address/length; a real in-progress transfer always
  has its address and length programmed.
- Error/reserved bits, a control Reset bit, RS=0, or a changed status are
  rejected.
- If no guard is supplied (non-production callers), status `0x0` remains
  rejected as before.

No stop/start/reset was added to bypass the check; `dma_may_be_active` still
disables retry and holds the buffers.

## 3. Old-code failure evidence

New tests were written first. Against baseline `4e2c0513`:

- 12 new tests failed because the old constructor had no guard API
  (`TypeError: unexpected keyword argument 'first_transfer_guard'`), and the
  no-proof case reproduced the runtime error:

```text
OLD_CODE_FAILURE RuntimeError MM2S DMA is not idle
tx_written False
starts []
```

Saved at
`data/results/jj2026p-board-smoke-003-20261103d/offline_fix/old_code_runtime_error.txt`
and `.../old_code_tests.txt`.

## 4. Tests after the fix

`python -m pytest` on the full JPEG regression: **145 passed**

| Suite | Result |
|---|---|
| admission | 5 |
| display/HOLD | 13 |
| pre-load policy | 45 |
| transport integration | 4 |
| runtime/transport | 78 |

New transport tests cover:

- both channels and either single channel at first `status=0` with proof;
- normal second frame after completion without the exception;
- a new transport on a committed active transfer;
- partial submission anomalies (address-only and length-only, both channels);
- error status before the exception;
- missing/unowned/wrong guard proof;
- driver-started timeout HOLD, retry refusal, close refusal, no buffer free;
- no TX write, no RLE/JPEG start, no DMA transfer on every rejection.

The integration test exercises the production `build_transport_callback` guard
with the real `JpegRleDmaTransport` and shared MMIO state; it confirms the
guard accepts an unarmed started channel and rejects committed address/length.

Saved at `.../offline_fix/new_code_tests.txt` and `.../full_regression.txt`.

## 5. New independent package

Path: `C:/w/jj2026p/board-package-firstframe-fix-20261103`

The previous `C:/w/jj2026p/board-package` was not overwritten.

| Item | SHA-256 |
|---|---|
| BIT (unchanged) | `75577d743060a0908f5248e5a12062428bc1d6ad603c81fc5fdaedc9b15b230b` |
| HWH (unchanged) | `06cf10cafc545d930fa210cd8fdd88d09981d808cb1c4197146183f822901380` |
| Package manifest | `c86ea91cbfa133449eaff2254914a5b0cfcc8cbb3b14861eb955090dbf1dd35c` |
| Package SHA256SUMS | `9f8bb7a66bff864d5239a324777507a573bbcf3fe0437637346c115bb58f27b8` |
| Package README | `7164545865ceaf6b76b258e58c2ca3dfc3865c19169baa6a593474d07823cbf2` |
| `run_jpeg_counter.py` | `b7e51256a5ee6d0f456e495b0a9da382c4a3b80c2dd70c43234dea1d3841fa8b` |
| `jpeg_transport.py` | `0ad7c598d1f6e5748e4bfbe52ac9abb2b4f7960e7f4cc48bba76de89057cb13d` |
| `test_jpeg_transport.py` | `4b02f537e44849b29adb1bfc117b60d6a17ba56f1e0f645a17173589ec5626bb` |
| `test_jpeg_transport_integration.py` | `5cb692fbdb903d9d8b92e8c1e842af8fb3d0c17ff0dd078fe5c9f761313874ce` |

Package checks: `sha256sum -c SHA256SUMS` = 345/345 OK;
`verify_runtime_artifacts` = PASS; package regression = 145 passed; all four
changed sources are byte-identical to the worktree. The old package remains at
`3ca1049c...` / `b15215b4...` / `0c584199...` with unchanged BIT/HWH.

## 6. Board-side dma.py version/hash check

- The retained 2026-10-29 board probe contains
  `pynq/lib/video/dma.py` SHA-256
  `94346a4ae6a7a7f9e4b78887179e2a55ff821c7ed3724b584a7bb4c228fea30f`.
- It does **not** contain the generic `pynq/lib/dma.py` used by `_SDMAChannel`;
  that file's version/hash is **not verified from retained materials**. No board
  access was made to fill this gap.
- The fix does not rely on private driver fields (`_active_buffer`,
  `_first_transfer`). It relies on the driver's documented register effects
  (`transfer()` writes address/length) and verifies the hardware registers
  directly, so a different driver remains fail-closed: if the registers do not
  show the expected unarmed state, the exception is denied.

## 7. Remaining risks

1. **Concurrent owner race**: another process could construct `BaseOverlay`/arm
   the DMA between the guard proof and the first transfer. Mitigations: the
   runtime's process-ownership preflight, the guard's hardware proof,
   `dma_may_be_active` set before arming, single-threaded execution, and no
   retry. The window is small but not zero.
2. **Address-zero assumption**: the exception requires address `0`. A valid
   transfer to physical address 0 would be indistinguishable, but the
   transport's CMA buffers are nonzero and an in-progress PYNQ transfer writes
   a nonzero address. Address 0 is not a valid transport buffer.
3. **Guard is optional**: a non-production caller that omits it keeps the old
   fail-closed behavior. Production `build_transport_callback` always supplies
   it.
4. **No board validation of this fix**: the HOLD is untouched; a reboot/rerun
   requires separate authorization.
5. The generic `pynq/lib/dma.py` hash is unverified (section 6).

## 8. Documentation corrections

- Removed the absolute “only component” wording in
  `c_candidate_smoke003_trace_dma_stop_plan_20261103.md`; the historical caller
  remains unknown and only the PYNQ driver mechanism is verified.
- The trace transfer wording is “no transfer armed at sampling”, not “never
  transferred”; an earlier completed transfer cannot be excluded.
- MM2S/S2MM control/status/address/length offsets are distinguished in the
  run report.
- “Display start call returned” is used instead of implying a validated
  physical picture.

## 9. Audit stop point

This report and package are delivered for Codex audit. No board access, no
retry, no reset/reload/reboot, no push/merge was performed. The next action
after approval is a separately authorized reboot and one smoke-003 rerun with
this package.