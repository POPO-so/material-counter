# C candidate — KNOWN_BASE pre-load policy (offline)

Date: 2026-11-03
Worktree: `C:/fpga/wt/claude-jpeg-20261102`
Scope: offline policy extension, tests, and repackaging only. No board access,
upload, reboot, candidate load, or smoke this round. Reviewed RTL, BIT/HWH, and
constraints are unchanged.

## 1. Why

Smoke-003 stopped before any upload/load because the post-reboot PYNQ base
overlay (`base.bit`, SHA-256 `3239e9cc...`) was not in the known table:
classification UNKNOWN. That is a policy gap, not a candidate failure. This
round adds a content-pinned `KNOWN_BASE` branch.

## 2. Evidence used (already captured, not fetched this round)

From the read-only board probe on 2026-10-29
(`data/results/standalone-20261029-g0/board-probe-final.json`):

| Item | Value |
|---|---|
| `pynq/overlays/base/base.bit` SHA-256 | `3239e9cc37ee4993c25f02ae6c97da0b3372f0e722e267d10073e20adc5d6717` |
| same file SHA-1 | `39b937b490015ec0e1d29f18a9143292821f74ad` |
| `pynq/overlays/base/base.hwh` SHA-256 | `222898ef2c0ccc4a0c47608e99d8dc7c9c6cdfed5130964884ff35005827ef3a` |
| Confirmed base bus master | `video_axi_vdma` (`xilinx.com:ip:axi_vdma:6.3`) at `0x43000000` (`0x4300FFFF`) |
| Other confirmed video IP | VTC `0x43C20000`/`0x43C30000`, HLS controls `0x43C40000`..`0x43C70000`, GPIO `0x41220000`/`0x41230000` |

The smoke-003 precheck re-confirmed the same base.bit SHA-1/SHA-256, PYNQ
`GLOBAL_STATE_PRESENT`, `shutdown_ips={}`, FPGA manager `operating`/flags `0`,
boot `base.bin`, and no owner.

The earlier first-smoke external abort at candidate `0x40400004` while the base
design was loaded is recorded evidence that the base design does not map the
candidate DMA address; it is never assumed on the base path.

Missing offline material (declared, not fetched): the full `base.hwh` XML is not
present on the host; only its SHA-256 and the parsed video/buttons IP subset
from the 2026-10-29 probe are available. Therefore the runtime compares the
*complete* DMA bus-master map parsed from the hash-pinned HWH against the pinned
set. If the actual HWH contains an additional DMA-capable master (for example a
general `axi_dma`) or differs in any way, the run stops as `PreloadUnsafe` with
no MMIO and no load. This is fail-closed and does not probe unconfirmed
addresses on the board.

## 3. Changes

`scripts/experiments/run_jpeg_counter.py`:

1. `KNOWN_BASE` classification:
   - entered only from `GLOBAL_STATE_PRESENT` whose state SHA-256 matches the
     pinned base entry;
   - the **actual file content** is re-hashed (SHA-1 and SHA-256) and must agree
     with both the state-recorded SHA-1 and the pinned pair; a matching path,
     `base.bit` name, or `active_name` is not identity;
   - FPGA manager `operating`/flags `0`, boot bitstream `base.bin`, empty
     `shutdown_ips`, and no FPGA owner are still required;
   - a missing/different file, hash, boot, shutdown, or owner state is UNKNOWN.
2. `KNOWN_BASE` pre-load probe:
   - verifies the adjacent `base.hwh` SHA-256 against `222898ef...`;
   - parses its complete DMA bus-master map and requires it to equal the
     captured map (`video_axi_vdma` at `0x43000000`);
   - reads only the confirmed VDMA status registers (`0x43000004`,
     `0x43000034`); both must be halted+idle and error-free;
   - an active/faulty VDMA or any hash/map mismatch raises `PreloadUnsafe`
     before `Overlay.download`; nothing is reset, freed, reloaded, or retried;
   - candidate addresses `0x40400000`, `0x43C20000`, `0x43C50000`,
     `0x43C80000` are not touched before load.
3. `run_candidate_sequence` accepts `KNOWN_BASE` and stages
   `known_base_probe` / `known_base_probe_done` before `overlay_download`.
4. CMA evidence and budget:
   - `/proc/meminfo` CmaTotal/CmaFree is recorded before the run and again after;
   - budget = `JPEG_MAX_BYTES + RX_CAPACITY` + 4096 KiB display/allocator
     reserve; insufficient free CMA stops before the load;
   - one reading is not treated as a leak signal; no automatic reboot/retry.

Unchanged: COLD_BASE, KNOWN_OLD, UNKNOWN, candidate post-load identity/HWH
contract, candidate DMA idle check, display bring-up, transport factory,
FAIL_HOLD lifecycle, stage persistence, BIT/HWH, RTL, constraints.

## 4. Tests

