# WO-0016 Offline/Mock Verification

**State:** Offline/Mock implementation verified. **Board Stage A Attempt 5 completed 30/30 frames at 11.409 FPS, below the 15 FPS target; physical counting accuracy remains unverified.**

## Scope and environment

- Worktree branch: `codex/wo0016-counting-20261017`
- Base commit: `e3f7d7ab9753b1f67263a81815b442cf79a768ac` (`feat/pl-stream-skeleton`)
- Attempt 5 software commit: `3a4349f8fde1946ae57bff9956842f2ee0640c58`
- Runtime: Python 3.12, NumPy 2.5.2, OpenCV 5.0.0, pytest 9.1.1
- Result JSON: [`mock-run.json`](mock-run.json), SHA-256 `e246cec8183d39c1cb0072c4ba1771c26d453a0a2b0637e468740ca56e1c3e17`
- The host test run uses Mock boundaries and does not import PYNQ. Board Stage A was a separate, explicitly authorized operation.

## Verification commands and results

| Command | Result |
|---|---|
| `py -3.12 -m pytest -q scripts/wo0016 scripts/wo0015` | 228 passed, 1 skipped in 19.06 s |
| `C:\fpga\material-counter\.venv\Scripts\python.exe scripts/wo0016/run_stream_counter.py --mode mock --mock-frames 8` | `MOCK_PASS`; 8 frames, 2 predicted/2 synthetic ground-truth objects, 0 count error, 100% synthetic score. |

The saved Mock result reports 15.8176 frames/s for this host run, including local preview server startup and JPEG rendering. It is not camera, conveyor, or FPGA performance; `hardware_fps` is null. The scene contains two synthetic moving 7×7 components and a 9-pixel noise blob below the configured area threshold.

Additional checks: board mode without opt-in is rejected before PYNQ import; simulated DMA ownership, uncertain-buffer release gating, camera warm-up/timing boundaries, 60 FPS negotiation, nearest-neighbor channel extraction, preview interval/quality, per-frame counting, exact AFI1 readback gates, and preference for the isolated WO-0015 helper bundle are covered by offline tests. `git diff --check` returned no whitespace errors. A host-only image conversion microbenchmark measured 2.254 ms for BGR-to-gray plus bilinear resize and 0.082 ms for first-channel plus nearest-neighbor resize; this is not Cortex-A9 evidence.

## Source SHA-256

| File | SHA-256 |
|---|---|
| `scripts/wo0016/stream_counter.py` | `a176154dacda6b2059b71c374f0377c979b8293eecec469ef1b0c2a5d0e8c547` |
| `scripts/wo0016/mjpeg_preview.py` | `d8136bc34d360837e954efbd9a2813f9f905cbff76b78bccd927cc4ab76d112` |
| `scripts/wo0016/run_stream_counter.py` | `ac8680972c672959dcae9cdeb3f0adc95ec19ea9ea449a72f4bb7aa108da0b50` |
| `scripts/wo0016/test_stream_counter.py` | `8f195d5af022850a43f9fdb1c81d185143aee54ebe3c98b1fd4031ff92bb58cc` |
| `scripts/wo0016/test_mjpeg_preview.py` | `c12e55e2de98aa4e1fbc7d407d727a208e6586e1bde10d970009f8f3d441f3c6` |
| `scripts/wo0016/test_run_stream_counter.py` | `3bf743ab76edc65b53765e02ba7221448916822d783029e70bf1d7c2a675880a` |

## Acceptance boundary

The separate Stage A Attempt 5 JSON is available at [`stage-a-30frames-attempt5-result.json`](../wo0016-stage-a-attempt5-20261018/stage-a-30frames-attempt5-result.json), SHA-256 `46006ef24ae9dc0e31b97ee5f017bec18e65f128920aa6de9c452815f1f49244`. It records `BOARD_STREAM_COMPLETE`, 30/30 frames, 11.4089 FPS, `avg_capture_ms=38.231`, `avg_packer_check_ms=0.535`, `avg_roi_count_ms=51.054`, `avg_mjpeg_encode_ms=49.696` (8 encoded samples), and `avg_postprocess_ms=68.735`. Both DMA status registers ended at `0x00001002` and buffers were released. The 15 FPS threshold was not met. No ground-truth items were presented, so accuracy is unknown; conveyor speed and M6 nut/washer acceptance remain `NOT RUN`. See the [board acceptance record](../../docs/verification/wo-0016-board-acceptance.md) for the full preflight, guard, and post-run evidence.
