# WO-0014 基线验收与隔离时序优化

日期：2026-10-14；分支：`feat/pl-stream-skeleton`；工具：Vivado 2026.1；器件：`xc7z020clg400-1`。全部操作离线执行，没有连接板卡、加载 Overlay、发送 DMA、push 或合并。

## 本地基线提交

Commit：`ca6a68c7e454d87aeda414c65579271ffa948362`。

标题：`feat: add WO-0014 offline DMA image pipeline integration`。

17 个源代码/测试/脚本/文档及 32 个精选验收证据，共 49 个文件独立提交。未提交大型向量/输出二进制、DCP、仿真缓存、临时文件、历史无关产物或全局配置。Vivado 文本报告的尾部空白和空行已规范化以通过 staged diff check，数值和路径未修改，原始报告副本仍留在本地 `.rpt.raw` 文件中。提交正文明确记录 100 MHz WNS −1.668 ns，时序尚未闭合。

提交前重新执行新链路 15 项 XSim、487 帧 H3 黄金比对及 134 项 Python 回归；均通过。基线 commit 保持不变，后续优化和本报告作为独立未提交增量保留。

## 实际路径与单轮优化

基线最差路径：`packet_adapter/received_bytes_reg[2]/C` → `packet_adapter/payload_sum_reg[31]/D`；10 ns setup 要求；数据延迟 11.661 ns（逻辑 5.674、估计走线 5.987）；22 级逻辑（14 CARRY4、8 LUT）；WNS −1.668 ns、TNS −29.856 ns、29 个失败 endpoint。原始报告：[timing-ooc-final.rpt](../../data/results/wo0014-offline-20261014/reports/timing-ooc-final.rpt)。

只修改 `fpga/experimental/wo0014/axis_dma_rgb_adapter.v` 的校验和组合路径：

- 用常量尾字节数和完整字节边界比较替代 32 位 `PAYLOAD_BYTES-received_bytes` 减法。合法计数每次推进四字节，到尾字再接收 1/2/3/4 字节。
- 用两个 9 位部分和及一个 10 位平衡加法树求四 lane 字节和。最大为 1020，零扩展后累加到既有 32 位寄存器，保留模 2^32 校验和。

没有增加流水级、改变状态机或包格式；SOF/EOL、输入/输出握手、packet_done/error、复位和错误排空语义保持。基线适配器从上述 commit 提取并改模块名，与候选共享输入、复位及 downstream ready；每个时钟沿后比较 input ready、RGB data/valid/SOF/EOL、包事件、错误码和 frame_id。全部 15 个场景通过逐周期对照；这是已运行场景的等价证据，不是形式证明。

| 相同 10 ns OOC 约束 | 基线 | 优化后 |
|---|---:|---:|
| Slice LUT | 700 | 668 |
| FF | 377 | 377 |
| BRAM / DSP | 0 / 0 | 0 / 0 |
| WNS (ns) | −1.668 | +2.354 |
| TNS (ns) | −29.856 | 0.000 |
| 失败 setup endpoint | 29 | 0 |

优化后最差路径：`packet_adapter/m_tdata_reg[17]/C` → `rgb_to_gray/m_tdata_reg[7]/D`，10 级逻辑、7.671 ns 数据路径；适配器校验和路径的报告 slack 为 +3.762 ns。资源减少 32 LUT，WNS 改善 4.022 ns。综合阶段 0 errors / 0 critical warnings / 0 warnings。详见 [timing-ooc.rpt](../../data/results/wo0014-timing-20261014/reports/timing-ooc.rpt)、[worst-paths.rpt](../../data/results/wo0014-timing-20261014/reports/worst-paths.rpt)、[utilization.rpt](../../data/results/wo0014-timing-20261014/reports/utilization.rpt)。

**100 MHz 综合后 OOC 寄存器间时序达到要求。完整系统和布线后时序仍未验证。** 与基线相同，仅约束 `create_clock -period 10`；不添加 false path、multicycle、降频或人为 IO delay。Methodology 保存 TIMING-18：219 个缺失输入/输出 delay 的检查记录；check_timing 为 40 个输入、147 个输出未设 delay，内部 unconstrained endpoint 为 0。OOC HD.CLK_SRC 未指定，走线/时钟为估计。没有豁免这些问题，也没有把 OOC PASS 扩展为系统接口、板级 100 MHz 或实测吞吐 PASS。

## 优化后的完整功能回归