`python -m pytest` on the full JPEG regression: **110 passed**

| Suite | Result |
|---|---|
| admission (`test_run_jpeg_counter.py`) | 5 passed |
| display/HOLD (`test_jpeg_display_and_hold.py`) | 13 passed |
| pre-load policy (`test_jpeg_preload_policy.py`) | 26 passed |
| transport integration (`test_jpeg_transport_integration.py`) | 3 passed |
| runtime/transport (`scripts/rle/...`) | 63 passed |

The 26 pre-load tests cover:

1. exact content-pinned known base is accepted;
2. same path with a different state hash is UNKNOWN;
3. actual base.bit content differs from the pinned SHA-256 → UNKNOWN;
4. state SHA-1 and actual SHA-1 disagree → UNKNOWN;
5. missing/unreadable base.bit → UNKNOWN;
6. non-`base.bin` boot or non-empty `shutdown_ips` → UNKNOWN;
7. owner present → UNKNOWN;
8. KNOWN_BASE pre-load MMIO is exactly `0x43000000`; no candidate address;
9. active/faulty base VDMA → `PreloadUnsafe`, no `Overlay.download`;
10. HWH evidence failure → no MMIO, no `Overlay.download`;
11. HWH parser accepts the pinned map and rejects an additional `axi_dma`;
12. HWH hash mismatch is refused;
13. CMA budget covers explicit buffers plus reserve;
14. cold-base and known-old regressions still pass.

The same 26 tests pass against the packaged copy
(`board-package/software/...`), with `PYTHONDONTWRITEBYTECODE=1`.

## 5. New package

| Item | SHA-256 |
|---|---|
| BIT (unchanged) | `75577d743060a0908f5248e5a12062428bc1d6ad603c81fc5fdaedc9b15b230b` |
| HWH (unchanged) | `06cf10cafc545d930fa210cd8fdd88d09981d808cb1c4197146183f822901380` |
| Package manifest | `498f22c36f5e87a899e504e923038eb1748a41580355d52922af430994f1e7bb` |
| Package SHA256SUMS | `ed299851008200f1d18e2e2871562bd2ccc663332a05574003e3abdc3d03761d` |
| Package README | `bee00f86dac14a446b12f82a8431340b4b12444cf52d03e0863e95a4a042d4c5` |
| `run_jpeg_counter.py` | `bd389e04bfcad9509c3a09cfd4df3fad9db250d6fd4dc0466057bfaa17b5067c` |
| pre-load test | `8e6d1ac40579bac356f22e37563b99ed4955db0d1b4e0761f945beceb0f8864d` |

`sha256sum -c SHA256SUMS` = 345/345 OK; package `verify_runtime_artifacts` =
PASS; BIT/HWH full SHAs unchanged.

## 6. Pending smoke-003 command (not executed)

Board is safely in the PYNQ base overlay; no HOLD and no candidate load. This
round did not access it. After separate authorization, upload to a fresh
directory (do not reuse `jpeg-candidate-20261103a/b`):

```bash
# Windows host
scp -r C:/w/jj2026p/board-package xilinx@192.168.2.99:/home/xilinx/jpeg-candidate-20261103c

# Board
cd /home/xilinx/jpeg-candidate-20261103c
sha256sum -c SHA256SUMS
test "$(sha256sum SHA256SUMS | cut -d' ' -f1)" = "ed299851008200f1d18e2e2871562bd2ccc663332a05574003e3abdc3d03761d"
test "$(sha256sum artifact-manifest.json | cut -d' ' -f1)" = "498f22c36f5e87a899e504e923038eb1748a41580355d52922af430994f1e7bb"
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

Before launch, verify report/stage/hold/console paths do not exist, redirect
stdout/stderr, set stdin to `/dev/null`, start in the background, and record the
real Python PID.

Expected KNOWN_BASE evidence if the current PYNQ base is unchanged: stage order
`preflight` → `known_base_probe` → `known_base_probe_done` →
`overlay_download`; pre-load MMIO only `0x43000000`; base VDMA `mm2s/s2mm =
0x3`; actual base.bit SHA-1/SHA-256 and HWH SHA-256 in the report; CMA
before/after snapshots. If the HWH map, hashes, or VDMA state differ, the run
stops as `PreloadUnsafe` without loading and without touching a candidate
address.

## 7. Status and limitations

- smoke-003 remains **"preload blocked, not started"**; it is not a candidate
  run failure or success.
- `count_accuracy` remains `NOT_MEASURED`; no physical HDMI or accuracy claim.
- Only the confirmed base VDMA is checked; no other base address is probed. The
  full base HWH is not available offline, so an unexpected additional DMA
  master in the installed HWH will stop the run for review instead of being
  probed blindly.
- No board access, upload, reboot, load, smoke, deployment, push, or merge this
  round. No PYNQ state was hand-edited.