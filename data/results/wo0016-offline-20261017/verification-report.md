# WO-0016 Offline/Mock Verification

**State:** Offline implementation verified. **Board acceptance: NOT RUN.**

## Scope and environment

- Worktree branch: `codex/wo0016-counting-20261017`
- Base commit: `e3f7d7ab9753b1f67263a81815b442cf79a768ac` (`feat/pl-stream-skeleton`)
- Runtime: Python 3.12.10, NumPy 2.5.3, OpenCV 5.0.0, pytest 9.1.1
- Result JSON: [`mock-run.json`](mock-run.json), SHA-256 `e246cec8183d39c1cb0072c4ba1771c26d453a0a2b0637e468740ca56e1c3e17`
- Board/PYNQ access during offline validation: `board_accessed=false`, `pynq_imported=false`

## Verification commands and results

| Command | Result |
|---|---|
| `C:\fpga\material-counter\.venv\Scripts\python.exe -m pytest -q scripts/wo0016` | 30 passed in 3.15 s |
| `C:\fpga\material-counter\.venv\Scripts\python.exe -m pytest -q -rs scripts/wo0016 scripts/wo0015` | 208 passed, 1 skipped in 18.41 s. Skip: `test_cma_compat.py::test_actual_probe_satisfies_authorized_kernel_accounting_admission`; live metadata artifact is absent in this checkout. |
| `C:\fpga\material-counter\.venv\Scripts\python.exe scripts/wo0016/run_stream_counter.py --mode mock --mock-frames 8` | `MOCK_PASS`; 8 frames, 2 predicted/2 synthetic ground-truth objects, 0 count error, 100% synthetic score. |

The saved Mock result reports 15.8176 frames/s for this host run, including local preview server startup and JPEG rendering. It is not camera, conveyor, or FPGA performance; `hardware_fps` is null. The scene contains two synthetic moving 7×7 components and a 9-pixel noise blob below the configured area threshold.

Additional checks: board mode without opt-in is rejected before PYNQ import; simulated DMA ownership, uncertain-buffer release gating, stuck-camera read timeout, MJPG 640×400 negotiation, exact AFI1 readback gates, and preference for the isolated WO-0015 helper bundle over a repository fallback are covered by offline tests. `git diff --check` returned no whitespace errors.

## Source SHA-256

| File | SHA-256 |
|---|---|
| `scripts/wo0016/stream_counter.py` | `f21a546c10d5d51caf6d2335ee8721fd5d6c1444807247d9cfaff5d38dca7caf` |
| `scripts/wo0016/mjpeg_preview.py` | `333020bbdc0330f48fc6d97a6e1c430983d74c2b1c89b9723a8db7fbdfb1b3e0` |
| `scripts/wo0016/run_stream_counter.py` | `4f50c6e9384d5fc196af7a07b4728781c06d70b38faf9b98d02ac46ae6263ef6` |
| `scripts/wo0016/test_stream_counter.py` | `90996838bfa7988dbdeb242d84dd115d905b7c5498ce3229f2e83ec19cda34eb` |
| `scripts/wo0016/test_mjpeg_preview.py` | `7d06a23cd8176c395f089c23fe9ddfb85380c734536e596b00b73c398a49247a` |
| `scripts/wo0016/test_run_stream_counter.py` | `9b2bd5e1c55e341b7bf37e352200e6a3a8729b547ca7d362b847e331e3c816a1` |

## Acceptance boundary

No PYNQ-Z2 operation or camera capture was run for this offline report. Conveyor speed, M6 nut/washer ground truth, real count accuracy, end-to-end board FPS, and browser FPS remain `NOT RUN`; see the [board acceptance record](../../docs/verification/wo-0016-board-acceptance.md). Synthetic score and host FPS must not be presented as physical acceptance.
