# Handoff: g2-rle-hdmi-integration

## Metadata

- Generated At: 2026-10-30T05:01:47+08:00
- Project Root: C:\w\mc-g2
- Git Branch: feat/集成RLE与HDMI位流
- Head Commit: 19a85d2

## Original Goal

按 [计划.md](../../计划.md) 推进脱机计数与灰度 HDMI 显示：G1、G1.5 独立显示门禁已完成后，继续把 RLE 计数接入 HDMI、板载按钮与 monitor 的整机设计，并逐阶段完成集成验证。当前阶段目标是完成 G2；不得把独立显示或 RLE 单项回归写成整机通过。

## Current State

当前在隔离 worktree `C:\w\mc-g2`、分支 `feat/集成RLE与HDMI位流`，基于 `19a85d2 合入G1.5独立显示验收`。G1.5 证据与状态已合入原分支 `LJ-01`。G2 目前只完成独立 RLE RTL 回归及小参数 OOC 综合后的功能回归；尚未创建或验证 RLE+HDMI+buttons+monitor 集成 block design，也没有 G2 综合实现或板测结论。当前代码改动仅为进度/证据文档；本次 G2 基线与交接记录在此分支提交，尚未合回 `LJ-01`。

原始工作区 `C:\Users\14173\Documents\material-counter` 有用户已有的 `.gitignore` 修改和未跟踪证据，另有早前运行产生的 `data/results/standalone-20261030-g2-baseline/` 原始目录。它们已保留；新会话只在 `C:\w\mc-g2` 继续，不清理或覆盖原始工作区内容。

## Completed This Session

- G1.5 已通过独立 HDMI、三槽普通 RAM 共享帧 IPC 和绘图子进程门禁；`e6ac2f6 记录G1.5板端验收通过` 已合入 `LJ-01`，合并提交为 `19a85d2`。G1.5 未接 RLE 计数。
- 创建 G2 隔离 worktree/分支；将 RLE 基线回归日志、综合网表、输入场景清单和工具启动记录归档至 `data/results/standalone-20261030-g2-baseline/`。
- 更新 `计划.md`、`PROJECT_INDEX.md`、`README.md`，明确 G2 仅基线通过、集成门禁未完成；修正基线报告中 `axis_dma_gray_adapter.v` 的目录路径。
- 生成本交接文件，供新会话从当前分支继续。

## Validated

- Command: 使用 Vivado 2026.1 batch/XSim 执行 `scripts/rle/run_sim.tcl` 指向的完整 RTL 场景回归；原始输出在 `data/results/standalone-20261030-g2-baseline/rtl-xsim/`。
  Result: `RLE_ENCODER_PASS frames=4`、8 个 `RLE_PIPELINE_640X400_PASS` 和 `RLE_XSIM_ALL_PASS cases=8`；包含 baseline、ROI、erode、dilate、empty、overflow、bad_checksum、bad_header。
- Command: 使用 `scripts/rle/run_netlist_sim.tcl` 对 `axis_rle_encoder`（WIDTH=5、HEIGHT=3、MAX_RUNS=2）执行 OOC 综合与综合网表仿真。
  Result: `RLE_ENCODER_PASS frames=4`、`RLE_POST_SYNTHESIS_PASS`；该结果只验证小参数模块功能，不覆盖集成时序/实现。
- Evidence check: 检查上述两份归档控制台日志中的终点标记；核对基线报告列出的 9 个源文件 SHA-256，均与当前 G2 worktree 中对应文件一致。对本次编写的 Markdown 执行暂存 whitespace 检查；完整暂存检查会报告 Vivado 原始日志与综合网表自带的行尾空白，因此保留原始输出不作改写。
- Synthesis log: 0 errors、0 critical warnings、9 warnings；包含 `m_tkeep` 常量位及 `Netlist 29-101` floorplanning 提示。该 OOC 结果不代表集成 DRC 或实现通过。

## Not Yet Validated

- Assumption: 现有 RLE DMA/HP0+HP1 拓扑与独立显示/HP2 结构可组合为单一候选设计，具体时钟、复位、地址映射、互连和 PS 资源仍须根据现有 Tcl 与器件约束审查后决定；显示接口仍是 ADR-0005 的候选状态。
- Risk: G1.5 的 CPU0、CPU1 各约 96% P50；即使 G2 位流能构建，HDMI 持续扫描与有目标/高游程 RLE 并发也可能造成共享 DDR 争用、反压或 FPS/延迟退化，因此 G2.5 必须单独实测。
- 集成 block design、综合/实现、时序、CDC、DRC、新制品一致性、板端 RLE+HDMI 行为、按钮和 monitor 联动均未验证。G2.5、G3、G4 尚未开始。

