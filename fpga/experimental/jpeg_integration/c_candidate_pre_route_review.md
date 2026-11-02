# C candidate — pre-route review material (SerialClk, capture, mailbox, hold)

Date: 2026-11-02
Worktree: `C:/fpga/wt/claude-jpeg-20261102`, branch `codex/claude-jpeg-20261102`
Stage root: `C:/w/jj2026m` (fresh staging; no route)
Scope: synthesis and offline review only. No route, board access, deployment,
push, or merge. Candidate RTL `811db981...` is unchanged.

## 1. SerialClk `Timing 38-249` (item A, reopened)

### 1.1 Root cause

The synthesis log contains:

```text
CRITICAL WARNING: [Timing 38-249] Generated clock
display_i/hdmi_out/frontend/rgb2dvi_0/U0/SerialClk has no logical paths from
master clock hdmi_out_PXL_CLK_O.
[display_vtc_out_0_clocks.xdc:2]
```

The vendor scoped constraint
`display_rgb2dvi_0_0/src/rgb2dvi_clocks.xdc` still defines

```tcl
create_generated_clock -source [get_ports PixelClk] -multiply_by 5 [get_ports SerialClk]
```

but this design sets `kGenerateSerialClk=false`: `SerialClk` is supplied by the
`axi_dynclk` MMCM `CLKOUT0`, not generated from `PixelClk` inside the IP. The
vendor definition therefore has no logical path from its master and emits
`38-249` before `display_timing.xdc` overwrites it. The later overwrite makes
the final clock correct (`clocks.rpt`: master `I`, source MMCM `CLKOUT0`,
period 2.000 ns), but the critical warning remains in the log and in the
capture.

### 1.2 Fix

`joint_build.tcl` now, after `generate_target all` and before `launch_runs`:

* disables only the in-context `rgb2dvi_clocks.xdc`;
* enables `display_timing.xdc` for synthesis;
* reads both properties back and fails if either differs;
* keeps the vendor OOC XDC untouched.

After `open_run synth_1` it asserts:

* exactly one `SerialClk` clock exists;
* period is 2.000 ns;
* master clock is `I`;
* source is `display_i/hdmi_out/frontend/axi_dynclk/U0/Inst_mmcme2_drp/mmcm_adv_inst/CLKOUT0`.

The synth capture review also rejects any `CRITICAL WARNING` in the native-log
range, and a dedicated `synth_log_clean` check fails if `38-249` is still
present. The candidate constraint is therefore checked after regeneration, not
just assumed from the earlier run.

Status: confirmed in the formal jj2026p receipt. `SerialClk` is 2.000 ns,
master `I`, source MMCM `CLKOUT0`; `38-249` is absent from the synth native
slice. The candidate `display_timing.xdc` is applied post-synthesis; the vendor
in-context clock file is disabled and read back.

## 2. Capture patch review (separate, narrow)

### 2.1 What was wrong

Commit `02b36bd2` changed the generic `ipcfg::capture` so that **any** negative
severity delta was skipped:

```tcl
if {$emitted < 0} { ... continue }
```

That applied to every capture and every severity, including `CRITICAL WARNING`
and `ERROR`, and it did not preserve an auditable native-log review. It could
therefore hide a real clock critical if the in-process counter reset.

### 2.2 What is now in place

* The generic `ipcfg::capture` patch is reverted; the original strict counter
  equality check is restored (`git diff` shows only the seven removed lines).
* The adaptation lives in the project layer:
  * `joint::capture_synth` in `joint_common.tcl` accepts only the exact
    `CAPTURE_FAIL: ... WARNING emitted=-N exported=M` signature;
  * it requires zero `CRITICAL WARNING` and zero `ERROR` in the capture record;
  * it passes the original FAIL receipt, the exact native-log byte range, and
    the WARNING lines to `joint_capture_review.py --scope synth`;
  * the supplemental review independently parses the native-log range, rejects
    any `ERROR` or `CRITICAL WARNING`, and checks that the WARNING lines match
    the original receipt exactly;
  * the original FAIL receipt and `.native.log` slice are preserved; only a
    separate `.supplement.json` carries the supplemental result;
  * any other capture error or any non-WARNING message fails closed.
* `joint_capture_review.py` keeps the existing `refresh_region` behavior as the
  default; `--scope synth` only removes the single-operation result-token
  comparison (the synth capture wraps a command block, not a returning
  operation). All native-range and severity checks remain.

An intermediate jj2026n run exposed a second adapter issue: passing 215
`--warning-message` arguments exceeded the Windows command-line limit
(`file name too long`). The synth capture now writes the exact warning lines to
a short `NNN-synth.messages.txt` file and passes only its path; the Python
review reads that file. The same review was replayed against the real jj2026n
native-log range and returned PASS with
`WARNING=215, CRITICAL WARNING=0, ERROR=0`.

