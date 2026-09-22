# WO-0005 · Encoder / Camera I/O POC

| 项 | 内容 |
|---|---|
| **状态** | `OPEN` —— 第一轮并行开发任务，待负责人执行 |
| **负责人** | 成员 C |
| **前置** | `MASTER_PLAN.md`；`docs/00_scope.md`；`docs/02_interfaces.md` 接口 A/D；`docs/05_risks.md`；`hw/pin_budget.md`；`docs/adr/adr-0001-video-source.md` |
| **性质** | 1～3 天的硬件接口可行性核查；不是完整系统集成 |

## Objective

完成编码器输入和全局快门摄像头候选路线的工程可行性核查，为后续接口冻结提供证据边界。输出必须明确哪些结论是已经由官方资料或本机/板卡实测支持的，哪些仍然是 Planned、Hypothesis 或 Blocked。

本 WO 不实现 RTL、驱动、PYNQ overlay 或摄像头采集链路；它只建立候选、约束、测量方法和下一步验证计划。

## Background

当前正式方案是“振动盘/供料器 + 单条可调速主输送带 + 全局快门摄像头 + PYNQ-Z2”。编码器负责获取主输送带实际速度，未来可形成 PS 可见的 `encoder_speed`；现有接口文档明确该字段尚未实现。摄像头路线仍需基于板卡接口、传感器输出、电平、时钟和工具链证据继续核查，不能把候选路线写成已冻结方案。

`hw/pin_budget.md` 中的编码器部分是规划边界，不等于已分配引脚或已验证电气连接。所有具体 pin、bank、VCCO、输入电压和摄像头路线都必须附来源；没有来源就只能标为 `Hypothesis` 或 `Planned`。

## In Scope

- 编码器候选：单路脉冲、正交 A/B、推挽、开集电极等输出形式的差异和适用条件。
- PYNQ-Z2 可用连接器/PL 输入候选、引脚复用、I/O bank、VCCO 和时钟域注意事项。
- 编码器输出电压与 PYNQ-Z2 输入电压的兼容性；是否需要上拉、电平转换、隔离或保护。
- 固定闸门计数和周期测量两种脉冲测速方法、单位换算、分辨率和失效判断。
- 全局快门摄像头候选接口路线：传感器输出形式、时钟、数据宽度、电平、帧同步信号，以及与现有视频源抽象的关系。
- 官方文档、datasheet、原理图、板卡实测和本机工具检查的证据登记。
- 下一步硬件验证清单和风险排序。

## Out of Scope

- 编码器捕获 RTL、CDC 模块、滤波/去抖逻辑、PYNQ Python 驱动或 `encoder_speed` 实际接口实现。
- 摄像头驱动、MIPI/HDMI/DVP 完整采集、Vivado Block Design、DMA、bitstream 和板上实时视频。
- 在没有实测或官方出处时确定最终编码器型号、最终摄像头型号、最终 pin 或电平转换器件。
- 修改现有接口 B 的字段布局，或把 `encoder_speed` 写成 Implemented。
- 以“板上有连接器”代替电气连通性、时序和工具链可集成性证明。

## Deliverables

1. `hw/encoder_camera_poc.md`：编码器和摄像头候选路线的证据矩阵、结论和未决问题。
2. 一张编码器候选表：输出形式、供电/输出电平、脉冲含义、PPR/CPR 信息、可用接口候选、是否需要电平转换、证据来源。
3. 一张摄像头候选表：全局快门证据、输出接口、数据/时钟信号、电平、PYNQ-Z2 路线、是否需要额外桥接、证据来源。
4. 一段测速方法说明，至少给出固定闸门和周期测量的公式、单位、分辨率以及无脉冲/溢出时的 `invalid` 条件。
5. 一节 `Verified / Planned / Hypothesis / Blocked` 总结，以及下一步需要谁、用什么仪器/工具完成验证。

## Step-by-step Tasks

