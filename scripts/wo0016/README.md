# WO-0016 Stream Counter

This package adds a host-testable two-slot Binary8 DMA consumer, ROI crossing counter, and MJPEG preview to the WO-0015 320×240 frame path.

## Offline use

From the repository root:

```powershell
python -m pytest -q scripts/wo0016
python scripts/wo0016/run_stream_counter.py --mode mock --mock-frames 8
```

The default CLI mode is Mock. It creates synthetic moving components, filters a small noise blob, runs the actual counter and JPEG renderer, and briefly serves the latest frame over a local ephemeral HTTP port. Output labels this as offline synthetic evidence and leaves `hardware_fps` unset.

## Board mode

Board mode is a finite, one-shot operation against the already loaded WO-0015 50 MHz Overlay. It requires a manifest whose BIT/HWH hashes are reviewed, a same-boot WO-0015 Stage 1 Overlay PASS receipt, the manifest SHA-256, a new absolute evidence JSON path, camera device, and all three explicit opt-in flags:

```text
--mode board --execute --authorize-stage wo0016-stream --approve-once
```

The runner defaults to `data/results/wo0015-gray8-baseline-20261027/board-package/run-manifest.json`; optionally override it with `--manifest`. Still supply the separately reviewed `--manifest-sha256`, `--max-frames`, and `--evidence-json` (or `--output-json`). The runner reuses the already loaded BIT with `Overlay(download=False)` and requires live AFI1 readbacks of exactly RD `0x00000001` and WR `0x00000F01` before creating the DMA driver. The `CmaFree` admission watermark is 32,768 KiB before and after each buffer allocation. Never reuse a consumed guard or evidence path. The program does not load/reload the Overlay, reset or retry DMA, or free buffers if DMA ownership is uncertain. It binds the MJPEG server to `0.0.0.0:8080` by default; override `--host`/`--port` when the network setup requires it.

For an isolated board deployment, place the required WO-0015 Python modules in a sibling `wo0015/` directory inside the package directory. The runner prefers that bundle and falls back to the repository's `scripts/wo0015/` while running from source. Required modules: `board_once.py`, `board_preflight.py`, `camera_dma_oneshot.py`, `camera_adapter.py`, and `prepare_vectors.py`. Preserve the exact executor hashes bound by the selected WO-0015 manifest: do not edit or patch `board_preflight.py` to bypass its boot check. This keeps deployed helpers isolated without replacing the board's existing WO-0015 files.

After a reboot, rebind the offline WO-0015 package to the current Boot ID with `prepare_board_bundle.py --boot-id <UUID>`. Then pass that same value through `--approved-boot-id` and pass the newly printed `--manifest-sha256` to preflight, `board_once.py`, and this runner. The explicit Boot ID is accepted only when the manifest bytes are hash-pinned; runtime checks still compare the live Boot ID against the manifest before touching hardware.

PYNQ uses `channel.transfer(buffer, nbytes=...)` to start a nonblocking transfer; it returns `None`, and completion is reported by the channel object. The local `transfer_async()` adapter prefers a daemon thread around `channel.wait()` because PYNQ's `wait_async()` requires a configured interrupt handle. Capture requests MJPG 640×400 at 60 FPS, requests a one-frame V4L2 buffer, and checks that V4L2 reports at least 50 fps. Two warm-up frames run before steady-state timing; a single worker reads and resizes at most one next frame while the current frame is encoded, transferred, and counted, then is joined before normal camera release. Measured color frames use the monochrome sensor's first channel and nearest-neighbor resizing. After `sync_from_device()`, each completed RX mask is copied once into a preallocated cached NumPy array; Packer validation and ROI counting then use that cached copy. The counter processes every received mask. Preview callbacks copy a quality-70 snapshot every fourth frame; JPEG encoding runs on a low-priority worker only while an MJPEG client is connected. Timing reports separate RX synchronization/copy, ROI counting, preview handoff, and background JPEG encoding.

ROI extraction uses the cached 320-pixel-wide slice spanning 49 rows around the counting line. Row and column projections are computed from that slice; a C-level foreground count skips connected-component labeling when the band has fewer foreground pixels than `min_area`. Nonempty bands use 16-bit labels with OpenCV `CCL_GRANA` when available. NumPy filters out below-threshold component labels before Python creates detection records; older OpenCV builds fall back to `connectedComponentsWithStats`.