Unit tests: `python -m unittest fpga.experimental.jpeg_integration.test_joint_capture_review -v`
— 20 tests, all PASS, including synth-scope cases that reject a `Timing 38-249`
critical and a native `ERROR`, and a messages-file case.

The formal jj2026p synth capture review passed with
`WARNING=215, CRITICAL WARNING=0, ERROR=0`. The original FAIL receipt remains
`C:/w/jj2026p/evidence/synth/006-synth.tcldict` (sha256 `f9da858c...`); the
supplement is `006-synth.supplement.json` (sha256 `8167208f...`).

## 3. Explicit mailbox endpoint selection

`joint_prepare.py` now generates
`<stage>/snapshot/constraints/candidate_offload_cdc.xdc` and binds its SHA-256
in the manifest. `joint_connect.tcl` adds this file and deliberately does not
add the frozen `scripts/standalone/offload_cdc.xdc`.

The candidate XDC selects destinations by an anchored basename pattern:

```tcl
set cfg_dst [get_cells -hier -regexp {.*binary_overlay.*/(enable_p|id_p|count_p|boxes_p|box_valid_p|line_p|line_valid_p)_reg(\[[0-9]+\])?$}]
```

The `/` anchor removes the accidental `id_p` match inside `valid_p`. The file
asserts:

| Group | Expected |
|---|---:|
| `enable_p` | 1 |
| `id_p` | 32 |
| `count_p` | 5 |
| `boxes_p` | 640 |
| `box_valid_p` | 16 |
| `line_p` | 10 |
| `line_valid_p` | 1 |
| total | 705 |

It also asserts the publish side is 33 source / 33 destination registers, and
applies the false path and both `max_delay -datapath_only 10` constraints.

Frozen original: `C:/w/jj2026m/snapshot/scripts/standalone/offload_cdc.xdc`
has the same SHA-256 as `C:/w/jj2026j/snapshot/.../offload_cdc.xdc`
(`3a8be351...`). It is retained on disk and not used by the candidate project.
The connect live-file list contains the candidate XDC and not the frozen
original.

At synthesis, `joint_build.tcl` re-reads the destination set and asserts 705
total, 32 `id_p`, 16 `box_valid_p`, 1 `line_valid_p`, and that all 17 new
endpoints carry `MaxDelay Path 10.000ns -datapath_only`.

Status: confirmed in the formal jj2026p receipt (candidate XDC sha256
`a1a75ca5...`): 705 total, `id_p` 32, `box_valid_p` 16, `line_valid_p` 1,
publish 33/33; all 17 new endpoints carry `MaxDelay Path 10.000ns
-datapath_only`, min slack +8.968 ns. `exceptions.rpt` row 19 shows the
anchored explicit regex.

## 4. Remaining hold review (rgb2dvi, 8 endpoints)

### 4.1 Clock and reset topology

The 8 failing endpoints are intra-clock, on `hdmi_out_PXL_CLK_O` (10.000 ns),
not CDC:

* source: `hdmi_out/frontend/rgb2dvi_0/U0/LockLostReset/SyncAsyncx/oSyncStages_reg[1]/C`
  (FDPE);
* destination: eight OSERDESE2 `RST` pins in
  `ClockSerializer/{SerializerMaster,SerializerSlave}` and
  `DataEncoders[0..2].DataSerializer/{SerializerMaster,SerializerSlave}`;
* path: `ResetBridge` -> `SyncAsync` second stage -> `pRstLck` -> OSERDESE2 `RST`;
* Slack `-0.167 ns` (hold, fast corner), data path 0.484 ns, clock skew
  0.145 ns, requirement 0.000 ns.

`SyncAsync` is a two-stage synchronizer: `aReset` forces both stages to
`kResetTo`; `aIn` is shifted on `OutClk`; `oOut` is the second stage. `ResetBridge`
drives `aReset` from the asynchronous lock/reset input and `aIn` from the
inactive level, so `pRstLck` is released synchronously. The first stage input
and the PRE/CLR pins are false-pathed by the vendor `rgb2dvi.xdc`; the
second-stage Q to OSERDESE2 `RST` is intentionally still timed.

### 4.2 Constraint validity

* `hdmi_out_PXL_CLK_O` is a generated clock from the `axi_dynclk` BUFR output
  (10.000 ns); source and destination are both on it, so the hold check is a
  real intra-clock check, not a CDC artifact.
* `SerialClk` is a separate 2.000 ns generated clock from MMCM `CLKOUT0`; it is
  not on this path.
* The vendor false paths cover only the synchronizer capture stage and the
  PRE/CLR pins. No false path or waiver covers the `oSyncStages_reg[1]` ->
  `OSERDESE2/RST` path.
