---
name: dma-vdma-safe-status
description: Use when checking PYNQ AXI DMA or HDMI VDMA status, capturing a readback snapshot, or deciding whether a hardware run may be considered safely stopped. Prefer documented read-only status APIs, preserve the single-owner boundary, and treat unknown or active state as a stop condition.
---

# DMA/VDMA safe status

Use documented register snapshots and the existing runtime owner protocol. A safe-looking status bit is only one part of shutdown evidence; also account for errors, buffers, camera ownership, and the unique hardware owner.

## Scope

- Apply to status inspection and evidence review for the exact admitted BIT/HWH and current run.
- This skill is not a generic DMA register map, stop sequence, reset recipe, or permission to reconfigure PL.
- Prefer the project's readback/preflight modules. Do not probe undocumented control offsets or infer a safe state from process absence.

## Workflow

1. Identify the current run, boot ID, unique owner, loaded BIT digest, and matching HWH/manifest. If the hardware identity is unknown or mismatched, stop and escalate.
2. Prefer `scripts.standalone.g2_readback.read_pipeline()` for the documented G2 pipeline offsets. It validates the RLE signature, geometry, maximum run count, and rejects busy/error state. `snapshot_counters()` is a latched counter operation (it writes the documented latch command at `0x3c`), not a purely read-only function; use it only after the exact new BIT has been admitted, as its docstring requires.
3. For boot admission, follow `scripts/standalone/boot_integrated.py`: it checks pipeline readback and reads the documented DMA/VDMA status locations, requiring idle/halted indications and no masked error/active bits. Do not copy these offsets to a different hardware snapshot without checking its HWH/RTL register map.
4. For package-specific preflight, inspect `scripts/wo0015/board_preflight.py` and its CLI help before using it. Its offline manifest/HWH gate is separate from live DMA state; a preflight pass does not itself prove runtime DMA/VDMA safety.
5. At shutdown, record the same-run status plus buffer/camera release and owner release. If any status is unknown, inconsistent, active, or erroneous, preserve the owner and resources in the project's fail-hold behavior and escalate to the responsible Manager/operator. Do not kill, reset, or launch a second owner as a shortcut.

## Failure and invalidation conditions

Stop when a status read errors, the register signature or geometry is wrong, the pipeline is busy or reports error, DMA is not idle, VDMA is not halted, any error bit is set, the snapshot belongs to another boot/session/BIT, or buffer/camera/owner release is unproven. Re-capture after every state-changing run or shutdown; a prior run's safe status does not authorize a later reconfiguration.

## Verification effect and limits

A successful snapshot documents only the specific registers and resources checked for that boot/session and hardware identity. It is not proof of display correctness, physical camera behavior, a fully released system unless ownership and buffers are also checked, or material-count accuracy. The readback code itself validates the named pipeline registers; the caller must preserve provenance and include other required release evidence.

## Source cases

- `scripts/standalone/g2_readback.py` documents a safe pipeline snapshot and an atomic counter latch. The code explicitly notes that the counter page is unsupported by older BITs.
- `scripts/standalone/boot_integrated.py` checks loaded BIT identity, clocks, pipeline readback, and DMA/VDMA status before boot admission continues.
- `scripts/standalone/DEPLOYMENT.md` states that `FAIL_HOLD` retains resources, that safe switching needs same-run DMA idle / VDMA halted / no error / buffer and owner release evidence, and that there is no generic verified G2-to-G1 rollback command.
- `data/results/jpeg-pl-board-20261102/README.md` records a failed JPEG probe where S2MM remained active; recovery proceeded only after observing halted status and resource release. This is a case-specific record, not a universal reset procedure.
