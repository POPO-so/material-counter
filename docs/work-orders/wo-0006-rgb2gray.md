# WO-0006 · AXI4-Stream RGB888 to Gray8

## Objective

Implement and independently verify one common PL frontend stage. This work package
does not freeze a Route or change the governed source/feature/CSR interfaces.

## Background

Audit date: 2026-10-12, branch `feat/pl-stream-skeleton`. Actual source contains the
untracked one-beat skeleton and self-checking TB; README/PROJECT_INDEX still describe
the earlier no-RTL baseline. Preserve those documents and distinguish source from
verification. H3 supplies Gray8 320x240 PNGs; replication into R/G/B alone cannot
verify color weights, so add explicit color vectors.

The plan's 2026-11-03 delivery is 22 calendar days away; 2026-10-28 functional freeze
is 16 days away. No Route freeze is established by the audited records.

## In Scope

- Standalone RGB888 -> Gray8, AXI handshake, SOF/EOL propagation, one elastic register.
- Integer contract: `floor((77*R+150*G+29*B)/256)`, unsigned 16-bit sum, no rounding.
- R=`s_tdata[23:16]`, G=`[15:8]`, B=`[7:0]`. This local module convention must be
  reconciled with a future source adapter; interface A does not yet assign channels.
- Inputs: `aclk`, synchronous active-low `aresetn`, `s_tdata[23:0]`, `s_tuser[0:0]`,
  `s_tlast`, `s_tvalid`; output `s_tready`.
- Outputs: `m_tdata[7:0]`, `m_tuser[0:0]`, `m_tlast`, `m_tvalid`; input `m_tready`.
- A beat transfers only on VALID && READY. Output and sidebands hold during a stall.
  Reset discards a pending beat and clears valid; payload while invalid is unspecified.
- H3 replay + independent Python golden vectors + bounded XSim, lint and OOC synthesis.

## Out of Scope

ROI, threshold, filter, background storage, morphology, feature extraction, tracking,
CSR, DMA, camera, display, bitstream and board testing. No configuration changes,
protected-document edits, automatic commit or push.

## Deliverables

`fpga/rtl/axis_rgb2gray.v`, `fpga/sim/tb_axis_rgb2gray.sv`,
`python/refmodel/rgb2gray_vectors.py`, `python/refmodel/check_rgb2gray_output.py`,
`fpga/scripts/run_rgb2gray_mcp.py`, this WO,
and dated raw evidence under `data/results/rgb2gray-*`.

## Step-by-step Tasks

1. Manager fixes the arithmetic, reset, channel and handshake contract above.
2. Worker writes TB first; Manager runs missing-module elaboration as RED evidence.
3. Worker implements only the selected stage; Manager inspects actual source.
4. Python makes 512 directed/seeded vectors plus three existing H3 frames (230912 beats).
5. Use official Ross stdio MCP schemas to run the existing skeleton regression, then
   RGB-to-gray twice (always-ready and deterministic multicycle backpressure).
6. Require PASS, stall/bubble/reset coverage and independent dumped-output comparisons.
7. Run Vivado lint and constrained OOC synthesis; read diagnostics/resources/timing.
8. Verify original protected/source files and global/project configuration unchanged.

## Acceptance Criteria

- Explicit XSim TEST_PASS in both modes; exact beat count, data and SOF/EOL equality.
- No loss, duplication, reordering; stable stalled payload and valid; reset flush tested.
- Coverage of neutral grays 0..255, primary colors, white/max sum, seeded colors,
  source bubbles, sink stalls, and three complete 320x240 H3 frames.
- RTL output dumps equal golden byte-for-byte; reconstructed frames equal H3 pixels.
- Lint report and synthesis diagnostics inspected, no unexplained critical failure.
- OOC resources and 10ns clock report recorded without claiming implementation closure.
- No camera/DMA/board or counting-accuracy claim from these tests.

## Tests

From repository root:

