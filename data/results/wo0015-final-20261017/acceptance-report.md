# WO-0015 Final Boot Hardware Acceptance

Date: 2026-10-17
Boot ID: f8b24aee-ed7e-430a-b3f3-d26056746bfd
Step 3 manifest SHA-256: 2c24709bbe60711c095b5ccbdf4b3582e93e65d98a9538ad2b413136ad942e8b
Camera manifest SHA-256: f0bafdd7c99892574a41a6fce6ecfa76221402ea0fcc7d63a94132cd1c16ca87

## Reported hardware results

- Overlay-only: OVERLAY_LOAD_PASS; AFI1 32-bit synchronization passed with RD=0x00000001 and WR=0x00000f01.
- Step 2 single frame: DMA_SINGLE_PASS; 1 frame, 76,800 output bytes, 0 byte mismatches.
- Step 3 continuous loop: DMA_THREE_PASS; frame IDs 0, 1, and 2 each report 0 byte mismatches for 76,800 pixels.
- OV9281 camera frame: CAMERA_DMA_SINGLE_PASS; captured from /dev/video0, frame ID 3, 230,432 packet bytes to 76,800 output bytes, 0 byte mismatches. The recorded PL output SHA-256 equals the software Golden SHA-256: 24b78ed82c1a48aeb345dadaadfcd0739c2d37c4d74086e2da126210523c8a39.
- Pre-merge source worktree: 179 passed with python -m pytest -q scripts/wo0015; that worktree had an untracked legacy board-cma-probe.json fixture from boot 822a9c29-37c0-438c-81c4-da998d57073b.
- Merged target checkout: 178 passed, 1 skipped with python -m pytest -q -rs scripts/wo0015. The skipped test is test_cma_compat.py::test_actual_probe_satisfies_authorized_kernel_accounting_admission because the optional legacy probe fixture is absent from the target checkout.

## Evidence and provenance notes

The compact stage receipts, result JSON files, Step 3 manifest, camera manifest, camera run record, offline prepare verification, and three PNG inspection images are stored beside this report. Temporary terminal transcripts and raw packet dumps are excluded from this compact evidence bundle.

The hardware result records identify camera_dma_oneshot.py SHA-256 5b9684f4b02988e8a822c36ef0ac7faec78c0a7980f2fca1574afd3027c9df28 and board_preflight.py SHA-256 f6556d811f2b4488b1ad471ea5d8ad565b74ae194b6db0b4356ae907e05de2d6. The current branch files hash to 8ec0a6242670f3cc32faef1974d93b59f972fd41cca8f2f8b4bdefc68c86fbe4 and df0fb5966822a9cb482cf42d38e1816d109d88e88366d9f4d1c26bd7189cfe43, respectively. Therefore the board PASS data is recorded, but exact execution of the current repository versions is not established by these hashes.

The local final verification receipt SHA-256 for dma-three-result.json matches the file in this worktree (cabe117bd4edf70fe7e40fc6aa8321f0c7b58f97401d55e86276dc66a27e1436). Its parsed content reports DMA_THREE_PASS and [0, 0, 0] frame mismatches.

Overall: hardware outputs report PASS. Two source-version hash exceptions remain for camera_dma_oneshot.py and board_preflight.py. This record does not claim the currently committed camera script version was exercised on the board.