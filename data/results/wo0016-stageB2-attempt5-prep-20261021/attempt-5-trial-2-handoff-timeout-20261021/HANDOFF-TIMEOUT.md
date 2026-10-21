# Trial 2 handoff timeout evidence

- Boot ID: d9f65950-d59b-4ea4-bee4-b28656cb176f
- Permit: attempt-5-trial-2; SHA-256 da290b60f8874e15a26d8a0d2c7c3e35bd96d8869b9f36ea5da2b6ed2b4e13fd
- Outcome: BOARD_STREAM_FAILED; exit code 2; frame_records=0; frames_processed=0.
- Control events: WAITING_FOR_BROWSER_OPERATOR_CLICK, then EXECUTOR_EXITED; no BROWSER_OPERATOR_CLICK_ACCEPTED.
- Error: operator start was not received before the handoff timeout.
- No FRAME_0_CAPTURE_START_FEED_NOW event occurred; no sampling/feed happened.
- Handoff issue: I left the 8080 preview visible and failed to surface the fresh 8081 control page to the user. This is an operator-page handoff failure, not a physical sample result.
- Original archive SHA-256: 734d3cd2c4220baaabaad34d6cfb1f61b9f536a8d885adbcad5836d555ec64f7
- Replacement permit issuer for the page handoff retry SHA-256: 2436c8b41b30c31f9c4e1531865c7d45529855bbcd2f7a9f3c97621747730042

All original evidence and the consumed marker remain archived unchanged.