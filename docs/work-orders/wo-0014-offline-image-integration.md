# WO-0014 offline image replay implementation plan

User authorized this isolated offline workflow on 2026-10-14. No board, production RTL, protected document, Route/G1 decision, commit or Phase B work.

**Goal:** Compare a new RGB DMA packet through existing Gray8/Binary8 RTL against Python over H3.

**Architecture:** Header/payload/trailer parser with byte reservoir generates RGB888 SOF/EOL. Instantiate existing Gray/Binary without edits. Handshake checker and self-checking TB validate valid pixels and reject malformed packet metadata.

**Tech Stack:** Verilog/SystemVerilog, Python/Pillow/NumPy, Vivado2026.1 XSim, lint CSV, OOC synthesis.

1. Preserve WO0013 commit and protected/production source hashes.
2. Use [packet contract](../../fpga/experimental/wo0014/README.md); generate independent vectors in python/refmodel/experiments/wo0014 with all487 H3 PNGs and directed RGB cases.
3. Add axis_dma_rgb_adapter.v, axis_binary_output_checker.v, axis_dma_image_pipeline.v under fpga/experimental/wo0014; self-checking TB under fpga/sim/wo0014.
4. Run deterministic XSim cases with gaps, backpressure, invalid lengths, resets. Compare every valid pixel and SOF/EOL against golden; repair implementation without changing expectations.
5. Via Vivado MCP run lint and OOC synthesis, preserve actual reports; run existing RGB/Binary/Morph/WO0013 simulations and Python regressions.
6. Check protected hashes and final Git state. Keep WO0014 uncommitted. Report offline evidence separately from board results. A complete board BIT is not required for this RTL-only experiment.
