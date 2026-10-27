# WO-0015 isolated Vivado build

> **Historical build baseline:** this file documents the earlier WO-0015 build project and its RGB-era constraints/output paths. Current production code no longer contains the old RGB build/replay path; the unique current `GRAY8_PACKET_V1` build is `scripts/gray8/run_sim.tcl` → `create_bd.tcl` → `build.tcl` → `export.tcl`, with details in [`scripts/gray8/README.md`](../gray8/README.md). Existing WO-0015 results remain historical evidence and are not relabeled.

These scripts build a fresh 50 MHz PYNQ-Z2 project for the Binary8 S2MM return path. The project short name is `wo15_50m`, its Block Design is `wo15`, and outputs are restricted to `data/results/wo0015-hw-50mhz-20261015/`. `create_s2mm_bd.tcl` creates the project, configures and audits the BD, snapshots the seven RTL inputs, and generates the BD wrapper. It does not launch synthesis.

The DMA keeps MM2S at memory64/stream32 through the existing HP0 path. S2MM is enabled in direct-register mode with memory32/stream32, 18-bit length, 32-bit address, no DRE and no Micro DMA. Its AXI4 master reaches PS HP1 through the new 1x1 `axi_interconnect`; HP1 is configured as 32-bit AXI3. Vivado's AXI Interconnect performs the AXI4-to-AXI3 protocol conversion. The generated HWH finalizer checks the endpoints and interface properties instead of assuming conversion succeeded.

## Controlled invocation

Use the dedicated Vivado 2026.1 session opened for this worktree. The `vivado-ip-configurator` helper must be loaded and its `::ipcfg::native_log_path` bound to that session's real native log before either script runs. The build scripts preserve generated reports and fail on configuration/readback mismatches, negative implemented timing, nonzero timing violation totals, or DRC/methodology Error and Critical Warning findings. Ordinary warnings remain in the reports and severity counts.

From Vivado Tcl, set the worktree root and the one fixed output directory, then source the BD script:

```tcl
set wo15_repo [file normalize {C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter}]
set wo15_output [file join $wo15_repo data results wo0015-hw-50mhz-20261015]
set wo15_clock_mhz 50
source -notrace [file join $wo15_repo scripts wo0015 create_s2mm_bd.tcl]
```

Run the required Packer, wrapper, three-frame, random-backpressure, malformed-packet, and reset XSim checks with the simulation workflow. Only after the Manager has verified every required XSim case, set the explicit gate and source the build script in the same Vivado session:

```tcl
set wo15_xsim_gate PASS
source -notrace [file join $wo15_repo scripts wo0015 build.tcl]
```

`build.tcl` runs synthesis and implementation through `write_bitstream`, waits for both runs, and then sources `export.tcl`. `export.tcl` writes routed timing, utilization, DRC, methodology, clocks, clock interactions, exceptions, constraints, and a routed checkpoint. It copies the BIT/HWH pair from that project run and calls `finalize_build.py`; no board connection, overlay load, MMIO, or DMA operation is part of this flow.

The finalizer uses only Python's standard library. It verifies the source snapshot hashes, the actual run/export BIT and HWH hashes, the DMA and HP0/HP1 parameters, the HWH AXI and AXI4-Stream interfaces, DDR address windows, the 50 MHz clock, timing, and serious DRC/methodology severities. It writes `export/pair-manifest.json` and `reports/build-summary.json` once; it refuses to overwrite an existing result. A successful result is an offline implemented artifact and does not constitute board validation.
