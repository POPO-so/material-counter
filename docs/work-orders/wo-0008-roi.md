# WO-0008 · AXI4-Stream true ROI crop

## Approved contract and implementation plan

User selected true crop on2026-10-12. This freezes this work package behavior,
not the final system Route, camera input, feature interface or hardware architecture.
Manager owns the RTL/reset/framing contract and final verification; Worker implements
only new TB, Python vector/checker and Ross runner files. No global configuration,
protected-document, H3-rule, WO-0006/0007 refactor, automatic commit/push or PR1 merge.

Workspace: C:/Users/POP/.codex/worktrees/wo-0008-roi/material-counter,
branch codex/wo-0008-roi, based on PR1 head0e276cf. Original checkout/PR1 are preserved.
Current baseline H3 regression7 passed; generated21 sequences from unchanged spec.
135 tracked baseline files are SHA256 snapshotted in data/results/roi-manager-20261012.

1. Check AGENTS/index/interfaces/actual sources; select true crop and isolate work.
2. Worker TB first; Manager observes missing-module RED before writing RTL.
3. New one-beat crop register and accepted-input coordinates; inspect actual sources.
4. Run bounded XSim matrix, independent Python comparisons, Lint and OOC synthesis.
5. Review all transcripts/dumps/coverage/resource diagnostics and source preservation.

## Interface and parameters

New module axis_roi_crop. Same aclk, synchronous active-low aresetn, s_tdata[7:0],
s_tuser[0:0], s_tlast, s_tvalid, s_tready and m_* as current Gray/Binary stages.
ROI input may be Gray8 or Binary8. Preserve pixel values inside the crop; outside
pixels deliberately produce no output. SOURCE interfaceA stays RGB888 before Gray;
this new module is an internal8-bit stage, not a governed interface replacement.

Integer parameters FRAME_WIDTH=320, FRAME_HEIGHT=240, ROI_X0=0, ROI_Y0=0,
ROI_X1=FRAME_WIDTH, ROI_Y1=FRAME_HEIGHT. Window is half-open:
ROI_X0 <= x < ROI_X1 and ROI_Y0 <= y < ROI_Y1. Default full-frame is pass-through.
Width/height positive, 0<=X0<=X1<=WIDTH and 0<=Y0<=Y1<=HEIGHT. Equal bounds give
empty ROI; reversed, negative/out-of-range or zero frame dimensions must be rejected.
No CSR or runtime configuration implemented; actual-material window remains uncalibrated.

## Handshake, coordinates, framing and reset

Accept input only when reset released and s_tvalid&&s_tready. Coordinates advance
only on accepted beats. Before the first accepted SOF, accept/drop unsynchronized data.
An accepted SOF is original coordinate(0,0), overriding previous input coordinates.
Input TLAST advances to the next row and resets x. Declared final-row TLAST ends
input synchronization; the next frame needs SOF. Normal source frames must match the
parameterized dimensions and SOF/EOL conventions. Arbitrary malformed-frame repair
and error CSR reporting are out of scope.

Each nonempty ROI produces (X1-X0)*(Y1-Y0) outputs per normal input frame, scanning
in source order. Output SOF is the selected source pixel(X0,Y0); output TLAST is the
selected last column(X1-1) of each retained row. Empty ROI produces no pixels and
no synthetic SOF/EOL token. Example6x4 ROI[2,5)x[1,3) produces3x2 pixels;
SOF at original(2,1), EOL at(4,1) and(4,2), even though those input flags were zero.

One-deep elastic output register. s_tready = !m_tvalid || m_tready; it is acceptable
to backpressure even outside ROI while an output is pending. Input rate under a
ready sink remains one beat/clock; consecutive retained pixels sustain one output
per clock. Crop gaps and source bubbles legitimately create output gaps, especially
through the3-stage validation chain; do not impose a single-stage throughput premise.

