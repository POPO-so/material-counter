# WO-0015 Retry 3 execution plan

Approved boot: 822a9c29-37c0-438c-81c4-da998d57073b. Fixed BIT/HWH remain unchanged. Keep all history and both workspaces uncommitted/unmerged.

1. Apply the new MEMINFO_IOMEM_VERIFIED architecture using failing tests first. Exact CMA metadata remains preferred; the authorized static envelope is [0x00100000,0x20000000), not an exact CMA pool. Total must be 131072 KiB and free at least 32768 KiB.
2. Add before/after-each-allocation and after-release CmaFree samples; record actual physical spans and exact bytes before any register write. Preserve MM2S's stricter 8-byte alignment, HP limits, overlap gate and uncertain-DMA HOLD semantics.
3. Run offline regression, independent review, new package creation and locked artifact/hash checks. Deploy to a new ordinary-user-owned directory, unpack without sudo; execute PYNQ stages using sudo.
4. Run static Tier 1/offline checks. Invoke Overlay stage once, then a single supplemental read-only initial register snapshot. Require signatures and both DMA Halted=0/Err=0; do not write RUN or reset to repair a failed state.
5. Only after Step 1 acceptance run one single-frame stage; review all frame/counter/pixel/CMA evidence. Only after its PASS run one three-frame stage and review every frame.
6. Any anomaly stops subsequent stages. Save evidence, kernel log and safe OS ownership checks. Never reset/retry, reload, or free possibly DMA-owned buffers. Close handles normally when no DMA is in flight, write a complete report in this directory, and do not commit or merge.
