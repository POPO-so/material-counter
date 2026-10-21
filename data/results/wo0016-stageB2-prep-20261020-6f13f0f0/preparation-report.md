# Stage B-2 开机重核验与预备报告

**结果：软件门禁 PASS，等待操作员物理就绪。Stage B-2 尚未启动：0/165 帧。** 当前 Boot ID 为 `6f13f0f0-4433-445f-a023-fa403685aeee`。本轮没有开灯、启带或投料；没有启动 Stage B-2 采样。

| 门禁项 | 当前证据 | 结果 |
|---|---|---|
| 本次启动身份 | `/proc/sys/kernel/random/boot_id` = `6f13f0f0-4433-445f-a023-fa403685aeee` | PASS |
| 初始化 oneshot | `ov9281-init.service` 本次 boot 日志成功；`Result=success`、`ExecMainStatus=0`、inactive/dead（oneshot 正常结束） | PASS |
| Stage 1 回执 | `OVERLAY_LOAD_PASS`，绑定当前 Boot ID；manifest SHA-256 `c5cb8ebd7bf50e8025d46d798ecf9aedb9cd043df5e16b34de294762dfb3b551` | PASS |
| FPGA Manager / 镜像身份 | `operating / flags=0`；Stage 1 回执和 PYNQ 全局态指向 `board-runtime-currentboot-v2/scripts/wo0015/wo15.bit`；BIT SHA 与基线一致。Checker/Packer 签名 `0x574F3134 / 0x574F3135` | PASS |
| BIT/HWH 持久软链接 | phase110 旧执行目录中的 `wo15.bit`、`wo15.hwh` 现直接指向 `/home/xilinx/wo0015/wo15.{bit,hwh}`，不再经过 `/tmp`；两者分别为基线 SHA-256 `a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420`、`bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6` | PASS |
| HWH 阈值 | 当前镜像配对 HWH 的 `THRESHOLD` 参数为 `128`；B-2 配置/READY Guard 也固定为 `128` | PASS |
| CMA 根因诊断 | CMA 曾从 `73,336` 降至 `21,416 KiB`。诊断时没有 `/dev/video*`、`/dev/mem`、`/dev/uio*`、DMA heap 持有句柄；8080 无监听；`Cached=319,040 KiB`，CMA 可用页计数偏低。未发现残留 HUD/相机句柄。 | PASS（已诊断并恢复） |
| CMA 受控恢复 | 无设备句柄时执行 `sync && echo 3 > /proc/sys/vm/drop_caches`，CmaFree 恢复到 `116,872 KiB`。Stage 1 后 `106,976 KiB`；只读导入 WO-0016/CV 模块并验证 Guard 后读数为 `68,268 KiB`，未打开相机或创建 attempt marker；再次受控清缓存恢复到 `116,972 KiB`。 | PASS |
| HUD 驻留 CMA | HUD 启动/HTTP 检查时读数约 `76,876–78,636 KiB`；后续 30 秒 6 次样本为 `66,592`、`66,592`、`66,280`、`66,228`、`66,228`、`66,104 KiB`，全部高于 `32,768 KiB` 门槛。之后复核 `66,304 KiB`。 | PASS |
| V4L2 | HUD 运行时读回 MJPG 640×400；`auto_exposure=1 (Manual Mode)`、曝光 `50`、防闪烁 `1`、Gamma `100` | PASS |
| 当前 Boot Stage B-2 Guard | `/var/lib/wo0016/stageb2-ready-6f13f0f0-4433-445f-a023-fa403685aeee.json`，root:root `0600`，父目录 `0700`；SHA-256 `f15464025abeabc2e52681aaa000caf25da217cdf5505781c3d7fcba45cd5b9b`；绑定 manifest/BIT/HWH、5 颗 M6 无垫片螺母、3 秒、165 帧、threshold 128，`guard_status=READY, consumed=false` | PASS |
| Guard 不消费入口 | `run_stream_counter.py --no-consume-guard` 只读校验 READY Guard；实际执行时独立 O_EXCL 单次 attempt marker `stageb2-attempt-<boot>-consumed.json` 用于 fail-closed 防重跑。当前 attempt marker 不存在，READY Guard 未变更。 | PASS（执行入口已部署，未调用） |
| Stage B-2 执行链路 | 同进程 PYNQ 3.1.1；直接 `pynq.MMIO`；同一 Stage B 进程构造 `Overlay(bit_path, download=False)`，不调用 `.download()`；不依赖 `PYNQ_BACKEND`。每帧检查 MM2S/S2MM DMASR 恰为 `0x00001002`；安全 idle 后释放缓冲，DMA 归属不确定时 fail-hold 保留缓冲。以上为代码/离线验证，未执行采样。 | PASS（准备完成） |
| 8080 HUD | `wo0016-stageb2-hud-6f13f0f0.service` active，`0.0.0.0:8080` 正在监听；`/` 返回 HTTP 200 HTML，`/stream.mjpg` 返回 200 multipart 且收到完整 JPEG；视频句柄由该 HUD PID 独占。 | PASS |
| Stage B-2 采样 / 物理环境 | 用户声明环境静默；本轮无灯光、传送带、投料操作。Stage B-2 仍为 `NOT RUN`，0/165 帧；当前 Boot attempt marker 不存在。 | 等待用户物理就绪 |