Generate sidebands from coordinates at input acceptance and register them with data.
Holding VALID while stalled must preserve data/SOF/EOL. Consuming a buffered output
while the accepted next input is outside ROI must clear VALID, not duplicate old data.
Input SOF does not flush an unconsumed output. Reset clears VALID/coordinates/sync,
drops a buffered beat, and waits for a fresh accepted SOF before new output. Invalid
payload is unspecified, consistent with WO0006/0007. Nominal retained-beat latency1clock.

Output coordinates are local to the ROI. Preserve configured origin(X0,Y0) for future
integration; no bbox, centroid, coordinate-compensation logic or feature interfaceB
change in this package. RGB->Gray->Binary->ROI is a validation chain, not a final
system placement decision; Route and camera remain unfrozen.

## New deliverables

fpga/rtl/axis_roi_crop.v; fpga/sim/tb_axis_roi_crop.sv;
python/refmodel/roi_crop_vectors.py; python/refmodel/check_roi_crop_output.py;
fpga/scripts/run_roi_crop_mcp.py; this WO; independent data/results/roi-* evidence.
Do not infer any of these planned paths exists or passes before actual checks.

## Acceptance coverage

Full frame, center, each boundary, single pixel, empty width/height and1x1 source;
invalid bounds/dimensions; multiple frames, source bubbles, deterministic seeded
random and continuous long stalls; stable held pixels; before-SOF drops and reset
at mid-frame/with pending output; fresh-SOF recovery; correct regenerated SOF/EOL.
Independent coordinate-coded/random Gray8 and unchanged H3 input, explicit counts
and packed pixel+sideband comparison. RGB->Gray->Binary128->ROI reference composed
in Python; do not change H3 GT/spec or claim cropped GT has already been integrated.

Use official Vivado MCP/Skill workflows. Standalone OOC synthesis must use a nontrivial
center ROI as well as the default full-frame behavior; record actual LUT/FF/DSP/BRAM,
Lint messages and diagnostics. Clock-only timing is not full timing convergence.
No FPGA board test, actual material segmentation/counting or live camera claimed.

## Verification and acceptance record · 2026-10-12

Offline implementation and Manager acceptance completed in this isolated worktree.
No new commit, push, PR or PR1 merge was made. The six new delivery files above
are present, not merely planned. Old RTL/scripts, protected docs and original
checkout contents remain intact. No live camera or board evidence is implied.

Native Manager MCP session vivado-20261012-182410-02a4ed6a captured missing-module
RED (VRFC10-2063), then a directed6x4 input24/output6 GREEN test proving the cropped
3x2 values and regenerated SOF/EOL. Logs are under data/results/roi-manager-20261012.
Native tools used vivado_start, vivado_execute and vivado_todos; no Vivado batch
substitute. The test was written and run before the new RTL was added.

Final full matrix: data/results/roi-crop-20261012-184057. All24 positive runs and
8 DUT parameter guards completed. Manager independently read complete positive
compile/elaboration/runtime logs, checked PASS/count/coverage gates, reran the
Python checker against every actual dump and checked each invalid guard's exact
frame/window values and time0 fatal. This prevents stale failed logs being mistaken
for the correct parameter rejection.

[Measured: roi-crop-20261012-184057] main matrix input3687135 accepted beats,
output1540059 cropped pixels, data mismatches0, SOF/EOL mismatches0. The different
input/output counts are intentional true crop. Empty windows produce no output.
Longest covered output stall50 cycles. Every positive job checks pre-SOF suppression
and reset recovery; every nonempty job checks pending-output reset and SOF resync
without flushing a buffered transfer. Continuous runs have no source bubbles or
unexpected stalls and check one output per clock within each retained row.

Cases:6x4 full/center/bottom-right single pixel/empty width/empty height,1x1 input,
2x2 power-of-two input,320x240 H3 full/center/left-border column. The12 H3 jobs each
use the unchanged3 PNGs plus one independently seeded random-color frame. Input
PNG SHA256 and RGB/Gray values are checked independently; cropped output is compared
as packed10-bit data/SOF/EOL. Direct Gray and RGB->Gray->Binary128->ROI paths are
both covered. Invalid zero dimensions, negative lower bounds, reversed bounds and
endpoints beyond the frame are rejected by the DUT's own parameter contract.

