# Low-Overhead Video Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dashboard's display-FPS/processing-delay chrome with measured camera-FPS, end-to-end delay, live-drop, resolution, stage, throughput, and PS feature-time telemetry while keeping the counting hot path low-overhead and unchanged.

**Architecture:** Add a small `FrameTelemetry` value object and a bounded `TelemetryWindow` aggregator. Pass capture and submit timestamps through existing `SharedFrames` metadata, aggregate only at the existing two-second sample point, and render the new top-level performance area plus a three-page detail view from the aggregated snapshot. PL module-level counters remain an optional `pl_counters.modules` extension with no RTL changes.

**Tech Stack:** Python 3.12, multiprocessing shared memory, NumPy, OpenCV, Pillow, pytest, existing PYNQ standalone pipeline.

**Spec:** `docs/superpowers/specs/2026-10-31-low-overhead-video-telemetry-design.md`

## Global Constraints

- Do not change counting algorithms, thresholds, region/tracking parameters, count results, control behavior, HDMI geometry, or frame-identity markers.
- Hot path: O(1) counters, fixed-length windows, and monotonic timestamp reads only. No per-frame JSON, SHA, logging, filesystem I/O, or MMIO polling.
- Aggregate and format only at the existing two-second sample point; keep `TelemetryCache.record(name, value, period_s, now)` and its stale rule unchanged.
- Camera FPS must be measured from camera-thread frame production, not `CAP_PROP_FPS`; requested 60 FPS is never reported as measured FPS.
- End-to-end delay means capture monotonic time to successful HDMI `writeframe` return. It is not screen scan completion.
- Missing or stale values render as unknown/`--`, never as zero. Missing PL module counters are unknown, not zero.
- Keep report schema `material-counter.integrated-run.v1`; add optional fields without deleting, renaming, or changing existing types, units, or meanings.
- Do not change RTL in this plan. `pl_counters.modules` remains empty with explicit unknown coverage.
- Preserve 1280x720 output, frame markers, count line, camera viewport, and bottom CPU/memory bars.
- Work only in the existing worktree `.runtime/worktrees/low-overhead-video-telemetry-20261031` on branch `design/low-overhead-video-telemetry-20261031`.

## File Structure

- Create `scripts/standalone/video_telemetry.py`: `FrameTelemetry`, `TelemetryWindow`, `pl_counters_snapshot`, and camera-rate helpers.
- Create `scripts/standalone/test_video_telemetry.py`: unit tests for statistics, unknown values, percentiles, sequence gaps, and PL coverage.
- Modify `scripts/standalone/shared_frames.py`: optional `capture_time_ns` frame metadata; use the pump's in-process telemetry window for submit time.
- Modify `scripts/standalone/preview_inputs.py`: pass `capture_time_ns` through preview publishing.
- Modify `scripts/standalone/video_pump.py`: submit latency, failures, and skip counters.
- Modify `scripts/standalone/camera.py`: camera-produced count, overwrite drops, and sequence errors.
- Modify `scripts/standalone/integrated_run.py`: frame telemetry, periodic aggregation, report fields, and dashboard mapping.
- Modify `scripts/standalone/controls.py`: three-page cycling.
- Modify `scripts/standalone/compositor.py`: top-link performance display and three detail pages.
- Modify the focused test files for the above modules.
- Modify `文档/项目树.md` and `PROJECT_INDEX.md`.

## Review Focus

1. **Unknown never becomes zero.** Test: `test_dashboard_unknown_metrics_stay_unknown`.
2. **Counting results are unchanged.** Test: `test_telemetry_does_not_change_result_metadata_or_count`.

   Add this test in Task 4: build a `MaskCounter` result for a fixed mask, call `result_metadata` twice with and without a telemetry snapshot, and assert `count`, `boxes`, and `tracks` are identical.
3. **Invalid capture time does not block publishing.** Test: `test_publish_rejects_invalid_capture_time_without_losing_write_lease`.
4. **Camera sequence reset is explicit, not negative drop.** Test: `test_camera_sequence_reset_is_reported_not_subtracted`.
5. **Pump timeout is counted once and not as skip.** Test: `test_submit_timeout_counts_failure_not_skip`.

---

### Task 1: Add the telemetry value and bounded window module

**Files:**
- Create: `scripts/standalone/video_telemetry.py`
- Create: `scripts/standalone/test_video_telemetry.py`

