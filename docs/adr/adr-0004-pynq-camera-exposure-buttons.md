# ADR-0004 · WO-0015 上的板载按键相机曝光控制

状态：**Proposed，待 PR、三人确认和正式批准；不得视为已冻结或已部署。**
日期：2026-10-21。

## 背景

当前 WO-0015 Block Design 没有板载按键输入。PYNQ-Z2 的 BTN0/BTN1 接在 PL 引脚，不能由当前处理流水线的软件直接读取。把 PYNQ BaseOverlay 加载到 PL 会替换整个 WO-0015 设计，无法满足在现有 DMA/图像处理基础上调曝光的需求。

## 候选决策

从现有 WO-0015 工程建立独立 WO-0017 bit/HWH 对，原有 PS、Gray/Binary 图像链、DMA HP0/HP1 数据路径、处理参数与 0x43C20000 图像控制地址保持一致。新增一个 AXI GPIO：

- AXI GPIO 仅两位输入，映射 BTN0=bit 0、BTN1=bit 1。
- GPIO 接入原控制 SmartConnect 的新增 M02 目标，建议地址 0x41200000、64 KiB；生成与 HWH 必须确认无地址重叠。
- 顶层按钮端口 `btn_gpio[1:0]`：BTN0=D19、BTN1=D20，LVCMOS33。PYNQ-Z2 按键按下产生逻辑高。
- PS 工具读取相机当前 Manual Mode；BTN0/BTN1 分别以 0.5 ms 步进减少/增加 V4L2 exposure。按键只在去抖后的上升沿触发；相机值按硬件范围钳位并回读。
- 曝光工具使用 `Overlay(wo17.bit, download=False)` 获取 HWH 元数据，检查 `is_loaded()`、bit/HWH SHA-256、GPIO 名称和地址；它不得调用下载/重载 bitstream。

原 WO-0015 脚本、产物、manifest 和历史验收记录保留，不在原输出目录上重建。USB 摄像头预览仍由 PS 侧 V4L2 进程运行，曝光工具不重启预览。

## 影响与边界

第一次切换到新 WO-0017 bitstream 会重配置 PL，期间现有 PL/DMA 工作会暂停。应在相机采集和 DMA 空闲时部署；该切换不要求替换为 BaseOverlay。应用启动后，按键工具只附着到已经加载的 WO-0017，不负责部署或恢复 bitstream。

新增 AXI GPIO、AXI-Lite 目标、按钮顶层端口和约束均属于硬件接口增量，正式生效前仍须遵循 `docs/02_interfaces.md` 的 PR、三人确认及 ADR 程序。当前候选不改变 Binary8/S2MM 数据合同，不批准任何板端操作。

## 验收条件

1. Vivado BD 验证和 HWH/参数 readback 确认原 DMA、HP0/HP1、时钟、复位、地址与图像链保持原值；新增 GPIO 的宽度、方向、地址、端口映射准确。
2. 综合/实现完成，50 MHz 时序通过，DRC 与 methodology 无 Error/Critical Warning；输出配对 BIT/HWH 和哈希 manifest。
3. 先在不采样的板端验证 BTN GPIO 原始位，再用受控按键验证 +/− 0.5 ms、边界钳位、手动模式门控、写后回读和现有相机预览持续运行。
4. 板端验证属于单独授权阶段，不得从本 ADR Proposed 状态推导执行许可。

## 批准记录

| 审批项 | 状态 |
|---|---|
| PR | 待建立 |
| 三人确认 | 待完成 |
| 批准人/日期 | 未记录 |
| Vivado 实现 | 未运行 |
| PYNQ 板端 | 未运行 |
