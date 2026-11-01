# G2 routed 构建摘要

日期：2026-11-02

分支：**merge/ps-pl-offload-to-lj01-20261101**

构建结果目录：**data/results/standalone-20261101-012910-g2-merge-integration/**

Vivado 流程内部输出目录：**standalone-20261101-012910-g2-integration**（构建脚本校验该固定后缀；成功后将结果目录改为本任务要求的 **g2-merge-integration** 名称）

启动时 Git HEAD：**ddd5174d1304ac901e5fe2ca05247f8c770d89de**

构建结束时 Git HEAD：**ddd5174d1304ac901e5fe2ca05247f8c770d89de**

器件：Zynq-7000 **xc7z020clg400-1**

工具：Vivado 2026.1，jobs=1、maxThreads=1

## 结论

本次新鲜 G2 merge integration 构建完成综合、route、routed timing、DRC、methodology、CDC、exception coverage 检查，并生成 BIT/HWH。route 成功，0 条未布线网络；routed setup/hold 门禁通过。Vivado batch 退出码为 0。

本轮未登录或操作板卡。依据 WO-0015 的验收边界，不部署 BIT，也不标记 ready_for_board。

## 构建与身份

- Vivado batch：2026-11-02 01:32:03 +08:00 开始，02:36:39 +08:00 结束；退出码 0（vivado-exit-code.txt）。
- jobs=1、maxThreads=1，与 16:32 成功构建一致。综合完成 31 个 OOC 子模块 run 和顶层 synth_1；实现完成 route 与 write_bitstream。
- Monitor 仿真退出码 0，结果包含 VIDEO_MONITOR_PASS。
- 构建开始/结束的 20 个源输入 SHA-256 完全相同：HASH_DIFF_COUNT=0。审计见 source-hash-audit.txt、source-hashes-before.sha256 和 source-hashes-after.sha256。
- scripts/standalone/build_g2_integration.tcl SHA-256 为 d03ce9f6b31274c686633c0a69380492d1534372107cd4dcce32447a52ec138d，与 16:32 构建输入清单中的版本一致。
- route 阶段峰值内存 2805.137 MB；整个 Vivado 构建日志记录的最高峰值为 bitgen 阶段 3220.445 MB，构建未发生 OOM。
- 构建输入 SHA-256 包含外部映射 external/PYNQ-base.xdc → P:\pynq-v3.1\boards\Pynq-Z2\base\vivado\constraints\base.xdc。

## Routed 时序

来自 reports-g2-integrated/timing.rpt：

| 指标 | 结果 | 失败端点 |
|---|---:|---:|
| WNS / setup slack | +0.273 ns | 0 / 180,118 |
| TNS | 0 ns | 0 |
| WHS / hold slack | +0.022 ns | 0 / 177,778 |
| THS | 0 ns | 0 |

Vivado 报告 All user specified timing constraints are met.

时钟 setup slack：FCLK0 50 MHz +2.170 ns、FCLK1 100 MHz +1.324 ns、HDMI pixel 100 MHz +0.273 ns、FCLK2 142.857 MHz +0.285 ns。独立 check_timing -verbose 报告退出码为 0，无 no-clock、unconstrained internal endpoint、multiple-clock、generated-clock 或组合环路问题。

## 资源与实现报告

| 资源 | 使用量 | 占比 |
|---|---:|---:|
| Slice LUT | 34,411 / 53,200 | 64.68% |
| Slice Register | 41,249 / 106,400 | 38.77% |
| BRAM Tile | 24.5 / 140 | 17.50% |
| DSP | 13 / 220 | 5.91% |
| Slice | 13,270 / 13,300 | 99.77% |

Slice 接近满载；LUT、寄存器、BRAM 和 DSP 使用率明显较低。详细数据见 reports-g2-integrated/utilization.rpt。

- Routed nets：未布线 0，部分布线 0；Router Completed Successfully。
- DRC：0 Errors、42 Warnings、3 Advisories（45 项检查），见 reports-g2-integrated/drc.rpt。
- Methodology：7 项均为 Warning，无 Critical、无 Error，见 reports-g2-integrated/methodology.rpt。
- write_bitstream 前置 DRC：0 Errors。

## CDC 与 exception 覆盖

- CDC 报告没有 CDC-1 Critical。汇总为 CDC-3 Info 116 项、CDC-6 Warning 2 项、CDC-15 Warning 1,703 项；报告见 reports-g2-integrated/cdc.rpt。
- 按键 PL 输入 false-path 为 4/4 端点、100% 覆盖（exception position 18）。GPIO 同步器路径 position 19 也显示 4/4 端点、100% 覆盖。完整 coverage 见 reports-g2-integrated/exception-coverage.rpt。
- check_timing 报告显示 4 个按键输入没有 input delay，但由 false path 覆盖；4 个 HDMI TMDS 输出端口没有 output delay（HIGH），HPD 输出由 false path 覆盖。HDMI 接收端/板级输出延迟没有建模，因此本报告只证明当前约束下的内部 routed timing。

## 与 16:32 成功构建的差异

| 检查项 | 16:32 构建 | 本次构建 | 对比 |
|---|---:|---:|---|
| jobs / maxThreads | 1 / 1 | 1 / 1 | 相同 |
| route 未布线网络 | 0 | 0 | 相同 |
| WNS / TNS | +0.273 ns / 0 ns | +0.273 ns / 0 ns | 相同 |
| WHS / THS | +0.022 ns / 0 ns | +0.022 ns / 0 ns | 相同 |
| LUT / Register | 34,411 / 41,249 | 34,411 / 41,249 | 相同 |
| BRAM / DSP / Slice | 24.5 / 13 / 13,270 | 24.5 / 13 / 13,270 | 相同 |
| DRC | 0 Errors、42 Warnings、3 Advisories | 0 Errors、42 Warnings、3 Advisories | 相同 |
| Methodology | 7 Warnings、无 Critical | 7 Warnings、无 Critical | 相同 |
| CDC | CDC-3 116、CDC-6 2、CDC-15 1,703 | CDC-3 116、CDC-6 2、CDC-15 1,703 | 相同 |
| 按键 false-path | 4/4 | 4/4 | 相同 |

本次启动 HEAD 为 ddd5174d1304ac901e5fe2ca05247f8c770d89de；16:32 构建摘要记录的 HEAD 为 063e22c09737048728c08da8b2dbfad7ef805cf7。20 个构建输入路径集合相同，其中 6 个文件内容不同：

- fpga/experimental/rle/rle_feature_tap.v
- fpga/experimental/standalone/video_binary_overlay.v
- fpga/experimental/standalone/video_binary_overlay_bd.v
- scripts/standalone/build_display.tcl
- scripts/standalone/connect_offload.tcl
- scripts/standalone/offload_cdc.xdc

其余 14 个输入相同；本次构建期间 20 个输入均无变化。BIT/HWH 字节数相同，SHA-256 因输入版本不同而不同。

## BIT / HWH

- export/g2-integrated.bit：4,045,770 bytes，SHA-256 42b950a0cfeea0f827e7cc70385a326aa6a5a079838d389a23ca53d3fd80ad92。
- export/g2-integrated.hwh：838,443 bytes，SHA-256 552f5ab45f971db9bb1652aaa2f33d2e1b20533b71986ec478bb678668d6005。
- 校验清单见 export-sha256.txt。

## 验收边界与风险

- 本地 routed timing、DRC、methodology、CDC 和 BIT/HWH 生成均已核验；本地构建通过。
- Slice 使用率为 99.77%，后续增加逻辑的物理 Slice 余量很小。
- HDMI TMDS 输出延迟未按接收端和板级数据约束；check_timing 对 4 个 TMDS 输出报告未指定 output delay。
- 本轮不登录、不部署、不重配 PYNQ-Z2；板端功能验收未执行，候选未标记为 ready_for_board。
- 本次构建前的准备阶段有未满足 monitor 仿真前置条件的尝试；完成仿真后已在全新输出目录重新完整构建成功。早期尝试目录保留在 evidence/attempts/，不参与本次门禁结论。
