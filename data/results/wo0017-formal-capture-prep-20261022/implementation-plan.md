# WO-0017 formal capture preparation

Goal: prepare B1 115-frame empty-belt and B2 220-frame five-nut capture with lossless paired arrays, without starting sampling.

Architecture: preserve the old WO-0016 165/5 contract. Add explicit WO-0017 profiles, private independent READY/attempt records and the hash-pinned current-boot root-02 Stage 1 receipt. The existing DMA span, CMA and fail-hold checks remain. Stop before invoking the hardware runner; operator confirmation is the final launch step.

1. Commit the existing calibration source and evidence after full regression (668 passed, 1 skipped, 5 subtests).
2. TERM only verified PID 7009; record restoration, released device/ports, boot/CMA and read-only DMA status.
3. Test first: reject mismatched profiles, Boot, receipt hashes and capture capacities; preserve old profile tests. Add `wo0017_capture_profile.py` and targeted runner integration. Permit uses a complete runtime source hash map as well as capture configuration.
4. Extend manifest validation with an explicit approved Boot argument, retaining the historical default. Reuse the original WO-0015 BIT/HWH/frame vectors in a new manifest; never alter old artifacts or manufacture an old-format PASS receipt.
5. Generate a private B1 READY Guard and unconsumed permit, new evidence/capture paths and a fixed launch script. B2 remains a reviewed specification until B1 passes and the operator separately authorizes B2.
6. Run focused and full offline tests; hash-verify a separate board deployment, validate B1 artifacts read-only, and record launch instructions. No Overlay download, camera sampling, conveyor control, DMA start or attempt consumption during preparation.

Capacity: bind 100663296 bytes (96 MiB), queue depth 2, schema 1. Conservative estimates: B1 35831808 bytes; B2 68517888 bytes. The default 64 MiB historical examples do not admit 220 frames.