**Interfaces:**
- Consumes: standard library `math`, `collections.deque`, and `dataclasses.dataclass`.
- Produces:
  - `FrameTelemetry(frame_id: int, capture_ns: int, consume_ns: int | None = None, transfer_done_ns: int | None = None, regions_done_ns: int | None = None, track_done_ns: int | None = None, publish_ns: int | None = None, compose_done_ns: int | None = None, submit_done_ns: int | None = None)`.
  - `TelemetryWindow(window_s: float = 2.0, max_samples: int = 4096)` with `record_frame`, `record_camera`, `record_pump`, `sample`, and `reset`.
  - `pl_counters_snapshot(raw: dict | None) -> dict` with schema `material-counter.pl-counters.v1`.
  - `percentile(values, percent)` with NumPy-compatible linear interpolation.

- [ ] **Step 1: Write failing tests for value freezing, percentiles, bounded samples, and unknown metrics**

Create `scripts/standalone/test_video_telemetry.py` with tests asserting frozen frame telemetry, invalid negative/bool timestamps, `percentile((), 95) is None`, `percentile((0, 10), 50) == 5`, `percentile((0, 10, 20, 30), 95) == 28.5`, a three-item bounded window, E2E p95, unknown empty summaries, and aggregate-only PL wrapping.

- [ ] **Step 2: Run the new tests and confirm they fail**

Run: `.venv\Scripts\python.exe -m pytest scripts/standalone/test_video_telemetry.py -q`
Expected: FAIL with `ModuleNotFoundError` or missing `FrameTelemetry`.

- [ ] **Step 3: Implement `FrameTelemetry`, `percentile`, `TelemetryWindow`, and `pl_counters_snapshot`**

Use a frozen slotted dataclass and a `deque(maxlen=max_samples)`. `sample(now_ns)` must filter frames by `capture_ns >= now_ns - window_s * 1e9`, compute `camera_wait_ms`, `queue_transfer_ms`, `ps_total_ms`, `regions_ms`, `track_ms`, `publish_ms`, `compose_ms`, `submit_ms`, and `e2e_ms`, and return `count/mean/min/max/p50/p95/p99` summaries. Missing timestamps append no sample for that metric. `pl_counters_snapshot(None)` returns `aggregate=None`, `modules=[]`, `coverage="unknown"`; a raw counter snapshot returns `aggregate=counters`, `coverage="aggregate-only"`.

- [ ] **Step 4: Run the focused module tests and confirm they pass**

Run: `.venv\Scripts\python.exe -m pytest scripts/standalone/test_video_telemetry.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/standalone/video_telemetry.py scripts/standalone/test_video_telemetry.py
git commit -m "feat: 新增视频链路统计窗口"
```

### Task 2: Pass capture and submit timestamps through shared frames

**Files:**
- Modify: `scripts/standalone/shared_frames.py`
- Modify: `scripts/standalone/preview_inputs.py`
- Modify: `scripts/standalone/test_shared_frames.py`
- Modify: `scripts/standalone/test_preview_inputs.py`

**Interfaces:**
- Produces `SharedFrames.publish(..., capture_time_ns=0)`, `FrameLease.capture_time_ns`, `PreviewLease.capture_time_ns`, and `PreviewInputs.publish(..., capture_time_ns=0)`. The submit timestamp is recorded by the pump, not stored in shared frames.
- All new parameters are keyword-only with default zero so existing callers keep working.

- [ ] **Step 1: Write failing shared-frame round-trip and validation tests**

Add tests to `scripts/standalone/test_shared_frames.py` asserting `capture_time_ns == 1234` survives publish/acquire and that `-1`, `True`, and `1 << 64` are rejected without losing the WRITING lease. Name the validation test `test_publish_rejects_invalid_capture_time_without_losing_write_lease`.

- [ ] **Step 2: Run the new shared-frame tests and confirm they fail**

Run: `.venv\Scripts\python.exe -m pytest scripts/standalone/test_shared_frames.py -q`
Expected: FAIL with `TypeError: publish() got an unexpected keyword argument 'capture_time_ns'`.

- [ ] **Step 3: Add timestamp slots, shared arrays, state serialization, validation, and lease readback**

Extend `FrameLease`, `SharedFrames.__init__`, `create`, `__getstate__`, `__setstate__`, `publish`, and `acquire_latest` with only `capture_time_ns`. Validate it with `_validate_uint64`; `publish` stores it before setting the slot READY, and `acquire_latest` reads it back. Do not add submit-time write-back to `SharedFrames`; the pump uses the in-process telemetry window instead.

- [ ] **Step 4: Run the shared-frame tests and confirm they pass**

