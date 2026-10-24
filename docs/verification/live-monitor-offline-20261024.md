# Live Monitor — host verification and remaining board gates

Date: 2026-10-24, Asia/Shanghai. Environment: Windows, repository `.venv` Python 3.12, NumPy/OpenCV, Microsoft Edge with Playwright. No board connection, camera operation, PYNQ import, MMIO/DMA or conveyor operation was performed.

## Implemented boundary

`scripts/wo0016/run_live_monitor.py` provides persistent `mock` and independently admitted `board` modes. Mock produces moving synthetic nut masks across X=160 and uses the real counter and JPEG renderer. Board mode reuses the existing camera prefetch and AXI DMA chain with current-boot manifest/Stage 1 receipt verification, ownership/signature/AFI/CMA/address checks and an exclusive live lock. It does not consume formal permits or historical calibration locks.

Live results/timing/error histories are bounded; completed tracks are discarded. No raw-frame archive or CaptureWriter is created. Reset applies a display offset at a frame boundary, preserves in-flight tracking and keeps new boundary-frame crossing events. HTTP reset/view/stop, Host/Origin restrictions, preview generation invalidation, current RSS and cached DMA/CMA health are implemented. Normal stop drains and frees only proven-idle DMA buffers. Uncertain ownership enters existing FAIL_HOLD.

## Host evidence

- New regression tests exercise 3,500 synthetic frames with a 500-frame warmup, require current traced-allocation growth below 256 KiB, and assert bounded retained state. This is a Python-allocation regression, not an RSS/CMA proof.
- Actual Edge tests cover the responsive mock dashboard, synthetic labels, dynamic MJPEG, count reset, Binary8 view, health display and stop. Existing dashboard browser tests also run. A legacy-only first-part duplicate is used to prime Chromium's single-frame decoder; unique sequence counting prevents transport priming from inflating measured delivery FPS. Live-control streams do not replay that priming frame.
- Injected hardware tests cover admission ordering/rejection, four allocations and single release, real safe-release logic, timeout/source closure and buffer quarantine/FAIL_HOLD. These are host tests with fake devices, not a physical DMA result.
- Windows RSS sampling caches its ctypes structure/API once; a regression ensures repeated sampling does not grow ctypes' pointer-type cache.

The preliminary 30-second mock run processed 734 frames. Its final sliding processing rate was 23.9485 FPS and preview publication rate 24.0522 FPS. RSS was 52,660 KiB at the first sample, 62,944 KiB at the ten-second sample, and 63,360 KiB at the last sample (+416 KiB after that warmup point). It completed one reset and a view switch, then stopped with both threads ended, the frame hub closed and no driver errors. The synthetic raw total was 7; it is not a physical accuracy measurement. The source files were hash-checked unchanged during this run. It ran before the feature commit, so its parent Git HEAD is not the implementation version; the six recorded file hashes bind that preliminary evidence.

Preliminary evidence is local at `C:/wt/live-monitor-20261024/mock-soak-precommit.json` and `.log`. Final fixed-commit pytest, root-collection diagnostics, delivery verification and a repeated mock run are recorded by path and SHA-256 in the append-only [benchmark ledger](../benchmarks/optimization_ledger.md). Logs are retained locally and are not synchronized by Git. The ledger contains the final tested commit and counts; this report does not backfill newer tests into earlier runs.

## Limits and acceptance gates

Board FPS, physical accuracy and CMA before/allocation/release are N/A. Multi-hour mock and board soaks remain NOT_VERIFIED. The short RSS observation does not demonstrate absence of a native allocator leak, camera-driver leak or CMA leak. Board execution must be separately authorized and use freshly reviewed current-boot artifacts; historical locks/permits are not reusable.

Root-level pytest collection includes historical copies under `data/results/` and `output/` and encounters duplicate-module/import errors. Maintained source regression is run explicitly as `python -m pytest scripts python`; it must not be described as root-level or whole-repository green. Platform-only and unavailable historical-fixture skips are enumerated with final JUnit evidence in the ledger test scope.

The current Windows port 8080 was already occupied during implementation. Tests and the short mock run used ephemeral loopback ports and did not stop the existing listener. User commands and hardware prerequisites are in [the runtime README](../../scripts/wo0016/README.md).
