# Gray8 to Binary8 verification, 2026-10-13

## Plan and inspection

The current checkout already contains the implementation and self-checking
testbench accepted in WO-0007. PROJECT_INDEX and README still describe an older
checkout; actual RTL, work orders and fresh evidence govern this verification.

1. Preserve the existing RTL, testbenches and governed documents by SHA256.
2. Extend the vector generator and MCP runner threshold matrix from 0/128/255
   to 0/73/128/200/255. The additional parameters exercise a multi-bit comparator.
3. Run all standalone/chained and continuous/random-stalled XSim cases, using
   the unchanged H3 PNG corpus and an independent Python pixel/sideband oracle.
4. Run the existing RGB and stream regressions, H3 pytest, standalone lint and
   OOC synthesis. Review raw logs, CSV, resource reports, and the actual diff.

The source interface remains RGB888, with internal Gray8 followed by Binary8.
The protected interface document does not specify Binary encoding, equality or
foreground polarity. Retain WO-0007's local compatibility choice: unsigned
Gray >= THRESHOLD maps to FF (bright foreground), otherwise 00; default 128.
THRESHOLD is a compile-time integer parameter in [0,255], not a runtime CSR.
This choice does not freeze the real-material foreground polarity or Route.

Each stage is a one-beat elastic register. Binary latency without stalls is one
clock; RGB->Gray->Binary latency is two. Both sustain one accepted pixel per
clock without stalls. SOF/EOL travel with pixels and hold while stalled.
Synchronous active-low reset drops pending beats; stream endpoints ignore
handshakes during reset, matching the existing RGB stage. Start a fresh frame
with SOF after reset. No frame repair, background subtraction or CSR is added.

## Fresh results

Transport: official AMD Ross MCP stdio JSON-RPC, using the existing project
configuration and isolated session state. Vivado reports version 2026.1 and
part xc7z020clg400-1. No Vivado batch-shell substitution or configuration edit.

Expanded evidence: `data/results/gray2binary-verify-20261013-final/`.
RGB regression: `data/results/rgb2gray-verify-20261013-154334/`.
Legacy saved-vector replay: `data/results/gray2binary-legacy-20261013-final/`.

| Check | Fresh result |
|---|---|
| Binary XSim matrix | 20/20 PASS: 0/73/128/200/255 x standalone/chained x continuous/random |
| Per-run corpus | 231168 beats: all 256 Gray8 values, 512 color vectors, 3 full H3 frames |
| Independent Python comparison | 4623360 pixels, 0 pixel/sideband/count mismatches; 60 H3 frame comparisons |
| AXI coverage | Source bubbles, output stalls, intermediate Gray stalls, stable stalled payload/markers, reset flush/recovery |
| Random-stall longest pause | 58 clocks, deterministic LFSR initial value 0x1ace |
| Continuous coverage | No stalls/bubbles; 231166 consecutive-throughput checks standalone, 231165 chained |
| Invalid parameters | -1 and 256 each trigger the intended time-zero fatal; separate from positive tests |
| Existing RGB regression | 2/2 PASS; 230912 pixels per case; PNG oracle and sidebands agree |
| Existing stream skeleton | PASS: in=8 out=8 stall=8 blocked=7 longest_pause=5 |
| Existing H3 pytest | 7 passed in 22.87s, exit 0 |
| Legacy saved-vector replay | 12/12 PASS with final runner; 2774016 pixels and 36 H3 frame comparisons, 0 mismatches |
| Binary RTL lint | Tcl exit 0, Vivado total 0, canonical CSV empty, no waivers |
| Binary OOC synthesis | THRESHOLD=128: 5 LUT, 4 FF, 0 DSP, 0 BRAM; no synthesis ERROR/CRITICAL WARNING |
| RGB regression lint | 2 WARNING: ASSIGN-1 and ASSIGN-6, same existing findings |
| RGB OOC synthesis | 90 LUT, 11 FF, 0 DSP, 0 BRAM |

Lint counts by rule and source: Binary has no rows. RGB ASSIGN-1 is 1/2 (50%),
WARNING, `axis_rgb2gray.v:27`; ASSIGN-6 is 1/2 (50%), WARNING,
`axis_rgb2gray.v:22`. The bounded weighted sum is at most 65280 and fits 16 bits;
low eight bits are intentionally discarded for floor division by 256. Retain
these known findings without unrelated RTL edits or waivers.