```powershell
.\.venv\Scripts\python.exe python\refmodel\rgb2gray_vectors.py --out data\results\rgb2gray-vectors-<new-id>
.\.venv\Scripts\python.exe fpga\scripts\run_rgb2gray_mcp.py --phase verify --vectors data\results\rgb2gray-vectors-<new-id>
.\.venv\Scripts\python.exe -m pytest python\synth\tests -q
git diff --check
```

The vector directory must be new. RED is a development-only expected missing-module
failure and is not run after the RTL exists. Native chat tools were unavailable at
audit time; the runner uses the already-installed official server's real JSON-RPC
MCP protocol, recording raw calls, not a direct Vivado batch replacement.

## Risks

- H3 has no color scene; only directed colors validate channel order and coefficients.
- Single-register arithmetic may need pipelining once integrated; OOC timing does not
  establish full-system timing or end-to-end latency.
- Synchronous reset drops buffered data; a future frame/DMA reset protocol is separate.
- A fixed integer luma approximation is not a camera-calibrated segmentation model.
- Documented 100MHz is a target; no measured camera FPS or board performance here.

## Dependencies

Existing Vivado 2026.1/Ross configuration, Python environment/Pillow, the skeleton,
H3 PNGs, `docs/02_interfaces.md` internal Gray8 intent. Official Skill workflows:
`vivado-simulate-rtl`, `vivado-rtl-lint`, `vivado-rtl-elaboration-analysis`.

## Suggested Branch

Keep the existing `feat/pl-stream-skeleton` checkout; it already has untracked user
work. Do not reset, stage everything or create a conflicting checkout.

## Next development order

1. This Gray8 stage (bounded common frontend).
2. Gray8 -> binary fixed threshold (define `>=` versus `>` and bright foreground;
   H3 has no frozen threshold; use data-derived reference tests).
3. ROI (first agree true crop versus mask, half-open bounds, SOF/EOL regeneration
   and frame dimensions; add border/empty ROI/backpressure tests).
4. Run Python Route A/B comparison, then implement only the formally selected bounded
   feature stage. Build PS tracking/counting separately and verify frame atomicity.
5. Integrate DMA/replay and real input, constrain/implement, then run board acceptance.

Planning estimate for stages 1–3: each 1–2 focused workdays, subject to verification;
not measured completion promises. Defer morphology/background complexity until evidence
shows it is needed. Full-system and hardware readiness remain separate gates.

## Acceptance record · 2026-10-12

Manager verified the final automatic run (process exit 0) at
`data/results/rgb2gray-verify-20261012-131451/`; raw MCP JSON-RPC transcript,
per-mode compilation/elaboration/runtime logs, pixel comparison, source hashes,
canonical lint CSV and OOC reports are preserved. Existing H3 pytest: 7 passed.

Both modes: 230912 input/output beats, zero pixel/sideband mismatches, one reset flush.
Always-ready: 0 stalls, 23091 source bubbles. Backpressure: 87813 output stalls,
86187 blocked input cycles, 22765 source bubbles. All three reconstructed H3 frames
equal their source pixels in both modes.

OOC synthesized: 90 LUT, 11 FF, 0 DSP, 0 BRAM; no latches. Synth 8 elaboration
parser: 8 INFO, 0 WARNING/CRITICAL/ERROR. Lint: **2 warnings**, neither waived:
ASSIGN-1 (sum width conservatively inferred as 18, but actual bounded sum is
`(77+150+29)*255=65280`, including first partial sum <=57885) and ASSIGN-6
(low 8 sum bits intentionally discarded for truncation). Read the canonical CSV
and the official per-rule guides; do not interpret this as a zero-warning lint run.

10ns clock-only synthesis report WNS=8.089ns describes internal control paths.
29 input ports and 12 output ports lack delay constraints; HD.CLK_SRC is unset.
It does **not** prove the input arithmetic path, full integration timing or 100MHz
board operation. Wave-window array-size warnings affect displaying vector memories,
not their scoreboard checks. No implementation, bitstream or board test performed.

The current native chat tool catalog did not expose Ross. Official Skills were read
from the existing project installation and the existing server was called through
real MCP stdio. A relevant AMD Doc Search MCP query returned UG900 plusarg/simulation
documentation. Configuration and protected documents were not changed.