## CMA 诊断边界

根因没有证据指向残留设备句柄或未退出 HUD。大页缓存与 CMA 可用量偏低同时出现；释放缓存后 CmaFree 大幅恢复。导入 WO-0016/CV 运行库但未打开相机时，CmaFree 又从 `106,976` 降到 `68,268 KiB`，说明运行库/文件缓存与可移动页分布会明显影响该指标；这是一项测得的关联，不能单独证明某个 Python 分配就是唯一原因。HUD 驻留的 30 秒观察范围为 `66,104–66,592 KiB`，最低读数仍比门槛高 `33,336 KiB`；随后复核为 `66,304 KiB`。

执行记录：Stage 1 的首个 shell 包装器被 `boardname.sh` 的未定义 `BOARD` 和非登录 profile 初始化错误挡在 Python 进程之前；没有加载 Overlay、写 Guard 或产生回执。修正为显式 `XILINX_XRT=/usr` 后，Stage 1 仅成功执行一次并 PASS。READY Guard 初始化器也曾因预期签名常量不符而在写入前拒绝；校对现存 Stage 1 回执后，仅成功创建了一份当前 Boot READY Guard。

HUD 与 Stage B-2 共用 `/dev/video0`。收到物理就绪指令后，采样前先停止 `wo0016-stageb2-hud-6f13f0f0.service`，等待 unit inactive，确认 8080 监听退出、`/dev/video*` 不再有持有者，再复核 Boot ID、FPGA Manager、CmaFree 和 V4L2 控件；随后只调用一次固定 B-2 命令。现有 HUD 处理 SIGTERM 并在 `finally` 中关闭 HTTP 服务、释放摄像头；此前同 Boot 的停止记录显示服务正常退出且端口/设备句柄释放。采样结束仅在安全退出且 CMA 许可时恢复 HUD。

旧启动的两条 Stage 1 记录归档于 `/var/lib/wo0015-s2mm/history/boot-f8b24aee-ed7e-430a-b3f3-d26056746bfd/`；五条旧 WO-0016 consumed Guard 归档于 `/var/lib/wo0016/history/boot-58e6af6c-6660-4d44-b0c5-e18f4a564202/`。归档保留原始字节并校验 SHA-256，再移除原运行路径标记。未改写旧回执。

## 本地实现与验证

本次 Stage B-2 no-consume 变更位于隔离 worktree `C:\Users\POP\.codex\worktrees\wo0016-stageb2-no-consume\material-counter`：`scripts/wo0016/run_stream_counter.py` 和对应测试。固定配置约束 165 帧、目标计数 5、`M6_nut_no_washer`、3 秒稳姿；每帧记录两路 DMASR。验证结果：WO-0016 `74 passed`；WO-0015 preflight/board executor `56 passed`；`git diff --check` 通过。板端 runner SHA-256 为 `938d429b61b1c25657ddf4abb855da16561938f57b12704c3a2674da3da83748`，与本地打包版本一致。

关键证据文件：`stage1-currentboot.json`、`image-symlink-repair.json`、`cma-diagnostic.json`、`hud-http-probe-result.json`、`stageb2-locked-config.json`。Stage 1 回执与 Manifest 绑定当前 Boot ID；所有物理采样仍需操作员明确物理就绪后才能开始。
