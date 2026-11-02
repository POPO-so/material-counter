# C candidate — routed result (jj2026p)

Date: 2026-11-02
Worktree: `C:/fpga/wt/claude-jpeg-20261102`, branch `codex/claude-jpeg-20261102`
Stage root: `C:/w/jj2026p`
Status: `ROUTED_REVIEW_PENDING`; all user timing constraints met.
Scope: offline place-and-route only. No bitstream, board access, deployment,
push, or merge. No new exception, `false_path`, or clock relaxation was added.

## 1. Input binding

| Input | Path | SHA-256 |
|---|---|---|
| Manifest | `C:/w/jj2026p/joint_manifest.json` | `1bc1ffc9...` |
| Synth receipt | `C:/w/jj2026p/joint-synth.tcldict` | `f2ddc23b...` |
| Candidate mailbox XDC | `C:/w/jj2026p/snapshot/constraints/candidate_offload_cdc.xdc` | `a1a75ca5...` |
| Route review receipt | `data/results/jj2026p-route-review-20261102/route-review.json` | `bc6f5092...` |
| Original route capture FAIL receipt | `C:/w/jj2026p/evidence/route/005-route.tcldict` | `2a4a86b7...` |
| Route capture supplement | `C:/w/jj2026p/evidence/route_continue2/route-capture.supplement.json` | `1c61c9bb...` |
| Routed DCP | `C:/w/jj2026p/joint-routed.dcp` | `c45429ef...` |
| Route receipt | `C:/w/jj2026p/joint-route.tcldict` | `3443612f...` |

## 2. Timing

| Metric | Value |
|---|---:|
| Setup WNS | +0.126 ns |
| Setup TNS / failing endpoints | 0.000 ns / 0 |
| Setup total endpoints | 130455 |
| Hold WHS | +0.018 ns |
| Hold THS / failing endpoints | 0.000 ns / 0 |
| Hold total endpoints | 128065 |
| Pulse width WPWS / failing | +0.333 ns / 0 |
| Pulse width total endpoints | 43436 |

`report_timing_summary` states: **All user specified timing constraints are
met.** No `Slack (VIOLATED)` path remains in the routed timing report.

## 3. RGB2DVI reset hold endpoints (the 8 previously failing paths)

All eight converged at route; every slack is positive.

| Endpoint | Hold slack (ns) |
|---|---:|
| `ClockSerializer/SerializerMaster/RST` | +0.442 |
| `ClockSerializer/SerializerSlave/RST` | +0.378 |
| `DataEncoders[0].DataSerializer/SerializerMaster/RST` | +0.316 |
| `DataEncoders[0].DataSerializer/SerializerSlave/RST` | +0.253 |
| `DataEncoders[1].DataSerializer/SerializerMaster/RST` | +0.556 |
| `DataEncoders[1].DataSerializer/SerializerSlave/RST` | +0.503 |
| `DataEncoders[2].DataSerializer/SerializerMaster/RST` | +0.287 |
| `DataEncoders[2].DataSerializer/SerializerSlave/RST` | +0.341 |

The dedicated report is `C:/w/jj2026p/reports/route/rgb2dvi_hold.rpt`
(sha256 `64510adc...`). The minimum of the eight is +0.253 ns; the previous
synthesis-stage value was -0.167 ns.

## 4. Route status

| Item | Count |
|---|---:|
| Logical nets | 94,254 |
| Routable nets | 65,210 |
| Fully routed nets | 65,210 |
| Nets with routing errors | 0 |

No unrouted net remains. The report is `reports/route/route-status.rpt`
(sha256 `702ab00e...`).

## 5. CDC

| ID | Severity | Count |
|---|---|---:|
| CDC-3 | Info | 126 |
| CDC-6 | Warning | 1 |
| CDC-13 | Critical | **0** |
| CDC-15 | Warning | 1703 |

The overlay mailbox remains under the explicit `max_delay -datapath_only 10`
constraint; no CDC-13 critical path is reported. Report `reports/route/cdc.rpt`
(sha256 `52402fc2...`).

