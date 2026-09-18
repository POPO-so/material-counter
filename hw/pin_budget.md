# PYNQ-Z2 扩展引脚预算与 DVP 候选映射（C3）

> 本文件是 WO-0002 的前置可行性记录，不是已冻结的视频源约束。
> 证据标签含义：`[Measured]` 只表示本机实际命令输出；`[Documented]` 必须有官方出处；`[Hypothesis]` 表示仍需工具链或板卡验证。

## 证据与边界

### 本机工具链检查

`[Measured]` 执行命令：

```powershell
Get-Command vivado -ErrorAction SilentlyContinue | Format-List Name,Source,Version
vivado -version
```

实际输出：

```text
vivado:
Line |
   2 | ... vivado -version
     | ~~~~~~
     | The term 'vivado' is not recognized as a name of a cmdlet, function, script file, or executable program.
```

解释：本机没有可调用的 Vivado。因此本文件不把 IP Catalog、封装引脚合法性、clock-capable 属性、XDC、综合或时序结果写成 `[Measured]`。所有这些项目均列入 **Pending Toolchain Verification**。

### 主要 `[Documented]` 来源

1. [PYNQ-Z2 官方 base overlay 文档](https://github.com/Xilinx/PYNQ/blob/master/docs/source/pynq_overlays/pynqz2/pynqz2_base_overlay.rst)，`HDMI`、`HDMI In`、`PYNQ-Z2 Block Diagram` 小节：HDMI 直接连接 PL pins，由 PL 中的 HDMI IP 控制；HDMI 输入可送到 PS DRAM；PmodA 的 8 个数据脚与 RPi 低 8 个数据脚共享。
2. [PYNQ 官方 PYNQ-Z2 base overlay XDC](https://github.com/Xilinx/PYNQ/blob/master/boards/Pynq-Z2/base/vivado/constraints/base.xdc)，`Pmoda` 行 672–695、`Pmodb` 行 696–718、`Arduino GPIO` 行 719–756、`HDMI signals` 行 799–822、`Raspberry PI` 行 922–958：给出官方 board constraint 中的 PACKAGE_PIN 与 IOSTANDARD。
3. [AMD UG865 Zynq-7000 Package Pinout](https://docs.amd.com/r/en-US/ug865-Zynq-7000-Pkg-Pinout)，XC7Z020 CLG400 package pinout：用于 package pin 到 I/O bank 的核对。由于本机无 Vivado，本次没有执行 `get_package_pins` 或 `report_property`；bank 列是基于官方 package pinout 的候选转录，不是本机工具链实测。
4. [AMD UG585 Zynq-7000 SoC Technical Reference Manual](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Power-Pins)，`Power Pins`：I/O bank 的 VCCO 是 bank 级供电约束；本表使用 `LVCMOS33`，仍需在板卡 schematic/电源表和 Vivado 中确认实际 VCCO。

### 证据限制

- `[Documented]` 的 package pin / IOSTANDARD 来源是官方 PYNQ XDC 与 AMD package pinout；本机未取得 TUL schematic 的本地副本，未进行板卡目视核对。
- 因此，Arduino GPIO 的候选线可以进入“约束草案”，但不能被表述为已验证的最终 camera connector pinout。
- PCLK 是否为 MRCC/SRCC 或其它可用于该设计的 clock-capable resource，必须由 Vivado package/clock property 查询确认；本文件明确不替它做推断。

## C3-① 可用扩展接口盘点

| 接口 | 可用逻辑 GPIO | 候选 I/O bank | I/O standard / 电压 | clock-capable 信息 | 板载/接口冲突 | 状态与来源 |
|---|---:|---|---|---|---|---|
| Pmod A | 8 | Bank 34（package pinout 转录） | `LVCMOS33` / 3.3 V 约束 | U18/U19 为 clock-related 候选，专用属性未在 Vivado 确认 | 8 个信号与 RPi 低 8 个共享；PYNQ base overlay 的 Pmod MicroBlaze/trace analyzer 使用 | `[Documented]` PYNQ XDC 行 672–695；共享关系见 PYNQ base overlay 文档 |
| Pmod B | 8 | Bank 34（package pinout 转录） | `LVCMOS33` / 3.3 V 约束 | 本次未确认可用专用时钟脚 | PYNQ base overlay 的 Pmod MicroBlaze/trace analyzer 使用 | `[Documented]` PYNQ XDC 行 696–718 |
| 双排 Pmod（A+B 合计） | 16 个逻辑端点 | Bank 34（候选） | `LVCMOS33` / 3.3 V 约束 | 不能把 16 根线整体视为 clock-capable；逐脚查询仍 Pending | Pmod A 的 8 根与 RPi 共享；两排需要两个物理连接器 | `[Documented]` PYNQ XDC；不是额外的第三个接口 |
| Arduino header | 20 个普通 GPIO；另有独立 I2C/SPI 与模拟通道 | Bank 34（package pinout 转录） | `LVCMOS33` / 3.3 V 约束 | 选定的 PCLK 候选 `T16` 的专用时钟属性 Pending | base overlay 的 Arduino MicroBlaze 使用；P15/P16、W15/T12/H15/F16 是独立 I2C/SPI，不占本候选 0–15 | `[Documented]` PYNQ XDC 行 719–774；模拟脚见行 775–798 |
| RPi header | 20 个逻辑 GPIO；与 Pmod A 存在共享信号 | 混合 PL bank，至少包含 Bank 13/35（package pinout 逐脚转录） | `LVCMOS33` / 3.3 V 约束 | connector 注释含 GCLK/PWM/SPI/UART 等复用属性；本次不选它承载 PCLK | 8 个低位数据脚与 Pmod A 共享；另有 I2C、SPI、UART、PWM 复用 | `[Documented]` PYNQ XDC RPi 表及行 922–958、AMD UG865；逐脚 bank/clock 属性仍 Pending |

逻辑端点的算术合计为 `8 + 8 + 20 + 20 = 56`，但 Pmod A/RPi 的 8 根共享，所以去重后的候选 PL 端点上限为 **48**。这不是“可立即复用”的承诺：base overlay 的 MicroBlaze 与 trace analyzer 必须在自定义 overlay 中移除或重新分配。

## C3-② DVP 需求清单

本候选按 `RESET` 与 `PWDN` 都保留来预算，共 16 根：

| 信号 | 根数 | 方向 | 备注 |
|---|---:|---|---|
| D[7:0] | 8 | in | 8-bit parallel pixel data |
| PCLK | 1 | in | **必须使用可接受的 clock-capable resource** |
| VSYNC | 1 | in | frame timing |
| HREF/HSYNC | 1 | in | line-valid/timing |
| XCLK | 1 | out | camera master clock，输出频率/占空比需另行定义 |
| SCCB SDA | 1 | inout | open-drain semantics，使用外部上拉 |
| SCCB SCL | 1 | out/open-drain | 使用外部上拉 |
| RESET | 1 | out | camera reset |
| PWDN | 1 | out | camera power-down |
| **合计** | **16** |  | 20 根 Arduino GPIO 中可放下，但 PCLK clock resource 尚未验证 |

## C3-③ 逐信号候选映射

以下候选使用 Arduino `GPIO[0:15]`，因为它们在官方 PYNQ XDC 中逐项具有 package pin 和 `LVCMOS33` 约束。表内的“物理排针”使用 PYNQ constraint 的逻辑 header net 名；正式连接器丝印编号仍需以 TUL schematic/板卡实物复核。

| 信号 | 候选物理排针 | FPGA 封装引脚 | I/O Bank | IOSTANDARD | clock-capable? | 冲突 | 可行性 |
|---|---|---|---|---|---|---|---|
| D0 | Arduino GPIO[0] | T14 | 34 | LVCMOS33 | n/a | base Arduino MicroBlaze 占用；与 HDMI package pins 不重叠 | `[Hypothesis]` 线/电平条件满足，overlay 重分配 Pending |
| D1 | Arduino GPIO[1] | U12 | 34 | LVCMOS33 | n/a | 同上 | `[Hypothesis]` |
| D2 | Arduino GPIO[2] | U13 | 34 | LVCMOS33 | n/a | 同上 | `[Hypothesis]` |
| D3 | Arduino GPIO[3] | V13 | 34 | LVCMOS33 | n/a | 同上 | `[Hypothesis]` |
| D4 | Arduino GPIO[4] | V15 | 34 | LVCMOS33 | n/a | 同上 | `[Hypothesis]` |
| D5 | Arduino GPIO[5] | T15 | 34 | LVCMOS33 | n/a | 同上 | `[Hypothesis]` |
| D6 | Arduino GPIO[6] | R16 | 34 | LVCMOS33 | n/a | 同上 | `[Hypothesis]` |
| D7 | Arduino GPIO[7] | U17 | 34 | LVCMOS33 | n/a | 同上 | `[Hypothesis]` |
| PCLK | Arduino GPIO[10] | T16 | 34 | LVCMOS33 | **Pending**：必须在 Vivado 查询 MRCC/SRCC/clock-capable 属性 | 不能与 DVP 其它信号重复；base Arduino MicroBlaze 冲突 | `[Hypothesis]` 只有在 clock resource 查询通过后才可升级 |
| VSYNC | Arduino GPIO[8] | V17 | 34 | LVCMOS33 | n/a | base Arduino MicroBlaze 占用 | `[Hypothesis]` |
| HREF/HSYNC | Arduino GPIO[9] | V18 | 34 | LVCMOS33 | n/a | base Arduino MicroBlaze 占用 | `[Hypothesis]` |
| XCLK | Arduino GPIO[11] | R17 | 34 | LVCMOS33 | output timing Pending | 需要 PL 时钟输出/ODDR 或等效资源；base Arduino MicroBlaze 冲突 | `[Hypothesis]` |
| SCCB SDA | Arduino GPIO[12] | P18 | 34 | LVCMOS33 | n/a | 不是官方 direct I2C P15/P16；需外部上拉和三态控制 | `[Hypothesis]` |
| SCCB SCL | Arduino GPIO[13] | N17 | 34 | LVCMOS33 | n/a | 不是官方 direct I2C P15/P16；需外部上拉和三态控制 | `[Hypothesis]` |
| RESET | Arduino GPIO[14] | Y11 | 34 | LVCMOS33 | n/a | base Arduino MicroBlaze 占用 | `[Hypothesis]` |
| PWDN | Arduino GPIO[15] | Y12 | 34 | LVCMOS33 | n/a | base Arduino MicroBlaze 占用 | `[Hypothesis]` |

来源：PYNQ 官方 XDC 的 Arduino GPIO mapping 行 719–756；bank 34 为 AMD UG865 `XC7Z020-CLG400` package pinout 的候选转录。`LVCMOS33` 是 PYNQ 官方 XDC 中对这些 header GPIO 的约束值。

### C3-④ 电平、bank、时钟与冲突检查

- **线数**：16 根候选信号可以放入 Arduino 20 个普通 GPIO，保留 4 根普通 GPIO；所以线数预算不是立即阻塞项。
- **电平**：候选行均使用 `LVCMOS33`。同一 bank 内没有混入 1.8 V I/O 的候选信号；不过实际 bank VCCO 与外接摄像头电平仍需 board schematic/电源检查和 Vivado DRC 升级确认。
- **PCLK**：`T16` 只因官方 XDC 给它一个普通 GPIO package mapping 而被选作候选，**没有据此宣称它是 clock-capable**。需要在 Vivado 中查询 `PACKAGE_PIN` 的 `IS_CLK_CAPABLE`/site 属性并执行时钟约束检查；在此之前 PCLK 可行性是 `[Hypothesis]`。
- **XCLK**：GPIO 输出可作为候选物理线，但摄像头需要的频率、占空比、抖动和 ODDR/PLL 方案尚未定义，也没有 timing closure 证据。
- **板载资源冲突**：PYNQ base overlay 为 Pmod、Arduino、RPi 提供 MicroBlaze 及 trace analyzer。自定义 PL 设计不能同时把同一组端口留给 base overlay；需要自定义 overlay 释放并重新约束。
- **非普通 I/O**：Arduino 的 direct I2C、direct SPI 和 analog channel 是单独资源；本 DVP 候选只取 `arduino_gpio_tri_io[0:15]`，没有把模拟通道或 direct I2C/SPI 错算为普通 GPIO。

## XDC 候选草案（未验证）

下面草案只展示候选约束，**不是可交付的已验证 XDC**。PCLK 周期 `20.000 ns` 只是 50 MHz 估算；实际 camera mode 未确定。`set_input_delay` 的数值同样是接口时序占位值，必须由 camera datasheet 与 PCB 走线时序替换。

`[Hypothesis] XDC draft — NOT Vivado-validated`：本机没有 Vivado，未执行 `read_xdc`、`report_drc`、`synth_design` 或 timing analysis。

```tcl
# [Hypothesis] DVP candidate only. NOT Vivado-validated.
set_property -dict {PACKAGE_PIN T14 IOSTANDARD LVCMOS33} [get_ports {dvp_d[0]}]
set_property -dict {PACKAGE_PIN U12 IOSTANDARD LVCMOS33} [get_ports {dvp_d[1]}]
set_property -dict {PACKAGE_PIN U13 IOSTANDARD LVCMOS33} [get_ports {dvp_d[2]}]
set_property -dict {PACKAGE_PIN V13 IOSTANDARD LVCMOS33} [get_ports {dvp_d[3]}]
set_property -dict {PACKAGE_PIN V15 IOSTANDARD LVCMOS33} [get_ports {dvp_d[4]}]
set_property -dict {PACKAGE_PIN T15 IOSTANDARD LVCMOS33} [get_ports {dvp_d[5]}]
set_property -dict {PACKAGE_PIN R16 IOSTANDARD LVCMOS33} [get_ports {dvp_d[6]}]
set_property -dict {PACKAGE_PIN U17 IOSTANDARD LVCMOS33} [get_ports {dvp_d[7]}]
set_property -dict {PACKAGE_PIN T16 IOSTANDARD LVCMOS33} [get_ports dvp_pclk]
set_property -dict {PACKAGE_PIN V17 IOSTANDARD LVCMOS33} [get_ports dvp_vsync]
set_property -dict {PACKAGE_PIN V18 IOSTANDARD LVCMOS33} [get_ports dvp_href]
set_property -dict {PACKAGE_PIN R17 IOSTANDARD LVCMOS33} [get_ports dvp_xclk]
set_property -dict {PACKAGE_PIN P18 IOSTANDARD LVCMOS33} [get_ports dvp_sccb_sda_io]
set_property -dict {PACKAGE_PIN N17 IOSTANDARD LVCMOS33} [get_ports dvp_sccb_scl]
set_property -dict {PACKAGE_PIN Y11 IOSTANDARD LVCMOS33} [get_ports dvp_reset_n]
set_property -dict {PACKAGE_PIN Y12 IOSTANDARD LVCMOS33} [get_ports dvp_pwdn]

# Estimate only: 50 MHz PCLK, not a camera-derived value.
create_clock -name dvp_pclk -period 20.000 [get_ports dvp_pclk]
set_input_delay -clock dvp_pclk -min 0.000 [get_ports {dvp_d[*] dvp_vsync dvp_href}]
set_input_delay -clock dvp_pclk -max 3.000 [get_ports {dvp_d[*] dvp_vsync dvp_href}]
```

Vivado 安装后应执行：

```tcl
create_project -in_memory -part xc7z020clg400-1
read_xdc <extracted-dvp-candidate.xdc>
get_package_pins -filter {NAME =~ "T16"}
report_property [get_sites -of_objects [get_package_pins T16]]
report_drc
```

实际执行时应把 Tcl block 提取为 `fpga/constraints/dvp_candidate.xdc`，再运行 `read_xdc fpga/constraints/dvp_candidate.xdc`；这里没有新建该文件，以免把未验证草案误当成工程约束。

## C3 结论

`[Hypothesis]` Arduino 端口的**线数和 3.3 V I/O 约束看起来足以容纳 16 根 DVP 信号**，但本次不能升级为“DVP 已可行”：PCLK clock-capable 属性、实际 VCCO、base overlay 释放、XCLK 时序和真实摄像头电平/时序仍未完成验证。缺少 Vivado 是这些项目的直接 blocker；安装后应按上面的 package/DRC/时序步骤验证。

## 与 HDMI IN 的关系

HDMI 使用板载 PL 连接，不消耗本 DVP 候选的扩展 GPIO；这不自动证明 HDMI 能接入本项目流水线。HDMI 与 DVP 必须分别完成工具链/硬件验证后再比较，**本文件不冻结视频源路线**。
