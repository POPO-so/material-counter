# Phase 1.6 PYNQ discovery retry

## Disposition

**BLOCKED before Stage B guard consumption.** The optimized read-only probe checked the FPGA Manager sysfs state and skipped `pynq.Device.devices`, but PYNQ's explicit `EmbeddedDevice` initialization did not return. The Python 10.5-second alarm did not interrupt the blocked native call; the outer 12-second timeout terminated the probe with exit code `124`.

The 3-frame static dark-field smoke test was **NOT RUN**. No Stage B guard was consumed, DMA was not started, no frame completed, and no `DMASR` was sampled. The 8080 preview service was **NOT STARTED**.

## Board state at the final read-only check

- Boot ID: `58e6af6c-6660-4d44-b0c5-e18f4a564202` (authorized ID unchanged).
- `CmaFree`: `45,192 KiB`, above the `32,768 KiB` gate. Samples changed during the session; the cause of the change is unknown.
- FPGA Manager: `operating`, flags `0`.
- Same-boot WO-0015 overlay pass receipt: present; WO-0015 terminal-failure marker: absent.
- WO-0016 smoke3 and batch guards: absent.
- No Python/DMA/preview process found; `/dev/video0` had no owner; TCP 8080 had no listener.
- No light or conveyor controls were operated. Keep the 24 V light and conveyor off while this blocker remains.

## PYNQ discovery evidence

The deployed probe checked the legal FPGA Manager state, loaded PYNQ without touching the global `Device.devices` property, and attempted an explicit `EmbeddedDevice` setup. The PYNQ 3.1.1 source on the board shows that global enumeration itself calls `EmbeddedDevice()`; the explicit construction therefore still enters the board's blocking XRT initialization path. The 10.5-second catchable Python alarm did not return before the 12-second shell hard stop, and no load-duration JSON was produced. This does not establish a successful latency improvement.

The runner and device probe fail closed before guard consumption when PYNQ setup does not return. No retry, Overlay reload, DMA, or camera preview was performed after the timeout.

## Package and offline verification

- Deployed package: `/tmp/wo0016-phase16-20261019-device-discovery-105s`.
- Package ZIP SHA-256: `e32bedc7643821156f4da248dd5b84bbe0fddc522447e1f8ded2bf53e8f6458a`.
- Stage B manifest SHA-256: `b51aeee7fb97eb64c3ee40f53f6bc21a3968bfd623eb9765d893d873892820c4`.
- WO-0015 manifest SHA-256: `e07316b9398166c6fc7a459faaca271eaf32170c18b4deaa4c16208bcaa933f2`.
- BIT/HWH hashes still match the same-boot Stage 1 receipt; the fresh package references the exact previously loaded BIT/HWH paths.
- Offline regression: **302 passed, 1 skipped**.
- `git diff --check`, shell syntax check, and Python byte-compilation passed.

The primary checkout remains untouched; changes and evidence are in the isolated worktree. Before another board attempt, the native XRT/PYNQ initialization hang must be resolved. Do not open the light or start the conveyor until a later authorized run completes all three frames and the preview reports ready.
