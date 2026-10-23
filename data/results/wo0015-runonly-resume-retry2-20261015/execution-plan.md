# WO-0015 CMA watermark continuation Retry 2

This is the user-authorized single continuation attempt. No new Overlay load, DMA reset, or repeated Run-only writes.

1. Authenticate once and compare all Guard hashes from board read_bytes plus raw Base64.
2. Deploy only the independent continuation package, checking the fixed archive/source hashes. Original deployment, BIT/HWH and base manifest remain unchanged.
3. Read the exact Run-only clean register baseline and all Tier 1 / loaded-bit / ownership gates. Archive exact copies of the previous CMA-accounting terminal and single consumed token. Remove only terminal; keep the original single token to block the legacy executor.
4. A single fixed retry2 DMA guard and receipt namespace binds this newly authorized attempt. The continuation wrapper holds an exclusive file lock, executes verified bytes, confirms the base preflight import path/hash, and binds all PASS results to the new executor/config digests.
5. Run single once; manager reviews full result, physical spans, 16 MiB watermarks, DMA/Checker/Packer and zero byte mismatches. Only a complete PASS permits three once.
6. On any failure, no later stage or retry; persist terminal and read-only evidence. Preserve uncertain DMA buffers through the existing HOLD path.

Global CmaFree differences and exact restoration are telemetry. The actual integer physical spans, exact lengths, alignment, non-overlap and >=16384 KiB samples remain hard gates. Tier 1 admission remains >=32768 KiB.

All evidence stays in this new directory; preserve historical directories and keep both code workspaces uncommitted/unmerged.
