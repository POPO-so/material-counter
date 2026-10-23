# WO-0017 独立 Vivado 构建

> 历史编号说明：本分支的曝光按键候选曾使用“WO-0017”编号；当前主线 WO-0017 已用于物理标定。本分支仅作为 Proposed 候选保留，不代表已采纳。继续推进前应先在主线分配独立 Work Order 编号并更新 ADR。

此候选从 WO-0015 的七个 RTL 输入和 50 MHz DMA/图像 Block Design 配置建立新工程，输出固定到 `data/results/wo0017-hw-50mhz-20261021/`。原 WO-0015 构建脚本和输出不改写。WO-0017 不修改 RTL，只将控制 SmartConnect 从 1×2 扩为 1×3，新增 BTN0/BTN1 AXI GPIO、地址段、顶层输入和 XDC。

现有数据面应原样保留：MM2S memory64/stream32 经 HP0；S2MM memory32/stream32 经 HP1；PS/DMA/图像流水线仍为 50 MHz。GPIO 是两个高有效输入，BTN0=D19、BTN1=D20，LVCMOS33。设计脚本在 BD 中核验其输入宽度、配置、AXI 连接和 0x41200000/64 KiB 地址；`finalize_build.py` 再检查 HWH、参数 readback、BIT/HWH 哈希及实现报告。

## Vivado Tcl 调用

从项目根目录启动 Vivado，使 `[pwd]` 指向仓库根目录。先在专用的 Vivado 会话中加载本项目的 `vivado-ip-configurator` helper，并将该会话真实 native log 路径绑定到 `::ipcfg::native_log_path`。然后创建 BD：

```tcl
set wo17_repo [file normalize [pwd]]
set wo17_output [file join $wo17_repo data results wo0017-hw-50mhz-20261021]
set wo17_clock_mhz 50
source -notrace [file join $wo17_repo scripts wo0017 create_s2mm_bd.tcl]
```

RTL 与 WO-0015 已通过的源哈希相同；继续使用 [WO-0015 XSim 记录](../../docs/verification/wo-0015-xsim-20261015.md) 中的既有仿真门。确认门证据和源哈希适用后，在同一 Vivado 会话设置门并构建：

```tcl
set wo17_xsim_gate PASS
source -notrace [file join $wo17_repo scripts wo0017 build.tcl]
```

构建会综合、实现并生成新 BIT/HWH，保存时序、利用率、DRC、methodology、时钟、约束和 routed checkpoint。Finalizer 为新文件生成配对 manifest；**Vivado 运行和板卡部署尚未发生**。

## 需要保留的构建边界

- 不在 `data/results/wo0015-hw-50mhz-20261015/` 上构建或覆盖文件。
- 只有在项目要求的接口 PR、三人确认和 ADR 完成后，才把 Proposed 候选升级为正式接口。
- 生成新 WO-0017 bitstream 会在首次切换时重配置 PL；必须在 PL/DMA 空闲时安排。板端操作须单独授权。
- 曝光程序只用 `Overlay(bit, download=False)` 读取匹配元数据；它不下载 bitstream。
