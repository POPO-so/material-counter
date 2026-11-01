# PYNQ-Z2 SW0 接入候选记录

日期：2026-11-02

状态：候选实现，未冻结正式接口，未部署到板端。

## 候选定义

- 复用现有 `btns_gpio` AXI 地址 `0x41200000`，GPIO 宽度由 4 扩展到 5。
- bit0..3 保持 BTN0..BTN3；bit4 为板载 SW0。软件仍只把 bit0..3 传给按键解码器。
- SW0 由顶层端口 `sw0` 输入，在 PL 的 FCLK0 域经过两级 ASYNC_REG 同步器，再送入 GPIO bit4。
- PYNQ-Z2 SW0 候选引脚为 M20、LVCMOS33，依据本机 PYNQ v3.1 源码 `boards/Pynq-Z2/base/vivado/constraints/base.xdc`；当前显示约束只筛选 HDMI 与 BTN 引脚，不占用 M20。
- 软件仅在 HWH 声明 `C_GPIO_WIDTH >= 5` 且读取有效时启用 SW0。SW0=ON + BTN2 提高曝光；SW0=OFF + BTN2 降低曝光；状态未知时忽略该曝光命令。

## 验收范围

- 仅本机候选设计检查与后续 BIT/HWH 构建；不写入板卡、不重载 PL。
- 正式接口冻结仍需项目规定的 PR、三人确认与 ADR；本记录不替代该流程。
- 当前系统可用物理内存不足以启动完整 Vivado 综合/布线，故 BIT/HWH 生成及 routed DRC/时序/CDC 报告待资源充足后执行。