For a board run, launch the PYNQ interpreter with `sudo -n env XILINX_XRT=/usr`; the selected value is included in the JSON evidence.

## WO-0017 optional raw capture

The guarded board runner can opt in to a separate lossless dataset with `--capture-dir <new-directory>`; capture remains disabled by default. Also set a finite `--max-frames` and, when needed, `--capture-max-bytes` (default 64 MiB). The output path must not already exist and its parent must have enough free space. Paths inside sealed `data/results/wo0016-*` archives are rejected. The Stage B-2 one-shot permit binds the capture path, byte limit, and fixed queue depth when capture is enabled.

Each frame stores the exact 320×240 Gray8 array handed to the packet encoder and the same slot's raw S2MM Binary8 output after sync/copy, as uncompressed `.npy` files. Frame IDs and host timestamps identify the camera input and slot pairing; the mask does not echo a frame ID from hardware. A bounded writer queue (depth 2) performs file writes outside the counter callback. Queue overflow, write errors, or a short frame set fail the capture run; the dataset cannot be presented as complete. When DMA is idle its manifest is finalized as `INCOMPLETE`; if ownership is uncertain, fail-hold takes priority, run evidence records `INCOMPLETE_FAIL_HOLD`, and the manifest remains non-`COMPLETE`. Evidence is never silently discarded. The manifest includes the counter configuration, threshold, schema, hashes, and capacity state. Every frame record carries the manifest config digest; the loader rejects a top-level config that differs from hash-covered frame snapshots. A ground-truth template is created alongside the arrays.

Validate and replay only complete datasets from the repository root:

```powershell
python scripts/wo0016/replay_capture_dataset.py <dataset-directory> --output <new-replay.json>
```

The replay reads the saved raw masks through the baseline `MaskCounter`; it does not reconstruct masks from previews. The Python API accepts a future counter factory so the same stored masks can be compared against a later V2 implementation. Entity-level PASS requires an independent observer/video record, full-pass visibility, unique entity IDs, in-range frame intervals, and manually reviewed entity-to-Track-ID links. Events outside an entity interval cannot satisfy it. A reviewed empty Track ID list records an independently confirmed miss. Empty-belt PASS requires zero raw components, detections, and events in the independently confirmed interval. Replay output records the Ground Truth SHA-256. Missing or incomplete Ground Truth is `NOT VERIFIED`.

To locate losses caused by proximity clustering, compare the captured masks
under the recorded configuration and a diagnostic zero-gap configuration:

```powershell
python scripts/wo0016/diagnose_touching_capture.py <dataset-directory> --output <new-diagnosis.json>
```

The output lists qualified Binary8 component counts, precluster and final
detection counts, rejected large components, track associations, and crossing
events for every frame. `proximity_merge_candidate` means at least two
qualified pixel components became fewer observations; a changed count is not
an accuracy result without independent object annotations. The command only
reads a complete, hash-verified capture dataset and does not access the board.

For a temporary optical check before the current board has a valid PL Binary8
capture path, `capture_touching_camera_only.py` records Gray8 from `/dev/video0`
without accessing FPGA or DMA. Use a new absolute output directory for each run.
`--compact` keeps only 320×240 Gray8 and permits up to 1800 frames, enough to
cover conveyor startup. Its manifest explicitly marks the result
`CAMERA_ONLY_NOT_PL_BINARY8`; thresholding those frames is provisional and does
not establish board acceptance. For objects that start outside the camera view,
`--wait-for-object-s 180` keeps reading until three consecutive frames show at
least 100 pixels at Gray8 >= 128 in the left half, then saves the requested
number of frames. The manifest records the actual wait duration.

Capture is not proof that the hardware mask carries an echoed frame ID, nor is a software-synthetic dataset board evidence. Review the current capture report and hardware checklist before any separate board authorization.

## Evidence boundary

The offline simulation is not camera, conveyor, M6 nut/washer accuracy, or FPGA board evidence. Record physical measurements only in [`WO-0016 board acceptance`](../../docs/verification/wo-0016-board-acceptance.md). Current offline results are in [`verification report`](../../data/results/wo0016-offline-20261017/verification-report.md).
