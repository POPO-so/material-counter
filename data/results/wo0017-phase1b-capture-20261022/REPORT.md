# WO-0017 Phase 1B Capture and Ground Truth

Status: `READY_FOR_HARDWARE_CAPTURE` (software-only implementation and offline verification completed; no PYNQ board was connected, started, deployed to, or sampled in this phase).

## 1. Data path findings

The existing camera generator receives OpenCV-decoded frames from V4L2, takes channel 0 for color input, validates `uint8 640×400`, and nearest-neighbor resizes to the FPGA's 320×240 Gray8 input. It returns only the resized ndarray; before this change neither that array nor camera timestamps were saved.

`DoubleBufferStream` assigns software Frame IDs starting at zero. It writes a frame to a specific TX slot and starts S2MM into the RX buffer in that same slot. After both DMA waits complete, it synchronizes that RX buffer from the device and copies it into the slot's preallocated CPU `cached_mask`. Packer validation, MaskCounter, and preview consume that CPU copy. The slot is not returned to FREE until CPU processing and callbacks finish. The next frame may use the other slot while the current one is counted.

Therefore pairing is based on the current software Frame ID plus the same slot's MM2S/S2MM lifecycle; the Binary8 output does not echo a Frame ID that independently verifies the hardware association. Capture records this boundary explicitly. It never pairs “latest input” with “latest output,” and it saves the exact Gray8 encoder input and exact synchronized cached S2MM bytes before that slot is reused. JPEG/HUD previews and counter-processed masks are not used as raw evidence.

## 2. Implementation and use

`--capture-dir` enables capture; absence of the flag is the default. The requested directory must be new. `--capture-max-bytes` defaults to 64 MiB, and the fixed asynchronous writer queue has depth 2. A capacity and free-space estimate is checked before board-side preflight proceeds; writer initialization rechecks it before DMA channels are accessed. In Stage B-2 permit mode, the capture directory, byte cap, queue size, and schema version are part of the permit identity.

The writer copies the two arrays into a bounded queue and performs NPY encoding and filesystem writes on its worker thread. If the queue overflows, a write fails, the disk cap is exceeded, or the requested frame set is incomplete, the runner fails and the dataset cannot be presented as complete. When channels are confirmed idle the manifest is finalized as `INCOMPLETE`; if DMA ownership is uncertain, fail-hold takes priority and run evidence records `INCOMPLETE_FAIL_HOLD` while the manifest remains non-`COMPLETE` (often `IN_PROGRESS`).

Example for a later, separately authorized run:

```powershell
python scripts/wo0016/run_stream_counter.py --mode board `
  --execute --authorize-stage wo0016-stream --approve-once `
  --manifest <reviewed-manifest.json> --manifest-sha256 <sha256> `
  --max-frames <finite-frame-count> --evidence-json <new-run-evidence.json> `
  --capture-dir <new-capture-directory> --capture-max-bytes 67108864
```

This example does not authorize board access. Existing live operator, manifest, READY Guard, CMA, DMA, one-shot and fail-hold checks remain in force. Do not reuse a consumed permit or output path.

Replay a complete raw dataset through Baseline:

```powershell
python scripts/wo0016/replay_capture_dataset.py <dataset-directory> `
  --output <new-replay-report.json>
