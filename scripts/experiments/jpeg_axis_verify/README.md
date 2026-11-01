# JPEG AXIS Gray8 verification

This directory contains only the verification harness. Captured JPEGs and
vendor C-sim references are read in place. Generated vectors, Gray8 references,
configs, source snapshots, logs, and HLS artifacts go under the external
`C:/w/mc-jpeg-axis-hls-20261101` work directory.

## Packet checker

`jpeg_axis_gray_tb.cpp` accepts modes `smoke`, `faults`, `entropy-cut`, `align`,
and `batch`. It drives input as little-endian 32-bit AXIS words with byte-valid
TKEEP and TLAST. Every good frame checks status, the four-word
`Gray8_PACKET_V1` header and trailer, 640×400 geometry, header and trailer frame
IDs, exactly 256000 pixel bytes, every output TKEEP, TLAST only on the final
beat, payload checksum, and reference-kind-specific Y difference. It also
reports threshold-128 counts and checks zero pixel mask flips.

The vendor `planar_padded_yuv.bin` reference has the Y plane in the first
256000 bytes. Frames 1 and 98 have vendor references suitable for bit-exact Y
comparison; run them first. `-ReferenceKind vendorY` requires maxdiff exactly
0; `-ReferenceKind opencv` allows maxdiff ≤1. Both require zero threshold-128
mask flips against the selected reference. Frame inputs and references remain
read-only. To run frame 98, pass its JPEG and
`data/results/jpeg-probe-20261101/hls/frame_000098/planar_padded_yuv.bin` with
`-FrameId 98`.

## Tail alignment vectors

`prepare_vectors.py` inserts a valid APP15 segment immediately after SOI,
producing four valid JPEG files with total lengths modulo four equal to 0, 1, 2,
and 3. APP15 leaves scan and entropy bytes untouched. It
does not edit or copy over its input. For frame 1:

```powershell
python scripts/experiments/jpeg_axis_verify/prepare_vectors.py `
  --input data/results/jpeg-probe-20261101/camera-120-frames/frame_000001.jpg `
  --output-dir C:/w/mc-jpeg-axis-hls-20261101/vectors/frame_000001 --frame-id 1
pwsh -File scripts/experiments/jpeg_axis_verify/run.ps1 -Flow csim -Mode align `
  -Input C:/w/mc-jpeg-axis-hls-20261101/vectors/frame_000001 `
  -Reference data/results/jpeg-probe-20261101/hls/frame_000001/planar_padded_yuv.bin `
  -FrameId 1 -EndFrameId 4 -ReferenceKind vendorY
```

## Negative tests and recovery

Run `smoke` first, then `faults`. Faults covers declared-length mismatch,
header-internal early TLAST before SOS, malformed TKEEP, 512001-byte input,
missing EOI, wrong dimensions, and progressive/wrong mode. Every rejection must
return nonzero status; zero error-output beats are allowed. Every error is
followed by a complete valid frame whose packet must pass the full checker.
Every returning call also asserts that the input stream is empty. The
`missing-eoi → good` case reuses the exact same input/output `hls::stream`
objects without reset; all output from the error call is drained before the
next call. If an error leaves input beats queued, that case fails before any
recovery claim.

The entropy truncation is isolated in `entropy-cut`. It locates SOS, sends
through the middle of entropy data, asserts early TLAST, and stops feeding. If
the top returns, the status must be nonzero and a good frame must recover on
the same input/output streams without reset. Any returned error packet must
have structurally valid header/trailer/AXIS qualifiers and an invalid payload
checksum; empty output is also accepted. A
fixed vendor MCU/block-count deadlock is bounded by the runner process-tree
watchdog and recorded as timeout/FAIL; it does not invalidate earlier smoke or
header-negative evidence. It must never be described as recovery when it times
out. A returning entropy-cut call must also report an empty input queue before a
recovery frame is attempted.

For 120-frame batch comparison, `prepare_batch_refs.py` writes disposable
OpenCV Gray8 `.gray8` files under `<WorkDir>/references/opencv-gray8/`. The
accepted per-pixel max difference remains 1. The runner defaults `-PythonExe`
to the project `.venv` Python 3.12.10 and allows overriding it. On this host the
Python process cannot write into `C:/w`, so `-PrepareBatchReferences` generates
into an ignored temporary directory in this verification folder, then moves
only the generated `.gray8` files into WorkDir using PowerShell. Generate
references automatically before running batch with:

```powershell
pwsh -File scripts/experiments/jpeg_axis_verify/run.ps1 -Flow csim -Mode batch `
  -Input data/results/jpeg-probe-20261101/camera-120-frames `
  -Reference C:/w/mc-jpeg-axis-hls-20261101/references/opencv-gray8 `
  -FrameId 1 -EndFrameId 120 -ReferenceKind opencv -PrepareBatchReferences
```

