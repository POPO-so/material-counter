# WO-0017 Physical Calibration Plan

**Goal:** Measure the full-width physical mapping, three material pixel distributions, and 300-frame board timing without touching formal permits or historical attempts.

**Architecture:** Keep `main` unchanged and implement a calibration-only entry path in a fresh worktree from the merged full-width baseline. Before the browser click, perform read-only boot, manifest, Stage 1, CMA, ownership, DMA, and camera-control admission; after the click, atomically claim one new root-owned calibration lock and invoke the existing bounded board runner. Preserve a single combined Gray8/Binary8 NPZ and bounded frame metadata in the new calibration result directory.

**Tech Stack:** Python 3, pytest, existing WO-0015/WO-0016 board runtime, PYNQ-Z2 V4L2 camera, loopback HTTP operator gate.

---

### Task 1: Isolate the calibration runner

**Files:**
- Create: `C:\wt\wo0017-physical-calibration-20261023` worktree from `main`.
- Create: `data/results/wo0017-physical-calibration-20261023/implementation-plan.md`.

- [ ] Record baseline commit and run the focused WO-0016/WO-0017 tests in the isolated worktree.
- [ ] Keep `C:\fpga\material-counter` and sealed attempt directories unchanged.

### Task 2: Add a one-shot calibration-only board mode

**Files:**
- Modify: `scripts/wo0016/run_stream_counter.py` for strict calibration lock identity, read-only prelaunch validation, once-only claim, and mapping timing evidence.
- Modify: `scripts/wo0016/operator_gate_launcher.py` for a calibration-specific 8081 standby page and click-to-preflight flow.
- Modify: `scripts/wo0016/test_run_stream_counter.py` and `scripts/wo0016/test_wo0017_prelaunch.py` for immutable lock validation, cross-boot/hash/config rejection, no reuse, and no formal permit dependency.

- [ ] Write focused failing tests before changing runtime behavior.
- [ ] Require exactly 300 frames, `full-width-mixed`, X=160, full-height ROI, threshold 128, raw dual-array capture, and a new calibration ID/path.
- [ ] Require current Boot ID, hash-pinned manifest and Stage 1 receipt, root-owned private calibration guard, CMA above 32768 KiB, empty device ownership, idle/no-error MM2S/S2MM, loaded matching overlay, and standard camera readback.
- [ ] Keep prelaunch free of PYNQ import, camera open, DMA, and lock consumption. On authenticated start, create only the calibration namespace's exclusive consumed marker; never read/write a formal Permit or WO-0016 Guard.
- [ ] Add capture-frame mapping time and preserve existing camera/DMA/CCL/tracker/end-to-end timing evidence.

### Task 3: Build and arm the board-side standby

**Files:**
- Create: a unique board deployment directory under `/home/xilinx/wo0017/` and a unique state path under `/var/lib/wo0017/calibration/35983d66-bb1d-4a8a-85e5-802170825515/`.
- Create: this result directory's `deployment-manifest.json` with per-file SHA-256 values and lock identity.

- [ ] Re-read board Boot ID, CmaFree, device owners, V4L2 mode/exposure/brightness, and read-only DMA registers immediately before deploying.
- [ ] Deploy only into new paths; verify remote/local SHA-256 for each runtime file.
- [ ] Create a fresh root-owned READY calibration lock and start the 8081 loopback standby; do not start camera or DMA before browser click.

### Task 4: Capture and analyze

**Files:**
- Create: `calibration_summary.json`, `README.md`, and at most a few representative original/mapped/boxed images under this result directory.

- [ ] Await the operator start click and the separate conveyor authorization before any belt motion.
- [ ] Capture 300 mapped Gray8/Binary8 pairs plus bounded per-frame records in a single archive; stop on first anomaly and never retry the consumed lock.
- [ ] Measure the two 180 mm boundaries, inspect active-area coverage and glare, compute material/fragment/neighbor/touching distributions, quantify merge-rule margins, and report mapping/DMA/CCL/tracker/end-to-end timing and motion only when physically observed.
- [ ] Re-check final CMA, camera/control release, and DMA idle/error status; preserve all raw evidence needed to substantiate the report.

### Task 5: Verify and hand off

- [ ] Run the focused tests and relevant full WO-0016 test set in the isolated worktree.
- [ ] Verify result JSON, NPZ arrays/metadata and representative images; state every measurement's evidence source and any unavailable measurement explicitly.
- [ ] Report worktree branch, `main` status, Board ID, samples, CmaFree, guard path/hash, gate state, output paths, tests, and remaining physical conditions.
