# WO-0017 · WO-0015 处理链上的 BTN0/BTN1 相机曝光控制

日期：2026-10-21。状态：**Proposed / candidate implementation**。关联：[ADR-0004](../adr/adr-0004-pynq-camera-exposure-buttons.md)。

## 目标

在当前 WO-0015 PS、DMA 和图像处理 Block Design 上增加 PYNQ-Z2 BTN0/BTN1 输入，让用户在准备采集图像时通过实体按键调整 USB 相机曝光。不得以加载 PYNQ BaseOverlay 替代当前设计。

## 候选行为

- BTN0 每个去抖按下沿将 `exposure_time_absolute` 减少 5 个 V4L2 单位（0.5 ms）；BTN1 增加 5 个单位。
- 长按只触发一次；稳定同时按下两键时忽略；超出相机范围时钳位。
- 只在相机已经处于 Manual Mode 时允许写曝光；启动阶段仅读取，写入后回读确认。
- 曝光工具只管理相机 V4L2 控件和 GPIO 读数；不启动输送带、采样循环、DMA 或计数程序，不结束已有相机预览。

## 硬件增量

候选 Vivado 流程位于 `scripts/wo0017/`。从现有 WO-0015 七个 RTL 源文件和 Block Design 配置重建新输出目录 `data/results/wo0017-hw-50mhz-20261021/`，新增：

- AXI GPIO `exposure_buttons_gpio`：32-bit AXI4-Lite、两个输入、无第二通道、无中断。
- 控制 SmartConnect 从 1×2 扩展为 1×3，新增 M02 接 GPIO；初拟 GPIO 地址 0x41200000 / 64 KiB，必须由 Vivado 地址分配与 HWH 共同确认。
- 顶层输入 `btn_gpio[1:0]` 及 `btn_exposure_buttons.xdc`：bit0/BTN0/D19，bit1/BTN1/D20，LVCMOS33。
- GPIO 与 DMA/图像处理共用 FCLK_CLK0 及现有 peripheral reset。

现有 DMA、HP0/HP1 内存路径、50 MHz、图像 AXI-Lite 地址和 RTL 不变。原 WO-0015 目录和证据保持只读。

## 软件增量

`scripts/camera_setup/pynq_exposure_buttons.py` 要求显式提供 WO-0017 BIT/HWH/manifest 与相机设备身份。它在 `download=False` 模式附着到 HWH，验证 manifest 哈希、bitstream 已加载、GPIO 名称/地址和相机控件后轮询 GPIO。代码不得包含 Overlay 下载或 BaseOverlay 路径。

## 验收/证据

1. 按 `docs/02_interfaces.md` 完成接口 PR、三人确认和 ADR 批准后，方可把候选转为正式设计。
2. Vivado BD validate、关键 IP/地址 readback、约束、HWH、原 DMA/流水线连接逐项通过；通过现有 RTL XSim 门后进行 50 MHz 综合实现和报告审查。
3. 用 manifest 将 BIT/HWH 配对，并保留源码、构建脚本、约束哈希。离线构建不能标记为板测。
4. 单独授权后，在输送带停机且相机仅预览时先读 GPIO，之后每次只试一个 BTN；验证曝光读回变化、范围钳位和预览保持。异常立即停止，不开始采样或重试。

## 当前执行状态

- 软件按键处理与 Vivado 独立候选源已准备。
- Vivado 不在当前环境可用；候选 BD、实现、BIT/HWH 尚未构建。
- 未连接 PYNQ、未写 PL、未改曝光、未影响当前预览。
- 新增接口尚未完成三人评审，因此不能称为正式冻结或板上可用。