No original JPEG is copied into the harness.

## HLS config and runner

`hls_config.cfg` is a portable template. `run.ps1` resolves the worktree root
from `$PSScriptRoot`; `-Input`, `-Reference`, `-WorkDir`, `-ToolRoot`, and
`-PythonExe` are explicit parameters. Defaults are `C:/w/mc-jpeg-axis-hls-20261101` and
`C:/AMDDesignTools/2026.1/Vitis`. Each run writes a unique labeled config
snapshot, config SHA-256, log, and log SHA-256 under
`<WorkDir>/runs/<RunLabel>/`. A 1200-second process-tree watchdog bounds each HLS
flow. `sources-before.json` and `sources-after.json` bind each new run to source
hashes and identify concurrent source changes. `-PrepareOnly` renders and hashes
a config snapshot without launching HLS. Do not reuse a run label.
Every new run copies the complete candidate source/vendor tree and the TB into
`<run>/source/` and writes `source-snapshot.json`; cfg points at these snapshots.
`transitive-headers.json` fingerprints compiler-reported headers: synthesis
preprocessor line markers include tool/system headers, whereas Csim `.d`
dependencies omit compiler system headers. The raw dependency evidence is also
preserved. Cosim requires `synthesis-state.json` from a successful source-bound
csynth, verifies matching design hashes, and reuses that exact synthesis source
snapshot; a Csim result cannot substitute for this gate.

