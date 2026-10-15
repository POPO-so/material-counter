# WO-0014 PG021 首帧判据修复与 50 MHz 单帧 DMA 板测报告

日期：2026-10-15，Asia/Shanghai。

## 1. PG021 软件修复与本地测试

core.py 的 RUNSTOP 轮询仅要求 Halted=0 且错误位为零，允许 Idle=0/1；SA 写入后的 LENGTH 前门同样允许首帧 Idle=0，同时继续检查 DMACR.RS=1、Reset=0。LENGTH 后完成判据仍要求 Idle=1、Halted=0、无错误与 Checker 完成；VERIFY 的 IOC/地址/长度/Golden 检查没有放宽。32 位 DMA 地址配置保持，仅写 SA，不引入 SA_MSB 或改变接口。

修复依据：[AMD PG021 MM2S_DMASR](https://docs.amd.com/r/en-US/pg021_axi_dma/MM2S_DMASR-MM2S-DMA-Status-Register-Offset-04h) 与 [Simple DMA 编程序列](https://docs.amd.com/r/en-US/pg021_axi_dma/Direct-Register-Mode-Simple-DMA)。所有阶段期限、健康门、单次守卫及失败时保留资源的规则保持。

新增 6 项回归，RED 中首帧 DMASR=0 场景复现失败；修复后 diagnostic50 144 PASS，legacy 11 PASS +3 subtests，vectors 7 PASS，DDR 10 PASS +16 subtests，合计 172 PASS、19 subtests PASS。RED/GREEN 原始日志、core.diff 与完整 preflight 在本目录及 reviewed-local.json 所指路径。

## 2. PID6311、CMA 与失败 Guard 回收归档

现场先核对同一 boot、旧部署哈希、原 SEND 超时状态及 SA/LENGTH 写入次数均为 0。回收方式：pidfd stop then kill；PID/starttime/完整 argv/可执行文件核对后，以 pidfd 绑定目标，先暂停并保存现场，再终止。回收后进程状态 Z、FD/maps 均空，无硬件占用。
CMA：之前 CmaFree=8664 KiB，之后 25980 KiB，增加 **17316 KiB**；旧包按页取整为 228 KiB。
归档：远端 `/home/xilinx/wo0014_pg021_archive_20261015/`；本地 `archived-board-verified/`，含完整旧 Guard、失败状态、进程/CMA 快照、日志与 archive-manifest.json，下载后逐项 SHA 核对。在取得旧锁并重新核对完整 Guard 文件集合及 SHA 后，才移动旧目录并建立新固定 Guard；新部署字节再次核对。

## 3. 阶段 A：50 MHz Overlay-only

**PASS**。唯一一次 Overlay 调用返回，用时 **1.752867511 秒**。加载前 ENV/HEALTH、加载后 HEALTH 与独立 OS 门均 PASS；正式 Overlay 和监督 receipt 均保存于 receipts/。
boot_id=`52955bb3-db18-4505-b209-78cbf3969d89`，BIT SHA-256=`56c1af5f043148c1c19a4ad182622ea3d5400344cb701da43da7a48755e03d1d`；Manager operating/flags=0、shutdown_ips={}。

## 4. 阶段 B：单帧 DMA + Checker MMIO

**PASS**。通过 Overlay 与 MMIO 监督 receipt 后，仅一次 MM2S LENGTH=230432。受控 CMA 缓冲区包哈希核验及 sync_to_device 完成。LENGTH 调用开始至 DMA Idle 完成态首个读回为 **0.018533372 秒**，至 DMA/Checker 完整统计为 **0.332707858 秒**（包括软件日志/轮询/读回，未测量纯 AXI 线速延迟）。

| DMA 轨迹 | DMACR | DMASR | LENGTH |
|---|---:|---:|---:|
| 初始态 | 0x00010002 | 0x00000001 | 0 |
| RS=1，LENGTH 前 | 0x00010003 | 0x00000000 | 尚未写入 |
| 完成态 | 0x00010003 | 0x00001002 | 230432 |

| 寄存器 | 地址偏移 | 初始 | 实测 | Golden | 比对 |
|---|---:|---:|---:|---:|---|
| drain_enable | 0x00 | 1 | 1 | 1 | PASS |
| magic | 0x04 | 1464807732 | 1464807732 | 1464807732 | PASS |
| status | 0x08 | 0 | 9 | 9 | PASS |
| error_code | 0x0c | 0 | 0 | 0 | PASS |
| frame_id | 0x10 | 0 | 0 | 0 | PASS |
| packet_done_count | 0x14 | 0 | 1 | 1 | PASS |
| packet_error_count | 0x18 | 0 | 0 | 0 | PASS |
| input_bytes | 0x1c | 0 | 230432 | 230432 | PASS |
| beats | 0x20 | 0 | 57608 | 57608 | PASS |
| tlast_count | 0x24 | 0 | 1 | 1 | PASS |
| first_word | 0x28 | 0 | 1464807732 | 1464807732 | PASS |
| last_word | 0x2c | 0 | 3235823636 | 3235823636 | PASS |
| input_byte_checksum | 0x30 | 0 | 6620674 | 6620674 | PASS |
| observed_pixels | 0x34 | 0 | 76800 | 76800 | PASS |
| sofs | 0x38 | 0 | 1 | 1 | PASS |
| eols | 0x3c | 0 | 240 | 240 | PASS |
| binary_checksum | 0x40 | 0 | 571200 | 571200 | PASS |
| accepted_output_total | 0x44 | 0 | 76800 | 76800 | PASS |
| stalled_cycles | 0x48 | 0 | 0 | 无固定周期 Golden | 仅观测 |

DMA 最终读回：`{"dmacr": 65539, "dmasr": 4098, "length": 230432, "sa": 361234432}`。二值输出累加和 `571200` 对应 `2240` 个前景像素（0/255 Binary8 统计推导）。

该 Checker 验证的是帧协议与聚合统计；没有逐像素输出回读，因此不宣称本轮每个输出像素均已逐项验证，也不扩展为 Route、跟踪计数或异常恢复板测结论。

## 5. 测试后健康、边界及建议

最后独立 OS 门：boot 连续，Manager operating/flags=0，dmesg 尾部连续且无新增异常，硬件占用/不可审计进程为空；原生 SSH 与工作站阶段前后 Ping 均由 controller 保存证据。
DMA 缓冲区正常路径 freebuffer 返回，子进程正常退出；本轮 DMA 不重试。
建议先扩展 50 MHz 多帧/不同合法包与逐像素输出验证，再以独立准入与同协议对照验证已具备交付检查的 100 MHz 比特流。P2/P3 摄像头与 S2MM 属于后续接口/闭环工作，需单独安排；本轮 MM2S 聚合统计不等于 S2MM、实时视频、Route 或跟踪计数通过。
四份受保护文档、已有生产 RTL 与原始 helper 的本地预检保护校验通过。未编辑 RTL/BD、未 commit/push。
软件变更：core.py、新 test_pg021_ready.py、executor-manifest.json；现场执行与审计脚本/原始结果均在本结果目录。
