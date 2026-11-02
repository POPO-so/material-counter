# C candidate — first-frame exception strict-zero revision (offline)

Date: 2026-11-03
Worktree: `C:/fpga/wt/claude-jpeg-20261102`
Baseline: `53749e47`
Scope: offline code/test revision and a new independent package. No board
access, no contact with HOLD PID 17659 or its buffers, no reset/reload/retry/
reboot, no RTL/BIT/HWH/constraint or startup-bitstream change, no push/merge.

Status: **offline revision awaiting Codex audit**; `count_accuracy` remains
`NOT_MEASURED`.

## 1. Audit finding and fix

Codex identified that `_require_unsubmitted_first_transfer()` returned early
when the guard status became `Idle`/`Halted`:

```python
if status & (DMA_IDLE | DMA_HALTED):
    return
```

That let a status transition during the proof bypass the address/length and
strict-zero conditions. The revision requires both the outer preflight status
and the guard status to be **strictly `0x00000000`**:

- `observed_status != 0` is rejected before the guard;
- a guard status that differs from the outer status is rejected as
  `state changed during first-transfer proof`;
- a guard status that is nonzero is rejected as
  `first-transfer status is not zero`;
- the early Idle/Halted return is removed.

The ordinary `_preflight` path for a normal Idle/Halted channel is unchanged,
and the second frame after completion still uses that path. RS/Reset,
error/reserved, address/length, ownership, `_submitted_frames`, HOLD and
no-retry/no-free protections are unchanged.

## 2. Tests

New tests use the **production guard** from
`scripts/experiments/run_jpeg_counter.py::build_transport_callback` and the real
`JpegRleDmaTransport.transfer()` entry, not a substitute guard or the private
helper:

- first frame accepted, second frame accepted after normal completion;
- nonzero outer status rejected before TX write/pipeline/DMA start;
- status changing to `0x1` (Halted) or `0x2` (Idle) during the proof rejected;
- `DMACR.RS=0` and `DMACR.Reset=1` rejected;
- all rejection cases assert no TX buffer write, no RLE/JPEG start, and no
  DMA transfer.

### Old code (53749e47) failure evidence

`data/results/jj2026p-board-smoke-003-20261103d/revision_strict_zero/old_53749e47_integration_output.txt`:

```text
3 failed, 7 passed
FAILED ... test_production_guard_transfer_rejects_nonzero_outer_status
FAILED ... test_production_guard_transfer_rejects_state_change_during_proof[1]
FAILED ... test_production_guard_transfer_rejects_state_change_during_proof[2]
```

The failures are the exact bypass: nonzero status and Idle/Halted during the
proof were accepted by the old code. RS/Reset and normal first/second-frame
tests pass on both revisions (they are regression coverage).

### Current tree

- Integration: **10 passed**, EXIT=0
  (`revision_strict_zero/new_integration_output.txt`).
- Full JPEG regression: **151 passed**, EXIT=0
  (`revision_strict_zero/new_full_regression_output.txt`).

| Suite | Result |
|---|---|
| admission | 5 |
| display/HOLD | 13 |
| pre-load policy | 45 |
| transport integration | 10 |
| runtime/transport | 78 |
| **total** | **151** |

Remaining gap: a first `transfer()` that itself raises (e.g.
`recv_channel.transfer` raises) still has no dedicated test. The earlier gaps
"state change during proof" and "Reset/RS" are now covered by the new tests.

## 3. New package

Path: `C:/w/jj2026p/board-package-firstframe-fix-20261103b`
(the previous `board-package` and `board-package-firstframe-fix-20261103`
are preserved).

| Item | SHA-256 |
|---|---|
| Package manifest | `851fe94631fccce95a619fc05e6b9b07979eb8347ef55cf76ca7ddf312cf35b9` |
| Package SHA256SUMS | `58e06f0e719793c68aa6b65fcf10eace822647437833c4b5af94b09c16654f44` |
| Package README | `5a5fdabdc80484efa7e0595d69327d1d16281dd01cf4016da4bd4d05d27b6028` |
| BIT (unchanged) | `75577d743060a0908f5248e5a12062428bc1d6ad603c81fc5fdaedc9b15b230b` |
| HWH (unchanged) | `06cf10cafc545d930fa210cd8fdd88d09981d808cb1c4197146183f822901380` |
| `jpeg_transport.py` | `bbc29d27f04fc9128cb7bc90a98118623d89fbcb14f15d1d1a60bb2e6a9d23ea` |
| `test_jpeg_transport_integration.py` | `4e5245c597bd05c091af578031e550146f3083fabadf5c5ef45eec60a2de583d` |

`package_root` in the revision manifest is corrected to
`C:/w/jj2026p/board-package-firstframe-fix-20261103b` (the previous package
still carried the old `board-package` root).

Verification:
- `sha256sum -c SHA256SUMS` = 345/345 OK;
- manifest source-hash check = no mismatches;
- package regression = 151 passed;
- BIT/HWH full SHAs unchanged;
- worktree/package source hashes match for both changed files.

The old package hashes remain unchanged; no failure evidence was overwritten.
No board access was made for this revision.

## 4. State

**Offline revision awaiting Codex audit.** No board access, no HOLD contact,
no kill/reset/reload/retry/reboot, no hardware artifact change, no push/merge.
`count_accuracy` remains `NOT_MEASURED`.