1. 从当前 `main` 创建建议分支 `feat/encoder-poc`，先阅读 `hw/pin_budget.md`、接口 A/D 和 ADR-0001；不要先写结论。
2. 检查本机是否有 Vivado/板卡/相关 datasheet。记录实际工具版本、板卡是否可用和不能验证的项目；缺工具或缺硬件时不要用推测替代实测。
3. 为编码器至少列出两种输出类别，记录电气输入条件、脉冲/转含义、是否需要上拉、是否可能有 A/B 相位和最大频率信息。
4. 对每个 PYNQ-Z2 接口候选查官方 schematic/board guide：记录 connector、pin、bank、VCCO、复用和信号方向。没有官方出处的 pin 不得标为已选。
5. 根据编码器型号的真实输出电平与 PYNQ-Z2 bank 条件给出兼容性判断。需要电平转换的，记录器件类别和验证条件；没有型号时只写候选方案。
6. 写出测速方法：固定闸门 `f_pulse = count / gate_time`，或周期法 `f_pulse = 1 / period`，再结合每转脉冲数和轮周长换算线速度。所有示例数值如果不是实测，都标为 Hypothesis/example。
7. 梳理全局快门摄像头候选：必须确认 shutter type、输出协议、数据/同步/时钟、电平和是否能映射到现有视频源抽象。USB/file replay 可以作为开发路线，但不能代替最终实时相机路线。
8. 对每条结论添加证据标签：`[Measured]` 必须有命令/仪器/实际输出；`[Documented]` 必须有官方 URL 和章节或 PDF 页码；`[Hypothesis]` 必须写升级为 Verified 所缺的步骤；无法继续的标 `[Blocked]`。
9. 只新增 POC 报告；若认为必须改 `hw/pin_budget.md` 或正式接口文档，先在报告中提出修改建议，不在本 WO 内直接冻结或伪造实现状态。
10. 用 `git diff --check` 和链接/证据清单检查报告，然后提交本分支供团队评审。

## Acceptance Criteria

- [ ] 报告同时覆盖编码器输出形式、PYNQ-Z2 接口/引脚候选、电压兼容性、电平转换需求和脉冲测速方法。
- [ ] 报告同时覆盖至少两条全局快门摄像头候选路线或明确说明当前只有哪些候选，并记录每条路线的输入/输出、时钟和电平约束。
- [ ] 每一个具体“已支持/可连接/电平兼容/已验证”结论都有 `[Measured]` 或 `[Documented]` 证据；没有证据的内容没有被包装成 Implemented。
- [ ] `[Measured]` 结论包含可复现操作和实际观察结果；`[Documented]` 结论包含官方出处；`[Hypothesis]`/`[Blocked]` 写明缺失验证。
- [ ] 测速公式、脉冲数定义、单位和无效样本条件足够让另一名队友复核，但没有声称已经完成测速 RTL 或 `encoder_speed`。
- [ ] 报告明确指出最终摄像头路线和编码器型号是否仍未冻结，并与 `docs/02_interfaces.md`、`hw/pin_budget.md` 的 Planned 状态一致。
- [ ] 本 WO 没有修改或删除现有 synthetic dataset、GT、测试、历史 WO，也没有提交临时工具生成目录。

## Tests

至少运行以下文档级检查：

```text
git diff --check
```

验收者应逐项检查证据 URL/页码、pin 与 bank 是否来自同一版本 schematic、输出电压是否来自具体型号 datasheet，以及报告中的实测声明是否有原始记录。若有板卡和仪器，可增加低频脉冲输入、逻辑电平和接口连通性实测；没有板卡/仪器时应保留为 Planned 或 Blocked。

## Risks

- 编码器“脉冲输出”可能是开集电极、推挽、差分或带 A/B 相位，接线和电平假设错误会损坏 IO 或产生错误计数。
- PYNQ-Z2 的连接器存在不等于对应信号已经被当前设计、bank 电压和约束支持；pin 证据必须逐项核对。
- 摄像头分辨率/协议可用不等于满足全局快门和实时输入要求；必须分开记录传感器属性与板级集成属性。
- 固定闸门法和周期法在低速、高速、丢脉冲和时钟跨域情况下误差不同；本 WO 只做方法边界，不提前选择最终 RTL。
- 网上二手资料、未经核对的 AI 建议和没有页码的截图不能单独升级为 Documented 结论。

## Dependencies

- `hw/pin_budget.md` 当前引脚预算边界。
- `docs/adr/adr-0001-video-source.md` 当前视频路线决策状态。
- `docs/02_interfaces.md` 接口 A 以及 Planned 的接口 D。
- PYNQ-Z2 官方 schematic/board guide、候选编码器 datasheet、候选全局快门摄像头 datasheet。
- 不依赖 WO-0003 的 RTL 或 WO-0004 的 Python 实现。

## Suggested Branch

`feat/encoder-poc`
