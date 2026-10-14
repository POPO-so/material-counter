# WO-0008 morphology verification, 2026-10-13

Status: Implemented, Simulated and OOC Synthesized. Board testing, implementation
timing closure, camera integration and real counting accuracy are not verified.

## Inspection and scope

Actual checkout already contained RGB->Gray and Gray->Binary plus their TBs,
reference models and 2026-10-13 reports. No morphology RTL existed. The older
README/PROJECT_INDEX implementation descriptions are stale; actual files and
fresh tests govern this work. `wo-0008-roi.md` remains an untouched draft; this
session explicitly assigns WO-0008 to morphology and records it separately in
`docs/work-orders/wo-0008-morphology.md`.

MASTER_PLAN's HLS/stretch preference and complexity warnings were read. This
explicit user assignment authorizes a bounded RTL block, not a Route freeze,
feature-extraction architecture change or protected-document update. No new IP,
package, CSR, opening/closing cascade or PL connected-component engine is added.

## Files

All source/document deliverables below are new in this round:

| File | Responsibility |
|---|---|
| `fpga/rtl/axis_morphology3x3.v` | Three line stores, centered 3x3 operation, complete frame drain |
| `fpga/sim/tb_axis_morphology3x3.sv` | Direct and full-chain self-checking AXI/reset/frame tests |
| `python/refmodel/morphology_vectors.py` | Directed/H3 inputs, golden vectors, source hashes and model self-checks |
| `python/refmodel/check_morphology_output.py` | Independently recomputed pixel/marker oracle and offline shape diagnostics |
| `fpga/scripts/run_morphology_mcp.py` | Official Ross MCP runner, logs, lint and OOC resource reports |
| `docs/work-orders/wo-0008-morphology.md` | Local contract, minimal architecture and implementation plan |
| `docs/morphology-verification-20261013.md` | This final verification report |

The two tracked modifications from the previous round remain unchanged:
`fpga/scripts/run_gray2binary_mcp.py` and `python/refmodel/gray2binary_vectors.py`.
All 138 preexisting snapshotted source/config/document files match their initial
SHA256, including MASTER_PLAN, scope, interfaces, routes, existing RTL/TBs and the
ROI draft. H3 source manifest and all three PNG hashes are also verified.
No staging, commit, push, PR, global setting or unrelated file edit was performed.

## Interface and boundary behavior

AXI ports match `axis_gray2binary`: `aclk`, synchronous active-low `aresetn`,
8-bit `s_tdata/m_tdata`, `s_tuser[0]/m_tuser[0]` SOF, `s_tlast/m_tlast` EOL,
`s_tvalid/s_tready` and `m_tvalid/m_tready`. Accepted pixels are strictly 00 or FF;
FF means foreground. Payload and markers remain stable whenever output VALID is
high and READY is low. Input coordinates advance only on accepted pixels.

Compile-time parameters: FRAME_WIDTH=320, FRAME_HEIGHT=240, MODE=0.
MODE 0 bypasses the operation, MODE 1 erodes by AND of all nine neighborhood
bits, MODE 2 dilates by OR. Width/height must be positive; other modes are invalid.
The mode is static and is not an implemented runtime configuration register.

Every uninterrupted, well-formed input frame produces exactly W*H output pixels
in raster order. The centered 3x3 square uses constant zero outside all four
edges, for both erosion and dilation. Output size is unchanged. SOF is regenerated
only at (0,0); EOL only at (W-1,y), then cached with the corresponding result.
Boundary pixels are output, even when their value becomes background under erosion.

Three rotating one-bit row stores receive a row, then emit the preceding output
row. The first output row waits for two real input rows, or one when H=1. A scan
column warms horizontal taps, and a virtual zero column emits the rightmost pixel.
After the final real input row, a virtual zero bottom row autonomously flushes
the final output row. No next-frame input is required to finish the current frame.
Horizontal taps reset for every output row; top/bottom masks prevent stale RAM
from entering boundary windows, including 1x1, 1xN, Nx1 and 2x2 geometries.

Input READY is low while emitting a row. This deliberately bounded first version
does not sustain one input pixel per clock across a frame: nominal engine work
is about 2*W*H+H clocks, before external stalls and integration overhead. Output
can sustain consecutive pixels within a row. Bypass uses the same buffering and
schedule, so it is an identity operation with latency, not a fast combinational path.
Logical line storage is 3*W bits; there is no whole-frame buffer.

Reset clears valid, synchronization, counters and taps without clearing RAM.
It intentionally aborts an in-progress frame. After reset, pixels without SOF
are accepted and discarded; a new accepted SOF starts a fresh frame. Early/missing
SOF/EOL or changing dimensions are not repaired: simulation asserts a well-formed
raster contract. During reset, endpoints ignore handshakes as in the existing stages.