Run: `.venv\Scripts\python.exe -m pytest scripts/standalone/test_shared_frames.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing preview capture-time test**

Add `test_preview_round_trip_preserves_capture_time` to `scripts/standalone/test_preview_inputs.py`, publishing with `capture_time_ns=12345` and asserting `lease._frame_lease.capture_time_ns == 12345`.

- [ ] **Step 6: Run the preview test and confirm it fails**

Run: `.venv\Scripts\python.exe -m pytest scripts/standalone/test_preview_inputs.py::test_preview_round_trip_preserves_capture_time -q`
Expected: FAIL with an unexpected-keyword `TypeError`.

- [ ] **Step 7: Add capture-time passthrough to `PreviewInputs.publish`**

Change the signature to `publish(self, gray, binary, session_uuid, frame_id, metadata, capture_time_ns=0)` and pass the value to `self._frames.publish(...)`.

- [ ] **Step 8: Run the preview tests and confirm they pass**

Run: `.venv\Scripts\python.exe -m pytest scripts/standalone/test_preview_inputs.py -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add scripts/standalone/shared_frames.py scripts/standalone/preview_inputs.py scripts/standalone/test_shared_frames.py scripts/standalone/test_preview_inputs.py
git commit -m "feat: 透传帧采集时间戳"
```

### Task 3: Add camera, pump, and PL telemetry collection

**Files:**
- Modify: `scripts/standalone/camera.py`
- Modify: `scripts/standalone/video_pump.py`
- Modify: `scripts/standalone/test_camera.py`
- Modify: `scripts/standalone/test_video_pump.py`
- Modify: `scripts/standalone/test_video_telemetry.py`

**Interfaces:**
- Produces `LatestCamera.stats()` with `captured_total`, `overwrite_drops`, and `sequence_errors`.
- Produces `VideoPump.skipped`, `VideoPump.submit_failures`, and `VideoPump.submit_latency_ms`.
- Produces `VideoPump.skipped`, `VideoPump.submit_failures`, `VideoPump.submit_latency_ms`, and optional `VideoPump(..., telemetry: TelemetryWindow | None = None)` recording. On successful HDMI submission, the pump records capture-to-submit latency directly into the same in-process telemetry window.
- Camera rule: increasing index gap adds `gap - 1` overwrite drops; non-increasing index adds no drops and increments `sequence_errors`.
- Pump rule: mismatched session increments `skipped`; timeout or submit exception increments `submit_failures`; acquire-time errors do not increment `submit_failures`.

- [ ] **Step 1: Write the failing camera statistics test**

Add `test_camera_stats_report_produced_overwritten_and_sequence_errors` to `scripts/standalone/test_camera.py`, feeding indices `0, 1, 2, 7, 8, 3, 4` and asserting `captured_total == 7`, `overwrite_drops == 4`, and `sequence_errors == 1`.

- [ ] **Step 2: Run the camera test and confirm it fails**

Run: `.venv\Scripts\python.exe -m pytest scripts/standalone/test_camera.py::test_camera_stats_report_produced_overwritten_and_sequence_errors -q`
Expected: FAIL with `AttributeError: 'LatestCamera' object has no attribute 'stats'`.

- [ ] **Step 3: Implement bounded camera statistics**

Inside `LatestCamera._capture`, update counters under the existing condition lock. Add `stats()` returning the current values under the same lock.

- [ ] **Step 4: Run the camera tests and confirm they pass**

Run: `.venv\Scripts\python.exe -m pytest scripts/standalone/test_camera.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing pump tests**

Add `test_success_records_submit_latency_and_no_failure_or_skip`, `test_submit_timeout_counts_failure_not_skip`, and `test_mismatched_session_counts_skip_not_submit_failure` to `scripts/standalone/test_video_pump.py`.

- [ ] **Step 6: Run the new pump tests and confirm they fail**

Run: `.venv\Scripts\python.exe -m pytest scripts/standalone/test_video_pump.py -q -k "success_records_submit_latency or submit_timeout_counts_failure or mismatched_session_counts_skip"`
Expected: FAIL with missing `submit_failures` or `skipped`.

- [ ] **Step 7: Implement pump telemetry counters**

Add `skipped`, `submit_failures`, and `submit_latency_ms` fields. Increment `skipped` for mismatched session. Increment `submit_failures` only on failures after acquiring a frame and before/within `writeframe`. Append successful submit latency.

- [ ] **Step 8: Run the video-pump tests and confirm they pass**

Run: `.venv\Scripts\python.exe -m pytest scripts/standalone/test_video_pump.py -q`
Expected: PASS.

- [ ] **Step 9: Add and run the no-raw PL wrapper test**

