# WO-0015 AFI1 v3 单帧闭环验收

Boot ID: `2cc2ab91-2e3f-450e-b9e7-361ec38e2815`
Manifest SHA-256: `e4d4da53e66c2fd4f1a3df082439dd439e54fa4ef1ba422367cab25ae9e98935`

Stage 1: **OVERLAY_LOAD_PASS**；Stage 2: **DMA_SINGLE_PASS**。
TX 230,432 B → RX 76,800 B；byte_mismatches = **0**。
MM2S / S2MM DMASR 均为 `0x00001002`（Idle=1、IOC_Irq=1、Halted=0、错误位=0）。

## 前置路径与寄存器核查

PYNQ 全局 BIT 路径：`/home/xilinx/wo0015/wo15.bit`。
上次子目录 Manifest 期望路径：`/home/xilinx/wo0015/afi1-rerun-v3/deploy/wo15.bit`。
本次规范期望路径：`/home/xilinx/wo0015/wo15.bit`。
只读前置核查的 AFI1 RD / WR 分别为 `0x00000001` / `0x00000F01`。

## 寄存器实测

| 阶段 | FPGA0_CLK_CTRL | FPGA_RST_CTRL | AFI1_RDCHAN_CTRL | AFI1_WRCHAN_CTRL |
|---|---|---|---|---|
| pre-overlay | `0x00400800` | `0x00000000` | `0x00000001` | `0x00000F01` |
| post-overlay | `0x00400800` | `0x00000000` | `0x00000001` | `0x00000F01` |
| post-dma | `0x00400800` | `0x00000000` | `0x00000001` | `0x00000F01` |

## 本地原始转储独立复算

| 32-bit Word 组 | Word 数 | Poison Word / 字节 | Zero Word / 字节 | Golden Match Word / 字节 | Mismatch 字节 |
|---|---:|---:|---:|---:|---:|
| even | 9,600 | 0 / 0 B | 9,250 / 37,000 B | 9,600 / 38,400 B | 0 |
| odd | 9,600 | 0 / 0 B | 9,250 / 37,000 B | 9,600 / 38,400 B | 0 |

Poison 和 Zero 按完整 `0xA5A5A5A5`、`0x00000000` Word 统计，字节数为对应 Word 覆盖的字节数；Zero 与 Golden Match 可重叠。逐字节统计 `0x00` 时，even / odd 两组各为 37,280 B。

## 归档完整性

6 份板端生成的结果/诊断/原始文件已按板端 SHA-256 验证；4 份活跃 Guard / PASS receipt 已按原始字节备份。板端不存在 terminal-failure.json。RX 与 Golden 的 SHA-256 完全一致。
原始完整文件保存在 `dma-single-diagnostics/`，本目录另保留 `rx_raw.bin`、`tx_raw.bin`、`expected_golden.bin` 三个规范文件名副本。
旧部署完整保存在 `/home/xilinx/wo0015-backup-20261017-canonical-v3/`；旧路径失败 Guard 的归档位置见 `guard-archive.json`。

本次结果为单帧硬件闭环验收。未修改或重新打包授权 v3 载荷。
