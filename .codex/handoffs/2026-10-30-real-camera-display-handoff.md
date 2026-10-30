# Handoff: 真实相机板端显示与 G3/G4 交接

## Metadata

- Generated At: 2026-10-30T21:15:25+08:00
- Project Root: C:\w\mc-progress
- Git Branch: fix/修复真实相机首帧等待
- Head Commit: 7beb5e9

## Original Goal

按 `文档/计划-剩余进度重排-20261030.md` 推进 material-counter 项目。用户要求先推进代码，不运行测试/构建/仿真，等代码阶段完成后统一验证。当前优先事项是启动板端**真实相机画面与实时计数**供用户本人测试；确定性回放不能替代真实相机。

## Current State

### 代码与工作区

- 工作树：`C:\w\mc-progress`；当前分支 `fix/修复真实相机首帧等待`，从 `feat/记录G3运行源码身份` 的 `ecec89f` 建出。最新代码提交 `18d04b5`；后续候选包和进度记录提交 `7beb5e9`。尚未合并回基线分支；测试延后，暂不合并。
- 根因：集成主循环对第一次相机帧也只等 250ms。此板相机初始化和丢弃前两帧需要数秒，已有历史启动最长约 12 秒；运行器因首帧超时关闭相机并重试。
- 修复：`scripts/standalone/integrated_run.py` 对首次取帧及重连设置 15 秒等待，稳态仍为 250ms。当前源文件 SHA-256 为 `5578c79cef1044ece2e565506821e7b5cf54736520b382c3db27fa4f6f3c805d`，与板端副本一致。

### 板端实时运行（保持开启）

- 板卡：`xilinx@192.168.2.99`，boot ID `e48ff153-bd85-4dd6-98f6-438bd51da08d`；隔离运行包 `/home/xilinx/standalone-g25-20261030`。不要在当前进程运行时启动第二个计数器或抢占相机/HDMI。
- 集成运行器主 PID `795`，绘图子进程 PID `841`；使用真实摄像头 `/dev/v4l/by-id/usb-HSK_USB_Camera_20210616103-video-index0`、`--source camera --display on --continuous`，进程仍在运行，供用户现在测试。
- 用户触发的运行快照 `/tmp/material-counter-user-display-20261030-fixed.snapshot-0.json`：`state=RUNNING`、`processed=1532`、`submitted=1267`、`total_count=0`、相机恢复错误列表为空。`total_count=0` 是尚未放入测试物料时的状态。
- 快照证明相机 Gray8 帧被处理并有显示帧提交；屏幕物理可见性仍由用户现场确认。当前没有人工物料真值和准确率结论。

## Completed This Session

- 板端 V4L2、OpenCV 原始 MJPEG、项目 `camera_frames()` 解码及 `LatestCamera.read()` 单独读取都能提供 640×400 Gray8 帧；集成失败是首帧等待边界过短。
- 提交 `18d04b5` 修复首帧/重连等待，并更新 G4 候选源码副本及 `package-source.json`；提交 `7beb5e9` 更新 G3/G4 进度记录、`PROJECT_INDEX.md` 和本 handoff 的来源说明。
- 已将修复版 `integrated_run.py` 放入板端隔离包并启动真实相机 + HDMI 连续运行。没有执行代码测试、构建或仿真。
- 原有计划里 G0/G1/G1.5/G2/G2.5 已通过；G3 有回放、遥测冻结、协作式绘图退出、源身份等证据，但整体仍未放行。详见 `data/results/standalone-20261030-g3-functional/README.md`。

## Validated

- 类型：用户要求的板端实时操作快照，不是本轮自动化测试或 G3 验收。
- 观测：运行快照为 `processed=1532`、`submitted=1267`、`total_count=0`、`state=RUNNING`，相机恢复错误列表为空；本地和板端运行器文件 SHA-256 一致。
- 本轮没有运行 pytest、构建、仿真或 G4 package index 核验。历史测试/仿真结果仅按现有 G3/G4 记录引用，不表示本轮重跑。

## Not Yet Validated

- 用户尚未确认 HDMI 屏幕上肉眼可见，也未放入物料核对实时计数；`submitted > 0` 是软件提交证据，不能代替人工目视和物料真值。
- 代码修复尚未经过自动化测试；用户要求等代码阶段结束后统一验证。
- 250ms 稳态超时与 15s 首帧等待的修复效果已由运行器开始持续收帧支持，但 G3 的有目标准确率/边界行为仍未验收。

## Remaining Work

