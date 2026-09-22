# WO-0003 · PL Stream Skeleton

| 项 | 内容 |
|---|---|
| **状态** | `OPEN` —— 第一轮并行开发任务，待负责人执行 |
| **负责人** | 成员 A / 队长 |
| **前置** | `MASTER_PLAN.md`；`docs/00_scope.md`；`docs/02_interfaces.md` 接口 A；Vivado/XSim 或可替代 RTL 仿真器 |
| **性质** | 1～3 天的最小 PL 数据流实验；不实现正式视觉算法 |

## Objective

建立一个可重建、可仿真、可综合的最小 PL 数据流骨架，理解 AXI4-Stream 的 `TVALID`、`TREADY` 和传输条件，为后续像素流处理提供可复用的起点。

本 WO 的完成不代表图像采集、滤波、二值化、形态学或连通域已经实现。

## Background

当前接口契约 A 采用统一视频流抽象：`TDATA` 携带像素数据，`TUSER` 表示帧起始，`TLAST` 表示行结束，只有 `TVALID && TREADY` 同时为 1 时才发生一次传输。当前仓库保存了接口和规划文档，但本 WO 开始前不假设已有可复用的正式 RTL 数据通路。

实验应使用小型、固定宽度的数据流，而不是接入摄像头或完整图像算法。建议把一拍定义为一个 24-bit RGB888 样例，并保留 `TUSER`/`TLAST`，使实验与接口 A 对齐。

## In Scope

- 一个最小的 1-deep AXI4-Stream elastic buffer 或等价的带反压 pass-through 模块。
- `TDATA[23:0]`、`TUSER[0:0]`、`TLAST`、`TVALID`、`TREADY` 的基本行为。
- 一个独立 Testbench，能够发送固定数量的数据拍，并主动制造下游 backpressure。
- 使用可用的 RTL 仿真器完成仿真；优先使用 Vivado XSim。
- 使用 Vivado 对该最小模块进行综合，并生成 utilization 与 timing summary 报告。
- 一份面向初学者的实验结果说明，记录握手规则、工具版本、命令和已知限制。

## Out of Scope

- 摄像头、PYNQ-Z2 板卡、DMA、PS、AXI4-Lite 或显示输出集成。
- ROI、滤波、二值化、形态学、连通域、特征提取和跟踪计数。
- 完整 Vivado Block Design、bitstream、板上实测和性能宣称。
- 为本实验引入复杂的多级 FIFO、图像缓存或自定义协议。

## Deliverables

负责人在自己的分支提交以下小范围产物；文件名可以按仓库实际 RTL 命名习惯调整，但不能扩大到正式视觉链路：

1. 最小 RTL 模块，例如 `fpga/rtl/axis_stream_skeleton.v`。
2. Testbench，例如 `fpga/sim/tb_axis_stream_skeleton.sv`。
3. 仿真运行说明和 PASS 日志，写入 `docs/verification/wo-0003.md` 或等价文档。
4. 综合产生的 utilization/timing 结果摘要。大型工具生成目录不提交；只提交可读的摘要和复现命令。
5. 如果使用时钟约束，提交与该实验对应的最小 XDC 或在验证文档中明确虚拟时钟约束来源。

## Step-by-step Tasks

1. 从当前 `main` 创建建议分支 `feat/pl-stream-skeleton`，先阅读接口 A，不修改接口契约。
2. 画出 1-deep buffer 的三个状态：空、存有一拍且等待输出、输出拍被接收后可装载下一拍。写清楚 `fire = VALID && READY`。
3. 编写 RTL：输入端可以被 backpressure；输出端在 `VALID=1 && READY=0` 时必须保持 `TDATA/TUSER/TLAST` 不变。
4. 编写 Testbench，至少发送 8 个带有不同数据值的 beat，并包含帧首、行尾标志。
5. 在 Testbench 中安排连续 ready、间歇 ready 和至少一次多周期 `READY=0`。检查无丢拍、无重复、无乱序。
6. 先运行仿真，再阅读波形，确认每次计数只发生在 `VALID && READY` 为 1 的周期。
7. 用 Vivado 对最小 RTL 综合，记录 Vivado 版本、目标 part/时钟约束、utilization 和 timing summary。没有有效时钟约束时必须明确标为“无时钟约束的报告”，不能写成 timing closure。
8. 整理 `docs/verification/wo-0003.md`：写出命令、测试输入、观察结果、报告路径和未完成项。
9. 只提交本 WO 相关文件；不要修改 `python/synth/`、GT、既有回归测试或其他成员的任务文档。

## Acceptance Criteria

- [ ] RTL 可以被选定的仿真器编译，仿真以进程退出码 0 结束并输出明确的 `PASS`。
- [ ] Testbench 实际覆盖至少 8 个 beat、帧首/行尾标志和一次多周期下游停顿。
- [ ] 在 `TVALID && !TREADY` 期间，输出数据和边界标志保持稳定；恢复 ready 后继续正确传输。
- [ ] 仿真统计显示输入与输出 beat 数相同，数据顺序完全一致，没有丢失、重复或提前消费。
- [ ] 已生成 utilization 与 timing summary；报告注明目标器件/时钟约束，任何 no-clock 或未实现限制都被如实记录。
- [ ] 验证文档能让另一名队友只按命令重新运行仿真，并能区分“已仿真”“已综合”和“未上板”。
- [ ] 变更没有声称完成正式 PL 图像处理，也没有改变接口 A 的正式契约。

## Tests

建议至少运行：

```text
# 仿真器命令按本机安装情况填写，例如 Vivado xsim 或 iverilog/vvp
<simulator command> <testbench> <rtl source>

# Vivado Tcl 中生成的最小报告
report_utilization -file <wo-0003-utilization.rpt>
report_timing_summary -file <wo-0003-timing-summary.rpt>
```

验收者应检查仿真日志、关键波形或断言、报告摘要和 `git diff --check`。如果本机没有 Vivado，允许完成仿真学习部分，但综合验收必须标为未完成，不能用推测替代。

## Risks

- 初学者把 `TVALID=1` 误认为数据已经被接收；必须以 `TVALID && TREADY` 为唯一传输条件。
- 直接组合透传可能在停顿时改变输出；应使用寄存器或等价的稳定保持逻辑。
- 本机工具版本或目标 part 不一致会影响报告；报告必须记录实际环境。
- 没有时钟约束时 timing 报告不具有时序收敛意义；不得把报告存在写成时序已通过。

## Dependencies

- `docs/02_interfaces.md` 接口 A 的字段和边界语义。
- 可用的 RTL 仿真器；综合验收需要 Vivado。
- 不依赖摄像头、PYNQ-Z2 板卡、synthetic dataset 或其他成员的未完成代码。

## Suggested Branch

`feat/pl-stream-skeleton`
