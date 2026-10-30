# G2 RLE 基线回归记录

- 日期：2026-10-30
- 工作区 HEAD：`19a85d2`
- 工具：Vivado v2026.1 64-bit（SW Build 6511674 / IP Build 6504888 / SharedData Build 6501428）

## 结论与范围

G1、G1.5 已通过。G2 已开始，本记录仅证明独立 RLE 基线 RTL 回归及小参数 OOC 综合后的功能回归通过。它不是 RLE+HDMI+buttons+monitor 集成验证，也不代表 G2 或整机通过。

以下工作尚未完成：集成 block design、集成综合/实现、时序分析、CDC、DRC、新集成制品生成与一致性校验、板测。G2.5、G3、G4 尚未开始。

## 方法与结果

### RTL 仿真回归

- 流程入口：[`scripts/rle/run_sim.tcl`](../../../scripts/rle/run_sim.tcl)，仿真向量由 [`scripts/rle/sim_vectors.py`](../../../scripts/rle/sim_vectors.py) 生成。
- 场景：`baseline`、`roi`、`erode`、`dilate`、`empty`、`overflow`、`bad_checksum`、`bad_header`；完整参数见 [`cases.txt`](rtl-xsim/cases.txt)。
- 完整回归终端标记：`RLE_ENCODER_PASS frames=4`、8 个 `RLE_PIPELINE_640X400_PASS`、`RLE_XSIM_ALL_PASS cases=8`。
- 日志：[`rtl-xsim/`](rtl-xsim/)；每场景日志位于 [`rtl-xsim/xsim/`](rtl-xsim/xsim/)。
- 首次批处理只完成 4/8 后，外层执行调用被中断；无设计错误证据，但没有完整套件 PASS。原始证据保留在 [`partial-attempt/`](partial-attempt/)，不计作通过。之后 fresh retry 在 `rtl-xsim/` 完整通过。

### 综合后功能回归

- 流程入口：[`scripts/rle/run_netlist_sim.tcl`](../../../scripts/rle/run_netlist_sim.tcl)。对 `axis_rle_encoder` 使用 `WIDTH=5`、`HEIGHT=3`、`MAX_RUNS=2` 执行 OOC `synth_design`，再对综合网表做功能仿真。
- 终端标记：`RLE_ENCODER_PASS frames=4` 与 `RLE_POST_SYNTHESIS_PASS`。
- 报告、仿真日志和综合网表：[`post-synth/`](post-synth/)。
- 这是参数化模块的小规模综合后功能检查，不是集成位流、时序或实现验证。

### Vivado 状态与 warning

本次 Vivado synthesis 记录为 0 errors、0 critical warnings，合计 9 warnings。已审阅的不同 warning 类型包括：

- 四条 `m_tkeep` 位常量为 1：所有 lane 均有效，符合本协议的有效数据约定。
- `Netlist 29-101` floorplanning hierarchy 提示。

这些解释只对应本次 RLE 基线综合记录，不构成集成设计的综合或 DRC 结论。Vivado 原始报告见 [`post-synth/vivado.log`](post-synth/vivado.log) 与 [`post-synth/vivado-console.txt`](post-synth/vivado-console.txt)。

## 启动与执行记录

Vivado MCP 启动曾返回 `child exited: exit status 1`，原始日志保留于 [`mcp-startup/`](mcp-startup/)。随后通过同一已安装 Vivado 2026.1 的 batch/XSim 路径执行 RTL 与综合网表功能回归并通过。MCP 启动问题属于工具启动路径，不是 RTL 失败。

## 源文件 SHA-256

以下为本次基线对应源文件哈希：

| 文件 | SHA-256 |
|---|---|
| `fpga/experimental/rle/axis_rle_encoder.v` | `2606fc95105671a0f0864f5af01207ae2a2415fa76c9f45523c5e06103ed1bb7` |
| `fpga/experimental/gray8/axis_dma_gray_adapter.v` | `1d6f303af3ecbbd2fa72d9ff1921d23bce22f2e33bcfc90c03b4ff8a829137d4` |
| `fpga/rtl/axis_morphology3x3.v` | `41bd8ac2bd659357807c172ddc0e14b0b6a85d14afc8d5323836c60e8b277cb1` |
| `fpga/experimental/rle/axis_dma_rle_pipeline_bd.v` | `5e5eb3881db11c492589f651963dff2a61e1173f157ef093ea936788cdf0e3ac` |
| `fpga/sim/rle/tb_axis_rle_encoder.sv` | `b05b6c8edd3bf7f885a75aeec791a62a6747045670a87125846878630f323be0` |
| `fpga/sim/rle/tb_axis_rle_pipeline.sv` | `b7be38f257db7cc72768318813e143d75c68eac1f34620de49b5f97e0b79a0fe` |
| `scripts/rle/sim_vectors.py` | `d52b8fddbd6327c53419bded268b34da6bfc886cdd1e4593013a1e71ae9865f7` |
| `scripts/rle/run_sim.tcl` | `f17225b1965a7cc4ce8a8d09629957496b0ef30a2d47329b850a3b6d4395cd16` |
| `scripts/rle/run_netlist_sim.tcl` | `3185d533fe8e6a54955d30ae0bf4be43663a0d5c7c0f379fd49356f604d60c7d` |

## 局限与下一步

本基线未把 HDMI 显示、按钮和监测器纳入设计，未验证集成 block design、实现后时序、CDC、集成 DRC、制品一致性或板端行为。G2 尚未通过。

下一步完成 RLE+HDMI+buttons+monitor 的集成 block design 与综合/实现，审阅时序、CDC、DRC 并生成和核对新制品；通过后再进入 G2.5 并发压力验证。G1.5 的 CPU0、CPU1 均约 96% P50，故 G2.5 必须专门覆盖 HDMI 持续 720p60 扫描与有目标/高游程 RLE 并发，记录共享 DDR 竞争、反压、错误和 RLE 延迟分位数。G2.5、G3、G4 当前均未开始。
