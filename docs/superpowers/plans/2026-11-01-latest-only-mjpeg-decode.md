# Latest-Only MJPEG Decode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decode only the latest camera MJPEG frame selected by the processing consumer, reducing JPEG work for frames overwritten before use.

**Architecture:** Keep camera capture continuously reading MJPEG into a single latest-raw-frame slot. Move Gray8 decoding into `LatestCamera.read(after=...)`, snapshotting sequence, bytes, and capture timestamp under the condition lock and decoding outside it. Cache the last decoded Gray8 frame so repeated reads do not decode the same packet again. Preserve the existing decoded `camera_frames()` iterator as a wrapper for callers that need decoded frames.

**Tech Stack:** Python, OpenCV, NumPy, pytest, threading conditions.

**Spec:** `docs/superpowers/specs/2026-11-01-latest-only-mjpeg-decode-design.md`

## Global Constraints

- Do not change camera FPS or V4L2 mode.
- Do not change RLE, counting, display, or PL interfaces.
- Preserve the existing `(sequence, gray8, timestamp)` result from `LatestCamera.read()`.
- Preserve the deadline set at `read()` entry across synchronous decode. If decode finishes after it, cache the frame and raise a `TimeoutError` subtype carrying it; the integrated runner consumes that frame, while later reads may return the cache immediately. Genuine no-frame timeouts keep triggering recovery.
- Preserve JPEG SOI and 640×400 Gray8 validation and explicit capture/decode errors.
- Keep memory bounded to the latest compressed packet, one cached decoded Gray8 frame, and any packet held by an active decode.
- Do not claim runtime CPU/FPS improvement without comparable board measurements.

## Review Focus

- Multiple frames arrive before a read: return the newest captured sequence and decode only that packet once. Test: `test_latest_camera_decodes_only_newest_compressed_packet_once`.
- The newest-frame test waits on a producer event confirming all three packets were published before calling `read()`.
- Capture advances during decode or a repeated read sees no new capture: keep the selected packet stable, preserve capture sequence/timestamp, and reuse cached Gray8 without decoding again. Tests: `test_latest_camera_keeps_capture_sequence_and_timestamp_when_latest_advances`, `test_latest_camera_reuses_cached_decode_without_new_capture`.
- Decode exceeds the call's original timeout: raise `TimeoutError` while retaining the decoded tuple for an immediate cached retry. Test: `test_latest_camera_times_out_when_decode_exceeds_read_deadline_and_caches_result`.
- Integrated caller unwraps only the late-decode timeout into its carried frame; a genuine no-frame timeout continues to recovery. Tests: `test_integrated_camera_consumes_late_decode_timeout_without_recovery`, `test_integrated_camera_read_preserves_genuine_no_frame_timeout`.
- Idle/exhausted capture source times out without a packet, decoder control-flow exceptions propagate after in-flight cleanup, and concurrent reads share one decode. Tests: `test_latest_camera_times_out_when_source_publishes_no_packet`, `test_latest_camera_clears_decode_state_and_reraises_control_flow_baseexception`, `test_latest_camera_deduplicates_concurrent_reads_for_selected_packet`.
- Invalid or non-JPEG data reaches the decoder: reject it with the existing explicit validation error. Test: retain and run `test_camera_mjpeg_decode_preserves_all_source_pixels` and invalid-input assertion.
- The camera source raises or produces no fresh frame: readers must retain explicit error and timeout behavior. Test: `test_camera_failure_and_timeout_are_explicit`.
- Close occurs while capture is blocked: wait for the reader to exit before returning. Test: `test_close_waits_for_blocking_camera_read_to_finish`.

---

### Task 1: Keep compressed frames until the consumer selects one — Complete

**Files:**
- Modify: `scripts/rle/run_counter.py` — add a raw MJPEG iterator and retain `camera_frames()` as its decoded compatibility wrapper.
- Modify: `scripts/standalone/camera.py` — store the latest raw packet, decode the selected packet once per new capture, and reuse the last decoded result.
- Modify: `scripts/standalone/test_camera.py` — prove latest-only decode, stable metadata, cache behavior, and existing lifecycle behavior.
- Modify: `scripts/rle/test_entrypoint.py` — ensure the decoded camera iterator and JPEG pixel validation remain intact.