Additional accepted runner checks:

- roi-crop-20261012-184645: focused default/full-frame lint, authoritative count0.
- roi-crop-20261012-184736: focused center OOC synthesis success.
- roi-crop-20261012-184847: combined all-phase small-center smoke,3 positive runs,
  Python comparisons, lint0 and center OOC synthesis. This exercises final scripts.

The runner's isolation setup markers changed after the full matrix to prevent
MCP duplicate-request suppression when lint and synth run consecutively. Only the
runner hash changed; RTL/TB/vector/checker hashes still match the full matrix.
The combined run records the final runner hash and validates the final all-phase
entry. No historical hash file was rewritten to conceal this distinction.

| Standalone OOC parameters | LUT | FF | DSP | BRAM |
|---|---:|---:|---:|---:|
| Default320x240 full-frame | 28 | 29 | 0 | 0 |
| Center[80,240)x[60,180) on320x240 | 34 | 29 | 0 | 0 |

Default resource evidence: roi-manager-20261012/utilization-full.rpt. Center is
confirmed by both Manager native utilization-center.rpt and final combined runner
resource-summary.json. Both native syntheses report0 synthesis warnings/errors/
critical warnings; no latches. Default and center RTL lint report0 messages.

Official empty-table lint parser again reads trailing waiver prose as two bogus
rows. Raw parser outputs are retained; the unchanged official parser's JSON support
produces canonical rows=[] based on Vivado's explicit zero count. No waiver or
severity downgrade. Detailed lint analysis is in Manager evidence under
vivado_agentic_ai_reports/rtl-lint/rtl_lint_report.md.

Diagnostic handling: TB driver reset sampling was corrected to the first active
clock edge, and resync wait samples after the monitor's active-edge observation
to avoid sending SOF twice. Failed development runs183701/183943 are retained but
not acceptance evidence. The transfer-edge old-output check remains enabled.
No DUT repair was needed for those testbench issues. glbl generic propagation and
oversized vector display warnings concern simulation setup/display, not lost data.
Vivado's long-path advisory occurred but compile/elaboration/simulation completed.
Clock-only OOC timing has no I/O delays and HD.CLK_SRC is unset; no timing convergence.

Four accepted stdio MCP phases contain164 actual tool calls; raw tool requests/
responses and schemas are preserved separately in each evidence directory.
Manager native baseline/lint/synthesis calls are additional. Summary and source
provenance: roi-manager-20261012/source-provenance-and-mcp.json.
Manager full matrix recheck: roi-crop-20261012-184057/manager-verification.json and
manager-python-recheck.log. Final source hashes:184847/source-hashes.json.

The135 tracked baseline files were snapshotted after H3 regeneration.43 generated
config/GT JSON files had checkout-formatting differences; their parsed JSON was
equal to HEAD, and only these newly generated metadata files in this new worktree
were restored to checkout formatting. H3 PNGs/spec/rules and original checkout
were untouched. Remaining tracked sources match baseline hashes; tracked Git
status is clean. Details: roi-manager-20261012/h3-metadata-restoration.json.
Independent read-only Worker review found no concrete RTL/validation blocker.

## Reproduce

From this worktree, with Python3.11+, the existing requirements and local Ross setup:

```powershell
C:\fpga\material-counter\.venv\Scripts\python.exe fpga\scripts\run_roi_crop_mcp.py --phase all
```

The existing H3 inputs can be regenerated with the unchanged command documented in
frontend-handoff.md. Each evidence directory must be new. --phase verify/lint/synth
permits focused runs; --case small-center --skip-invalid-parameters is a bounded
all-phase smoke, not a substitute for the full matrix. --phase red intentionally
omits the DUT and is a development-only expected failure.

Before team publication, use an explicit new-file/evidence list and a separate PR
scope for WO-0008. Do not publish .codex/config.toml, all cached projects or every
data/results directory. Actual-material window calibration, full-system timing,
hardware integration and FPGA validation remain pending.
