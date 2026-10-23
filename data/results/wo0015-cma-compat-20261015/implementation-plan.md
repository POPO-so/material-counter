# WO-0015 CMA Compatibility Implementation Plan

**Goal:** Collect only the approved board CMA metadata and repair offline CMA admission without touching hardware artifacts or running Retry 3.

**Architecture:** Prefer exact device-tree, kernel-log or debugfs CMA intervals. If exact metadata is unavailable, permit only explicitly evidenced RAM candidate bounds with provenance and stronger post-allocation checks; never describe inferred bounds as exact CMA. Keep all Guard and transfer acceptance behavior unchanged.

**Tech Stack:** Python standard library, existing unittest/pytest and Mock board API.

1. Run one native-authenticated metadata probe, save raw cmdline/bootargs, filtered dmesg, iomem, debugfs fields, meminfo and boot identity. No PYNQ, Overlay, device access, allocation or DMA.
2. Add failing regression tests in `scripts/wo0015/test_cma_compat.py` for source precedence, absent DT, precise kernel/debugfs ranges, insufficient/conflicting evidence, and invalid DMA buffers before register writes. Run the tests to establish RED.
3. Modify only CMA parsing/admission in `board_preflight.py` and CMA-related integration/post-allocation checks in `board_once.py`. Preserve public compatibility where practical, record exact-vs-candidate source, require a positive aligned physical address, exact buffer lengths and non-overlap inside permitted RAM/HP bounds.
4. Run focused tests and the complete WO-0015 offline suite, then the repository's configured full offline Python tests. Review actual diffs and independent safety review. Do not run board executors.
5. Package a new independently named bundle from the locked BIT/HWH and existing fixtures using the existing packager; do not overwrite earlier bundles or change RTL/build logic. Verify hashes, offline and Mock results, and archive all test evidence.
6. Report raw board evidence, unknowns, source classifications, test counts and new script/package hashes. Request Retry 3 authorization; do not execute it. Keep all directories uncommitted/unmerged.