## 6. DRC and methodology

* DRC: 102 checks, all Warning (`DPIP-1` 46, `DPOP-1` 13, `DPOP-2` 19,
  `PDCN-1569` 4, `REQP-1839` 8, `REQP-1840` 8, `RTSTAT-10` 1); no Error or
  Critical Warning. `reports/route/drc.rpt` (sha256 `58b254de...`).
* Methodology: 7 checks, all Warning (`LUTAR-1` 4, `TIMING-9` 1, `ULMTCS-1` 1,
  `XDCB-5` 1); no Error or Critical Warning.
  `reports/route/methodology.rpt` (sha256 `acf75e51...`).

## 7. Exception coverage

`reports/route/exceptions.rpt` row 19 shows the explicit, anchored candidate
constraint:

```tcl
-from [get_cells -hier -regexp {.*binary_overlay.*cfg_mb_.*_reg.*$}]
-to   [get_cells -hier -regexp {.*binary_overlay.*/(enable_p|id_p|count_p|boxes_p|box_valid_p|line_p|line_valid_p)_reg(\[[0-9]+\])?$}]
max_dpo=10
```

The publish side remains covered by row 20. The 705/32/16/1/17 endpoint
selection and the `MaxDelay 10` exception were re-read at synth; no waiver was
added at route.

## 8. Post-route resources

| Resource | Used | Utilization |
|---|---:|---:|
| Slice LUTs | 35,275 | 66.31% |
| Slice Registers | 36,173 | 34.00% |
| Block RAM Tile | 57.5 | 41.07% |
| DSP48E1 | 49 | — |

## 9. Capture tooling note

The route itself completed successfully, but the formal route stage's generic
`joint::capture` failed because `launch_runs` resets Vivado's in-process message
counter (`CAPTURE_FAIL: ... WARNING emitted=-3 exported=6`; 0 critical,
0 error). The route capture was therefore handled exactly like the previously
approved synth case:

* the original FAIL receipt and native-log byte range are preserved
  (`evidence/route/005-route.tcldict`, sha256 `2a4a86b7...`);
* `joint_route_capture_review.py` independently parsed that range and returned
  PASS with `WARNING=6, CRITICAL WARNING=0, ERROR=0`
  (sha256 `1c61c9bb...`);
* `joint_route_continue.tcl` re-ran the formal post-route gates on the already
  completed `impl_1` run and wrote the routed DCP and route receipt;
* no gate was skipped, no waiver/false_path/clock relaxation was added, and the
  route receipt status is `ROUTED_REVIEW_PENDING`, not a hand-written PASS.

Follow-up for the next flow revision: add a route-scoped `capture_route`
adapter and the `fully routed == routable` route-status fallback directly to
`joint_build.tcl`.

## 10. Report hashes

| Report | SHA-256 |
|---|---|
| `bus-skew.rpt` | `7a85d6c0...` |
| `cdc.rpt` | `52402fc2...` |
| `drc.rpt` | `58b254de...` |
| `exceptions.rpt` | `96f6b4aa...` |
| `methodology.rpt` | `acf75e51...` |
| `route-status.rpt` | `702ab00e...` |
| `timing.rpt` | `816960b5...` |
| `utilization.rpt` | `68656064...` |
| `rgb2dvi_hold.rpt` | `64510adc...` |

All formal report SHAs in `joint-route.tcldict` match the files on disk.

## 11. Restrictions and open items

1. No bitstream, board access, deployment, push, or merge was performed.
2. `joint-route.tcldict` is `ROUTED_REVIEW_PENDING`; board acceptance remains
   pending.
3. The route-stage capture adapter is a project-layer recovery step; the next
   flow revision should fold it into `joint_build.tcl`.
4. No new exception, `false_path`, or clock relaxation was introduced; the
   routed timing closure is from place-and-route, including the eight RGB2DVI
   reset hold endpoints (+0.253 to +0.556 ns).