Add `test_pl_counters_snapshot_without_raw_input_is_unknown_not_zero`, then run:
`.venv\Scripts\python.exe -m pytest scripts/standalone/test_video_telemetry.py -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add scripts/standalone/camera.py scripts/standalone/video_pump.py scripts/standalone/test_camera.py scripts/standalone/test_video_pump.py scripts/standalone/test_video_telemetry.py
git commit -m "feat: 采集相机与显示遥测"
```

### Task 4: Wire telemetry into integrated_run and the report

**Files:**
- Modify: `scripts/standalone/integrated_run.py`
- Modify: `scripts/standalone/test_integrated_run.py`

**Interfaces:**
- Produces `dashboard_values(...)` keys `count`, `state`, `detail_page`, `camera_fps`, `effective_input_fps`, `e2e_ms`, `drop_total`, `resolution_chain`, `link_summary`, `page_lines`, `cpu_percent`, `memory_percent`.
- Produces periodic `video_telemetry` and `pl_counters` cache/report values.
- Keeps existing sample keys and keeps report schema `material-counter.integrated-run.v1`.

- [ ] **Step 1: Write failing dashboard mapping tests**

Add `dashboard_values` to the imports in `scripts/standalone/test_integrated_run.py`. Add `test_dashboard_unknown_metrics_stay_unknown` and `test_dashboard_prefers_fresh_video_telemetry_and_marks_stale_unknown`.

- [ ] **Step 2: Run the dashboard tests and confirm they fail**

Run: `.venv\Scripts\python.exe -m pytest scripts/standalone/test_integrated_run.py -q -k dashboard`
Expected: FAIL because new dashboard keys are missing.

- [ ] **Step 3: Extend `dashboard_values` and preserve stale/unknown semantics**

Read fresh `video_telemetry`; populate the new keys from it. Keep `performance` and `resources` for CPU/memory/diagnostics. Return `detail_page=int(page)`. Missing or stale metrics are `None`.

- [ ] **Step 4: Run the dashboard tests and confirm they pass**

Run: `.venv\Scripts\python.exe -m pytest scripts/standalone/test_integrated_run.py -q -k dashboard`
Expected: PASS.

- [ ] **Step 5: Wire per-frame telemetry and two-second aggregation**

Create `FrameTelemetry` after camera read and fill `consume_ns`, `transfer_done_ns`, `regions_done_ns`, `track_done_ns`, `publish_ns`, `compose_done_ns`, and `submit_done_ns` at existing boundaries. At the existing two-second sample, merge frame, camera, and pump samples and record `video_telemetry` with period 2.0.

- [ ] **Step 6: Add report fields**

Initialize an unknown `pl_counters` object by calling `pl_counters_snapshot(None)`. Add `video_telemetry` and wrapped `pl_counters` to each periodic sample. At final report update, set optional top-level `report["pl_counters"] = pl_counters_snapshot(report["counters_after"])` and `report["video_telemetry"]`. This keeps the PL wrapper visible in the final report and periodic samples, satisfying the design extension point without RTL changes.

- [ ] **Step 7: Add and run the count-unchanged regression**

Add `test_telemetry_does_not_change_result_metadata_or_count` in `scripts/standalone/test_integrated_run.py`. It must compare `result_metadata` from the same `MaskCounter` result with empty telemetry and with a populated telemetry snapshot, asserting `count`, `boxes`, and `tracks` are identical.

Run: `.venv\Scripts\python.exe -m pytest scripts/standalone/test_integrated_run.py::test_telemetry_does_not_change_result_metadata_or_count -q`

Expected: PASS.

- [ ] **Step 8: Run the integrated-run tests**

Run: `.venv\Scripts\python.exe -m pytest scripts/standalone/test_integrated_run.py -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add scripts/standalone/integrated_run.py scripts/standalone/test_integrated_run.py
git commit -m "feat: 接入链路统计与报告"
```

### Task 5: Replace the top dashboard chrome and add three detail pages

**Files:**
- Modify: `scripts/standalone/compositor.py`
- Modify: `scripts/standalone/controls.py`
- Modify: `scripts/standalone/test_compositor.py`
- Modify: `scripts/standalone/test_controls.py`

**Interfaces:**
- Produces `ControlState.page` cycling `0 -> 1 -> 2 -> 0`.
- Produces four key cards at x = `24, 340, 656, 972`, y = `58..126`, width `300`.
- Produces summary rows at y = `136..205` and detail page rows at x = `1044`, y starting `344`, row stride `46`.
- Removes `display_fps` and `processing_ms` from the top cards; existing values may remain in the report/detail diagnostics.

