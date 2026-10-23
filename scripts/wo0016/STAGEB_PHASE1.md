# WO-0016 Stage B Phase 1 analysis helper

`stageb_analysis.py` adds offline analysis primitives for a future Stage B
runtime integration. It accepts independent 320×240 Gray8 and Binary8 arrays,
computes background gray mean and foreground gray peak, reports connected
component areas, and draws the ROI, threshold, FPS, and gray metrics over a BGR
copy of the Gray8 frame. Background mean, foreground peak, foreground pixel
count, and connected-component areas are full-frame metrics; the ROI is drawn
for reference and does not limit these measurements. Foreground peak is `null`
when the mask has no foreground pixels.

The batch recorder accepts caller-supplied monotonic timestamps and optional
`FrameResult` events and observed `DMASR`. Event timestamps mean the time the
event was observed in a frame result; they do not claim the exact physical
crossing time. `DMASR` remains `null` until an actual value is supplied. The
false-trigger pixel count is reported only when the caller explicitly marks
that sample with the `empty_belt_assumption`; it is an assumption, not ground
truth. `observation_span_at_least_5s` reports only the time span between the
first and last supplied timestamps. Gaps between observations are unknown, so
`five_second_sampling_verified` always remains false until a real sampling
coverage check is integrated. Reports classify spans below 5 seconds as
`under_5_seconds`. Frame IDs and monotonic timestamps must both strictly
increase; supplied DMASR values must fit 32 bits.

The CLI requires `--offline` and paired PNG directories, and writes
`report.json` plus one calibration PNG per pair. Its timestamps are host
processing observations, not camera capture timestamps. It does not import
PYNQ, open a camera, start an MJPEG server, or access a board. Example:

```powershell
python scripts/wo0016/stageb_analysis.py --offline `
  --gray-dir .\gray --binary-dir .\binary --output-dir .\analysis `
  --threshold 96 --roi 0 0 320 240 --empty-belt-assumption
```

The output directory must be a new path. Existing directories, including
empty ones, are rejected so prior audit evidence cannot be overwritten.

This helper is not a live calibration or board batch-run entry point. Runtime
wiring, `/dev/video0`, DMA status collection, and 8080 MJPEG publication remain
unimplemented here. With the current hardware boot/manifest/receipt/guard
gate unresolved, no deployment is authorized by this artifact.

## Evidence status

- Offline unit tests: 11 passed with the existing project virtual environment.
- Board / camera / DMA: not run.
- Three-frame smoke test: **NOT RUN**.
- Five-second empty-belt sample: **NOT RUN**.
- Thirty-second idle-noise run: **NOT RUN**.
- Single-item ×10 and continuous-batch runs: **NOT RUN**.
- Live 8080 calibration stream: **NOT CONNECTED**.