- 15 项 XSim 全部 PASS，包含 H3、彩色/复位定向、阈值 0/73/128/200/255、TKEEP=1/3/7/F、七类坏包及后续合法包恢复；每项都启用基线逐周期对照。
- 全部 H3：21 序列、487 帧、37,401,600 像素，Binary8/SOF/EOL 零差异。Python 重新核对源 PNG 哈希并重算黄金输出；actual/golden SHA-256 均为 `3e2f35b2dbbb21b55408dcc62da11aa29217e304a9f976f7feac207aa384cced`。
- Python 全量回归：134 passed（37.95 秒）。定向 Python 比对：5 阈值、2 partial-keep、7 negative-recovery，全部零差异。
- 旧 XSim：RGB 230,912 像素、Binary 231,168 像素、Morphology 230,400 像素/3 帧、WO-0013 Monitor 76,816 字节/19,204 拍全部 PASS；前三项输出再与保存的独立 golden 全量比对，零差异。

完整新链路 `source` 在 MCP 客户端等待 300 秒时报告超时；随后 Vivado status 确认命令已完成且 `last_completed_code=0`，15 个独立日志都有 RESULT PASS。没有把客户端超时当作功能通过证据或重新运行硬件。证据汇总：[delivery-summary.json](../../data/results/wo0014-timing-20261014/delivery-summary.json)；详细 H3/定向报告在同目录 `reports/`，原始日志及本地生成文件保留。

## DMA 长度与后续硬件方案

独立评估：[wo-0014-dma-length-assessment-20261014.md](wo-0014-dma-length-assessment-20261014.md)。RGB 包为 16 字节头 + 230,400 字节 RGB888 + 16 字节尾，230,432 字节/57,608 拍，只有末尾 trailer 拍置 TLAST。

推荐未来独立 BD 副本将 Buffer Length Width 从 17 改为 18（最大 262,143 字节），保持单次 MM2S 包。IP 规格允许；32 位地址和现有 HP0 DDR 窗口有容量，但新的连续缓冲区、实际基址/范围、缓存同步及生成 HWH/软件长度参数必须重新核验。DRE 关闭时沿用 8 字节源地址对齐。维持 17 位分包会产生中间 TLAST，当前解析器拒收；处理它需要分块状态/协议或 TLAST 重写及额外错误恢复验证，风险更高。此次没有生成或验证 18 位 BD。

下一阶段先在独立副本完成 DMA IP 包边界仿真、可读 packet/checker 状态或输出采集设计、BD 验证和综合实现。首轮板测建议保留既有 50 MHz，隔离长度改动；100 MHz 系统实验另以完整实现时序为前提。离线证据通过并获得硬件批准后，单次、无重试发送一个合法包，验收 DMA/输入 Monitor 和流水线输出统计，操作后检查板卡健康。仅有统计不能声称全像素板级比对；坏包恢复也需要单独批准多包实验。

## 剩余问题与变更边界

生产 RTL、四份受保护文档以及额外 README、WO-0013 已跟踪内容和原 BIT/HWH 均保持；保护原始 SHA-256 逐项复核。G1/Route、正式 PL CCL、WO-0013 Phase B 未变。

checker 不能单独检测最终帧 underflow 或提前新 SOF；坏包可能输出前缀，必须整帧作废；packet_done 不保证输出已经排空。这些原有语义未在时序优化中扩大或修复，硬件状态采集必须按包校验、输出完整性及排空分别验收。

相对基线仅 1 个已跟踪修改：实验 adapter。新增 `scripts/wo0014_timing/{prepare.py,run_ooc.tcl,verify_delivery.py}`、本报告、DMA 评估及独立结果目录。优化增量未暂存/提交。历史无关未跟踪文件原样保留，未清理或纳入提交。

复现：先运行 `python scripts/wo0014_timing/prepare.py`；在专用 Vivado Tcl 会话 source `scripts/wo0014_timing/run_ooc.tcl` 和结果目录的 `generated/run_full.tcl`、`generated/run_legacy.tcl`。`run_directed/run_negative` 是 run_full 的子脚本，不能独立启动。Python 比对使用现有 compare_output.py/compare_run_set.py；在仓库 `.venv` 中运行 pytest，然后执行 `python scripts/wo0014_timing/verify_delivery.py`。生成大向量/输出/缓存保留本地，不自动暂存。

所有本轮授权离线工作完成后停止，等待下一阶段硬件测试批准。