```

`replay_capture_dataset()` also accepts a future counter factory, so a later V2 can read the same stored masks without changing evidence. This branch does not add V2 or change detection/counting algorithms.

## 3. Data format and synthetic sample

See [DATA_FORMAT.md](DATA_FORMAT.md). The `synthetic-sample/` dataset is generated from software arrays only and exists to exercise serialization, validation and replay. Its `COMPLETE` status means its one synthetic frame is internally complete, not that any camera, FPGA, conveyor, or nut was measured. The baseline replay output is `synthetic-sample/replay/baseline-replay.json` (API) and `synthetic-sample/replay/cli-baseline-replay-final.json` (CLI); entity validation is `NOT VERIFIED` because the sample has no independent physical Ground Truth.

The dataset contains independent input and mask `.npy` arrays, per-frame JSONL with pixel hashes, a manifest-config digest and processing evidence, a manifest, and `ground-truth.template.json`. The loader cross-checks effective replay settings against hash-covered frame snapshots; replay JSON records the Ground Truth file SHA-256. These hashes detect inconsistency but are not signatures against coordinated rewriting. Ground Truth is entered separately as `ground-truth.json` only after an independent observer/video confirms the actual number of entities in the detection window.

## 4. Ground Truth protocol

Use [GROUND_TRUTH_PROTOCOL.md](GROUND_TRUTH_PROTOCOL.md) and the [GROUND_TRUTH_TEMPLATE.json](GROUND_TRUTH_TEMPLATE.json). Record actual entities that entered the window, not merely the planned number placed on the belt. `Track ID` is an algorithm association and cannot establish physical identity by itself. Identity links need a reviewer plus a cited independent observation source. When those links are missing, or an event falls outside an entity’s independently observed frame interval, the entity result cannot pass even if total count happens to match. Replay outputs include the Ground Truth file SHA-256 and effective capture config is cross-checked against hash-covered per-frame metadata.

The four supported experiment classes are: A, one nut per pass; B, five spaced nuts; C, two close nuts; D, empty-belt capture. The empty-belt case confirms zero false positives with capture enabled.

The empty-belt evaluator requires a separately confirmed frame interval and reports raw connected components, post-filter detections, and count events independently. Any component, detection, or event in the confirmed empty-belt interval is a false-positive failure.

## 5. Offline verification

Command:

```powershell
$env:PYTHONPATH = Join-Path $env:TEMP 'wo0017-phase1b-testdeps'
python -m pytest -q scripts/wo0016
```

`pytest` was not installed in the provided Python 3.12/3.14 environments, so verification used an isolated temporary target under `%TEMP%` (not repository dependencies). Full regression result on 2026-10-22: `142 passed in 8.92s`; no failures. The `pytest` target install was under `%TEMP%`, and the test suite did not access PYNQ hardware.

Coverage includes raw pixel round-trip, Binary8 validation, frame sequence and metadata/config integrity, missing files, queue overflow and stuck-writer fail-closed behavior, capacity/write errors, protected archive path rejection, same-slot pairing under ping-pong reuse, no extra Gray snapshots when disabled, camera timestamps, baseline replay, Ground Truth hashing, and independent-GT PASS/FAIL/NOT VERIFIED cases including empty-belt false detections. Tests are synthetic/offline only. The saved host benchmark (`HOST_WRITER_BENCHMARK.json`) used 165 synthetic 320×240 Gray8/Binary8 pairs paced at 60 frames/s on Windows, Python 3.12.10, local temporary storage, and queue depth 2. All 165 frames completed; including final flush the run took 2.77 s (59.56 effective frames/s), total dataset files used 25,608,770 bytes versus 25,344,000 raw pixel bytes. `CaptureWriter.submit()` mean/p95/max was 0.1515/0.2947/0.4582 ms per frame; empty-mask `MaskCounter.update()` mean/p95/max was 0.2287/0.3870/0.5627 ms over 165 calls. This is one host run and not a PYNQ CPU, storage, or throughput result.

## 6. Resource and evidence limits

For each frame, raw payload is `320×240×2 = 153,600` bytes. 165 frames require 25,344,000 raw pixel bytes, plus NPY headers, metadata, manifest and Ground Truth template. The default 64 MiB cap includes a conservative per-frame allowance and fixed metadata reserve. The writer also checks current free disk before creating the dataset.

Capture-only slot snapshots use about 307,200 bytes for two Gray8 inputs plus two raw Binary8 masks. With a depth-2 queue plus one item currently being written, the immutable queued/in-flight Gray8+mask copies use about 460,800 bytes. Normal extra array storage is therefore about 768,000 bytes (750 KiB), excluding Python objects, NPY/JSON buffers and brief temporary copies. On queue overflow a temporary copy can raise the peak to roughly 900 KiB before the sample fails closed. These are array-size estimates, not measured PYNQ memory use.

The callback copies arrays and queues them after the frame has been counted; disk encoding/writing runs separately. The slot remains CPU-owned until its snapshots enter the bounded queue. If storage cannot keep up, the operation fails rather than skipping evidence. If DMA ownership is unsafe, the runner enters fail-hold before waiting for storage; run evidence records `INCOMPLETE_FAIL_HOLD` and the dataset manifest stays non-`COMPLETE`. Board CPU, RAM, storage, and throughput overhead remain `NOT VERIFIED`; no PYNQ performance claim is made.

## 7. Pre-capture checklist and risks

See [PYNQ_CAPTURE_CHECKLIST.md](PYNQ_CAPTURE_CHECKLIST.md). The Phase 1A audit found no raw Attempt #4/#5 camera frames or Binary8 Masks and no independent entity identities; historical Stage B-1 has 115 per-frame CCL records but no raw mask dataset. Therefore real-data replay, capture-on Stage B-1 false-positive acceptance, and all physical Ground Truth remain `NOT VERIFIED`. Phase 1B creates a collection path; it does not retroactively resolve those failures or improve algorithm accuracy.

Hardware capture status: `READY_FOR_HARDWARE_CAPTURE`.
