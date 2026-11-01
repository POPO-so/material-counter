# G2 routed 构建摘要

日期：2026-11-01

分支：`feat/优化PS与PL分工`

构建目录：`data/results/standalone-20261101-163250-g2-integration/`

启动时 Git HEAD：`063e22c09737048728c08da8b2dbfad7ef805cf7`

器件：Zynq-7000 `xc7z020clg400-1`

工具：Vivado 2026.1，jobs=1、maxThreads=1

## 结论

本次新鲜 G2 构建完成综合、布线和 BIT/HWH 生成，内部 routed setup/hold 门禁通过。它修复了此前 `standalone-20261101-151222-g2-integration` 的负 setup 时序门禁。此结论只覆盖当前约束下的设计时钟路径；HDMI TMDS 外部输出延迟尚未按接收端和板级数据约束，因此不代表完整外部 HDMI IO 时序收敛。没有操作板卡；候选未标记为 `ready_for_board`。

## 构建与身份

- Vivado batch 退出码：`0`（见 `vivado-exit-code.txt`）。BD 验证、31 个 OOC 综合运行、顶层综合、route 和 `write_bitstream` 均完成；route 为 0 条未布线网络。
- `synth_1`：0 Critical Warnings、0 Errors；203 Warnings。按钮首级同步器数量断言为 4/4。
- 构建开始/结束的 20 个源输入 SHA-256 完全相同：`HASH_DIFF_COUNT=0`。10 份 RTL 快照均与当前对应源文件匹配。审计见 `source-hash-audit.txt`、`rtl-snapshot-audit.txt`。
- `export/g2-integrated.bit`：4,045,770 bytes，SHA-256 `4fcca329d763b42d8dc667ff3a9d86c9c82a414395c505247d369296184ab847`。
- `export/g2-integrated.hwh`：838,443 bytes，SHA-256 `610a79f773c1bf605705424a5f864dfccc26e29bfce27078a6d5082eab8ff253`。

## Routed 时序

来自 `reports-g2-integrated/timing.rpt`：

| 指标 | 结果 | 端点 |
|---|---:|---:|
| WNS / setup slack | +0.273 ns | 0 / 180,118 setup 端点失败 |
| TNS | 0 ns | — |
| WHS / hold slack | +0.022 ns | 0 / 177,778 hold 端点失败 |
| THS | 0 ns | — |

时钟 setup slack：FCLK0 50 MHz +2.170 ns、FCLK1 100 MHz +1.324 ns、HDMI pixel 100 MHz +0.273 ns、FCLK2 142.857 MHz +0.285 ns。Vivado 报告 `All user specified timing constraints are met.`

## 资源与实现报告

| 资源 | 使用量 | 占比 |
|---|---:|---:|
| Slice LUT | 34,411 / 53,200 | 64.68% |
| Slice Register | 41,249 / 106,400 | 38.77% |
| BRAM Tile | 24.5 / 140 | 17.50% |
| DSP | 13 / 220 | 5.91% |
| Slice | 13,270 / 13,300 | 99.77% |

Slice 已接近满载，虽然 LUT、寄存器、BRAM 和 DSP 比例较低，后续增加 LUT 逻辑仍可能受物理 Slice 封装容量限制。详细 utilization 报告见 `reports-g2-integrated/utilization.rpt`。

DRC：0 Errors、42 Warnings、3 Advisories。Methodology 检查 7 项均为 Warning：LUTAR-1 4 项、TIMING-9 1 项、ULMTCS-1 1 项、XDCB-5 1 项；无 Critical。对应报告为 `drc.rpt`、`methodology.rpt`。

## CDC 与 exception 覆盖

- 按键输入的 4 条路径均被识别为 CDC-3 Info、两级同步，未出现按键 CDC-1 Critical。独立 routed netlist audit 确认 4 个 BTN 首级 D pin，并确认 PL 按键 false-path 覆盖 4/4（100%）。
- 消抖后的 `stable_state_reg[3:0]` 到 AXI GPIO 同步器在 CDC 报告中为 CDC-6 Warning，false-path 覆盖为 4/4；该多位同步警告保留待后续 CDC 决策。
- CDC 汇总另有 CDC-15 Warning 1,703 项，属于已有 frame monitor mailbox 的 clock-enable CDC 结构；报告中没有 CDC-1 Critical。
- 旧 exception position 16（外部按钮到 GPIO 首级同步器）覆盖率为 0%，因为该直接路径已不再连接，当前不切除任何路径。当前 GPIO 路径由 position 19 覆盖；PL 按键首级路径由 position 18 覆盖。详见 `evidence/button-cdc-audit.txt` 与 `evidence/button-exception-coverage-return-string.rpt`。

## 验收边界

- routed 内部 timing gate：通过。
- routed 资源、DRC、methodology、CDC 和 bus-skew 报告：已生成并保留在本目录。
- HDMI 外部输出 delay：未建模；需取得显示接收端/板级时序依据后再评估完整 IO timing closure。
- 板端按键中断、HDMI 画面/帧配对、曝光操作和 CPU A/B：未执行。
- BIT/HWH 是本地候选产物；WO-0015 禁止本轮板卡操作，因此没有部署，也没有标记 `ready_for_board`。
