# ADR-0001 · 视频源路线选择

| 项 | 内容 |
|---|---|
| **状态** | `Proposed` —— **candidate assessment only；Video source route NOT FROZEN** |
| **日期** | 2026-09-18 |
| **决策者** | 三人全体 |
| **关联** | MASTER_PLAN C2 / C3 / H1 / R1；WO-0002 |

## 背景

PYNQ-Z2（Zynq-7020）没有 MIPI CSI 座子。项目的核心叙事是“PL 处理像素流”，因此视频源通路是 R1 的关键风险。

本次 WO-0002 的目标是核实 HDMI IN 的工程接入条件和 DVP 候选引脚预算，不是冻结最终真实视频源。当前本机没有 Vivado，所以 IP Catalog、XDC 合法性、clock resource、综合和硬件时序均不能冒充已完成。

## 证据等级与本机环境

### `[Measured]` 工具链

执行：

```powershell
Get-Command vivado -ErrorAction SilentlyContinue | Format-List Name,Source,Version
vivado -version
```

实际输出为 PowerShell 的命令不存在错误：

```text
The term 'vivado' is not recognized as a name of a cmdlet, function, script file, or executable program.
```

解释：Vivado NOT FOUND。所有需要 Vivado 的项目标为 **Pending Toolchain Verification**，未安装 Vivado，也未伪造 IP、XDC 或时序结果。

### 主要 `[Documented]` 来源

