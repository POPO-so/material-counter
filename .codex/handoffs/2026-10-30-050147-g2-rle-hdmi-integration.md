# Handoff: g2-rle-hdmi-integration

## Metadata

- Generated At: 2026-10-30T05:01:47+08:00
- Project Root: C:\w\mc-g2
- Git Branch: feat/集成RLE与HDMI位流
- Review Base Commit: 8987964

## Original Goal

按 [计划.md](../../计划.md) 推进脱机计数与灰度 HDMI 显示：G1、G1.5 独立显示门禁已完成后，继续把 RLE 计数接入 HDMI、板载按钮与 monitor 的整机设计，并逐阶段完成集成验证。当前阶段目标是完成 G2；不得把独立显示或 RLE 单项回归写成整机通过。

## Current State

当前在隔离 worktree `C:\w\mc-g2`、分支 `feat/集成RLE与HDMI位流`，基于 `19a85d2 合入G1.5独立显示验收`。G1.5 证据与状态已合入原分支 `LJ-01`。G2 目前只完成独立 RLE RTL 回归及小参数 OOC 综合后的功能回归；尚未创建或验证 RLE+HDMI+buttons+monitor 集成 block design，也没有 G2 综合实现或板测结论。本次已完成 G2 集成架构审查并形成待实例化候选；当前代码改动仅为进度/证据文档；本次 G2 基线与交接记录在此分支提交，尚未合回 `LJ-01`。

原始工作区 `C:\Users\14173\Documents\material-counter` 有用户已有的 `.gitignore` 修改和未跟踪证据，另有早前运行产生的 `data/results/standalone-20261030-g2-baseline/` 原始目录。它们已保留；新会话只在 `C:\w\mc-g2` 继续，不清理或覆盖原始工作区内容。

## Completed This Session

- G1.5 已通过独立 HDMI、三槽普通 RAM 共享帧 IPC 和绘图子进程门禁；`e6ac2f6 记录G1.5板端验收通过` 已合入 `LJ-01`，合并提交为 `19a85d2`。G1.5 未接 RLE 计数。
- 创建 G2 隔离 worktree/分支；将 RLE 基线回归日志、综合网表、输入场景清单和工具启动记录归档至 `data/results/standalone-20261030-g2-baseline/`。
- 更新 `计划.md`、`PROJECT_INDEX.md`、`README.md`，明确 G2 仅基线通过、集成门禁未完成；修正基线报告中 `axis_dma_gray_adapter.v` 的目录路径。
- 生成本交接文件，供新会话从当前分支继续。

- Manager 已完成 G2 集成架构审查：确认单 PS7、11-MI 控制互连候选、FCLK/复位分域、HP0/HP1/HP2 路径、非重叠 AXI-Lite 地址及压缩流与 HDMI 像素流隔离；证据与未验证项见下文。

## Validated