## Actual simulation and regression results

Transport: configured official AMD Ross MCP stdio JSON-RPC, with fresh isolated
state/session directories. Vivado reports 2026.1, target xc7z020clg400-1. The
official vivado-simulate-rtl and vivado-rtl-lint Skills were applied. No Vivado
batch-shell replacement, assertion suppression or lint waiver was used.

| Check | Result |
|---|---|
| Final morphology matrix | 84/84 PASS: 7 scenes x 3 modes x 2 chains x 2 ready patterns |
| Pixel and sideband comparison | 2,806,344 pixels; zero data, SOF/EOL, order or count mismatches |
| Full H3 outputs | 2,764,800 compared pixels: three unchanged 320x240 PNGs x 12 combinations |
| Complete frames compared | 528, from a 44-frame directed/H3 corpus |
| Standalone/chained | Binary->Morph and RGB->Gray->Binary->Morph both pass |
| Reset | All 84 cases interrupt a pending output, clear stage VALID and recover at fresh SOF |
| Cross-frame overlap | All 42 ready=1 cases hold prior-frame final output for 12 clocks and accept next-frame SOF |
| Random stalls/gaps | Fixed reproducible seed 6862 (0x1ace), plus directed long pauses; max output stall 60 clocks |
| Unstalled sink | All 42 ready=0 cases have zero output stalls; internal row backpressure is expected |
| Existing Binary regression | 20/20 PASS, 4,623,360 pixels compared, zero data/marker mismatches |
| Existing RGB regression | 2/2 PASS, 230912 pixels each, exact H3/source-marker comparison |
| Existing AXI skeleton | PASS: in=8 out=8 stall=8 blocked=7 longest_pause=5 |
| Existing H3 pytest | 7 passed in 25.17s, exit 0 |
| Python compilation / whitespace | py_compile and git diff --check pass |

Scene corpus: 1x1 (2 frames), 1x9 (4), 9x1 (4), 2x2 (3), directed 9x7 (15),
directed 17x11 (13), H3 320x240 (3). Directed frames include noise, single pixels,
2x2/3x3 objects, thin rings, different hole sizes, separated and touching targets,
corners and thick targets against all four edges, black/white/checker/random frames.
Individual frame labels remain in the manifest despite grouping by dimensions.

The checker is separate from the generator: it recomputes every neighborhood,
verifies all 21 expected files (701586 beats), input RGB->Gray->Binary equivalence,
source PNG pixels/hashes, complete output counts and every marker coordinate.
It does not accept simulator PASS alone. A golden-copy checker selftest is explicitly
labeled as Python plumbing evidence and is excluded from the 84 RTL-case count.

Manager independently inspected all positive compile/elaboration/runtime logs,
terminal labels and coverage counters, checker JSON, source hashes, lint CSV and
resource reports. `MORPHOLOGY_MANAGER_ACCEPTANCE_PASS` is recorded in
`data/results/morphology-acceptance-20261013/manager-verification.json`.

## Lint and synthesis

| Mode | Lint messages | LUT total | LUT as distributed RAM (included) | FF | DSP | BRAM | Latches |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0 bypass | 1 WARNING | 105 | 30 | 45 | 0 | 0 | 0 |
| 1 erosion | 0 | 112 | 30 | 55 | 0 | 0 | 0 |
| 2 dilation | 0 | 112 | 30 | 55 | 0 | 0 | 0 |

These are actual independent OOC results for 320x240 on xc7z020clg400-1, not
integrated top-level resources. LUT totals include RAM LUTs; do not add 30 again.
Synthesis inferred all three distributed line memories; no register-array explosion.
No synthesis ERROR/CRITICAL WARNING was observed. No latch was inferred.

Lint counts by rule/source: in MODE 0, ASSIGN-6 is 1/1 (100%), WARNING,
`axis_morphology3x3.v:63`, `window_bits`. The compile-time identity mode reads
the center tap, so its 9-bit operation window is unused. This is accepted without
a waiver; modes 1 and 2 use the window and have empty canonical CSVs.
Initial lint function-return ASSIGN-5 diagnostics were removed by explicit bank
rotation wires; input EOL now controls row completion, removing ASSIGN-10.
The full final matrix ran after these changes.

The official empty-ASCII-table parser again consumed trailing prose for zero
results. Preserve raw parsing, normalize Vivado's authoritative zero through
the unchanged parser's supported JSON input, and require canonical row count to
equal the reported total. Per-mode Tcl results, structured diagnostics and
normalization records are retained. Existing RGB lint retains its two documented
ASSIGN-1/ASSIGN-6 warnings; no unrelated RTL edits were made.