The template pins top `jpeg_axis_gray`, `xc7z020clg400-1`, 20 ns, C++14, the
Vitis include directory, the top header directory, `vendor/`, and
`vendor/jpegDec/`. It supplies explicit `main(argv)` arguments to csim and
cosim. It also sets `cosim.random_stall=true` per [AMD UG1399 Co-simulation](https://docs.amd.com/r/en-US/ug1399-vitis-hls/Co-simulation).
Synthesis adds `Vitis/include` with `-idirafter` so the compiler's native
synthesis headers win over the C simulation `hls_stream.h` model. Csim uses an
explicit `syn.csimflags=-I.../include`, and the TB also includes that directory.
`-CosimTrace none` is the default; `port` and `all` remain explicit options.
The first native
port-viewer run stopped making progress and was interrupted; this is not a DUT
failure verdict. Official cosim passed with `-CosimTrace none` while keeping
random stalls enabled. Direct replay of the exact elaborated RTL/VIP snapshot
with `capture_axis_ports.tcl` produced a real port VCD; `analyze_axis_vcd.py`
checks input/output counts, actual stalls/gaps, stalled-token stability, reset
continuity, and each frame's input-to-output span. The observed association with
the original viewer/driver path has not been established as a complete root
cause.

Required order is smoke and header faults in csim, then csynth, then cosim.
Run entropy-cut as a separate bounded csim process. Csim does not test
backpressure. Do not run full joint BD or route.

## Initial red evidence

Vitis 2026.1 compiled the TB and failed at link with
`undefined symbol: jpeg_axis_gray(...)`, referenced from the TB call site. The
public `jpeg_axis_gray.h` existed while `jpeg_axis_gray.cpp` was absent. The
preserved initial red log is `C:/w/mc-jpeg-axis-hls-20261101/logs/initial-red-link-fail-20261101T054010.log`.
Its SHA-256 is `1d28653714a555f195b654a2c8d4fa153703a0a1b756fe1954847d06e868a181`.
The initial include-order compile failure and the PATH Python missing-cv2
failure are historical and have been resolved. Project `.venv` Python 3.12.10 /
OpenCV 4.10.0 generated all 120 references. Historical Csim evidence includes
two vendor-Y bit-exact frames, header errors and recovery, all four APP15 tail
alignments, and 120 OpenCV comparisons (maxdiff 1, zero threshold flips).
Historical entropy truncation failed with an empty stream read. See
`verification-summary.json` for exact log hashes and source-binding limits.
The implementation owner subsequently froze a guard/resource revision; its
tests are recorded separately rather than inheriting the historical passes.

`hls-green-smoke1-d695-20261101T140124.json` is an immutable receipt for normal
frame1 Csim/csynth/official RTL cosim only; its evidence paths refer to unique
run directories, and entropy cosim is explicitly pending in that receipt.
Do not modify a published receipt or its bound artifacts. Any subsequent full
error/recovery receipt must be a new filename.

To replay the generated cosim snapshot after a completed HLS run:

```powershell
pwsh -File scripts/experiments/jpeg_axis_verify/replay_cosim.ps1 `
  -RunLabel unique-waveform-run -TimeoutSeconds 300
```

This uses the existing random-stall VIP, runs a finite simulation bound, and
by default elaborates an independent `jpeg_axis_gray_trace` snapshot with
`-debug typical` before recording port-only VCD. The Tcl variable is local to
that XSim invocation; no global environment hook is installed. Script errors
exit nonzero rather than leaving an interactive prompt. It is diagnostic
evidence and does not replace HLS
post-check. For the two-transaction entropy test, invoke `analyze_axis_vcd.py`
with `--transactions 2 --jpeg-bytes 20436 --output-beats 128016`; each frame's
beat and TLAST counts remain independently visible in its JSON.

`hls-green-recovery-d695-20261101T140606.json` is a separate immutable receipt
covering all source-bound Csim cases and the official random-stall
`entropy-cut → good` two-transaction C/RTL post-check. It retains the initial
smoke receipt unchanged. The optional entropy-specific VCD is recorded as a
separate artifact rather than changing either receipt.

`rtl-entropy-waveform-d695-20261101T141242.json` records the completed optional
two-transaction waveform replay: 5109 accepted input beats / 20436 bytes,
128016 output beats, 21013 output-stalled cycles, zero stalled-token stability
errors, and no reset between the transactions. Official HLS control latencies
are 157260 and 157083 cycles; the VCD input-to-output spans are 157270 and
157080 cycles because their measurement boundaries differ.

## Result classification and handoff

The final D695 source-bound suite passed vendor-Y frame1/frame98, four APP15
alignments, seven header/structure errors and recovery, 120 OpenCV references,
entropy-cut same-stream recovery, C synthesis, normal RTL cosim, and the
two-transaction entropy-cut/recovery RTL cosim. All official final cosims kept
`random_stall=true`, used `trace_level=none`, and completed HLS C/RTL post-check.
Both normal and two-transaction port-VCD replays separately demonstrated actual
stall/gap activity and signal stability.

Historical non-PASS results are retained and are not inherited by D695:

- Missing implementation gave the intended undefined-symbol pre-implementation
  red result.
- A source include-order compile error was corrected by the implementation
  owner; the old entropy truncation empty-stream read was fixed by D695's guard.
- Harness C-string concatenation and alignment filename bugs were corrected
  locally, then their cases reran and passed.
- PATH selected LibreOffice Python without cv2. Using the specified project
  `.venv` resolved that; its restriction writing `C:/w` is handled by staging
  derived references briefly in the owned directory and moving them externally.
- The explicit simulation-header include path shadowed synthesis headers;
  synthesis now uses `-idirafter`, while simulation keeps its explicit `-I`.
- The first native port-viewer cosim stopped progressing and was deliberately
  interrupted. This is an interrupted tool/driver observation, not a DUT FAIL.
  Its complete root cause remains unproved; the final headless official flow
  passed without disabling random stalls.
- An optional VCD replay initially used a trace-none elaboration and hit
  Simulator45-10. That diagnostic was terminated immediately; independent
  `-debug typical` elaboration resolved the diagnostic setup. The preceding
  official entropy cosim PASS was unaffected.

`verification-summary.json` retains the current/historical distinction and
links to unique run evidence. `stage-files.json` lists only verification source
and small manifests for Manager staging. Generated references, vectors, source
snapshots, and tool outputs are outside the repository; do not stage the old
untracked experiments tree. No commit, implementation/vendor edits, full BD,
route, or board operation is part of this verification delivery.
