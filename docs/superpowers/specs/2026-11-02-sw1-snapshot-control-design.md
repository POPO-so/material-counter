# SW1 快照保存控制设计

## 意图

让操作员用 PYNQ-Z2 的 SW1 控制诊断快照落盘。仅当 SW1 为 ON、BTN1 已进入诊断模式且 `--crossing-snapshots on` 时记录快照；SW1 OFF 时停止新增采集并结清当前上下文。

## 目标与边界

- G2 PL 的 `btns_gpio` Channel 1 扩为六位：BTN0–BTN3 占 bit0–3，SW0 占 bit4，SW1 占 bit5。
- SW 输入在 PL 内经两级同步后进入 GPIO；按钮仍走原有消抖链。
- PS 按 HWH 声明的 GPIO 宽度配置 PYNQ Channel 1。旧四位 HWH 继续运行，但报告 SW1 不可用并关闭快照保存。
- 快照只在现有计数线事件触发时保存；不新增连续录像或检测区域触发规则。
- 不改变 BTN0–BTN3 语义、计数算法、诊断模式语义、部署流程或已加载 PL。

## 方案比较与选择

1. **将两只拨码开关并入现有 Channel 1**：把 BTN0–BTN3 保留在低四位，SW0/SW1 分别接到 bit4/bit5。软件仍从同一个按钮通道读值，BTN1 的低位协议不变；这与既有 `read() & 0x20` 控制契约一致。**采用此方案。**
2. **使用 AXI GPIO Channel 2**：可将开关与按键分开，但会改变 PYNQ 软件接口，且不能兑现已有的 bit5 方案。
3. **直接把拨码开关接入 GPIO**：连线少，但跨入 50 MHz 域时没有明确同步器；采用两级同步器以限制 CDC 边界。

## 接口和行为

PYNQ-Z2 的 SW0/SW1 分别使用 M20/M19，拨到上方输出逻辑高。顶层 `sws_2bits_tri_i[0]` 对应 SW0，`[1]` 对应 SW1。PL 将按钮消抖后的四位与两位同步开关拼成六位 GPIO 输入，故 `SW1 == (channel1.read() & 0x20) != 0`。

PS 每个处理帧在现有 video driver owner 内读取 SW1。快照 writer 仅在 CLI、诊断模式和 SW1 三项同时允许时创建/采集。有效采集变为无效时调用 `reset_context`，保存已触发事件并清掉前后文；再次启用时只累积新上下文。报告和预览状态会区分 SW1 未接入、OFF、ON/采集与读取未知。

## 验收边界

- Tcl 构建门禁确认 HWH GPIO 宽度为 6、switch 输入端存在、同步器两级端点及 bit4/bit5 拼接约定。
- 软件不把四位旧 HWH 的高位当作 SW1；新 HWH 将 PYNQ Channel 1 设为六位。
- 本任务不下载/部署新 bitstream，不改动板端运行状态。硬件构建依赖缺失时如实记录，留给可用 Vivado/PYNQ 源环境完成。