OOC timing reports have only a 10ns clock, no I/O delays, and HD.CLK_SRC is unset.
Timing 38-242 therefore appears. No place/route or timing-convergence claim follows.
VRFC 10-3532 ignores TB generics for generated glbl; actual PASS labels verify
their application to the intended TB. Wavedata 42-489 concerns only large arrays
not shown in the wave window; scoreboard checks still operate on all pixels.

One small-case session logs startup Common 17-1257, failed creation of directory
'C', before MCP initialization. All subsequent project/XSim commands succeed;
all positive simulation logs are free of unexpected errors/fatals. Root cause
of that intermittent startup diagnostic remains unresolved. It is recorded
separately and is not described as a clean aggregate tool log.

## Shape effects and risks

These measured output-pixel statistics are test-image diagnostics, not real
counting accuracy or an implemented PL connected-component algorithm.

| Directed target | Input foreground | Erosion foreground | Dilation foreground | Observed concern |
|---|---:|---:|---:|---|
| 1 pixel | 1 | 0 | 9 | Erosion removes the target |
| 2x2 | 4 | 0 | 16 | Small targets disappear |
| 3x3 | 9 | 1 | 25 | Substantial shape/area change |
| Thin ring | 24 | 0 | 72 | Erosion destroys ring; dilation narrows its hole |
| 5x5 square with 1-pixel hole | 24 | 0 | 49 | Dilation fills the hole |
| Two objects separated by one column | 18 | 2 | 45 | Dilation joins two Python 8-connected regions into one |
| 3x3 object against an edge | 9 | 1 | 20 | Zero padding changes edge behavior |

![Actual RTL output examples](C:/fpga/material-counter/data/results/morphology-acceptance-20261013/rtl-morphology-examples.png)

The panel uses actual chain=1/ready=0 RTL dumps, each independently checked
against the oracle. Detailed foreground, 8-connected-region and 4-connected-hole
statistics are in feature-impact.json; the connectivity analysis runs only in Python.

Limits: mode and dimensions are static; one deterministic random seed and one
pending-output reset position per geometry were tested, not exhaustive formal
proof or all reset positions. Full-frame one-pixel/clock input throughput is not
implemented. Real-material segmentation quality, board speed and timing closure
remain unverified. Bypass remains the default until real imagery shows a useful
operation and target-size/hole/spacing tradeoff. No universal denoising or counting
improvement is claimed.

Next: use captured washer/nut images to choose whether erosion/dilation is useful
and quantify disappearing targets, hole changes and joining risk. Keep region
analysis gated by the existing Route/ADR/resource process. If continuous one-pixel
input throughput becomes necessary, design overlapping capture/emission with
explicit row-end drain tests, instead of changing this verified contract silently.

## Evidence and reproduction

Primary evidence directories under `data/results/`:

- `morphology-vectors-20261013-final/`: manifest, 35 hashed vector files, labeled Python selftest.
- `morphology-verify-small-final-20261013/`: 48 final RTL cases, raw MCP and XSim logs, comparisons.
- `morphology-verify-images-final-20261013/`: 36 final RTL cases including H3, comparisons and shape diagnostics.
- `morphology-lint-final-20261013/`: three mode reports/CSVs, Tcl totals and structured diagnostics.
- `morphology-synth-final-20261013/`: three standard/hierarchical utilization and timing reports, resources.
- `morphology-binary-regression-20261013/` and `rgb2gray-verify-20261013-161051/`: old regression evidence.
- `morphology-acceptance-20261013/`: independent Manager audit, final summary and actual RTL example panel.

Development-only: `morphology-red-20261013/` records the expected missing-module
XSim failure before RTL creation; `morphology-smoke-20261013/` and
`morphology-lint-initial-20261013/` are not final acceptance evidence.

From repository root, the default all-phase command creates a fresh evidence
directory and regenerates the same corpus without editing existing H3 data:

```powershell
.\.venv\Scripts\python.exe fpga\scripts\run_morphology_mcp.py --phase all
.\.venv\Scripts\python.exe fpga\scripts\run_gray2binary_mcp.py --phase verify --rgb-vectors data\results\rgb2gray-vectors-20261012
.\.venv\Scripts\python.exe fpga\scripts\run_rgb2gray_mcp.py --phase verify --vectors data\results\rgb2gray-vectors-20261012
.\.venv\Scripts\python.exe -m pytest python\synth\tests -q
git diff --check
```

This final run split the seven scenes into two isolated Vivado sessions using
--scenes, with separate lint/synthesis sessions; all 84 combinations were still
executed, and source hashes are checked against the final source. The current
checkout remains uncommitted; no work was added to an existing PR.
