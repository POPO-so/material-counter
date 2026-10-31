# 2026-10-31 板端性能优化：CPU 证据与 v8 候选包

## 部署状态

v8 已通过完整性核验并由 Manager 完成高风险切换：`/opt/material-counter/current` 指向 `/opt/material-counter/releases/standalone-20261031-v8-perf-e2fb595`，service 为 `active/running`、PID `18172`，unit 自启动仍为 `disabled`，没有下载 overlay。源码提交 `e2fb5952bfc4f36c3eacd53a8bdb6bdeec6c4fe2`、分支 `perf/board-performance-20261031`；43 个模块均逐字节匹配 Git blob，静态非源码文件与 v7 包一致。

- 新 deployment index SHA-256：`e127d991371f5077bd270b2ed037508d2e76c5dfdd9c9573940fdfb2a4d1707e`。
- 旧 v7 信任基线 index SHA-256：`ca9348788e98efe187752a29dcbe3ddc0970efa8ff85a1f8e43bbee9142ae58a`。
- 暂存完整性报告：[package-integrity.json](package-integrity.json)，新包 `verify_package` 为 `PASS`、`files_checked=62`；旧包基线 `PASS`、`files_checked=61`。
- 模块 Git blob 与包内 bytes 对照：[v8-source-module-correspondence.json](v8-source-module-correspondence.json)。
- 激活记录：`/var/lib/material-counter/f1462f32-61d2-44c7-9b42-d46d7d4d64c6/v8-perf-e2fb595-activation.json`；旧 env 备份：`/var/lib/material-counter/f1462f32-61d2-44c7-9b42-d46d7d4d64c6/pre-perf-e2fb595.env`。
- [v8-candidate-summary.json](v8-candidate-summary.json) 是激活前的暂存摘要；运行态以本段和 Manager/Worker1 的后续报告为准。

## v8 运行态快照（2026-10-31）

服务运行态快照和启动、admission、activation 原始记录见 [snapshot](v8-camera-running-snapshot.json)、[startup](v8-camera-running-startup.json)、[admission](v8-camera-running-admission.json)、[activation](v8-camera-running-activation.json)；窗口汇总见 [running-summary.json](running-summary.json)。快照为一次 SIGUSR1 导出，PID `18172`，状态 `RUNNING`，processed `16461`、submitted `15428`、duration `909.837 s`、total_count `0`、invalid `0`、recoveries `[]`，counting profile `field-v1`。启动记录 main_pid 与 PID 一致；admission index 为 `e127d991371f5077bd270b2ed037508d2e76c5dfdd9c9573940fdfb2a4d1707e`。激活记录保存的是启动前状态；当前 active/running、current=>正式 v8 root 由 Manager 核验。

实际加载的 32 个 Python 模块均能在固定 index 的 43 模块声明中找到，SHA-256 逐项一致。compact 快照诊断 observed `16461`、triggered/saved/dropped/errors 均为 `0`。固定边界且仅纳入 fresh telemetry：processed≥100 共446条，处理 FPS 中位数 `18.320`（11.712–22.214）、显示 `17.416`（含5条0）；processed≥300 共441条，处理 `18.317`（11.712–19.958）、显示 `17.426`（9.728–19.499）。后半窗口包含所有 fresh 样本，不按 display_fps 筛选；末三样本处理/显示依次为 `15.00/14.50`、`13.52/11.59`、`12.65/9.73` FPS，保留低帧率变化，不把快照中的下降隐藏。运行核验未隔离诊断、SSH等开销，不作严格性能 A/B；低窗口原因未单独定量确认。

本轮 v8 的独立 600 帧结果是空场景（runs 全零），v7 同配置现场 600 帧结果 total_count=4；现场输入不同，因此两次 FPS 不构成严格同输入因果比较。物料真值准确率和密集持续 FPS 尚未验收。

## 600 帧实机对照

