# WO-0017 Live Calibration -04

Client verification date: 2026-10-22 (Asia/Shanghai). Worktree `C:\wt\w17`, branch `codex/wo0017-algo-accuracy`, base HEAD `ddefbe6c40e96763192c5154f52a1ecd4893d6e6`. Changes remain uncommitted. Local main and origin/main remain `1d32593610c2643b3f022ce048d31f70fd7a4417`; original checkout remains clean.

## Delivered behavior

- OV9281 preprocessing extracts channel zero as a NumPy view. Three strided copies perform the exact 640x400 to 320x240 nearest-neighbor sampling map, avoiding OpenCV's full-frame packing of a strided channel. Gray8 and threshold output buffers are reused. OpenCV nearest equivalence was checked on Windows and board OpenCV 4.5.4.
- Component upper bounds default to area 900, width 85 and height 85. Oversized components are removed before clustering/tracking, with per-frame rejection counts, areas and contours. An object physically connected to a rejected reflection is also rejected; this filter cannot recover its independent silhouette.
- Preview x=160 and hysteresis [145,175]; HUD and `/state.json` include BG Mean, ROI mean, Over-128 area/ratio, glare area, candidates/areas and rolling pipeline FPS. Rejected contours are dashed red. BG Mean estimates background using ROI pixels <=128; Over-128 includes nuts and glare. ROI is x=[0,320), y=[96,145).
- Optional `--calibration-mode` enables only exposure_time_absolute (50..10000) and brightness (-64..64). Local JSON API `/api/calibration` requires loopback and same-origin access. Reset restores exposure=50, brightness=0 with readback. Graceful/error exit restores standard controls; a killed process may not restore, so the formal camera wrapper independently rejects nonstandard controls before open and before yielding its first frame. Formal server has no control callback.
- Preview discards finished tracks, prunes trails and bounds timing history. Camera buffer size is 1. Image and mask snapshots are copied under the publisher lock before reuse.

## Verification

`C:\fpga\material-counter\.venv\Scripts\python.exe -m pytest python scripts -q --junitxml=data/results/wo0017-live-calibration-04-20261022/pytest.xml`

**662 passed, 1 skipped, 5 subtests passed**. `git diff --check` clean. H3 test assets missing from the isolated worktree were copied read-only from the original checkout: 487 PNG source/destination SHA-256 pairs matched. Source assets were not modified.

Windows offline synthetic benchmark: old median 0.23545 ms, new median 0.02610 ms (9.02x). Board offline synthetic benchmark: old 18.94838 ms, new 2.57642 ms (7.35x). Sampling microbenchmark measured 2.34383 ms for three-copy resize plus threshold; alternative gather/index methods were slower. These are separate from live camera evidence.

Live preview startup delivered 180 frames at **27.392 FPS**. A Windows HTTP client then received 180 JPEG frames in 5.85834 seconds at **30.5547 FPS**; pipeline FPS at that capture was 30.9648. Live preprocessing median varied around **2.57..2.75 ms**, down substantially from the historical 22.08 ms. **The <=2.5 ms live target is not yet met consistently.** The running -04 process was left intact rather than restarted.

## Board deployment and running preview

Runtime files were deployed to `/home/xilinx/wo0017/app/`; 13 source hashes matched (`deployment-files.json`, `remote-sha256.txt`). Prior app backup: `/home/xilinx/wo0017/app-before-calibration04`, with a separate SHA-256 inventory. Board offline synthetic compatibility passed before the camera attempt.

Exactly one launch marker: `/home/xilinx/wo0017/launch04.once`. Attempt directory: `/home/xilinx/wo0017/camera-preview-35983d66-bb1d-4a8a-85e5-802170825515-04`. Boot ID unchanged. Process PID 432 runs as UID/GID 1000, with supplementary groups removed. Its ability to use sudo -n for V4L2 was checked after identical privilege dropping; the reset API was exercised successfully after launch.

Read-only DMA gate: base 0x40400000, offsets 0x04/0x34, both DMASR=0x00000001. No DMA transfers, Overlay reload or formal sampling. CmaFree at ready was 36012 KiB, above the 32768 KiB guard. Service continues its CMA/boot guard while running.

Live view: <http://192.168.2.99:8080/>. Status: <http://192.168.2.99:8080/state.json>.

To use the local-only adjustment buttons from this PC, run:

```powershell
ssh -o BatchMode=yes -N -L 127.0.0.1:18080:127.0.0.1:8080 xilinx@192.168.2.99
```

Then open <http://127.0.0.1:18080/>. Keep the SSH command running. The attempt to create that tunnel automatically was rejected by automatic approval review with `blocked by policy`; no more specific reason was supplied. No automatic tunnel was created.

Latest observed optics: glare area 0, candidate blobs 0; BG Mean below 20, status `ADJUST BACKGROUND: TARGET BG 20-45`. This scene does not reproduce the earlier T22 reflection; glare rejection has synthetic tests, not new physical identity/accuracy acceptance. Adjust the actual light angle/brightness while observing the HUD.

Board wall-clock values in raw JSON report 2025-05-07 and are stale. They are preserved as recorded; this task did not set the clock. Boot identity and monotonic FPS measurements were used for verification. SSH briefly disconnected during offline checks, recovered on the same boot, and no camera attempt had been started during that interruption.

Artifacts: `attempt-04-screen.jpg`, `attempt-04-preview-state.json`, `attempt-04-final-state.json`, `attempt-04-client-measurement.json`, `attempt-04-api-check.json`, `attempt-04-control-reset-and-launch.txt`, `attempt-04-launch.log`, `pytest.xml`, offline benchmark and compatibility logs.
