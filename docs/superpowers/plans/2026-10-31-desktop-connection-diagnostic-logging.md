# Desktop GUI Connection Diagnostic Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add low-volume, redacted, session-correlated logs that show whether a desktop observation session fails during SSH, deployment, board startup, HTTP validation, or frame delivery.

**Architecture:** Keep workstation events in the existing `.runtime/desktop-gui.log`. Emit board lifecycle events as JSON lines to the existing per-session `viewer.log`; use the same session ID on both sides. Preserve startup, ownership, camera, DMA, protocol, and counting behavior.

**Tech Stack:** Python, Paramiko, standard `logging`/`json`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-31-desktop-connection-diagnostic-logging-design.md`

## Global Constraints

- Logs must not contain passwords, Bearer tokens, SSH private keys, authorization headers, or image/frame contents.
- Runtime heartbeats are rate-limited; never write one log entry per frame.
- Add diagnostic information only; do not change auto-start, process priority, connection retries, single-instance/board ownership, camera/DMA behavior, counting algorithms, or protocols.
- Verification is offline. Do not connect to, deploy to, restart, or stop the current board session.

## Review Focus

1. SSH timeout/refusal/authentication errors identify the connection stage without credentials. Test: `test_ssh_failure_logs_stage_and_exception_type`.
2. HTTP 403 identifies status and endpoint without the bearer token. Test: `test_http_403_log_contains_status_without_token`.
3. HTTP 200 for another session records both IDs and refuses adoption. Test: `test_readiness_session_mismatch_logs_both_ids`.
4. Board lock/self-test failure before camera startup records stage and exception type. Test: `test_board_failure_log_contains_stage_and_type`.
5. Frame flow records its first frame and throttles later heartbeats. Tests: `test_first_processed_frame_logs_session_frame_and_count` and `test_runtime_heartbeat_due_at_sixty_seconds`.

---

### Task 1: Workstation connection and transport diagnostics

**Files:**
- Modify: `scripts/desktop_gui/controller.py`
- Test: `scripts/desktop_gui/test_client.py`

**Interfaces:**
- Add `_log_event(event: str, **fields: object) -> None` to emit `desktop_event=<JSON>` with local PID and monotonic time. Redact fields named `token`, `password`, `authorization`, `secret`, or `private_key`.
- Extend `_request(config, token, method, path, payload=None, timeout=4, *, expected_session_id=None)`; when `expected_session_id` is supplied, log HTTP status, endpoint, expected/actual session IDs and elapsed time, never headers or payloads.
- Extend `_ssh(config, *, session_id=None)`; log connection start/success/failure with elapsed time and exception class. `deploy` adds SFTP completion and remote process PID events without logging config or token.

- [ ] **Step 1: Write failing tests** named `test_log_event_redacts_secret_fields`, `test_ssh_failure_logs_stage_and_exception_type`, `test_http_403_log_contains_status_without_token`, and `test_readiness_session_mismatch_logs_both_ids`. Assert the session ID and useful stage/status fields remain present while supplied token/password values never appear.
- [ ] **Step 2: Run the focused tests and confirm the new assertions fail**: `\.venv\Scripts\python.exe -m pytest scripts/desktop_gui/test_client.py -q`.
- [ ] **Step 3: Implement structured workstation events** around GUI start, SSH/SFTP deployment, remote launch/result, readiness, first frame/reconnect, and stop. Readiness transport retries are logged no more than once per five seconds; normal status polling stays quiet.
- [ ] **Step 4: Run `test_client.py` and confirm all assertions pass.**
- [ ] **Step 5: Review the controller diff for token/password/header/payload leakage and commit** with `补充桌面连接阶段日志`.

### Task 2: Board startup and frame-flow diagnostics

**Files:**
- Modify: `scripts/desktop_gui/board_entry.py`
- Modify: `scripts/desktop_gui/rle_desktop_board.py`
- Test: `scripts/desktop_gui/test_rle_desktop.py`

**Interfaces:**
- Add `board_entry.log_event(event: str, **fields: object) -> None` to print a structured `DESKTOP_EVENT` JSON line with board PID and monotonic time to stdout, which the launcher already redirects to `viewer.log`. Redact sensitive field names.
- Add `board_entry.log_failure(stage: str, session_id: str, exc: BaseException) -> None` for failure stage and exception type only.
- Add `rle_desktop_board.log_first_frame(session_id: str, frame_id: int, total_count: int) -> None` and call it after the first frame is processed.
- Add `rle_desktop_board.heartbeat_due(now: float, last_logged_at: float, interval_s: float = 60.0) -> bool` and use it to rate-limit frame progress.

- [ ] **Step 1: Write failing tests** named `test_board_log_event_redacts_credentials_and_retains_session_id`, `test_board_failure_log_contains_stage_and_type`, `test_first_processed_frame_logs_session_frame_and_count`, and `test_runtime_heartbeat_due_at_sixty_seconds`. Assert secrets are absent, stage/session/type/frame/count fields are present, 59.9 seconds is not due, and 60.0 seconds is due.
- [ ] **Step 2: Run the focused tests and confirm the new assertions fail**: `\.venv\Scripts\python.exe -m pytest scripts/desktop_gui/test_rle_desktop.py -q`.
- [ ] **Step 3: Emit board events** for process entry, lock outcome, self-test start/pass/fail, camera open/first camera frame, first processed frame through `log_first_frame`, HTTP server readiness, once-per-minute frame/count progress, failure stage/type, and cleanup result. Keep existing traceback and result-file behavior.
- [ ] **Step 4: Run `test_rle_desktop.py` and confirm all assertions pass.**
- [ ] **Step 5: Review the board diff for secret fields and per-frame logging, then commit** with `补充板端采集阶段日志`.

### Task 3: Offline integration verification

**Files:**
- No additional source files unless a focused regression requires a correction.

- [ ] **Step 1: Run the desktop GUI regression set**: `\.venv\Scripts\python.exe -m pytest scripts/desktop_gui -q`.
- [ ] **Step 2: Run `git diff --check` and inspect the complete branch diff**, confirming the only product behavior change is diagnostic logging and no board operation was performed.
- [ ] **Step 3: Report test results, commit IDs, and the unmodified live-board boundary to the Manager for acceptance and merge.**
