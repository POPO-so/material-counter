# Exported JPEG kernel OOC area measurement

`jpeg_ooc.py` snapshots the actual `xilinx_verilogsynthesis` view from the
final exported `jpeg_axis_gray` component. The selected top must be
`jpeg_axis_gray`, and a `guard_coefficients` module must be present. It copies
the 142 Verilog compilation units, three include headers, the ROM `.dat`,
20 ns OOC XDC, component, HLS synthesis source-binding receipt and JPEG cpp
into a new short directory. The export/cosim workspace is read-only.

The measurement command used for this experiment is:

```powershell
& 'C:/AMDDesignTools/2026.1/Vivado/tps/win64/python-3.13.0/python.exe' -u `
  fpga/experimental/jpeg_integration/jpeg_ooc.py --out C:/w/jpo1101a --run
```

The script refuses an existing output directory. Omit `--run` for copy-only
preflight. `--verify-only --out <existing-output>` rechecks original/snapshot
hashes and driver identity. IP path, source cpp, HLS synthesis receipt, Vivado
executable and timeout are explicit CLI parameters.

This is single-thread Vivado synthesis for `xc7z020clg400-1`, OOC,
`flatten_hierarchy rebuilt`, at 20 ns. A relative `$readmemh` resolves only in
the copied Verilog directory. No blackbox is permitted in the synthesized
design. The output includes utilization/hierarchy/timing/DRC reports and a
post-synthesis DCP. It performs no place/route, bitstream, joined BD build,
RTL modification or functional acceptance.

`C:/w/jpo1101a/inputs.json` binds every copied byte to its source SHA-256.
The current cpp is
`d695aac91915f3bf8b4d4357d7b2ca539929197720b2f2390cfb5a2472108f7d`;
the final component is
`0bdfce509b1f51829aa1c2b12536fba14088925f89ce8c7da2558707246725d5`.
`results.json` records measured resources, complete diagnostic messages/counts,
run exit code, retained artifact hashes and the post-run source recheck. A
successful area result is labeled `PASS_OOC_AREA_ONLY`; the separate HLS
smoke/entropy RTL receipts control functional and integration acceptance.

The 2026-11-01 run completed at 14:05:06 local time, exit 0, in 270.6 seconds.
Original export, copied RTL/data, cpp and driver hashes all matched after the
run. Actual synthesized resources were:

| Resource | Used | Z7020 available | Utilization |
|---|---:|---:|---:|
| LUT | 17358 | 53200 | 32.63% |
| FF | 13758 | 106400 | 12.93% |
| BRAM36 equivalent | 28 | 140 | 20.00% |
| DSP48E1 | 34 | 220 | 15.45% |

BRAM primitives are 2 RAMB36E1 plus 52 RAMB18E1. Of the LUTs, 14535 are logic
and 2823 memory (1343 distributed RAM and 1480 shift-register LUTs). No latch
or unresolved blackbox remains. This measured new-kernel area is materially
below the HLS 51598-LUT estimate, but is not a joint-system fit/route result.

The synthesis timing report shows setup WNS 4.964 ns and hold WHS 0.174 ns at
20 ns. There is no physical clock source (`HD.CLK_SRC`) and no place/route, so
these are OOC synthesis estimates, not system timing closure.

All 78 native warnings are retained; there are no native critical warnings or
errors. Breakdown: `Synth 8-4446`×7 (unused component/base-info metadata
instances), `Synth 8-4767`×1 (one-word FIFO implemented in registers),
`Synth 8-6014`×64 (unused sequential `coef_mcu` elements removed),
`Synth 8-13373`×3 (parser DSP macros removed by constant propagation),
`Vivado 12-180`×1 (empty blackbox query), `Timing 38-242`×1 (no HD.CLK_SRC),
`DRC 23-814`×1 (OOC connectivity DRC coverage limit). The DRC report separately
lists DPIP-1×33, DPOP-1×11, DPOP-2×13 and ZPS7-1×1 warnings; these remain review
items. No generated RTL was edited to silence or change diagnostics.

The compact repository receipt is `jpeg_ooc.json`; detailed immutable input
binding, native log, hierarchical utilization, timing, DRC and DCP artifacts
remain in `C:/w/jpo1101a`. Functional RTL acceptance is controlled by Kant's
separate receipts; this run independently establishes area only.