The official lint ASCII parser again consumed two trailing prose rows after
Vivado's empty table. Preserve the raw parser result; normalize the authoritative
zero via the unchanged official parser's JSON input with rows=[]; the resulting
canonical CSV is empty. See `lint-normalization.json` and `lint-tool-result.json`.

Seven Synth 8-7129 warnings concern `s_tdata[6:0]`: Gray>=128 depends only on
bit7. Output FF bits also merge, so repeated FF bytes require one data register.
These resource numbers apply only to the synthesized default static threshold.
73 and 200 are functionally simulated; their synthesized resources were not measured.

Simulator diagnostics: VRFC 10-3532 ignores TB generics for generated glbl;
the PASS labels verify the actual TB parameters. Wavedata 42-489 concerns only
large vector memories omitted from the wave window; all pixels are checked.
The separate RGB session logs startup Common 17-1257 (cannot create directory
'C') before the MCP init script; subsequent project, simulation, lint and
synthesis Tcl calls succeed. AMD doc search found no exact applicable Answer
Record; its root cause is unresolved. The binary session has no such startup error.
Do not describe the aggregate tool logs as entirely error-free.

The 20-case run preceded a compatibility follow-up: the runner now selects its
matrix from a supplied manifest, prints the actual thresholds, and checks golden
files before starting Vivado. Fresh generation uses all five thresholds; old
saved vectors retain their original 0/128/255 matrix. The generator docstring
was also clarified. The expanded run's original source hashes remain intact;
the legacy replay captures the final source hashes. No design RTL changed.

Manager independently read all positive compile/elaboration/runtime logs,
checked the pixel-comparison JSONs, canonical lint CSV and Tcl exit, synthesis
diagnostics/resources, source hashes and preservation snapshot. Final audit:
`MANAGER_REVIEW_PASS`, saved in `manager-verification.json`. The expanded session
transcript records 79 vivado_execute calls, 1 start, 1 stop, 9 todo calls and
1 structured log inspection; no duplicate-skipped command. Both runner processes
finish with exit 0. Python compilation and git diff --check also pass.

## Files and preservation

Modified: `python/refmodel/gray2binary_vectors.py` (73/200 golden vectors and
explicit neighboring-boundary assertions); `fpga/scripts/run_gray2binary_mcp.py`
(manifest-selected matrix, early validation and actual case count).
Added: this report and the three fresh evidence directories above, including
`manager-review.py` and `manager-verification.json` in the expanded directory.

Reused without edits: `axis_gray2binary.v`, `axis_rgb2gray.v`,
`tb_axis_gray2binary.sv`, existing stream/RGB TBs and independent checkers.
SHA256 preservation check covers all six RTL/TB files and the four explicitly
protected documents. No staging, commit, push, or unrelated untracked-file edits.

## Reproduction and limits

Run from repository root; omit --out to create a new timestamped directory:

```powershell
.\.venv\Scripts\python.exe fpga\scripts\run_gray2binary_mcp.py --phase all --rgb-vectors data\results\rgb2gray-vectors-20261012
.\.venv\Scripts\python.exe fpga\scripts\run_rgb2gray_mcp.py --phase verify --vectors data\results\rgb2gray-vectors-20261012
.\.venv\Scripts\python.exe -m pytest python\synth\tests -q
git diff --check
```

Known limits: static threshold parameter only, no THRESH CSR or runtime update
semantics; bright-foreground polarity is a local compatibility choice, not a
real-material calibration result. One deterministic random-stall seed was used.
The reset test fills and interrupts both registers before restarting a complete
test stream; arbitrary in-frame reset positions were not swept. The three H3
frames are representative replay images, not a full real-video accuracy dataset.

Timing has only a 10ns clock, no I/O delays, and unset HD.CLK_SRC. No placement,
routing, timing closure, bitstream, camera, FPGA board or real counting result is
claimed. Next, establish actual foreground polarity and calibrated thresholds;
if runtime configuration is needed, define the THRESH CSR update boundary and
test threshold changes under stalls without changing already pending pixels.
