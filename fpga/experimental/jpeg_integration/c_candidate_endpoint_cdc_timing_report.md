# C candidate — new-netlist endpoint, timing, CDC and exception coverage

Date: 2026-11-02
Worktree: `C:/fpga/wt/claude-jpeg-20261102`, branch `codex/claude-jpeg-20261102`
Scope: read-only report pass on the synthesized candidate. No synthesis restart,
no route, no board access, no deployment, no push, no merge.

Companion records: machine-readable summary in `candidate_synth_results.json`;
XSim-only verification in `c_candidate_verification_report.md` (section 5f).

## 1. Netlist and evidence

| Item | Value |
|---|---|
| Candidate RTL | `fpga/experimental/standalone/video_binary_overlay.v`, sha256 `811db98140f55686ff54fd89d6456a57c9fe27f64f7c7290cf3476a18eacec57` |
| Frozen baseline | sha256 `01d5563c7be95e92d38b20028063b7e85cf67ef4428d27c6c9d411b3b70ef9f9` |
| Synthesized checkpoint | `C:/w/jj2026k/joint-synth-candidate.dcp`, sha256 `56cc8f680dd3a9566c475526e712ea069c51dee1a836acf0691ed2155dcc59f8` |
| Report directory | `C:/w/jj2026k/reports/synth-candidate/` |
| Synthesis status | `synth_design Complete! PROGRESS=100%` (0 errors) |

Report hashes:

```
ecc2e10d51047e221873e22d19babcc4a3728a9dd2c8d9f979c08e4334688b3e  cdc.rpt
689a51e1f871d15af837aebcbcf007b7af01dee09d1cc04414f65f0c64bde75f  clock-interaction.rpt
8dbc8658231ba9d8226b81796f9fc44da82ebcdebe2c89a12cd6e3ad750245e4  drc.rpt
734128e726cadf424e15f75ac9b4368c0457ab9d2577f9d08aff3b15535f4acd  exceptions.rpt
8e01a1214b0aa55dd155ace3f9057840e16c4702d769e73930027f21a673b064  methodology.rpt
8b020f3e93fbb71b90c981bdb535a0d3e4cf313136dfd7e704378a1ce9fbf20d  timing.rpt
06736d89017d04de81000bdc5eb07f3464acf7a8b2025c0dc53dc3437d5e6437  timing_paths.rpt
```

Formal-flow caveat: the synthesis process itself completed, but `joint::capture`
aborted with `CAPTURE_FAIL: ... WARNING emitted=-8 exported=210`. Therefore
`C:/w/jj2026k/joint-synth.tcldict` was not written, and the route gate (which
requires status `SYNTHESIZED_CDC_REVIEW_PENDING`) remains closed. The checkpoint
and the raw reports above are valid; the missing item is the formal receipt.

## 2. Endpoint discovery: clk_fpga_0 -> hdmi_out_PXL_CLK_O

Clock periods (from `clocks.rpt`): `clk_fpga_0` = 20.000 ns (50 MHz),
`hdmi_out_PXL_CLK_O` = 10.000 ns (100 MHz).

The clock-interaction table lists 740 endpoints for this pair. The CDC report
classifies them as:

| Class | Count | Constraint column |
|---|---:|---|
| CDC-15 clock-enable controlled CDC | 738 | `Max Delay Datapath Only` |
| CDC-3 1-bit synchronizer with ASYNC_REG | 2 | `False Path` |

The 738 max-delay endpoints are the config mailbox and publish payload:

| Destination group | Bits | New in candidate |
|---|---:|---|
| `enable_p` | 1 | |
| `id_p` | 32 | |
| `count_p` | 5 | |
| `boxes_p` | 640 | |
| `box_valid_p` | 16 | yes |
| `line_p` | 10 | |
| `line_valid_p` | 1 | yes |
| `displayed_frame_id` + `display_bank` | 33 | |
| **Total** | **738** | **17 new** |

The two `False Path` endpoints are the toggle synchronizers
`cfg_toggle -> cfg_req_s1/D` and `publish_toggle -> publish_sync1/D`.

### 2.1 New cross-domain endpoints

All 17 new endpoints were queried on the checkpoint with
`get_timing_paths -to <dest>/D`:

| Destination | Source | Exception | Requirement | Slack |
|---|---|---|---:|---:|
| `box_valid_p_reg[0..15]` | `cfg_mb_box_valid_reg[0..15]/C` | `MaxDelay Path 10.000ns -datapath_only` | 10.000 | 8.968 |
| `line_valid_p_reg` | `cfg_mb_line_valid_reg/C` | `MaxDelay Path 10.000ns -datapath_only` | 10.000 | 8.968 |

Minimum slack over the 17 new endpoints: **8.968 ns**. No unconstrained new
endpoint was found.

## 3. Exception coverage

`C:/w/jj2026k/snapshot/scripts/standalone/offload_cdc.xdc:5` is the relevant
constraint:

```tcl
set_max_delay -datapath_only 10 \
  -from [get_cells -hier -regexp {.*binary_overlay.*cfg_mb_.*_reg.*}] \
  -to   [get_cells -hier -regexp {.*binary_overlay.*(enable_p|id_p|count_p|boxes_p|line_p)_reg.*}]
```

On the checkpoint the `-to` regex matches **705 cells**, and the 17 new cells
are among them. The match is real but **incidental**:

* `box_valid_p_reg` and `line_valid_p_reg` do not contain the literal `line_p`;
* they contain the substring `id_p` inside `valid_p`, which satisfies the
  `id_p` alternative.

Evidence: `get_cells -hier -regexp {.*binary_overlay.*id_p_reg.*}` returns
**49** cells = 32 `id_p` + 16 `box_valid_p` + 1 `line_valid_p`.

Consequences:

* Coverage today: PASS for all 17 new endpoints.
* Robustness: the coverage depends on the spelling `valid_p`. A rename such as
  `boxvld_p` or `valid_bits_p` would silently remove the max-delay exception.
* Recommendation for Manager review (not applied here): make the destination
  set explicit, e.g. add `box_valid_p|line_valid_p` to the `-to` regex. Per the
  joint-flow README, pinned baseline CDC constraints are not changed in this
  review.

## 4. Timing

| Metric | Candidate (jj2026k) | Frozen baseline (jj2026j) |
|---|---:|---:|
| WNS | +1.006 ns | -9.875 ns |
| TNS / failing setup endpoints | 0.000 ns / 0 | -250.892 ns / 275 |
| WHS | -0.167 ns | -1.957 ns |
| THS / failing hold endpoints | -1.335 ns / 8 | -627.507 ns / 328 |
| WPWS / failing pulse-width endpoints | +0.333 ns / 0 | +0.333 ns / 0 |

Candidate setup timing is closed. The baseline worst setup path was
`binary_overlay/.../line_or_box5__7` (DSP48E1) -> `overlay_q_reg/D`, 19.708 ns
datapath over 26 logic levels; the candidate removes it via pre-scaled mailbox
coordinates and the pipelined hit vector.

The 8 remaining hold failures are all the same pre-existing `rgb2dvi` path:

* source `hdmi_out/frontend/rgb2dvi_0/U0/LockLostReset/SyncAsyncx/oSyncStages_reg[1]/C`
* destination `ClockSerializer/{SerializerMaster,SerializerSlave}/RST` and
  `DataEncoders[0..2].DataSerializer/{SerializerMaster,SerializerSlave}/RST`
* slack `-0.167 ns`, hold, fast corner.

The same `-0.167 ns` path appears in the frozen-baseline hold report, so this is
vendor-IP/pre-existing behavior, not introduced by the overlay candidate. It is
still open at synthesis: `joint_build.tcl` fails the route stage when the worst
min slack is negative.

## 5. CDC

| ID | Severity | Candidate count | Baseline count |
|---|---|---:|---:|
| CDC-3 | Info | 84 | 84 |
| CDC-6 | Warning | 2 | 2 |
| CDC-13 | Critical | **0** | **320** |
| CDC-15 | Warning | 1703 | 1686 |

The baseline's 320 `CDC-13` critical entries were direct 1-bit CDC paths into
non-FD primitives (`cfg_mb_boxes_*` -> DSP `line_or_box*` inputs). The candidate
routes every mailbox bit through a pixel-domain FD register, and the CDC report
shows no `CDC-13` for the netlist.

`methodology.rpt` still reports `TIMING-9#1 Warning` (unknown CDC logic): an
asynchronous crossing constrained by `set_max_delay -datapath_only` or
`set_false_path` without a capture-side two-flop synchronizer. This is the
expected classification for the stable-data mailbox + frame-boundary handshake;
it is a review item, not an automatic failure.

## 6. Resources, DRC, methodology

| Item | Value |
|---|---|
| Slice LUTs | 37,821 (71.09%) |
| Slice Registers | 37,432 (35.18%) |
| Block RAM Tile | 57.5 (41.07%) |
| DSP48E1 | 49 |
| Bonded IOB | 13 (10.40%) |

* DRC: 97 checks, all Warning (`DPIP-1` x46, `DPOP-1` x13, `DPOP-2` x19,
  `REQP-1839` x8, `REQP-1840` x8, `REQP-181` x2, `REQP-165` x1); no Error or
  Critical Warning.
* Methodology: 9 checks, all Warning (`LUTAR-1` x4, `TIMING-9`, `ULMTCS-1`,
  `XDCB-5`, `XDCC-1`, `XDCC-7`); no Error or Critical Warning.

## 7. Open items

1. Formal synthesis receipt is missing because `joint::capture` failed; the
   route gate cannot pass until this is resolved.
2. Hold `-0.167 ns` on the `rgb2dvi` OSERDESE2 reset paths is pre-existing and
   would fail the route min-slack gate.
3. The new `box_valid_p` / `line_valid_p` coverage relies on the `id_p`
   substring match; make the exception explicit before relying on it long-term.
4. `fpga/experimental/jpeg_integration/candidate_synth_inputs.json` still says
   `status: SYNTH_RUNNING`; it is stale relative to the completed run.