- [ ] **Step 1: Write the failing page-cycle test**

Update `scripts/standalone/test_controls.py` to assert page commands produce `1`, `2`, `0`.

- [ ] **Step 2: Run the control test and confirm it fails**

Run: `.venv\Scripts\python.exe -m pytest scripts/standalone/test_controls.py -q`
Expected: FAIL with page stuck at 0/1.

- [ ] **Step 3: Implement three-page cycling**

Change the page command to `self.page = (self.page + 1) % 3`.

- [ ] **Step 4: Run the control test and confirm it passes**

Run: `.venv\Scripts\python.exe -m pytest scripts/standalone/test_controls.py -q`
Expected: PASS.

- [ ] **Step 5: Write failing dashboard chrome tests**

Add `test_dashboard_replaces_display_fps_and_processing_delay_with_link_metrics` and `test_dashboard_unknown_metrics_render_placeholder_not_zero` to `scripts/standalone/test_compositor.py`.

- [ ] **Step 6: Run the dashboard tests and confirm they fail**

Run: `.venv\Scripts\python.exe -m pytest scripts/standalone/test_compositor.py -q -k dashboard`
Expected: FAIL because old top cards remain.

- [ ] **Step 7: Implement four key cards, summary rows, and page lines**

Replace the three-card loop. Draw cumulative count, camera FPS, E2E delay, and live drops as the four key cards. Draw resolution chain, stage/cycle summary, throughput, and PS feature time in the summary rows. Render `page_lines` only when `detail_page` is nonzero. Keep the marker and bottom resource bars unchanged.

- [ ] **Step 8: Run the compositor tests and confirm they pass**

Run: `.venv\Scripts\python.exe -m pytest scripts/standalone/test_compositor.py -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add scripts/standalone/compositor.py scripts/standalone/controls.py scripts/standalone/test_compositor.py scripts/standalone/test_controls.py
git commit -m "feat: 替换顶部链路性能显示"
```

### Task 6: Documentation and end-to-end verification

**Files:**
- Modify: `文档/项目树.md`
- Modify: `PROJECT_INDEX.md`

- [ ] **Step 1: Update the project tree**

Add `video_telemetry.py` and `test_video_telemetry.py` under `scripts/standalone/` with short Chinese descriptions.

- [ ] **Step 2: Update the project index**

Append a dated entry stating the design is confirmed, the top display is being replaced, and implementation/board acceptance is not complete.

- [ ] **Step 3: Run the focused regression set**

Run:

```powershell
.venv\Scripts\python.exe -m pytest `
  scripts/standalone/test_video_telemetry.py `
  scripts/standalone/test_camera.py `
  scripts/standalone/test_shared_frames.py `
  scripts/standalone/test_preview_inputs.py `
  scripts/standalone/test_video_pump.py `
  scripts/standalone/test_controls.py `
  scripts/standalone/test_compositor.py `
  scripts/standalone/test_integrated_run.py -q
```
Expected: PASS.

- [ ] **Step 4: Run the broader standalone regression**

Run: `.venv\Scripts\python.exe -m pytest scripts/standalone scripts/rle -q`
Expected: PASS; report pre-existing failures separately.

- [ ] **Step 5: Verify the hot-path and scope boundary**

Run `git diff --check` and inspect `git diff --stat`. Expected: no RTL, HDMI geometry, count algorithm, or frame-marker files change.

- [ ] **Step 6: Commit**

```bash
git add 文档/项目树.md PROJECT_INDEX.md
git commit -m "docs: 更新链路统计项目索引"
```

## Self-Review

**Spec coverage:** Task 1 owns the bounded statistics module and PL wrapper. Task 2 carries capture/submit timestamps. Task 3 collects camera, pump, and PL telemetry. Task 4 aggregates and reports it. Task 5 replaces the top chrome and adds three detail pages. Task 6 documents and verifies. The seven required metrics map to camera FPS (1/3/4/5), resolutions (4/5), stage cycles or delays (1/3/4/5), throughput (1/3/4/5), live drops (1/3/4/5), PS feature time (1/4/5), and E2E delay (1/2/3/4/5).

**Placeholder scan:** No `TBD`, `TODO`, or generic "handle edge cases" steps remain. Every code-changing step names its file, interface, and exact values.

**Type consistency:** `FrameTelemetry` fields, `TelemetryWindow` methods, `capture_time_ns`, `submit_failures`, `skipped`, `pl_counters_snapshot`, and dashboard keys are introduced before later tasks consume them. `detail_page` is an int, matching the three-page control change.




