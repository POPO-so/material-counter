# WO-0008 morphology implementation plan

**Goal:** Add independently verified 3x3 erosion, dilation and bypass after Binary8.
**Architecture:** Three rotating one-bit line stores, alternating line capture
and line emission, with one elastic output register. Generate full-size output
coordinates, including explicit right-edge and bottom-row flushes.
**Tech stack:** Verilog, SystemVerilog/XSim, Python/Pillow, Vivado 2026.1 via Ross MCP.

## Inspection and authorization

The current checkout contains WO-0006/0007 RTL, testbenches and fresh 2026-10-13
verification; it contains no morphology implementation. README/PROJECT_INDEX
still reflect an older checkout. An untracked `wo-0008-roi.md` is a separate,
unimplemented draft; preserve it. The user explicitly assigns WO-0008 to
morphology in this session. This filename separates the two records.

MASTER_PLAN describes morphology as HLS/stretch, and warns about RTL complexity.
This explicit work order authorizes a bounded RTL experiment without editing
MASTER_PLAN, scope, interfaces or routes. No Route freeze, region extraction,
opening/closing cascade, CSR, camera or board integration is included.

## Contract and alternatives

Parameters FRAME_WIDTH=320, FRAME_HEIGHT=240, MODE=0. Width/height must be
positive; MODE=0 bypass, 1 erosion (AND nine bits), 2 dilation (OR nine bits).
Compile-time mode avoids undefined mid-frame configuration semantics.
Binary8 input/output is strictly FF foreground and 00 background. The centered
3x3 square includes all nine locations; pixels outside the frame are zero for
both operations. Thus erosion can shrink edge targets and small objects; dilation
can join nearby targets and close holes. Bypass must reproduce every pixel.

AXI signal names and widths match axis_gray2binary. Coordinates advance only
when the input handshakes. Output has exactly width*height beats per well-formed
frame; SOF marks (0,0) and EOL marks (width-1,y), regenerated with each computed
pixel. Output payload/VALID/markers remain stable while stalled.

Synchronous active-low reset clears valid, coordinates and line/window state
without resetting RAM contents. After reset, accept/discard pixels until an
accepted SOF. Only well-formed width/height frames are supported; malformed
markers are rejected by simulation assertions, not repaired. A reset intentionally
aborts an in-progress frame; the first fresh SOF restarts a complete frame.

Recommended minimal implementation: receive the first two rows (one for H=1),
then emit the first output row. Each next captured row permits the preceding
output row; after the final real row, autonomously emit the bottom output row.
Scan each stored line left-to-right, warming two horizontal taps; a virtual
zero column flushes the rightmost output pixel. Reinitialize horizontal taps
for every row. Mask invalid top/bottom rows rather than clearing the memories.

This schedule deliberately asserts input backpressure during row emission.
Ignoring external stalls, the engine takes approximately 2*W*H+H clocks per
frame, not sustained one input pixel/clock. It stores 3*W logical bits plus
small control/window registers; actual mapped resource usage requires synthesis.
Alternatives: a concurrent capture/emit pipeline improves throughput but needs
more complex row-end scheduling; a full-frame buffer simplifies neighborhood
access but costs more memory and adds a whole-frame delay. Neither is needed
for this first independently verifiable block.

## Execution and file boundaries

1. Snapshot protected documents and all existing source/config files. Delegate
   independent verification files; Manager owns architecture and RTL.
2. Add self-checking `fpga/sim/tb_axis_morphology3x3.sv` and Python reference
   files `python/refmodel/morphology_vectors.py` and
   `python/refmodel/check_morphology_output.py`. First run an omitted-module
   XSim elaboration and require a specific missing-axis_morphology3x3 failure.
3. Add `fpga/rtl/axis_morphology3x3.v` using the contract above. No existing
   RTL, TB, H3 generator, interface or project/global configuration edits.
4. Add `fpga/scripts/run_morphology_mcp.py` using the existing Ross transport.
   Run small dimensions first, then all scenarios/modes, direct and
   RGB->Gray->Binary->morphology, continuous/random-stalled with input gaps.
5. Compare all output pixels and SOF/EOL to independently recomputed Python
   neighborhoods; cover 1x1, 1xN, Nx1, 2x2, noise, small objects, rings,
   adjacent/touching targets, all four edges/corners, random and real H3 pixels.
   Test pending-output and mid-frame reset plus resynchronization at SOF.
6. Run actual MCP lint and OOC synthesis, record counts and hierarchy/resources.
   Run existing RGB/Binary and H3 regressions. Inspect actual diffs, source
   hashes, raw logs, PASS labels and checker verdicts before final acceptance.

Commands from the repository root (new evidence directories only):

```powershell
.\.venv\Scripts\python.exe fpga\scripts\run_morphology_mcp.py --phase red
.\.venv\Scripts\python.exe fpga\scripts\run_morphology_mcp.py --phase all
.\.venv\Scripts\python.exe fpga\scripts\run_gray2binary_mcp.py --phase verify --rgb-vectors data\results\rgb2gray-vectors-20261012
.\.venv\Scripts\python.exe fpga\scripts\run_rgb2gray_mcp.py --phase verify --vectors data\results\rgb2gray-vectors-20261012
.\.venv\Scripts\python.exe -m pytest python\synth\tests -q
git diff --check
```

Functional PASS requires complete compile/elaboration/runtime logs without
unexpected fatal/error, a terminal TEST_PASS for every requested case, exact
frame/line/pixel counts and zero independent pixel/marker mismatches. Modeled
target-shape effects are not real counting accuracy. Timing with only a clock
is not timing closure. No automatic staging, commit, push, PR or board claim.

## Acceptance, 2026-10-13

Manager audited final evidence: 84/84 morphology XSim cases pass, 2,806,344
pixels and 528 complete-frame comparisons match the independent oracle. All
42 stalled cases observe a 12-clock final-pixel pause with next-frame SOF
accepted; all 84 cases pass reset recovery. Old Binary20/RGB2/H3pytest7 pass.
OOC 320x240: bypass105 LUT/45 FF, erosion/dilation112 LUT/55 FF each; DSP/BRAM0,
distributed RAM30 LUT included in totals. Lint: bypass1 expected unused-window
warning, erosion/dilation0. No timing closure or FPGA board result.

Full files, contract, limits, raw evidence and reproduction are recorded in
`docs/morphology-verification-20261013.md`. All 138 preexisting snapshotted files
are unchanged; no staging, commit, push or PR action was performed.
