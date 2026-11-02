# JPEG + BRAM region joint integration scripts

These `joint_*` files prepare an isolated candidate derived from the fixed
`c5a86807c93e6ed572e2f75e13a6f9c7f1320321` 21-file source snapshot. They do not
freeze a Route, deploy software/hardware, or change the baseline project.
`region*`, `rle_region_features.v`, and the JPEG wrapper remain owned by their
respective implementation/verification tasks. The BRAM region validation was
submitted separately at `f953885`; its `region_results.json` is read as evidence.

| Entry | Purpose |
|---|---|
| `joint_prepare.py` | Offline preflight; optional explicit clone staging; source/evidence hash, live-file, green and route-review gates. Never launches Vivado. |
| `joint_probe.tcl` | Open only the clone, check actual source associations/top/IP repos and unchanged baseline parameters/architecture using `ipcfg::capture`. No integration or build. |
| `joint_connect.tcl` | After green gates, add the exported HLS IP and connect it in the cloned BD; validate/read back/save. |
| `joint_build.tcl` | Explicit `synth` or `route` stage with source identity/resource/report gates; no bitstream. |
| `joint_common.tcl` | Shared path/readback/evidence helpers. Sourcing it alone has no side effects. |

## Preflight and staging

Run from the task worktree. Use a short build root: Windows Vivado has real
248/260-byte generated-path limits. Stage checks the predicted XCI/module-ref
paths and refuses a long root before copying. The first long-root probe failed
with `Common 17-680`; its separate failed log is retained under
`.runtime/jpeg-joint-selftest-20261101-1348/native-probe.log`.

```powershell
$repo = 'C:/Users/14173/Documents/material-counter/.runtime/worktrees/jpeg-probe-20261101'
$scripts = "$repo/fpga/experimental/jpeg_integration"
$python = 'C:/AMDDesignTools/2026.1/Vivado/tps/win64/python-3.13.0/python.exe'

# Read-only; optional --result must name a new JSON file.
& $python "$scripts/joint_prepare.py" --snapshot 'J:/'

# Safe staged selftest, allowed before HLS RTL green. Omitting --hls-green
# leaves the clone unable to pass connect/synth/route gates.
& $python "$scripts/joint_prepare.py" --snapshot 'J:/' `
  --region-green "$scripts/region_results.json" `
  --hls-ip 'C:/w/mc-jpeg-axis-hls-20261101/hls/impl/ip' `
  --stage 'C:/w/jj1101d'
