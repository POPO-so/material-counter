# WO-0016 Stage B Phase 1.5 Readiness

**Result:** Offline preparation is complete. Board Stage 1 and the three-frame smoke test remain **NOT RUN** because the authorized PYNQ runtime could not be initialized safely. Keep the 24 V light and conveyor off.

## Host and offline evidence

- Worktree: branch codex/wo0016-stageb-phase1-5, base 59268209148888c135ff7ff5c0aa7dba8ddbaf66; changes are isolated in the managed worktree.
- git diff --check: PASS.
- Reconstructed data/results/wo0014-offline-20261014/vectors/manifest.json with the historical deterministic generator. The original file was absent from Git history and the cold archive; this is a reproducible reconstruction, not recovery of the original bytes.
- Manifest: 192,452 bytes; SHA-256 8d123469853ad2425e03d91590aa7473de4ca72a04fffaa6bc2d8964b377317d. All 487 source PNGs passed hash, 320×240 geometry, and grayscale L mode checks.
- Offline tests: python -m pytest -q -p no:cacheprovider scripts/wo0015 scripts/wo0016 → **276 passed, 1 skipped** (22.39 s).
- Stage B calibration/batch analysis is connected in the board runner and finite MJPEG preview path. V4L2 open/config/readback/read/release, streaming PYNQ DMA start/wait, cache-sync, arm, and status/readback, each CMA freebuffer call, and post-release CMA telemetry run in daemon workers with a maximum 5-second per-call deadline. A DMA timeout marks ownership uncertain; an indeterminate free marks release uncertain. Both enter fail-hold without retry, retaining references. The MJPEG encoder close wait is capped at 0.25 seconds, with regression coverage for stuck encoder, DMA-start/MMIO, and buffer-free calls. The runner records same-frame grayscale/Binary8 analysis, ROI and threshold telemetry, foreground and component counts, count/FPS, and DMA status; the batch JSON records per-frame data.
- Final offline package: `board-runtime-final/` and `board-runtime-final.zip`. Archive SHA-256 `7beefbe5f9a0e4be1206019b66f09c33b78090a37e9025f73c2ca14a35ae2573`; package-manifest SHA-256 `6e3c6334bc95efcdcc4bac7490aca57b80125bfce2ec48f1f890bdc138b5fd7b`; Stage B runtime manifest SHA-256 `1ced1df436a387ebe7315582be534ed3fd453839695503d63e10e22eae89bf2f`; WO-0015 manifest SHA-256 `14d0f681adb5b77e15e90741765aaf5a3713a28dfca49cca4ca48cd72644392f`.
- The package contains the authorized BIT/HWH hashes: BIT `a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420`; HWH `bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6`. All 21 manifest-listed files plus `package-manifest.json` (22 ZIP entries total) matched the archive and manifest checks; packaged runner SHA-256 `83b286926749af617e5b289392a58003c8d1a1db6671f0c62a546bb81ddf02fa` exactly matches the worktree source. Package preparation accessed no board.
- Earlier package attempts are retained only under names containing superseded or failed-build-retained; use only board-runtime-final.zip for any later review.

## Authorized boot read-only checks

| Gate | Result |
|---|---|
| Target / authorized boot | 192.168.2.99 / 58e6af6c-6660-4d44-b0c5-e18f4a564202 matched |
| CMA watermark | Latest observed CmaFree=42,828 kB, above the 32,768 KiB threshold. Earlier reads were higher; the decrease is telemetry only and is not attributed to a cause. |
| BIT/HWH file hashes | Both matched the authorized values above |
| FPGA Manager | Read-only status was operating; no Overlay load was performed |
| Camera format/settings | Via sudo -n v4l2-ctl: /dev/video0, 640×400 MJPG, exposure 50, power-line frequency 1, gamma 100 |
| ov9281-init.service | inactive |
| Existing Python/DMA activity | No Stage B or DMA process was observed; expected system Python services, including Jupyter, were present |
| PYNQ runtime / device discovery | **BLOCKED.** A read-only import from the local /home/xilinx/pynq tree reported “No Devices Found.” With XRT sourced, a subsequent import exceeded its short timeout; a second bounded probe from / ended with the timeout tool reporting a core dump. Follow-up found no core file or remaining probe process. No MMIO/register writes were made. |

## Stage 1 / smoke / preview disposition

- No new board-side run manifest or one-shot guard was issued or consumed. The local offline package manifest is not a deployed board receipt.
- No current-boot overlay-receipt.json was produced. No Stage 1 Overlay load was attempted.
- CMA physical buffer allocations at MM2S 0x15880000 and S2MM 0x15860000 were not made or read back; physical address gate remains unverified on hardware.
- Initial or per-frame DMASR was not read. The three-frame V4L2→MM2S→PL→S2MM smoke test was **NOT RUN**; no DMA PASS is claimed.
- Five-second dark-field/background sampling was **NOT RUN**. Foreground false-trigger count and measured background mean are unavailable.
- Prepared preview URL: http://192.168.2.99:8080/. **Inactive:** the finite preview server starts only as part of an authorized board run, which did not start.
- Do not turn on the 24 V light or start the conveyor yet. That request is deferred until Stage 1 and the three-frame smoke both pass; then pause for physical confirmation before the five-second empty-belt sample.

## Next gate

Resolve the board-side PYNQ/XRT runtime discovery failure and the inactive camera initialization service, then repeat the read-only preflight against the same still-authorized boot. If the boot changes, obtain a fresh boot-bound authorization before any hardware execution.
