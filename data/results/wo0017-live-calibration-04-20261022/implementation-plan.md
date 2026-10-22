# Live Calibration Implementation Plan

**Goal:** Run attempt -04 with fast monochrome preprocessing, glare rejection and a live optical HUD.

**Architecture:** Reuse the formal counter's first-channel nearest resize and line x=160. Keep calibration metrics and bounded V4L2 controls in a camera-only module; formal sampling retains its strict profile checks. Extend the existing MJPEG publisher with optional calibration snapshots.

**Tech Stack:** Python, NumPy, OpenCV, pytest, V4L2, SSH.

1. Add failing tests for channel views, reusable Gray8/Binary8 buffers, exact ROI metrics, glare exclusion and calibration-only control validation; run targeted pytest.
2. Implement `scripts/wo0016/live_calibration.py` and integrate `camera_only_preview.py`; use `cv2.INTER_NEAREST`, preallocated resize/threshold destinations and x=160 / [145,175].
3. Worker updates `stream_counter.py` and its tests with component upper bounds and per-frame rejection diagnostics. Manager reviews diff and integration.
4. Extend `mjpeg_preview.py` with optional metrics, dashed rejected contours, live state endpoint and loopback-only JSON controls/reset button. Test state, control denial and HUD rendering.
5. Run repository source pytest (exclude sealed result copies), record deterministic old/new preprocessing benchmark and review diff.
6. Verify current boot, camera profile, CMA, process/port availability; back up remote app and verify deployed hashes. Launch exactly one -04 camera-only attempt with read-only DMA gate at 0x40400000 offsets 0x04/0x34, no DMA/Overlay/conveyor action.
7. Keep preview running; collect state, JPEG, delivered FPS and board preprocessing timing in a new evidence directory; verify main unchanged.