- [PYNQ-Z2 base overlay 文档](https://github.com/Xilinx/PYNQ/blob/master/docs/source/pynq_overlays/pynqz2/pynqz2_base_overlay.rst)，`HDMI` / `HDMI In` / `PYNQ-Z2 Block Diagram` 小节：HDMI 直接连接 PL pins、由 PL HDMI IP 控制，输入可以送入 PS DRAM；PmodA 低 8 个数据脚与 RPi 共享。
- [PYNQ video library 文档](https://github.com/Xilinx/PYNQ/blob/master/docs/source/pynq_libraries/video.rst)，`Pipeline Processing`、`Configuration`、`Color Space Conversion`、`Pixel Format` 小节：HDMI signal blocks 是 AXI stream，自定义 AXI-stream IP 通常插在 HDMI-In 的 `pixel_pack` 后；默认前端为 24-bit BGR；可做 RGB/BGR/其它像素格式转换。
- [PYNQ-Z2 官方 base XDC](https://github.com/Xilinx/PYNQ/blob/master/boards/Pynq-Z2/base/vivado/constraints/base.xdc)，`HDMI signals` 行 799–822、`Arduino GPIO` 行 719–756、Pmod/RPi 对应小节：官方 board constraint 中的 package pin 与 IOSTANDARD。
- [AMD PG236 HDMI RX Subsystem — AXI4-Stream Video Interface](https://docs.amd.com/r/en-US/pg236-v-hdmi-rx-ss/AXI4-Stream-Video-Interface)，`Table 1`：`tdata/tlast/tready/tuser/tvalid` 与 `s_axis_video_aclk`。
- [AMD PG236 HDMI RX Subsystem — Clocking](https://docs.amd.com/r/3.1-English/pg236-v-hdmi-rx-ss/Clocking)，`Clocking`：HDMI link/native/video/AXI 等接口可以处于不同 clock rate。
- [AMD UG934 AXI4-Stream Video IP](https://docs.amd.com/r/en-US/ug934_axi_videoIP/Streaming-Video-Input-Connection)，`Streaming Video Input Connection`：视频输入到 AXI4-Stream 的 timing/interface 连接以及必要的输入缓冲说明。
- [AMD UG865 Zynq-7000 Package Pinout](https://docs.amd.com/r/en-US/ug865-Zynq-7000-Pkg-Pinout)，`XC7Z020 CLG400` package pinout：封装 pin/bank 核对依据；本机未运行 Vivado package query。
- [AMD UG585 Zynq-7000 SoC TRM](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Power-Pins)，`Power Pins`：VCCO 是 bank 级电气约束。

## C2 · HDMI IN 工程集成条件

### C2-① 实际实现路径

`[Documented]` PYNQ 官方 base overlay 文档明确：PYNQ-Z2 的 HDMI IN/OUT 直接连接到 PL pins，没有板上外部 HDMI bridge；HDMI 由 PL 中的 HDMI IP 控制。官方 base XDC 给出的 HDMI IN 候选资源为：

| 功能 | FPGA package pin | IOSTANDARD | 依据 |
|---|---|---|---|
| HDMI input clock P/N | N18 / P19 | TMDS_33 | PYNQ base XDC `HDMI signals` |
| HDMI input data lane 0 P/N | V20 / W20 | TMDS_33 | PYNQ base XDC |
| HDMI input data lane 1 P/N | T20 / U20 | TMDS_33 | PYNQ base XDC |
| HDMI input data lane 2 P/N | N20 / P20 | TMDS_33 | PYNQ base XDC |
| HPD | T19 | LVCMOS33 | PYNQ base XDC |
| DDC SCL/SDA | U14 / U15 | LVCMOS33 | PYNQ base XDC |

`[Documented]` PYNQ base overlay 是可复用的 reference design：它把 HDMI IP 接到 PS DRAM，支持 HDMI-in 到内存以及从内存到 HDMI-out 的路径。该文档证明的是板级连接和参考 overlay 路径，不证明本项目的自定义 AXI pipeline 已在当前机器生成。

### C2-② 所需 IP / overlay / pipeline

| 组件 | 当前证据 | 状态 |
|---|---|---|
| PYNQ-Z2 HDMI/DVI front-end / HDMI IP | PYNQ video 文档说明 Z1/Z2 有 DVI-based front-end，PYNQ base overlay 文档说明 HDMI IP 在 PL 中 | `[Documented]`，但本机 IP Catalog 版本未确认 |
| AXI4-Stream Video interface | AMD PG236 Table 1 定义 `tdata/tlast/tready/tuser/tvalid` | `[Documented]` |
| `pixel_pack` / pixel format conversion | PYNQ video 文档说明 HDMI-In side 可在 `pixel_pack` 后插入 custom AXI-stream IP，且支持 24/8/16/32-bit conversion | `[Documented]` |
| BGR→RGB conversion | PYNQ video 文档给出 24-bit BGR 默认格式和颜色空间转换 | `[Documented]`；接口 A 的 RGB888 仍需在设计中配置/验证 |
| 320×240 resize / mode adaptation | PYNQ 文档只列出 HDMI 标准分辨率捕获，未证明本项目目标模式会由输入端直接产生 | `[Hypothesis]`，需 source mode test + Vivado video pipeline verification |
| DMA / frame buffer | PYNQ 文档明确 batch/frame-buffer 路径使用 DMA 与 DRAM；是否采用取决于定稿架构 | `[Documented]` for the reference path；custom stream path Pending |
| AXI clock conversion / async FIFO | 由独立 `s_axis_video_aclk` 与项目 100 MHz 的跨时钟需求推导 | `[Hypothesis]`，需在 Vivado block design/CDC report 中验证 |
| PYNQ overlay/driver | 官方 base overlay 和 `pynq.lib.video` API 提供 HDMI-in、mode、start、frame path | `[Documented]` |
| IP 版本、license、当前安装包 | 必须由 Vivado `get_ipdefs`/`get_board_parts` 与 license environment 得到 | **Pending Toolchain Verification** |

工单要求的 Vivado Tcl 查询尚未执行，原因是 `vivado -version` 已实测失败。Vivado 安装后直接执行：

```tcl
get_parts
get_ipdefs -filter {NAME =~ "*hdmi*"}
get_ipdefs -filter {NAME =~ "*v_tc*" || NAME =~ "*axis_vid*"}
get_board_parts -filter {NAME =~ "*pynq*"}
```

本次没有把这些命令的预期结果写成实际结果。

### C2-③ 接口 A 与 HDMI 路径

接口 A 的目标是 24-bit RGB888、TUSER[0] SOF、TLAST 行末、TVALID/TREADY。判断如下：

| 接口 A 字段/约束 | HDMI 路径能否满足 | 证据与缺口 |
|---|---|---|
| `TDATA` 24-bit RGB888 | **可通过配置/转换满足，非“直接原样”保证** | `[Documented]` PYNQ 默认 front-end 是 24-bit BGR，支持 colorspace conversion；AMD PG236 的 data width 由 BPC/PPC 配置。需在最终 BD 中配置 8 BPC、1 PPC，并确认 RGB channel order。 |
| `TUSER[0]` SOF | **接口语义匹配** | `[Documented]` AMD PG236 Table 1 把 `VIDEO_OUT_tuser` 定义为 Start of frame；最终 source IP 的具体产生方式仍需 Vivado/仿真确认。 |
| `TLAST` 行末 | **接口语义匹配** | `[Documented]` AMD PG236 Table 1 把 `VIDEO_OUT_tlast` 定义为 End of line；最终视频 timing 配置 Pending。 |
| `TVALID/TREADY` | **接口语义匹配** | `[Documented]` AMD PG236 Table 1 直接列出 valid/ready；接口连接与反压行为需 block design/simulation 验证。 |
| `320×240 @ ≥30 fps` | **不能从当前文档直接确认** | `[Documented]` PYNQ 文档说 HDMI-in 捕获标准 HDMI resolutions；没有给出本项目 320×240 模式的保证。`[Hypothesis]` 需要输入源提供可接收模式，或增加 scaler/video timing conversion；需 hardware mode test + Vivado design verification 升级。 |
| HDMI pixel clock vs project 100 MHz | **不是同一个时钟的默认假设** | `[Documented]` PYNQ base XDC 对 HDMI input clock 给出 `create_clock -period 8.334` 的板级约束示例；AMD PG236 明确 `s_axis_video_aclk` 与 HDMI link/video clocks 可以不同。`[Hypothesis]` 需 AXIS clock converter/async FIFO 或让处理 IP 使用 source AXIS clock；需要 CDC report。 |
| buffering / latency | **可做，但延迟模型未确定** | `[Documented]` PYNQ reference path 连接 PS DRAM，video library 的 batch path 明确有 DMA/DRAM frame buffer；AMD UG934 说明 VID-IN 必要时会缓冲。`[Hypothesis]` 若 custom IP 紧接 `pixel_pack`，可避免 PS full-frame round trip，但实际 FIFO/line/frame buffering 和端到端 latency 只能由硬件 timestamp/ILA 测量确认。 |

**C2-③ 四选一结论：`[Hypothesis] 需额外 IP 或转换级`。**

理由：HDMI RX/front-end →（必要的 BGR/RGB 与像素格式转换）→（可能的 scaler/mode adaptation）→（AXI clock conversion/CDC）→ interface A，不是 TMDS pins 直接连到 100 MHz custom stream。它不是当前证据下的“不可行”，但也没有 Vivado 或板卡输出可称作已验证的“可直接接入”。

### C2-④ Competition Version 适合性

`[Documented]` HDMI 输入是 PYNQ-Z2 的 PL 视频输入路径，且 PYNQ 有官方 HDMI reference overlay。`[Hypothesis]` 若现场摄像机输出符合该 HDMI 接收路径的模式，则它可以作为真实视频输入候选；具体 camera/mode 兼容性仍需 source test，不能由板上存在 HDMI 口直接推出。

`[Hypothesis]` 对本项目而言 HDMI 的工程风险高于“板上有接口”这一事实：目标 320×240@30、RGB888 顺序、source/AXI 时钟跨域、是否走 DRAM frame buffer、以及确定性 latency 都尚未完成验证。若使用 reference overlay 的 PS DRAM 路径，还必须把 DMA/帧缓冲延迟从 PL 处理延迟中分离出来。

本阶段适合性结论：**候选可保留，但不应作为已批准的 Competition Version 最终输入**。待 Vivado IP/BD/XDC 与板上 source test 完成后再比较 HDMI 与 DVP。使用 file replay/TPG 进行 PL 开发不等同于最终真实输入。

## C3 · DVP pin-budget 结论

`[Hypothesis]` `hw/pin_budget.md` 给出 16 根信号（含 RESET/PWDN）的 Arduino GPIO[0:15] 候选映射：每根有官方 PYNQ XDC 的 package pin 和 `LVCMOS33`，线数在 20 个普通 GPIO 内，候选 bank 为 34。

但本次仍不能把 DVP 判为已完成：

- PCLK 候选 `T16` 的 clock-capable 属性没有 Vivado 输出；
- 实际 bank VCCO 与 camera 端电平未做板卡/电源核对；
- Arduino base overlay 的 MicroBlaze/trace analyzer 释放和自定义 overlay 资源冲突未验证；
- XCLK 输出时序、SCCB 三态/外部上拉与真实摄像头时序未验证。

因此 C3 当前状态是“**线数/约束候选条件满足，但最终 pin feasibility Pending Toolchain Verification**”。完整逐信号表和未验证 XDC 草案见 `hw/pin_budget.md`。

## 四条候选路线对比

| 候选 | 是否最终真实视频输入 | 数据路径 | 当前证据/代价 | 当前状态 |
|---|---|---|---|---|
| HDMI IN | 是，若摄像机输出 HDMI | HDMI TMDS → PL HDMI IP/front-end → pixel/AXI stream；也可经 PS DRAM | `[Documented]` 板级连接和 PYNQ reference overlay；`[Hypothesis]` 本项目需要颜色/分辨率/CDC 处理，Vivado/板上验证未完成 | Candidate，**not selected** |
| DVP camera | 是 | camera parallel DVP → Arduino/Pmod candidate pins → custom PL receiver | `[Hypothesis]` 16 根线可放在 Arduino GPIO[0:15]；PCLK clock resource、VCCO、overlay 冲突未完成 | Candidate，**conditional** |
| USB camera | 是，但不是纯 PL 采集 | USB camera → PS/Linux/V4L2 → memory/DMA/MM2S → PL AXI stream（若要进 custom pipeline） | `[Hypothesis]` 这是 PS-mediated 数据路径，不是由“有 USB”直接证明的 PL source；需要 PS CPU、DMA、buffering，延迟/带宽和实时性须实测 | Candidate，PS-mediated |
| file replay / TPG | **否；development source** | file/TPG → PS or PL test stream → custom pipeline | 易复现、无摄像头依赖，适合开发/回归；不构成 Competition Version 的真实输入证明 | Development fallback only |

### 暂定工作建议（不是冻结）

`[Hypothesis]` 在工具链缺失期间，file replay/TPG 可继续用于 PL 接口开发；HDMI 应优先做板载路径的 Vivado/板上验证；DVP 继续保留为低级别、可控时序的候选；USB 只作为 PS-mediated fallback。此段不构成最终路线决策。

## Pending Toolchain Verification

Vivado 安装后需逐项完成并保存输出：

1. `vivado -version`、器件/board part 检查。
2. C2 IP Catalog：`get_parts`、`get_ipdefs`、`get_board_parts`；记录 HDMI RX、video timing、AXI video、clock conversion、scaler/VDMA 的实际版本与 license 状态。
3. 生成 HDMI custom block design：HDMI RX/front-end → color/pixel conversion → CDC/AXI clock conversion → interface A；运行 `validate_bd_design`、elaboration、synthesis、DRC、clock/CDC reports。
4. 对 `320×240 @ 30 fps` 做 source mode/capture test；如不能直接捕获，记录 scaler/format conversion 和 buffer depth。
5. 把候选 DVP XDC 提取为 `.xdc`，运行 `read_xdc`、`report_drc`、package pin/IOSTANDARD 校验；查询 `T16` 的 clock-capable 属性并重新选择 PCLK（若不通过）。
6. 板上测量 HDMI/DVP 端到端 latency、frame drop/backpressure 与实际时钟；不能以软件 frame read/write 延迟替代 PL pipeline latency。

## 决策

**Proposed / candidate assessment only。Video source route NOT FROZEN。**

本次没有足够 `[Measured]` 证据冻结 HDMI、DVP 或 USB 中任一路线。HDMI 当前结论为“需额外 IP 或转换级”，DVP 当前结论为“线数候选满足但 PCLK/电气/工具链 Pending”；后续真实验证决定最终路线。

## 后果

- 在 Vivado 缺失期间不能声称 C2/C3 完成，也不能生成“validated XDC”。
- H3 synthetic generator 与 dataset 不受本 ADR 影响；file replay/TPG 仅作为 development source。
- 最终 route 选择仍需把工具链验证、板上信号、buffering/latency 和比赛演示可靠性一起评估。