新版 v8 600 帧原始报告经 Manager 审阅：正常 `STOPPED`、`processed=600`、`invalid_frames=0`、`recoveries=[]`，camera/buffers 已释放，DMA 状态 `0x1002`、VDMA 状态 `0x11001`。接收 `1,324,800 = 600×2,208` bytes，600 帧均为 0 runs，属于严格空场景。`frames >= 300` 窗口处理/显示 FPS 中位数分别为 `18.36594/17.28186`。

旧 v7 同窗口中位数为 `12.90276/12.51154`，但该轮 `total_count=4` 且 RLE runs 有 `0/12/34/75`，属于混合现场工况，不是严格同空输入。两轮负载不同，不据此声称精确因果增幅。旧版原始报告：[v7-camera-600.json](v7-camera-600.json)；新版原始报告：[v8-camera-600.json](v8-camera-600.json)。

## 现场快照 CPU 回放

[原始 v7 CPU 回放](v7-cpu.json)、[原始 v8 CPU 回放](v8-cpu.json)和[逐帧配对 JSON](cpu-replay-comparison.json)涵盖 3 组事件、15 个配对帧、每组 5 次。所有组内 `semantic_sha256` 一致，native SO SHA-256 一致。墙钟、region、track 保存逐帧 5 次数据与中位数，不用组 p50 代替最慢帧。帧 9089 的 wall 为 `116.465→74.661 ms`、region `113.358→71.522 ms`、track `2.090→2.076 ms`；它是 v7 对应组的 wall/region 最慢帧。v8 该组 wall/track 最慢为帧 9088，region 最慢仍为 9089。

[v7 纯 CPU 微基准](v7-microbench.json)、[v8 纯 CPU 微基准](v8-microbench.json)和[对照 JSON](cpu-microbench-comparison.json)由两个独立进程串行运行，每项 5 次预热 + 30 次测量；输入 SHA、native SO SHA 一致，snapshot writer 均为 0 事件、0 保存、0 错误并干净关闭。

| 操作 | v7 p50 ms | v8 p50 ms | v7 p95 ms | v8 p95 ms |
|---|---:|---:|---:|---:|
| 640×400 Gray8 打包 | 6.235 | 1.831 | 6.287 | 1.925 |
| 空 RLE 解码 | 4.002 | 2.177 | 4.313 | 2.236 |
| 16,000 runs RLE 解码 | 46.834 | 45.069 | 53.072 | 45.211 |
| 现场帧 RLE 解码 | 5.911 | 5.799 | 5.966 | 5.901 |
| 空帧 `CrossingSnapshots.observe` | 6.353 | 1.741 | 6.505 | 1.844 |

CPU 临时 v8 副本中 `scripts/rle/protocol.py` 与 `scripts/standalone/crossing_snapshots.py` 的原始 SHA 与 Git blob 不同；已用现有 Git 源包逐字节核对，CRLF 转 LF 后两文件均与固定提交完全相同。CRLF 数分别为 376、425；详情见 [v8-cpu-source-normalization.json](v8-cpu-source-normalization.json)。因此 CPU 结果针对的是换行不同的临时副本，正式候选则由 Git blob 原始 bytes 组装并逐模块验证。

这些 CPU 回放不是完整视频、整机 FPS 或物料准确率证据。profile 含插桩、导入和等待，不能与未插桩包的整机 FPS 直接比较。v7 的 `total_count` 从 snapshot 144 到 final 50 有明确 `frame 18086 commands=['clear']` 记录，非计数回退；最终累计 50、processed 18433/1662.30 s、invalid 0，安全停止。Manager 提供的提交回归结果为 488 passed，本轮未重复运行整套回归。

可审查的本地暂存脚本：[stage_v8_candidate.py](stage_v8_candidate.py)；Manager 已执行指针与环境切换，并随后启动 service，执行记录见 [activation](v8-camera-running-activation.json)。一次性激活脚本 [activate_v8.py](activate_v8.py) 本身不启动 service、不下载 overlay。实际板端暂存命令记录：[stage-v8-candidate.sh](stage-v8-candidate.sh)。