- MCP recovery: started a persistent Vivado 2026.1 Tcl/webserver session (PID 61604, `http://localhost:8088`), connected with `vivado_connect`, then `vivado_execute` returned `2026.1` for `version -short` in session `vivado-20261030-053811-6443592e`. The current control path works; this is an externally managed session.
- Architecture evidence: `scripts/standalone/create_display.tcl` defines the nine existing control targets and FCLK0/1/2, while `scripts/gray8/create_bd.tcl` defines the two RLE AXI-Lite segments and HP0/HP1 data paths. Vivado 2026.1 local AXI Interconnect metadata allows NUM_MI 1–64. AMD [PG059 Clock Conversion](https://docs.amd.com/r/en-US/pg059-axi-interconnect/Clock-Conversion) documents per-SI/MI clocks and automatic clock converters. No integrated BD, CDC, DRC or implementation check was run.
- Command: 使用 Vivado 2026.1 batch/XSim 执行 `scripts/rle/run_sim.tcl` 指向的完整 RTL 场景回归；原始输出在 `data/results/standalone-20261030-g2-baseline/rtl-xsim/`。
  Result: `RLE_ENCODER_PASS frames=4`、8 个 `RLE_PIPELINE_640X400_PASS` 和 `RLE_XSIM_ALL_PASS cases=8`；包含 baseline、ROI、erode、dilate、empty、overflow、bad_checksum、bad_header。
- Command: 使用 `scripts/rle/run_netlist_sim.tcl` 对 `axis_rle_encoder`（WIDTH=5、HEIGHT=3、MAX_RUNS=2）执行 OOC 综合与综合网表仿真。
  Result: `RLE_ENCODER_PASS frames=4`、`RLE_POST_SYNTHESIS_PASS`；该结果只验证小参数模块功能，不覆盖集成时序/实现。
- Evidence check: 检查上述两份归档控制台日志中的终点标记；核对基线报告列出的 9 个源文件 SHA-256，均与当前 G2 worktree 中对应文件一致。对本次编写的 Markdown 执行暂存 whitespace 检查；完整暂存检查会报告 Vivado 原始日志与综合网表自带的行尾空白，因此保留原始输出不作改写。
- Synthesis log: 0 errors、0 critical warnings、9 warnings；包含 `m_tkeep` 常量位及 `Netlist 29-101` floorplanning 提示。该 OOC 结果不代表集成 DRC 或实现通过。

## Not Yet Validated

- Architecture candidate (reviewed, not yet instantiated): 复用单一 PS7 与显示 control AXI Interconnect；NUM_MI 从 9 扩为 11 并保留 M00–M08，追加 M09→axi_dma_0/S_AXI_LITE（0x40400000）、M10→image_pipeline/s_axi（0x43C20000）。控制 ACLK/S00_ACLK 留在 FCLK1 100 MHz；新增 MI、RLE DMA、pipeline 和 RLE memory path 使用 FCLK0 50 MHz。新增 MI 使用 FCLK0 及 50 MHz 域复位，等待 Vivado 生成后确认 clock converter 配置、约束和 DRC。
- Risk: G1.5 的 CPU0、CPU1 各约 96% P50；即使 G2 位流能构建，HDMI 持续扫描与有目标/高游程 RLE 并发也可能造成共享 DDR 争用、反压或 FPS/延迟退化，因此 G2.5 必须单独实测。
- 集成 block design、综合/实现、时序、CDC、DRC、新制品一致性、板端 RLE+HDMI 行为、按钮和 monitor 联动均未验证。G2.5、G3、G4 尚未开始。

## Remaining Work

- [x] Manager 已审查两份 Tcl、显示实现说明和 ADR-0005，形成本文件 Decisions Made 中的集成候选；G1 独立显示入口与 Route/视频接口候选状态均保留。
- [ ] 先建立可复核的 G2 设计/构建检查，再新增独立的集成构建入口并完成 Vivado block design、综合、实现、时序、CDC、DRC 与 BIT/HWH 一致性检查；按检查结果修正设计。
- [ ] G2 验证通过后再执行 G2.5 板端并发压力测试（720p60 HDMI 持续扫描 + 有目标/高游程 RLE），记录 DDR 竞争、反压、错误率及 RLE 延迟分位数；随后按计划推进 G3/G4。

## Decisions Made

- Candidate decision: reuse the single display PS7 and expand the existing GP0 AXI Interconnect from 9 to 11 MIs, preserving M00–M08 and appending M09 for DMA control and M10 for pipeline control. The AXI Interconnect IP supports up to 64 MIs; PG059 supports per-interface ACLKs with automatic conversion when an endpoint clock differs from the crossbar clock.
  Reason: this keeps existing display addressing and wiring stable, avoids a second PS/GP0 master, and directly supports the 100 MHz GP0 to 50 MHz RLE controls.
- Candidate decision: keep RLE control/data on FCLK0 50 MHz, display control on FCLK1 100 MHz, and display stream/HP2 on FCLK2 142.857 MHz. Use a 50 MHz proc_sys_reset output for the new RLE endpoints and verify generated clock-converter and reset behavior in the integrated BD.
  Reason: these frequencies, widths and domains are already declared in the two source Tcl flows; PG059 handles per-interface clocks, but final timing/CDC evidence is still pending.
- Candidate decision: retain AXI-Lite maps 0x40400000 for DMA and 0x43C20000 for the RLE pipeline; preserve display maps including 0x43000000, 0x43C10000, 0x43C30000, 0x43C40000, 0x43C60000 and 0x43C70000. Keep RLE M_AXIS connected to DMA S2MM/HP1 DDR and keep HDMI VDMA on HP2; do not connect the compressed RLE stream to the HDMI pixel stream.
  Reason: the ranges do not overlap in the current Tcl maps, and the two streams carry different formats and functions.
- Candidate decision: preserve the current Gray8 DMA interface with IRQ outputs left unconnected in this G2 candidate; keep the existing PYNQ channel-wait behavior. If future runtime requires DMA IRQs through the shared interrupt controller, add explicit 50-to-100 MHz CDC first.
  Reason: the current Gray8 BD does not route DMA IRQs, and no integrated interrupt-driven requirement has been established. The optional cooperative adapter polls status; a direct 50 MHz IRQ connection to the 100 MHz interrupt controller would cross clock domains.

- Decision: G1/G1.5 独立显示结果与 RLE 基线结果分别记账；当前不得宣称 G2 或整机通过。
  Reason: 目前没有把计数接入 HDMI 的集成证据，G2 的实现、时序/CDC/DRC、制品和板测门禁均未完成。
- Decision: 保留 G1 显示构建入口，集成拓扑先作为候选；ADR-0005、Route 和接口状态不得仅凭设计预期改写为正式冻结。
  Reason: 集成方案仍有架构与资源取舍需要检查和实测。
- Decision: G2.5 必须包含 HDMI 扫描与有目标/高游程 RLE 的并发压力测量。
  Reason: G1.5 CPU0/CPU1 P50 均约 96%，单独验证不足以代表并发资源余量。

## Blockers / Open Questions

架构审查已完成，当前无阻止继续实现的设计问题。MCP 的自动 `vivado_start` 路径仍会在握手后以 `child exited: exit status 1` 退出；这次通过 AMD 文档支持的手动 Vivado Tcl/webserver 启动与 `vivado_connect` 恢复了可用控制通道，且 `version -short` 已验证为 2026.1。自动启动根因仍未修复，不能把手动连接写成自动启动已恢复；当前 Vivado 会话可供后续 Tcl 工作复用。下一步建立独立的集成 BD/build 入口，再完成综合、实现、时序、CDC、DRC、制品校验和板测。

## Critical Files

- `AGENTS.md`、`PROJECT_INDEX.md`
- `计划.md`（G2 当前进度置顶）；`PLAN.md`（项目实现状态与历史上下文）
- `data/results/standalone-20261030-g2-baseline/README.md` 与其中 `rtl-xsim/`、`post-synth/` 日志
- `scripts/gray8/create_bd.tcl`（现有 RLE DMA / pipeline 构建）
- `scripts/standalone/create_display.tcl`、`scripts/standalone/build_display.tcl`（独立显示候选构建）
- `docs/superpowers/specs/2026-10-29-standalone-display.md`、`docs/adr/adr-0005-standalone-gray-display.md`
- 本文件：`.codex/handoffs/2026-10-30-050147-g2-rle-hdmi-integration.md`

## Recent Commits

- 8987964 记录G2基线进度与新会话交接
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
确认当前外部连接的 Vivado MCP 会话仍可用；按本交接的 Candidate decision 创建独立 G2 集成 BD/build 入口，保留 scripts/standalone/create_display.tcl 的 G1 入口与 M00–M08 映射。
```

## 2026-10-30 Board Smoke Follow-up

This update supersedes the earlier `Remaining Work` / `Next Command` status above; those sections describe the pre-build handoff state.

- G2 integration build, implementation, static timing/CDC/DRC, BIT/HWH/manifest validation, and full-size RLE wrapper post-synthesis 8/8 regression are complete. The integration decision remains a branch-local candidate; Route, video input, and formal interfaces are not frozen.
- User authorized safe recovery/retry. On fresh recheck, prior Fail-Hold PID 28352 was already absent, so no termination signal was sent. G1 BIT was still loaded before the smoke; the isolated missing `scripts.rle` closure was staged and passed a no-hardware import check.
- On the same boot ID `e48ff153-bd85-4dd6-98f6-438bd51da08d` and G2 BIT SHA-256 `2ec1f1dec5a2df2e07044e94ecacd5d20aeef19ec6787077c0728d24053a8b18`, the display runner completed 61.296 s camera output / 589 submissions / 32 clean monitor snapshots and stopped normally. It used the legacy display-only runner and G2 display manifest; it is not a combined G2 runner.
- A strict RLE manifest adapter was made from byte-identical G2 BIT/HWH and the build RTL snapshot. The first same-hash/different-path preflight was safely rejected before frames. Narrow opt-in SHA equivalence support was fixed on `fix/允许同哈希位流路径`, tested (25 passing), and merged into `feat/集成RLE与HDMI位流` (`2d64e9b`). With G2 already loaded, RLE then completed 12/12 pixel-verified moving synthetic frames, zero invalid frames, and released buffers.
- Board `Clocks` readback is FCLK0/1/2 = 50/100/142.857143 MHz; HP1 and HP2 32-bit synchronization checks passed. HP0 has static HWH evidence and a working RLE MM2S path but no standalone AFI0 width readback yet. VDMA is halted; RLE DMA is idle/error-free with its run/stop bit still set; PYNQ shutdown IPs are empty; the `/proc` device-owner FD audit immediately before the optional pipeline probe was empty, and no runner remained after the probe process exited.
- An additional post-run MMIO read of RLE pipeline page `0x43C20000` caused Linux external abort; root cause is unresolved. The failed read did not produce a full snapshot; no further access or register write was attempted. The concise record is `data/results/standalone-20261030-064700-g2-integration/board/g2-post-smoke-readback.json`; reports and byte-identical adapter are under the same `board/` directory.

### Next Actions

1. Diagnose the post-run external abort from HWH/address decode, clock/reset and Linux mapping evidence before attempting any further access to `0x43C20000`.
2. After resolving it, obtain a clean read-only RLE pipeline signature/geometry/status snapshot and a direct HP0/AFI0 board width readback, alongside the already captured clocks/HP1/HP2/owner/CMA evidence.
3. Keep G2 unaccepted until those board gates and coordinated stop evidence are closed. G2.5 remains a separate concurrent HDMI 720p60 plus target/high-run RLE DDR/backpressure/latency test; it has not started.