```

The last command is the actual short-root stage selftest command used during
implementation; it refuses an existing directory. Use a fresh root for each
attempt, preserving failures. The script also accepts explicit `--xpr`,
`--region`, `--jpeg-source`, `--ipcfg`, and resource limits. Omitted `--xpr`
resolves to `data/results/standalone-20261101-055148-g2-integration/project/display.xpr`
inside `--snapshot`. The original 21 hashes, declared snapshot overrides,
XPR/BD and directly referenced files are checked. The component contract is
derived from the actual `component.xml`, with widths, AXI4LITE, reset polarity,
clock association and register checks; missing exports produce `PENDING_HLS`.

Staging copies the manual sources and XPR/BD into `<stage>/snapshot`, copies
the complete exported HLS IP into `<stage>/ip/jpeg_axis_gray`, and replaces the
two cloned `rle_region_features.v` copies with the selected BRAM version.
`joint_manifest.json` records original/replacement hashes. The cloned XPR's
`Project Path` and every `File Path` point into the clone. Its top is fixed to
`display_wrapper`; the wrapper declaration is checked. Official baseline IP
catalog repositories remain external read-only dependencies. The BD has no
absolute source paths; unexpected future absolute paths are rejected.

The native probe verifies actual `get_files -all`, top and `IP_REPO_PATHS`,
not just files existing on disk. Replaced region RTL must be the file Vivado
actually associates with the project. Generated products may appear within
the clone; a manual source escaping to J:/baseline or another worktree fails.
All baseline and staged manual-source hashes are rechecked after each stage.

## Native batch invocation

MCP startup was unavailable in the earlier environment; direct Vivado batch
is the approved fallback. `-native-log` must be exactly the same fresh file
passed to Vivado `-log`; `ipcfg::capture` binds to that actual native log.
Each stage retains separate `.tcldict`, native message ranges and readbacks.
Run in a fresh Vivado process with cwd under the new build root.

```powershell
$vivado = 'C:/AMDDesignTools/2026.1/Vivado/bin/vivado.bat'
$stage = 'C:/w/jj1101d'
Push-Location $stage
try {
  & $vivado -mode batch -notrace -source "$scripts/joint_probe.tcl" `
    -log "$stage/native-probe.log" -journal "$stage/native-probe.jou" `
    -tclargs -spec "$stage/joint_spec.tcldict" -native-log "$stage/native-probe.log"
  if ($LASTEXITCODE -ne 0) { throw 'Joint probe failed; preserve its log.' }
} finally { Pop-Location }
```

Both Tcl initialization and builds cap `general.maxThreads=2` with readback.
The build entry also installs checked PRE hooks on synthesis and implementation
child runs so the limit applies inside those processes; existing hooks cause
a stop for review rather than being overwritten. Run launch jobs
are 2. No command silently resets/removes old runs, overwrites evidence, or
starts a later stage on failure.

## Green gates and actual integration

The committed region receipt is accepted only with `status:"PASS"`, matching
`source_sha256` and all evidence hashes, `reference_diff:"PASS"`,
`limits:"PASS"`, positive `memory_gate.stats_bram36`/`row_bram36`, and zero
`memory_gate.stats_lutram`. It is not rewritten or given invented `checks`.

Kant/TB's HLS green receipt must bind the final exported component and source:

```json
{
  "status": "PASS",
  "source_sha256": "<JPEG cpp SHA-256>",
  "component_sha256": "<actual final component.xml SHA-256>",
  "checks": {"csim": true, "csynth": true, "cosim": true},
  "evidence": [{"path": "<actual log/report>", "sha256": "<SHA-256>"}]
}
```

This schema is a source-bound verification receipt, not a replacement for
checking the retained reports. A pending/non-PASS receipt cannot authorize a
build. After both receipts pass, create a **new** stage with `--hls-green` and
`--region-green`. Connection and build entries reject missing receipts.

Manager launches the entries separately, with the same native batch pattern:

1. `joint_connect.tcl -spec ... -native-log ...`
2. `joint_build.tcl -spec ... -native-log ... -stage synth`
3. Review synthesis resource/DRC/methodology/CDC/clock/timing reports; supply a
   source-bound route review receipt.
4. `joint_build.tcl -spec ... -native-log ... -stage route -cdc-review <receipt>`


Connection changes only the clone: control NUM_MI 12→13, M12 appended at
`0x43C80000/0x10000`, 50 MHz FCLK0/reset_rle; DMA length width 18→19 with DRE=0
and original widths retained. MM2S is detached from `image_pipeline/s_axis`,
then connected through the actual exported JPEG input/output interfaces.
M00–M11 endpoints and addresses, GP0/HP0/HP1/HP2 paths, FCLK0/1/2, HDMI pixel
clock/reset and video path endpoints are compared before/after. Parameters,
port widths and assigned control addresses receive live readback.

The current final component exports `s_axi_control`, `jpeg_axis_in`,
`gray_axis_out`, `ap_clk`, `ap_rst_n`. Its register offsets are CTRL=0x00,
jpeg_size=0x10, frame_id=0x18, status=0x20, status_ctrl=0x24. Each new preflight
derives these again and records the component hash. No JPEG done/status signal
is wired into overlay acceptance. PS must wait for completion and valid JPEG
status, require `status==0` plus valid RLE and FRG, then decide `mask_accept`.
With DRE=0, the MM2S memory address is 8-byte aligned (64-bit memory interface),
S2MM is 4-byte aligned, and the final MM2S beat carries byte-count-derived
TKEEP. This script does not implement the PS runtime.

Synthesis checks LUT/FF/BRAM/DSP against explicit limits (defaults: device
capacities 53200/106400/140 RAMB36 equivalents/220) and verifies stats/row BRAM
mapping. Route requires HLS cosim PASS and a Manager receipt containing:

```json
{
  "status": "PASS",
  "manifest_sha256": "<joint_manifest.json SHA-256>",
  "cdc_reviewed": true,
  "constraints_reviewed": true,
  "report_sha256": {
    "cdc.rpt": "<SHA-256>", "clock-interaction.rpt": "<SHA-256>",
    "timing.rpt": "<SHA-256>", "drc.rpt": "<SHA-256>",
    "methodology.rpt": "<SHA-256>"
  }
}
```

Route status is read from `report_route_status`, not an assumed design
property. Missing/unparsed routing counts fail closed with the saved report.
Setup/hold and DRC/methodology errors are gated; final CDC/constraints and board
acceptance remain Manager review items. No bitstream or deployment is emitted.

## Selftest evidence

On 2026-11-01 the final staged selftest `C:/w/jj1101d` passed 11 offline
checks (`checks/selftest.json`) and a real Vivado 2026.1 batch probe exited 0
with `JOINT_PROBE_PASS` (`native-probe.log`, completed 13:54:31 local time).
`evidence/probe/002-baseline_probe.tcldict` and its native-log slice record
capture status OK, no new WARNING/CRITICAL/ERROR within the readback window,
the actual top `display_wrapper`, and approved external IP repositories.
`evidence/probe/live-files-1.txt` contains actual project associations;
`architecture.tcldict` records GP0/HP0/HP1/HP2, M00–M11 and clock/video nets.
The project-load warnings about missing generated XCI products and existing
overlay clock metadata remain visible in the full native log; this result is
not a warning-free or full BD-validation claim.

Offline checks covered clone XPR paths/top/wrapper, two region replacements,
unchanged baseline hashes, missing HLS green, rejected route without green,
existing-stage overwrite refusal, missing export, Windows path budget,
live-file escape to baseline, and hash tampering. Negative fixtures are kept
under `checks/` and cannot enable any build. The stage manifest has no HLS
green receipt, so connect/synth/route remain blocked.

Earlier failures remain separate: the long worktree-root attempt hit Windows
path limits; `C:/w/jj1101a` exposed the helper's global-namespace load contract;
`C:/w/jj1101b` exposed the helper's accepted status-prefix contract. Those two
wrapper issues were corrected before the successful `jj1101c` and final
`jj1101d` probes. These attempts never launched synthesis or route.

Preflight and stage are independent of HLS RTL acceptance. This successful
safe probe proves clone/source association and unchanged baseline readback
only. JPEG BD connection, joint synthesis, routed timing/CDC closure and board
readiness still require their explicit Manager-authorized stages and evidence.

## Narrow `refresh_region` capture-counter review

`joint::capture_refresh_region` is used only for the `update_module_reference`
operation in `joint_connect.tcl`. It may add a supplemental review only when
the original `ipcfg::capture` receipt is `status=FAIL`, Tcl `code=0`, and its
entire `capture_error` exactly matches the recorded signed WARNING-counter
delta and exported count. The original FAIL `.tcldict` is kept unchanged. The review
independently reads only the receipt's native-log byte interval, verifies the
interval is in-file and aligned to complete lines, verifies the native WARNING
lines equal the exported WARNING lines one by one, checks before/after counter
delta against the error text, checks `update_module_reference` returned the
expected module-reference XCI path, and rejects missing ranges, unknown error
text, ERROR, CRITICAL WARNING, malformed lines, or mismatches. A new
`*.supplement.json` records this review beside the original capture receipt.
The full native-log hash is labeled as a whole-file snapshot at review time;
because Vivado may append later output, subsequent review matches the recorded
byte offsets and immutable range SHA-256 instead.
All other `joint::capture` operations retain their ordinary fail-closed
behavior; this is not a shared `ipcfg` change or general status override.

Both region source copies replaced by staging must be reflected in the live
candidate: the `image_pipeline` module-reference refresh has the narrowly
scoped counter-review above, while `binary_overlay` uses ordinary capture and
its own normalized XCI path readback. The image-pipeline exception does not
extend to `binary_overlay`; a matching counter reset there stops connect for
Manager review.

`joint_capture_review.py` is listed as an immutable source in each new joint
manifest. Its targeted unit tests are
`python -m unittest fpga.experimental.jpeg_integration.test_joint_capture_review -v`.
The new clone's manifest binds the current JPEG source SHA and all
`joint_*`/review script SHA values. For this work round `joint_build.tcl` caps
both synthesis and (if separately authorized) route launch jobs to 1, while
the Vivado process `general.maxThreads` remains capped/read back at 2.
Do not change the pinned baseline CDC constraints as part of this review;
evaluate actual CDC reports before proposing any additional synchronizer
exceptions.

## 2026-11-02 continuation status

T6 BD wiring/readback completed in isolated stage `T:/jj1102e`; receipt
`T:/jj1102e/joint-connected.tcldict` is `CONNECTED_NOT_BUILT`, with manifest
SHA-256 `1c5cb94478f7b3923b34ef24727e98aa68ae0440d8e18a5fe7294afa00fc5dd4`.
The PS `run_counter.py` camera/hardware entry now defaults to the raw-MJPEG
runner and `JpegRleDmaTransport`. `--jpeg-pl` remains a compatibility alias;
`--software-decode` explicitly selects the legacy OpenCV-decode + PL-RLE path.
The JPEG runner still requires `--execute`, pinned routed BIT/HWH hashes, boot
ID validation, and its existing camera/DMA preflight. Earlier board evidence
used the explicit alias; the revised default dispatch has local focused tests
and is included in the source-bound [v8 runtime package](../../../data/results/jpeg-pl-board-20261102/jpeg-pl-runtime-v8-release.json).
That bundle also contains the exact routed BIT/HWH and native C++ source; if no
`--native-lib`/`RLE_NATIVE_LIB` is supplied, the runner builds a source-hash
named `.so` before touching the PL (so the board needs `g++`). The bundle awaits
the user's next board run.

T7 joint synthesis and route completed in candidate `T:/jj1102l`. Final route
resources were 36,483 LUT, 38,734 FF, 30 BRAM36 + 55 BRAM18, and 49 DSP.
Timing closed at WNS +0.032 ns / WHS +0.020 ns with zero failing setup or hold
endpoints. All 69,364 routable nets were fully routed, with zero routing errors.
The final route CDC report has no CDC-13 critical findings. Its 998 CDC-15
warnings (965 frame-monitor mailbox paths and 33 overlay publish paths) and one
CDC-6 VTC internal path have been reviewed against the routed report and exact
constraints; all are classified and retained as warnings, not suppressed.
The hash-bound review and source reports are in
`data/results/jpeg-pl-board-20261102/route-cdc-review/`. DRC/methodology
findings are warnings only. The route receipt is `T:/jj1102l/joint-route.tcldict` (`ROUTE_PASS`). A
BIT was then generated directly from `joint-routed.dcp` (4,045,770 bytes; the
bitgen precondition DRC reported 0 errors), and the HWH from the same candidate
BD was packaged beside it. Their SHA-256 values and provenance are recorded in
`T:/jj1102l/bitstream/joint-bitgen-manifest.json`. The immutable route receipt
still says `board_ready=0`; neither file has been loaded onto the board, and no
physical HDMI acceptance has been performed. The route was recovered from the
saved synthesis checkpoint to avoid repeating the 33 OOC jobs; the standard
route status parser was corrected to compare Vivado's routable and fully-routed
net counts instead of expecting an `unrouted nets` row.
