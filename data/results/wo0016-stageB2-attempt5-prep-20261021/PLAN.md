# WO-0016 Stage B-2 Attempt 5 Implementation Plan

**Goal:** Diagnose Attempt 4, fix bounded PS tracking and prepare a fresh one-shot Attempt 5 without sampling.

**Architecture:** Keep existing defaults and hardware paths. Enable explicit component clustering, corridor cooldown and bounded confirmed ROI-edge gap recovery through hash-bound counting configuration. Preserve all original evidence and dirty worktree files.

**Tech Stack:** Python, NumPy/OpenCV, pytest, existing PYNQ runner guards.

1. Export all saved qualified CCL records for frames 42-52, 93-104 and 150-160. Compare recorded count 4 and document missing mask/subthreshold evidence.
2. Add failing regressions to scripts/wo0016/test_run_stream_counter.py for split nut, weighted geometry, nontransitive clustering, cooldown boundaries, confirmed edge gap and rejection of ambiguous/nonedge/stale gaps. Run with C:/fpga/material-counter/.venv/Scripts/python.exe -m pytest.
3. Modify scripts/wo0016/stream_counter.py and run_stream_counter.py to share enabled live/replay logic and include every knob in permit identity. Retain existing public defaults and fail-closed hardware gates.
4. Replay all 165 saved CCL frames into a new evidence directory. Require one event per activity segment; label gap crossing inferred and physical identity unverified. Run WO0016 and WO0015 tests and git diff --check.
5. Read board boot/READY/CMA/device occupancy over noninteractive SSH. If gates pass, archive Attempt 4, verify package/source hashes, deploy tested sources and create exclusive hash-bound Attempt 5 permit. Do not start launcher, sampling, DMA or conveyor. Preserve consumed markers.
6. Write preparation report, verify original evidence hashes unchanged and report deployment/permit state plus operator feeding timing. Stop awaiting physical readiness.

## Completion record

- Diagnosis, regression fixes, independent review, 165-frame CCL replay and local Attempt4 archive: DONE.
- WO0016 final tests: 109 passed. Joint regression: 286 passed, 1 skipped, 1 failed due to the missing historical WO0014 source manifest; no fixture fabrication or bypass.
- Board reads: DONE, no sampling. Current boot differs from old Stage1/manifest; current READY is absent.
- Board archival/deployment/Attempt5 permit: NOT EXECUTED, fail-closed at current-boot provenance gate. Non-executable draft only.
- Hardware launch: NOT RUN, awaiting current-boot gates and separate explicit physical-ready/start instruction.