## Remaining Work

- [ ] 阅读 `scripts/gray8/create_bd.tcl`、`scripts/standalone/create_display.tcl`、显示实现说明与 ADR-0005；由 Manager 先确定 G2 集成候选的时钟/复位、PS FCLK、HP0/HP1/HP2、AXI 地址与生成制品边界。保留 G1 独立显示入口，不提前冻结候选 Route 或接口。
- [ ] 先建立可复核的 G2 设计/构建检查，再新增独立的集成构建入口并完成 Vivado block design、综合、实现、时序、CDC、DRC 与 BIT/HWH 一致性检查；按检查结果修正设计。
- [ ] G2 验证通过后再执行 G2.5 板端并发压力测试（720p60 HDMI 持续扫描 + 有目标/高游程 RLE），记录 DDR 竞争、反压、错误率及 RLE 延迟分位数；随后按计划推进 G3/G4。

## Decisions Made

- Decision: G1/G1.5 独立显示结果与 RLE 基线结果分别记账；当前不得宣称 G2 或整机通过。
  Reason: 目前没有把计数接入 HDMI 的集成证据，G2 的实现、时序/CDC/DRC、制品和板测门禁均未完成。
- Decision: 保留 G1 显示构建入口，集成拓扑先作为候选；ADR-0005、Route 和接口状态不得仅凭设计预期改写为正式冻结。
  Reason: 集成方案仍有架构与资源取舍需要检查和实测。
- Decision: G2.5 必须包含 HDMI 扫描与有目标/高游程 RLE 的并发压力测量。
  Reason: G1.5 CPU0/CPU1 P50 均约 96%，单独验证不足以代表并发资源余量。

## Blockers / Open Questions

没有外部阻塞。下一会话的首项工作是 Manager 主导的集成架构审查；检查现有 Tcl、规格与候选 ADR 后再确定具体连线和构建入口。Vivado MCP 本次启动返回 `child exited: exit status 1`，但同一 Vivado 2026.1 的 batch/XSim 回归成功；后续若 MCP 再失败，记录环境错误并按计划核对 batch 路径，不要把工具启动错误当作设计失败。

## Critical Files

- `AGENTS.md`、`PROJECT_INDEX.md`
- `计划.md`（G2 当前进度置顶）；`PLAN.md`（项目实现状态与历史上下文）
- `data/results/standalone-20261030-g2-baseline/README.md` 与其中 `rtl-xsim/`、`post-synth/` 日志
- `scripts/gray8/create_bd.tcl`（现有 RLE DMA / pipeline 构建）
- `scripts/standalone/create_display.tcl`、`scripts/standalone/build_display.tcl`（独立显示候选构建）
- `docs/superpowers/specs/2026-10-29-standalone-display.md`、`docs/adr/adr-0005-standalone-gray-display.md`
- 本文件：`.codex/handoffs/2026-10-30-050147-g2-rle-hdmi-integration.md`

## Recent Commits

- 19a85d2 合入G1.5独立显示验收
- e6ac2f6 记录G1.5板端验收通过
- ed2657d 加快G1.5灰度缩放并保留AREA默认
- 4dc4b38 降低同步采样阻塞频率
- 82cad28 优化合成线程并记录处理帧率

## Modified Files

- `PROJECT_INDEX.md`
- `README.md`
- `计划.md`
- `data/results/standalone-20261030-g2-baseline/`（README、仿真与综合后日志、综合网表）
- `.codex/handoffs/2026-10-30-050147-g2-rle-hdmi-integration.md`

## Context To Load

- `AGENTS.md`
- `PROJECT_INDEX.md`
- `计划.md` 顶部 G2 状态及对应 G1.5/G2 报告
- `PLAN.md` 中脱机显示实施相关章节（按需）
- `data/results/standalone-20261030-g2-baseline/README.md`
- 本交接文件

## Next Command

```text
Get-Content -LiteralPath 'PROJECT_INDEX.md' -TotalCount 45; Get-Content -LiteralPath '计划.md' -TotalCount 40; Get-Content -LiteralPath 'data/results/standalone-20261030-g2-baseline/README.md'; Get-Content -LiteralPath 'scripts/gray8/create_bd.tcl' -TotalCount 320; Get-Content -LiteralPath 'scripts/standalone/create_display.tcl' -TotalCount 300
```
