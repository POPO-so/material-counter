# WO-0015 AFI1 32-bit root-cause closure

## Offline mapping check

The previous captured `rx_raw.bin` and `expected_golden.bin` are both 76,800 bytes and contain 19,200 little-endian 32-bit words. The 9,600 even RX words (`rx_words[0::2]`) were compared with two candidate Golden mappings by `analyze_even_word_mapping.py`.

| Candidate mapping | Equal words | Different words | Matching bytes | Different bytes |
| --- | ---: | ---: | ---: | ---: |
| `golden_words[0::2]` | 9,180 | 420 | 37,280 | 1,120 |
| `golden_words[:9600]` | 8,850 | 750 | 36,000 | 2,400 |

The even RX words correspond to the Golden even-index sequence; the contiguous first-9,600-word mapping is a poorer fit. The even sequence still contains 1,120 differing bytes, so the evidence does not claim that all even words match.

## Hardware evidence and mechanism

The prior `dma-single-result.json` records `AFI1_RDCHAN_CTRL=0x00000000` and `AFI1_WRCHAN_CTRL=0x00000f00`; bit 0 was clear on both channels although the reviewed HWH declares PS7 `S_AXI_HP1` as 32-bit. The DMA reported 39,520 byte mismatches: 38,400 bytes in the 9,600 odd words remained `0xA5`, while the 9,600 even words contained 1,120 bytes different from Golden. This split is consistent with 32-bit S2MM traffic being accepted only in alternating 32-bit lanes while AFI1 remained in its 64-bit setting.

The offline fix in `scripts/wo0015/board_once.py` reads the two HWH width declarations, requires `FPGA_RST_CTRL == 0`, sets only AFI1 bit 0 through the two-address physical-write allowlist, immediately verifies each readback, and emits synchronization plus pre/post snapshots in both `overlay-only` and `dma-single` evidence. This note documents the previous failure and offline mapping; hardware acceptance remains determined by the new board run evidence.

## 2026-10-17 Stage 1 attempt outcome

The new package and its 14 SHA-256 payload entries passed board-side verification under the expected Boot ID. Stage 1 downloaded the Overlay, then stopped with `OSError: [Errno 22] Invalid argument` in the physical writer. A subsequent read-only register check showed `AFI1_RDCHAN_CTRL=0x00000001`, `AFI1_WRCHAN_CTRL=0x00000f00`, and `FPGA_RST_CTRL=0`; this proves the RD write reached the device before the file-mapping `flush()` call failed. DMA was not started and Stage 2 was not run. The active `overlay-only-consumed.json` and `terminal-failure.json` are preserved locally with hashes in `afi1-rerun/` and remain active on the board. The offline writer now avoids `mmap.flush()` for `/dev/mem` device MMIO; another board attempt requires a new explicit authorization to archive this newly generated one-shot guard and retry.

## 2026-10-17 canonical-v3 final hardware closure

The later canonical-v3 retry completed both stages on boot `2cc2ab91-2e3f-450e-b9e7-361ec38e2815` with the reviewed 50 MHz BIT/HWH and manifest SHA-256 `e4d4da53e66c2fd4f1a3df082439dd439e54fa4ef1ba422367cab25ae9e98935`. `OVERLAY_LOAD_PASS` was followed by `DMA_SINGLE_PASS`: a 230,432-byte MM2S packet returned as a 76,800-byte S2MM frame with `byte_mismatches=0`; both channels reported DMASR `0x00001002`.

The pre-overlay, post-overlay, and post-DMA snapshots all recorded `AFI1_RDCHAN_CTRL=0x00000001`, `AFI1_WRCHAN_CTRL=0x00000F01`, and `FPGA_RST_CTRL=0x00000000`. The preserved raw RX and expected Golden SHA-256 values match (`174164ae33086f8606b83113f43ebd5a52470f8640bde1c489e51e76f411a60e`), and the independent verifier recomputes zero mismatches. This closes the AFI1 width-mode root-cause hypothesis against the prior bit-0-clear mismatch and confirms the guarded 32-bit synchronization fix on hardware. The separate `/dev/mem` `mmap.flush()` portability failure was also removed; no PL RTL change was needed for this closure.

This evidence closes Step 2 only. The existing same-boot `dma-three` stage remains a separate one-shot operation and is not claimed as run or passed here.