**Interfaces:**
- Add `camera_mjpeg_frames(device, count)`, yielding each post-warmup raw MJPEG packet from the existing 640×400 V4L2 capture configuration.
- Keep `camera_frames(device, count)`, yielding Gray8 arrays by applying `decode_camera_frame()` to `camera_mjpeg_frames()`.
- Keep `LatestCamera.read(after=-1, timeout=3.0)` returning `(sequence, gray8, captured_monotonic)`; sequence numbers advance for each post-warmup raw packet captured, including packets overwritten before a read.

- [x] **Step 1: Write the failing camera tests.** Add:
  - `test_latest_camera_decodes_only_newest_compressed_packet_once`: deliver three distinct JPEG packets before reading; assert the newest sequence and pixels are returned and the decoder is called once for that packet.
  - `test_latest_camera_keeps_capture_sequence_and_timestamp_when_latest_advances`: block decoding after the read snapshots a packet, advance the source, then assert the in-flight result retains its original sequence and capture timestamp and the next read sees a newer sequence.
  - `test_latest_camera_reuses_cached_decode_without_new_capture`: repeat a read without a new source packet; assert the sequence/pixels are unchanged and decoder call count does not increase.
  - `test_camera_frames_decodes_raw_mjpeg_packets`: feed known JPEG packets through the decoded wrapper; assert Gray8 pixels equal `decode_camera_frame()`.
- [x] **Step 2: Run the focused tests to confirm the expected failures.** Run: `python -m pytest scripts/standalone/test_camera.py::test_latest_camera_decodes_only_newest_compressed_packet_once scripts/standalone/test_camera.py::test_latest_camera_keeps_capture_sequence_and_timestamp_when_latest_advances scripts/standalone/test_camera.py::test_latest_camera_reuses_cached_decode_without_new_capture scripts/rle/test_entrypoint.py::test_camera_frames_decodes_raw_mjpeg_packets -q`. Expected: FAIL because the current camera producer publishes decoded frames rather than retaining raw JPEG packets for consumer selection.
- [x] **Step 3: Add the raw MJPEG iterator and preserve the decoded wrapper.** Implement `camera_mjpeg_frames(device, count)` with the current format, dimensions, FPS request, buffer size, two-frame warmup, read failure handling, and capture release. Implement `camera_frames(device, count)` as a generator over it that decodes every yielded packet for existing direct callers.
- [x] **Step 4: Make `LatestCamera` retain and decode only the selected raw packet.** Preserve capture-side sequence and monotonic timestamp when replacing the raw slot. In `read()`, prefer the latest raw packet newer than `after`; snapshot it under the condition lock, decode outside the lock, cache its decoded tuple, and return it. Reuse the cache if no newer raw packet exists. Preserve error propagation, timeout, and close semantics.
- [x] **Step 5: Run the focused regression tests.** This regression command is distinct from Step 2's four-test Red selector list:
  `C:/Users/14173/Documents/material-counter/.venv/Scripts/python.exe -m pytest scripts/standalone/test_camera.py scripts/rle/test_entrypoint.py -q`
  The initial implementation run passed 16 tests; after the timeout-contract follow-up, the same focused regression command passed 17 tests.
- [x] **Step 6: Review the diff and commit the task.** Task 1 is complete; commits are recorded in branch history.
- [x] **Step 7: Apply the timeout-contract follow-up.** Added deterministic producer synchronization and decode-deadline coverage; updated the approved design, plan, regenerated brief, and appended exact verification evidence to the task report.

## Expected Limits

Local tests establish the selected-frame decode behavior and compatibility contracts. They do not prove CPU reduction, end-to-end FPS, or counting accuracy on the board. Because decode moves into the consumer call, compare those runtime measures separately before claiming an overall performance improvement.
