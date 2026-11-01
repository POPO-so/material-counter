# SW1 快照开关 G2 本地构建

## 结论

Vivado 2026.1 完成 `OFFLOAD_CANDIDATE=1` 的 G2 综合、实现、路由和 BIT 生成。最终路由 setup WNS 为 **+0.052 ns**、hold WHS 为 **+0.023 ns**，TNS/THS 均为 0，且所有用户时序约束满足。BIT 与 HWH 已导出，GPIO 宽度和 SW1 bit 映射核对通过。

这是尚未部署的候选制品。用户已明确暂缓板端验收；本次没有登录或操作板卡，也没有重载 PL。

## 制品与构建身份

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| [`export/g2-integrated.bit`](export/g2-integrated.bit) | 4,045,770 | `3216faed178485a48a3e1cbe7919bbd63fab54ab69f8821d02217f09dfe3a7b5` |
| [`export/g2-integrated.hwh`](export/g2-integrated.hwh) | 848,136 | `e7b5cf32dc7a119c46cbf60c87185f55531dec0ba7d10d202b82290625b7d0ed` |
| [`export/build-manifest.json`](export/build-manifest.json) | — | — |

- 源码提交：`a4fa257`（含 SW1 功能与 G2 构建门禁改动）。
- 工具：Vivado 2026.1，build 6511674；器件 `xc7z020clg400-1`。
- PYNQ 源码：v3.1 锁定版本 `19ed17d4fd4ea71f95a97d30f8ccd0d62b8dbaaa`，32/32 文件哈希核验通过。
- 视频 IP：Vitis 2026.1 导出的 `color_convert`、`pixel_unpack`，前置 CSim、csynth 与 IP export 已通过。
- 实现策略：单 implementation job、单线程；Explore placement/route 与 AggressiveExplore physical optimization。
- 集成 HDL 输入快照保存在 [`rtl/`](rtl/)，每个文件的 SHA-256 记录在构建清单中。

## 核验结果

- HWH 声明 `C_GPIO_WIDTH=6`，并包含 `sws_2bits_tri_i` 外部端口。
- BD 读回：BTN0–BTN3 为 bit0–3，SW0=bit4、SW1=bit5；开关管脚 SW0=M20、SW1=M19。
- 原始 BD 读回结果已保存在 [`gpio-readback.tcldict`](gpio-readback.tcldict)。
- 顶层综合/实现门禁确认 6 路 GPIO 同步输入和 2 个开关输入端口。
- 路由 0 unrouted / partially routed nets；最终 `timing.rpt` 显示 0 setup/hold failing endpoints。
- `drc.rpt` 无 Error；Vivado 运行汇总为 0 Critical Warnings。报告保留 DRC Warning/Advisory，详见 [`reports-g2-integrated/drc.rpt`](reports-g2-integrated/drc.rpt) 和 [`reports-g2-integrated/methodology.rpt`](reports-g2-integrated/methodology.rpt)。
- SW1 软件门控专项测试：17 项通过（结果记录见 [PLAN.md](../../../PLAN.md)）。
- CDC 报告包含 CDC-6 2 条、CDC-15 1,703 条 Warning。已完成人工分类与端点审查：按钮位为独立控制信号；VTC 告警属于文档化的 v_tc 6.2 内部 CDC；CDC-15 属于握手保护且有 datapath-only 最大延迟约束的 bundled-data 路径。原始告警保留，未创建 Vivado waiver。逐类依据与复位前提见 [`cdc-review.md`](cdc-review.md)；原始报告见 [`reports-g2-integrated/cdc.rpt`](reports-g2-integrated/cdc.rpt)。

## 尚未完成

- 板端 PL 重载和 SW1 实物开关验证按用户要求暂缓。当前 BIT/HWH 不应视为板端验收通过。

## 报告

实现与完整报告位于 [`reports-g2-integrated/`](reports-g2-integrated/)：`timing.rpt`、`utilization.rpt`、`drc.rpt`、`methodology.rpt`、`cdc.rpt`、`bus-skew.rpt`、`exception-coverage.rpt`。输入开关异步路径约束见 [`offload-switch-cdc.xdc`](offload-switch-cdc.xdc)。
