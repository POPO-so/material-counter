# WO-0016 Stage B live analysis and preview

`run_stream_counter.py --mode board` pairs each V4L2 camera Gray8 frame with its
S2MM Binary8 result and passes both to `stageb_analysis.py`'s `BatchAuditRecorder`.
The 8080 MJPEG stream carries the same-frame ROI, threshold, grayscale
mean/RMS, foreground peak/count, connected components, count, FPS, and DMA
telemetry. The JSON audit includes observed event timestamps, component areas,
frame-level `DMASR`, and camera/preview timing data.

Board runs require the boot-bound WO-0015 manifest and Stage B runtime
manifest, a current Stage 1 overlay receipt, the Tier 2 physical-address pair,
and an exclusive guard. Run profile smoke3 permits exactly three frames and
uses one fixed guard. Run profile stageb-batch permits a finite batch of 1–1800
frames and uses a different fixed one-shot guard. Each V4L2 open/config/readback/read/release call
and each streaming PYNQ DMA operation (channel start/wait, cache sync, MMIO
arm/status/readback), each CMA freebuffer call, and post-release CMA telemetry
run through daemon workers with a maximum 5-second per-call deadline. A DMA
timeout marks ownership uncertain; a freebuffer timeout marks release
uncertain. Both enter fail-hold without retrying, retaining references. Stuck
native calls cannot block the asyncio event loop. MJPEG
encoder shutdown waits at most 0.25 seconds; a stuck encoder is reported and
control proceeds into failure handling.

The 30-second stream-stage deadline covers V4L2 open/configuration, warm-up,
capture, and stream processing. It excludes board preflight, Overlay/CMA setup,
preview listener startup, report serialization, and cleanup. Per-operation
deadlines remain in force for streaming camera/PYNQ calls; a stuck operation
is not retried. Guards are boot-namespaced and are never reset by the runner.

Example finite smoke command (use the separately reviewed manifest digests
and a new evidence path for the authorized boot):

```sh
sudo -n /bin/bash --noprofile -c '
  source /etc/profile.d/xrt_setup.sh
  cd /var/lib/wo0016/boot-<authorized-boot-id>/package
  exec /usr/local/share/pynq-venv/bin/python run_stream_counter.py \
    --mode board --run-profile smoke3 --max-frames 3 --max-runtime-s 30 \
    --execute --authorize-stage wo0016-stream --approve-once \
    --manifest wo0015/wo0015-manifest.json --manifest-sha256 <reviewed-sha256> \
    --stageb-manifest stageb-runtime-manifest.json --stageb-manifest-sha256 <reviewed-sha256> \
    --analysis-json /absolute/new/path/analysis.json \
    --evidence-json /absolute/new/path/evidence.json --measure-preview-client'
```

`five_second_sampling_verified` remains false unless continuous observation
coverage is separately proven; a timestamp span alone is not proof. The
offline PNG analysis CLI remains available for replay and does not access the
camera, PYNQ, DMA, or the board.

## Current execution evidence

- Offline unit tests: **276 passed, 1 skipped** after bounded camera/DMA/CMA cleanup changes.
- Board Stage 1 receipt, DMA allocations, and 3-frame smoke: **NOT RUN**; the read-only PYNQ runtime/device-discovery precheck failed closed.
- Five-second empty-belt sample: **NOT RUN**; requires physical-light and conveyor operation after Stage 1 and the smoke succeed.
- Live 8080 server: inactive. It starts during a finite authorized board run and exits when that run finishes; it is not an unattended background service.
