# Capture dataset format (schema 1)

A dataset is a new directory containing:

```text
manifest.json
frame-records.jsonl
ground-truth.template.json
frames/
  000000.gray.npy
  000000.mask.npy
  000001.gray.npy
  000001.mask.npy
  ...
```

`gray.npy` is the contiguous `uint8[240,320]` array passed to the packet encoder. It is stored with `allow_pickle=False` and no compression. `mask.npy` is the contiguous synchronized S2MM output copied into the slot cache before counting. It is also uncompressed and must contain only 0x00 or 0xFF. These are exact pixel values; JPEG, preview/HUD output, thresholded reconstructions, and processed masks are not part of the format.

Every JSONL row has a sequential `frame_id`, relative gray/mask paths, SHA-256 of each uncompressed pixel byte array, a SHA-256 of the manifest capture configuration embedded in the record, and per-frame metadata. The loader cross-checks that digest and the effective counter settings against each hash-covered `algorithm_config` snapshot; editing only the manifest config cannot silently change replay parameters. These hashes detect inconsistency but are not signatures against an editor who rewrites the full dataset and all hashes. Metadata includes camera source sequence, monotonic and wall-clock capture timestamps, read duration, camera mode readback (when supplied by the Runner), DMA completion timestamp, slot index, software pairing statement, DMA register status, Packer verification, image size, threshold, ROI, algorithm configuration, raw and clustered detections, associations, track states, events, suppressed events and total count. Host capture timestamps are taken after `VideoCapture.read`; they are not sensor exposure timestamps.

The `manifest.json` records schema version, dataset status, requested/captured/accepted/dropped/missing frames, capacity estimate and free-space preflight, source type, config, array encoding and SHA-256 of the complete JSONL file. `COMPLETE` requires all requested frames, contiguous Frame IDs, zero dropped/missing frames and no writer error. `INCOMPLETE` datasets cannot be passed to the default replay validator. `load_dataset(..., require_complete=False)` can inspect a partial dataset if its record file exists and validates.

Use `load_dataset()` before replay. It verifies dataset status, JSONL digest, ID order, required timestamp/state metadata, file presence, pixel hashes, shape/dtype and Binary8 values. The validator rejects missing/mismatched evidence; do not repair archived evidence in place.

Ground Truth is separate: copy the template to `ground-truth.json` only after independent observation. Replay reports record its relative path and SHA-256, or `ABSENT`/`INVALID`, so the evaluated annotation is traceable. See the protocol document for required confirmation and provenance fields. Replay outputs belong in a new, exclusive output path and do not modify the captured arrays or Ground Truth.

A dataset inside any `wo0016-*` path is rejected to protect sealed WO-0016 archives. Choose a new WO-0017 capture directory. A writer timeout is marked `INCOMPLETE` with `writer_active: true`; the loader refuses to replay or inspect it because files may still be changing. A DMA fail-hold takes priority over closing the capture worker: the run evidence records `INCOMPLETE_FAIL_HOLD`, while the dataset manifest remains non-`COMPLETE` and is not replayable.
