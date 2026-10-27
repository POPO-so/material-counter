# WO-0014 isolated RGB DMA replay contract

> **Historical design baseline:** the RGB packet/RTL/replay code described below has been removed from the active implementation. The user authorized `GRAY8_PACKET_V1` as the sole current runtime path; there is no RGB fallback. Keep existing WO-0014 test and result records as evidence for their original configuration only. Current protocol, build and board-validation state are documented in [`scripts/gray8/README.md`](../../../scripts/gray8/README.md) and [`PLAN.md`](../../../PLAN.md).

## Archived RGB packet and RTL contract

Offline-only route-independent experiment. Production RTL/formal interfaces remain unchanged. Reuse axis_rgb2gray and axis_gray2binary; morphology is regressed but not inserted in this requested chain. WO0013's 76816-byte Binary8+metadata fixture is NOT RGB888 and is never reused as image bytes.

## Packet contract

All 32-bit words use little-endian DDR byte order. Header/trailer TKEEP=1111; their bytes never reach RGB conversion.

| Region | Words |
|---|---|
| Header | 0x574F3134, (HEIGHT<<16)\|WIDTH, 3*WIDTH*HEIGHT, frame_id |
| Payload | row-major R,G,B,R,G,B bytes packed into ascending byte lanes across 32-bit word boundaries |
| Trailer | 0x454E4434, frame_id, sum(payload bytes) mod 2^32, 0xC0DEC014 |

Last payload word uses the contiguous low-lane TKEEP mask for 1/2/3/4 remaining bytes. Padding lanes are ignored. TLAST is 0 everywhere except the final trailer word, where it is 1. Total packet bytes=32+3*WIDTH*HEIGHT; 320x240 is 230432 bytes. WO0013's 17-bit DMA length cannot carry that packet; a future board design needs separately reviewed capacity >=230432 (minimum18 bits) or a separate segmentation contract. No IP/Overlay changes are made here.

RGB output is {R,G,B}, red bits23:16. Generate SOF on pixel0 and EOL on each row's final pixel; packet TLAST is never copied as EOL. VALID/data/markers stay stable under backpressure. A reservoir and one output register handle cross-word pixels.

Errors in header, length, byte masks, trailer/checksum or TLAST emit packet_error and drain to an accepted TLAST, then seek a new header. No full-frame rollback: invalid frames may emit a prefix and must be rejected in their entirety. Gray/Binary are stateless pixel transforms. packet_done confirms valid packet metadata independently of the output geometry checker. This is not a production feature-record protocol.

Adapter axis_dma_rgb_adapter params WIDTH,HEIGHT; ports aclk,aresetn,s_tdata[31:0],s_tkeep[3:0],s_tlast,s_tvalid,s_tready,m_tdata[23:0],m_tuser[0:0],m_tlast,m_tvalid,m_tready,packet_done,packet_error,error_code[3:0],frame_id[31:0]. Error codes:1 header/geometry,2 length,3 TKEEP,4 TLAST,5 trailer/checksum. Top axis_dma_image_pipeline adds THRESHOLD, Binary8 egress and checker_error,observed_pixels,observed_sofs,observed_eols,observed_checksum. Checker snoops egress handshakes.

Tests: every valid output pixel/SOF/EOL against independent Python golden; all487 H3 input hashes; directed RGB colors and threshold equality; input gaps; random backpressure; back-to-back frames; reset in header/payload/stalled output; bad headers/length/TKEEP, early/missing TLAST, checksum mismatch/extra payload, recovery. No invalid packet_done; reset alone may discard a stalled valid beat.