* The path is present in the frozen-baseline hold report as the same
  `-0.167 ns` `rgb2dvi` path, so it is pre-existing vendor behavior, not
  introduced by the overlay candidate.
* No vendor RTL change, `false_path`, or waiver is added in this round.
* Final jj2026p timing: WNS +1.006 ns / TNS 0 / 0 setup failing; WHS -0.167 ns
  / THS -1.335 ns / 8 hold failing. DRC 97 checks and methodology 7 checks are
  all Warning; no Error or Critical Warning. The previous `XDCC-1`/`XDCC-7`
  scoped-clock-overwrite warnings are gone because the vendor in-context clock
  file is now disabled.

### 4.3 Implementation verification plan (after route authorization)

1. Re-run `report_timing -delay_type min` and `max` on the implemented design
   for the exact source/destination pair and record PXL WHS/THS.
2. Read back the implemented clock tree (MMCM/BUFR, `report_clock_utilization`,
   `report_clock_interaction`) and confirm the path remains intra-PXL.
3. Read back the reset bridge topology and the OSERDESE2 `RST` connectivity;
   confirm the synchronizer release is still synchronous.
4. If hold remains negative, evaluate placement/routing or a reviewed
   implementation-only fix. Do not modify vendor RTL and do not add a
   `false_path`/waiver without Manager approval.
5. If the path closes at implementation, record the before/after report slices;
   if it does not, escalate the vendor-IP constraint question with the exact
   report evidence.

## 5. Formal synth receipt (jj2026p)

Four formal attempts were used; the first three are evidence for the fixes, the
last is the receipt:

| Run | Manifest | Result |
|---|---|---|
| jj2026m | `805d4ac0...` | Connect PASS. Vendor XDC disabled and `38-249` absent. The candidate XDC used `if`/`foreach`, which XDC does not support (`Designutils 20-1307`, 5 criticals). The capture refused the real criticals. |
| jj2026n | `e2f49698...` | Connect PASS. XDC assertions moved out; `display_timing.xdc` restored to post-synthesis. 0 criticals, 0 errors. Full netlist verified: `SerialClk` 2.000 ns/master `I`; 705/32/16/1/17 endpoints; all 17 new paths `MaxDelay 10`, min slack +8.968 ns. Capture refused only because 215 warning arguments exceeded the Windows command line. |
| jj2026o | `1e9a9778...` | Connect PASS. Messages-file capture review PASS; `SerialClk` readback PASS. Mailbox readback failed on a Tcl regex quoting bug (`\\[`); no receipt. |
| jj2026p | `1bc1ffc9...` | Connect PASS; formal receipt `SYNTHESIZED_CDC_REVIEW_PENDING`. Capture supplement PASS (215 WARNING / 0 CRITICAL / 0 ERROR); `SerialClk` 2.000 ns/master `I`; mailbox 705/32/16/1/17, min slack +8.968 ns; 8 report hashes bound. |

Formal receipt:

* path `C:/w/jj2026p/joint-synth.tcldict`, sha256 `f2ddc23b...`
* staging manifest sha256 `1bc1ffc9...`
* candidate mailbox XDC sha256 `a1a75ca5...`
* original capture FAIL receipt sha256 `f9da858c...`
* supplemental review sha256 `8167208f...`
* reports `C:/w/jj2026p/reports/synth/`: `cdc.rpt`, `clock-interaction.rpt`,
  `clocks.rpt`, `drc.rpt`, `exceptions.rpt`, `methodology.rpt`, `timing.rpt`,
  `utilization.rpt` (all SHA-256 values are in the receipt).

No PASS value is written by hand. Status is
`SYNTHESIZED_CDC_REVIEW_PENDING`; route remains blocked until Manager review.

## 6. Open items

1. SerialClk `38-249` — resolved; formal jj2026p receipt confirms 2.000 ns /
   master `I` and absence of `38-249`.
2. Capture adaptation — resolved; generic patch reverted; project-layer review +
   20 unit tests PASS; jj2026p supplement 215 WARNING / 0 CRITICAL / 0 ERROR with
   the original FAIL receipt preserved.
3. Mailbox selection — resolved for synthesis; explicit XDC bound (sha256
   `a1a75ca5...`); 705/32/16/1/17 endpoints, `MaxDelay 10`, min slack +8.968 ns.
4. Hold `-0.167 ns` — open; pre-existing vendor path; implementation
   verification plan above; no vendor RTL, `false_path`, or waiver changes.
5. Route, board, deployment, push, and merge remain prohibited.
6. Route remains blocked pending Manager CDC/constraint review of the jj2026p
   receipt and the implementation hold plan.