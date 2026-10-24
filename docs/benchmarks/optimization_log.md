# Live Monitor optimization log

The append-only performance ledger mandated by this repository remains [optimization_ledger.md](optimization_ledger.md). This file records implementation details for the requested Live Monitor feature; it does not replace historical ledger rows.

## 2026-10-24 — independent continuous monitor

Added `scripts/wo0016/run_live_monitor.py` with persistent mock and board paths. The host path generates a moving synthetic nut, runs the actual X=160 tracker/counter and serves dynamic MJPEG and controls. Board execution retains manifest/current-boot/Stage 1 receipt and hardware admission checks and reuses the admitted OV9281 → MM2S → FPGA → S2MM Binary8 chain.

Memory is bounded by construction: four fixed CMA buffers, two cached CPU slots, latest preview snapshots, one retained FrameResult, capped timing/error samples and removal of finished tracks. Live mode never creates CaptureWriter or NPZ archives. The optional bounded settings leave finite evidence retention enabled by default in existing runners.

Count reset changes the display baseline at a completed-frame boundary while retaining active tracks; crossing events in the boundary frame remain counted. Preview generations are invalidated on reset/view changes. The no-auth monitor exposes same-origin reset/view/stop at 8080 without 8081 OTP; the old calibration endpoint retains its loopback restrictions.

Host checks include a 3,500-frame traced-allocation plateau regression (500 warmup + 3,000 measured frames, growth ceiling 256 KiB), real-browser UI interaction and injected DMA timeout/FAIL_HOLD checks. The traced-allocation check is not a whole-process RSS or CMA leak measurement. Fresh measured run details, tested SHA, pytest totals and evidence hashes are appended to the canonical ledger after fixed-source verification.

Board end-to-end FPS: N/A. Physical accuracy: N/A. Board CMA before/allocation/release: N/A. Multi-hour hardware soak: NOT_VERIFIED. No board was connected or operated for this implementation.