- [ ] 用户现在在板端实时 HDMI 画面下放置真实物料，确认图像可见并记录人工真值/计数；保持 PID 795 运行，不要改用回放。
- [ ] 用户测试后保存对应板端快照/最终报告和物料真值，再更新 G3 记录；若画面仍不可见，先保留运行状态和当前进程证据，不要并行操作 HDMI/相机。
- [ ] 代码阶段完成后统一运行用户授权的测试与检查；确认后再按项目 Git 规则合并回原开发基线。不要提前重建或宣称 G4 package index 有效。
- [ ] 完成 G3 缺项：GPIO 消抖/浮空/长按与帧边界竞态、HDMI 拔插/硬件恢复、绘图 `Process.start()` 故障执行、有目标相机及人工真值、缩放黄金图/插值策略收敛、门禁汇总。
- [ ] 完成 G4：离线第三方依赖闭包、候选包完整索引与环境摘要刷新、移除开发机/网络后的部署和冷启动、多次冷启动与有界长时运行；G3 未放行前 G4 不验收。

## Decisions Made

- Decision: 真实相机才满足当前用户测试目标，不能用回放替代。
- Reason: 用户明确要求真实相机画面计数，并准备亲自测试物料。
- Decision: 首帧及重连等待 15 秒，稳态仍等待 250ms。
- Reason: 板端真实摄像头初始化/预热超过 250ms；延长只作用于新相机的第一帧，保留稳态卡帧检测。
- Decision: 保持当前连续运行器开启，交给用户现场测试；不运行自动化测试/构建/仿真，等代码阶段完成后统一验证。
- Reason: 用户授权打开板端显示并表示由其测试；继续并发启动会争用硬件，停止进程会打断用户测试。

## Blockers / Open Questions

- 无代码侧阻塞。当前需要用户现场确认 HDMI 可见性并输入待测物料；若没有连接显示器、画面不可见或计数与人工真值不符，用户反馈后继续定位。
- G4 候选包 `deployment-index.json` 和 `package.env.candidate` 仍是源码刷新前的旧摘要；代码收尾后再统一重建与核对。第三方依赖离线闭包未完成。

## Critical Files

- `AGENTS.md`：项目与协作约束。
- `文档/计划-剩余进度重排-20261030.md`：当前剩余计划与最新进度。
- `PROJECT_INDEX.md`：项目状态导航。
- `scripts/standalone/integrated_run.py`：真实相机、计数与 HDMI 集成运行器。
- `scripts/standalone/camera.py`、`scripts/rle/run_counter.py`：相机线程、MJPEG 到 Gray8 解码。
- `data/results/standalone-20261030-g3-functional/README.md`：G3 已有证据及未放行缺项。
- `data/results/standalone-20261030-g4-offline-package-candidate/README.md` 和 `package/package-source.json`：G4 候选包来源与过期索引说明。
- 板端快照：`/tmp/material-counter-user-display-20261030-fixed.snapshot-0.json`。

## Resume Instructions

先读取本 handoff 和 `AGENTS.md`。板端 PID 795 当前仍应保持运行；恢复工作时先用只读 `ps`/现有快照查看进程状态，不要停止或重复启动。让用户在该真实相机画面下测试，并等待人工计数结果。测试回传后再归档报告、更新 G3 状态；按用户要求，暂不运行自动测试/构建，等代码阶段完成后统一验证。

## Recent Commits

- 7beb5e9 更新真实相机修复与候选包记录
- 18d04b5 修复真实相机首帧等待超时
- ecec89f 刷新G4候选包源码身份
- 68703d1 加入绘图进程启动故障注入
- 05448d9 更新G3与G4当前进度记录

## Modified Files

以下是创建 handoff 前的工作树快照；本次 `M` 文档将与 handoff 一起提交。下列 XSim `.pb`/`.Xil` 与 `runtime-pip-list-full.json` 是先前存在的未跟踪文件，必须保留且不要纳入本次提交。

- M PROJECT_INDEX.md
-  M data/results/standalone-20261030-g3-functional/README.md
-  M "\346\226\207\346\241\243/\350\256\241\345\210\222-\345\211\251\344\275\231\350\277\233\345\272\246\351\207\215\346\216\222-20261030.md"
- ?? data/results/standalone-20261030-123000-g2-integration/full-netlist/xelab.pb
- ?? data/results/standalone-20261030-123000-g2-integration/full-netlist/xvlog.pb
- ?? data/results/standalone-20261030-123000-g2-integration/monitor-sim/xelab.pb
- ?? data/results/standalone-20261030-123000-g2-integration/monitor-sim/xvlog.pb
- ?? data/results/standalone-20261030-123000-g2-integration/rtl-green/xsim/.Xil/
- ?? data/results/standalone-20261030-123000-g2-integration/rtl-green/xsim/xelab.pb
- ?? data/results/standalone-20261030-123000-g2-integration/rtl-green/xsim/xvlog.pb
- ?? data/results/standalone-20261030-123000-g2-integration/rtl-red/xsim/xelab.pb
- ?? data/results/standalone-20261030-123000-g2-integration/rtl-red/xsim/xvlog.pb
- ?? data/results/standalone-20261030-g4-offline-package-candidate/runtime-pip-list-full.json

## Context To Load

- AGENTS.md

## Next Command

```text
Get-Content .codex/handoffs/2026-10-30-real-camera-display-handoff.md -Raw
```
