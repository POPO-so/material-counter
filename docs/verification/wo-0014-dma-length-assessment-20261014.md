# WO-0014 DMA 长度离线评估

日期：2026-10-14。范围：只读核对 WO-0013 已验收 MM2S 配置、WO-0014 RGB 包及 AXI DMA 官方规格，并提出后续隔离验证方案。本报告没有连接板卡、加载 Overlay、写 DMA，也没有修改 BD、RTL 或受保护文档。

## 结论

建议未来为 WO-0014 的独立 BD 副本把 AXI DMA `Width of Buffer Length Register` 从 17 提高到 **18 位**，以一次 MM2S simple/direct-register 传输发送完整的 **230,432 字节**包。18 位最大可表达 262,143 字节，刚好覆盖本包；17 位最多 131,071 字节。AMD PG021 将该参数适用于 Direct Register Mode 的 `MM2S_LENGTH`，字段宽度范围为 8–26 位，长度以字节为单位。[PG021 Field Descriptions](https://docs.amd.com/r/en-US/pg021_axi_dma/Field-Descriptions)、[MM2S_LENGTH](https://docs.amd.com/r/en-US/pg021_axi_dma/MM2S_LENGTH-MM2S-DMA-Transfer-Length-Register-Offset-28h)

这是当前包格式下改动最少、风险最低的传输方案：保留一个连续缓冲区、一笔 DMA 传输和一个包尾 `TLAST`，WO-0014 解析器无需把 DMA 分段边界解释为新的视频行或新图像包。这里的结论来自容量规格和地址窗口算术；它没有验证 18 位新 BD、230,432 字节运行时缓冲区或全链路硬件行为。

## 现有已测配置与边界

WO-0013 的复现 Tcl 将 DMA 配置为 `C_INCLUDE_SG=0`、MM2S 开启、S2MM 关闭、Micro DMA 关闭；`c_sg_length_width=17`、地址宽度 32、MM2S 内存映射口 64 位、AXI4-Stream 口 32 位、MM2S DRE 关闭、最大突发 16。复核的修复版 HWH 具有相同参数，HWH SHA-256 为 `a31d71f8e7004de710fa4da07078b80e2a0a8b40ff52ac523d322f21362df1fb`。配置来源为 [`create_dma_replay_bd.tcl`](../../scripts/wo0013/create_dma_replay_bd.tcl#L45) 和验收记录中的 BIT/HWH 身份信息 [`wo-0013-level2-accepted-20261014.md`](wo-0013-level2-accepted-20261014.md#L3)。

`c_sg_length_width` 虽然名称带有 SG，但 PG021 明确说明 Buffer Length Register Width 同时决定 Direct Register Mode 下 `MM2S_LENGTH`/`S2MM_LENGTH` 的有效位宽。以 17 位写入 230,432 字节不安全：17 位字段只能表示 0–131,071；若软件或寄存器路径屏蔽高位，低 17 位会成为 99,360 字节，不能代表完整图像包。应在改配后核查生成 HWH 与软件实际采用的长度宽度，不要用一次 DMA 完成状态推断长度正确。[PG021 Field Descriptions](https://docs.amd.com/r/en-US/pg021_axi_dma/Field-Descriptions)

复核版 HWH 将 MM2S 的 HP0 地址窗口映射为 `0x00000000–0x1FFFFFFF`（512 MiB）；DMA 地址宽度为 32 位。WO-0013 板测记录的内存映射与运行时 RAM 窗口均为 `[0,0x20000000)`，当次 PYNQ/XRT CMA 总量记录为 128 MiB；当次真实 MM2S 缓冲区地址为 `0x15860000`，但只发送并验证了 76,816 字节。以该地址仅作边界算术示例，230,432 字节的区间终点（exclusive）是 `0x15898420`，仍位于 HP0 窗口和 32 位空间内。未来实际地址必须重新核验完整区间，不能假定分配器会复用示例地址。历史板测证据见 [`wo-0013-level2-accepted-20261014.md`](wo-0013-level2-accepted-20261014.md#L3)；本地实际复核 HWH 为 [`wo0013_phase_a.hwh`](../../data/results/wo0013-resetfix-20261014/export/wo0013_phase_a.hwh)。

DRE 关闭时，PG021 要求 MM2S 源地址按 MM2S memory-map 数据宽度对齐；本配置为 64 位，因此基址须 8 字节对齐。230,432 同时是 8 和 4 的倍数，末尾不需要部分 AXI 字；这减少了尾拍处理条件，但不免除基址对齐、缓冲区连续性、完整地址范围、CPU/设备缓存同步和分配容量检查。[PG021 MM2S Source Address](https://docs.amd.com/r/en-US/pg021_axi_dma/MM2S_SA-MM2S-DMA-Source-Address-Register-Offset-18h)

128 MiB 的 CMA 总量从静态容量角度远大于 230,432 字节，但报告未证明下一次启动时能获得该大小的连续块。现有实测仅覆盖 76,816 字节分配。因此，对大缓冲区只能给出“地址映射和总量有余量”的判断，不能提前宣称新的分配已验证。PYNQ/XRT buffer 必须用实际申请到的 device/physical address、size 和 offset 再做边界检查，并在 DMA 前完成 CPU 数据复核及 `sync_to_device`。

## WO-0014 包与 TLAST

包格式在 [`WO-0014 README`](../../fpga/experimental/wo0014/README.md#L5) 与独立向量生成器 [`image_vectors.py`](../../python/refmodel/experiments/wo0014/image_vectors.py#L86) 中定义：16 字节头、按行排列的 RGB888 载荷、16 字节尾。对 320×240 图像，RGB 载荷为 `320×240×3 = 230,400` 字节，总长为 `16 + 230,400 + 16 = 230,432` 字节（`0x38420`）。按 32 位流口打包是 57,608 拍；头、载荷和尾恰好都落在完整 32 位字上，所有拍的 `TKEEP=4'b1111`。

包尾只有最后一个 trailer word 置 `TLAST=1`。WO-0014 适配器把该 `TLAST` 作为**包结束**校验，不将它当作图像行尾；SOF 由第一个像素产生，EOL 按行宽生成。适配器会拒收载荷内的 `TLAST`。因此 230,432 字节应作为一个 DMA 包发出一个最终 `TLAST`。WO-0013 实际单帧板测观察到 76,816 字节、19,204 拍及一个 `TLAST`，但那是 Binary8 加元数据测试包，不是本 RGB 包，也没有验证 230,432 字节长度。[WO-0013 Level 2 记录](wo-0013-level2-accepted-20261014.md#L3)、[WO-0014 包约定](../../fpga/experimental/wo0014/README.md#L15)。

需特别处理旧监视器：[`axis_dma_rx_monitor_bd`](../../fpga/experimental/wo0013/axis_dma_rx_monitor_bd.v#L3) 默认 `EXPECTED_BYTES=76816`，底层监视器会在达到该长度但尚未看到 `TLAST` 时置 missing-TLAST 错误并撤销 `TREADY`（[`axis_dma_rx_monitor.v`](../../fpga/experimental/wo0013/axis_dma_rx_monitor.v#L184)）。不能把 230,432 字节包直接接入原监视器期望值的实例。独立 BD 副本需把监视器期望参数设为 230,432（其计数器为 32 位），或使用等价的专用完整包校验器，同时核对 57,608 拍、单个末尾 `TLAST`、头尾字和独立计算的字校验和。

`packet_done` 只表示包元数据校验完成，不等于所有 Binary 输出像素已被下游接受；输出可能仍有 stalled 数据。坏包在检测错误前可能已经输出像素前缀，适配器没有整帧回滚。硬件接收端必须在 `packet_error` 或输出计数/边界错误时作废整帧；有效帧只有在包校验、76,800 像素、一个 SOF、240 个 EOL 以及输出排空条件全部满足后才可接受。[WO-0014 错误与恢复语义](../../fpga/experimental/wo0014/README.md#L19)

## 扩宽与分包比较

| 方案 | DMA 与 TLAST | 解析器/帧影响 | 评估 |
|---|---|---|---|
| 独立 BD 将长度宽度设为 18 位 | 一笔 230,432 字节 MM2S；一笔传输对应一个包尾 | 现有头/载荷/尾状态机和行边界语义保持；仍需校准监视器期望值 | **推荐。** 只提高 1 位配置宽度；完整缓冲区/单次传输最容易与已验证包契约对齐。 |
| 保持 17 位，分两笔 simple-mode MM2S | 每笔长度必须 ≤131,071；DMA 传输边界会带来中间 `TLAST` | 当前适配器在载荷中见到第一笔的 `TLAST` 会报错；下一笔从载荷继续却被当成新包头，之前发出的前缀也必须作废 | 风险较高。需要额外的中间 `TLAST` 抑制/重写、分块状态和错误恢复验证，或定义新的分块包协议；这会扩大 RTL、PS 控制和测试范围。 |

若后续不得不评估分包，且保留单一逻辑 RGB 包，可以先研究两个 8 字节对齐、4 字节整拍且像素边界对齐的分块：131,056 字节（16 字节头 + 131,040 字节载荷，即 43,680 像素）和 99,376 字节（99,360 字节载荷，即 33,120 像素 + 16 字节尾）。两段之和为 230,432，且各自都低于 131,071。这个切分只解决长度、源地址递增和像素边界算术；**它不会解决中间 `TLAST`**。必须明确只允许最后一段的 `TLAST` 进入现有解析器，并验证 DMA 两笔间的暂停、状态保持、最终包尾和坏帧恢复；否则要设计新的有分块序号/长度/校验与帧作废合同。本轮没有实现或仿真该分包方案。

## 推荐的后续验证顺序

1. 在独立输出目录复制 WO-0013 重建 Tcl/工程配置，保持已通过板测的 BD、BIT、HWH 原件不变；在副本将 DMA 长度宽度设为 18，并把 230,432 字节监视器期望值显式设入副本。首轮保留原 50 MHz PL 时钟以隔离长度变更风险；100 MHz 系统实验需另有完整实现时序证据。将 WO-0014 RGB 适配器、Gray/Binary 与输出检查器接入副本，执行 BD 验证并检查生成 HWH 的长度、地址、DRE、数据宽度和 HP0 映射。在实验副本中为 packet/checker 状态添加可读寄存器或明确采集手段；现有 WO-0013 Monitor 只监视输入 DMA 流，不能代替图像流水线输出证据。
2. 离线先运行单包 230,432 字节 XSim：确认 MM2S 边界模型只产生最终一个 `TLAST`、所有拍 `TKEEP=F`，再检查 adapter `packet_done/error`、全量 76,800 Binary8 像素、SOF/EOL 计数、checksum、随机反压及坏包后恢复。对比独立 Python 黄金输出。此项不能用当前仅模拟 adapter 输入流的成功报告替代 DMA IP 端到端验证。
3. 综合/实现副本并检查新 IP 参数、资源和时序；保留原 WO-0013 BIT/HWH hash 作为不可覆盖基线。若离线验证通过，提交新的明确硬件实验请求，等待批准。
4. 经批准后才在板端执行单次、无重试试验：核对 BIT/HWH 身份；申请至少 230,432 字节连续 PYNQ buffer 并确认 8 字节基址对齐、完整区间在 `0x0–0x1FFFFFFF` 与 32 位地址范围内；生成并 CPU 回读核对一个完整 RGB 包，`sync_to_device`；配置 230,432 字节的 MM2S 长度和相同长度的监视器；仅发送一次。保存原始 DMA/Monitor 寄存器、字节数 230,432、拍数 57,608、一个末尾 `TLAST`、首尾字/校验和、`packet_done/error` 及输出 checker 完整帧计数、SOF/EOL 和校验和，并做操作后板卡健康检查。仅有输出统计时不得宣称板级逐像素黄金比对；全像素验收还需要独立输出采集/回读设计。坏帧恢复需要另行批准包含坏包和恢复包的多次传输，不计入本次单合法包试验。任何地址/长度/缓存/状态预检失败都应在写 DMA `LENGTH` 前停止。

硬件实验批准前，不得把这一建议视为新 Overlay 配置或已做板测结果。该工作单也不授权正式 PL CCL、WO-0013 Phase B、Route/G1 决策或回写 WO-0013 原始 BD。

## 来源

- 项目内 DMA 配置：[`scripts/wo0013/create_dma_replay_bd.tcl`](../../scripts/wo0013/create_dma_replay_bd.tcl#L45)；修复版 HWH：`data/results/wo0013-resetfix-20261014/export/wo0013_phase_a.hwh`；验收与硬件测试范围：[`docs/verification/wo-0013-level2-accepted-20261014.md`](wo-0013-level2-accepted-20261014.md#L3)。
- 项目内 RGB 包格式、TLAST、反压和错误语义：[`fpga/experimental/wo0014/README.md`](../../fpga/experimental/wo0014/README.md#L15)、[`python/refmodel/experiments/wo0014/image_vectors.py`](../../python/refmodel/experiments/wo0014/image_vectors.py#L86)、[`fpga/experimental/wo0014/axis_dma_rgb_adapter.v`](../../fpga/experimental/wo0014/axis_dma_rgb_adapter.v#L1)。
- 项目内原始接收监视器缺省预期长度与停止条件：[`axis_dma_rx_monitor_bd.v`](../../fpga/experimental/wo0013/axis_dma_rx_monitor_bd.v#L1)、[`axis_dma_rx_monitor.v`](../../fpga/experimental/wo0013/axis_dma_rx_monitor.v#L182)。
- AMD 官方 IP 规格： [PG021 Field Descriptions](https://docs.amd.com/r/en-US/pg021_axi_dma/Field-Descriptions)、[MM2S_LENGTH](https://docs.amd.com/r/en-US/pg021_axi_dma/MM2S_LENGTH-MM2S-DMA-Transfer-Length-Register-Offset-28h)、[MM2S source address](https://docs.amd.com/r/en-US/pg021_axi_dma/MM2S_SA-MM2S-DMA-Source-Address-Register-Offset-18h)。
