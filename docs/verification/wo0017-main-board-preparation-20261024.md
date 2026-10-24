# WO-0017 latest-main board-validation preparation

Use executing-plans for this preparation workflow.

**Goal:** Prepare a source-pinned, isolated PYNQ-Z2 split-validation candidate without contacting the board.

**Architecture:** Export runtime and matching tests from GitHub main, independently of the local integration checkout. Apply only the reviewed missing-splitter-hash fix to the exported finite runner. Keep capture authorization and all board-state checks outside this offline preparation.

**Tech Stack:** Python 3, Git/Git LFS, NumPy/OpenCV, pytest, existing WO-0015/0017 admission and calibration-lock protocols.

## Execution checklist

- [x] Compare main `59eea586` with deployment source `ee55267e`; distinguish absent live-monitor modules and changed counter options.
- [x] Test package provenance, the narrow runtime-hash repair, tamper detection and exclusive output creation.
- [x] Export the candidate and run its own main-source tests in a separate interpreter.
- [x] Replay hash-verified camera Gray8 windows with the exported main algorithm; retain per-frame geometry, Track IDs and crossings, plus single-object controls.
- [x] Write future upload, fresh admission, independent-GT and 300-frame capture instructions; mark all current hardware fields NOT_VERIFIED.
- Fixed-tooling-commit checks and the append-only benchmark record are authoritative in `docs/benchmarks/optimization_ledger.md`; no old test result is reassigned to the new package.

The later sections record results and executable operator steps after the checklist is completed. No SSH, camera, Overlay, MMIO, DMA, calibration lock or Permit action is authorized by this document.

## 结论与版本边界

准备于 2026-10-24 开始，最终固定提交复核在 2026-10-25 完成，时区为 Asia/Shanghai；文件名保留任务开始日。远端 `POPO-so/material-counter` 的最新 `main` 已通过 fetch 和远端 ref 核对，为 `59eea586d9cfd8289ed423e33d4ede4c00ffa20a`。旧部署源码为 `ee55267e288ccf05781545544391213a40dfe491`。本地工具编写分支是 `codex/wo0017-main-board-prep-20261024`，从上一轮本地集成提交继续；打包器从指定 main Git 对象导出源码，不复制工作区中的监控或计数器实现。

候选包采用 **main + 一处拆分器运行时哈希绑定修补**。修补只把 `split_touching.py` 加入 `formal_runtime_sha256()`，没有修改 main 的分割、跟踪、阈值或相机处理。包内 `runtime-binding.patch`、`source-audit.json`、`PACKAGE.json` 记录该差异及每个源文件的上游/实际 SHA-256。它不是未经修改的 main，也不是旧 live-monitor 的增量覆盖包。

| 项目 | 当前结论 |
|---|---|
| main 导出源码测试 | OFFLINE_PASS：288 passed、3 skipped；三个跳过为 Windows 无 POSIX 权限/所有者语义 |
| 打包、哈希、篡改、阈值边界及独占上传保护测试 | OFFLINE_PASS：11 passed |
| 两颗/三颗/再录三颗的计数窗口 | OFFLINE_PASS：2/2、3/3、3/3 |
| 两组独立单颗合成序列 | OFFLINE_PASS：各 41 帧、各计数 1、0 次误拆；不是实物单颗录像 |
| 子掩码连通性诊断 | FAIL：两段三颗录像分别有 9 帧、3 帧出现非连通子块 |
| 启动分配异常显式回收诊断 | FAIL：两条主机替身故障路径均未显式释放相应缓冲区 |
| 当前 PYNQ 环境、CMA、DMA、采样、部署运行 | NOT_VERIFIED；本轮板端连接和硬件动作均为 0 |

因此本包可供离线审查和之后单独授权的隔离上传，**尚不能启动实板采样**。需先修复/审查启动回收路径，处理子块连通性问题，并对修订源码重新打包、验证。不能通过降低 CMA 水位、混用旧监控或重试旧许可来跨过这些问题。

## 一、源码差异审查

以下文件行号均指明所列 Git 版本；不能把当前工作区文件行号当作 main 的行号。

| 文件/链路 | 旧部署 ee55267e → main 59eea586 |
|---|---|
| `split_touching.py` | 从保守双块窄颈扩展为 2–3 个封闭孔中心的像素分配；颈部比例 0.55→0.65。孔洞和回退分支都没有保证每个最终子块只有一个连通片。main:8–47、66–104 |
| `stream_counter.py` | 旧版默认关闭拆分、候选最小 280、子块最小 140；main 在 `guarded_merge=True` 且面积 `95 < area <= max_split_area` 时自动尝试拆分，传入 `min_child_area=min_area`，默认 12；接受 `2 <= len(pieces) <= 3`。main:428–444 |
| 子块重建 | 子块面积来自前景像素数，质心来自像素均值，bbox 来自 min/max 并加回 ROI 偏移。像素几何保留，但非连通像素集合仍可能被作为一个 Detection。main:431–444 |
| 同源防重合并 | `_split_origins` 保留原 CCL 标签；同源子块不能重新聚类为一个目标。标签不是 Track ID。main:383、443、479–498 |
| 轨迹与越线 | 核心关联、确认、迟滞区和 crossing 逻辑未改变；2/3 块进入同一套跟踪器。不同启动/轮次 Track ID 会重新编号，必须附带 round_id。main:635–636、745–763 |
| `run_stream_counter.py` | full-width profile 的拆分上限 900，邻近碎片合并上限仍 260；main 不接受 `--enable-touching-split`/`--min-split-area`，不能照抄旧 monitor 参数。main:304–329 |
| `run_live_monitor.py` | main 不含该文件，也不含 `live_monitor_state.py`、`dashboard_state.py`、`dashboard.html`。旧 monitor 来自 87788922/ee55267e 开发线；直接拼接会遇到 publisher、连续相机和 `stop_requested` 接口不兼容 |
| 预处理 | 两者 full-width 均 letterbox：640×400→320×200，置于 320×240 的 y=[20,220)，零填充上下边界。main 删除旧 `active_rows` 扫描优化；物理比例仍 NOT_MEASURED，摄像头高度不变也不能补造比例标定 |
| 阈值 | main 的光学软件 `MonochromePreprocessor` 使用 OpenCV `THRESH_BINARY`，即 `Gray8 > 128`。本轮主回放匹配这个软件语义；不能由此推断实际 PL 的等值边界行为 |
| 运行时源码绑定 | main:884–893 的哈希清单遗漏实际导入的 `split_touching.py`。候选只修此绑定；修改后所有旧源码锁身份均失效，必须重新准备 |

主线源码测试首次在导出目录运行得到 286 passed、2 failed、3 skipped：缺少历史回归夹具。随后从同一 main Git 对象导入四个原始夹具，保持其字节与来源，得到 288 passed、3 skipped；没有降低或删除断言。夹具位于包内 `data/results/`，仅供测试，不是 Manifest、Stage 1、READY 或 Permit。所有失败日志保留在本机审计目录。

## 二、原始录像回归及逐帧记录

原文件通过 Git LFS 获取；每个 NPZ 为 1,800 帧、138,277,036 字节。只回放既有诊断窗口，避免把后续未标注手部遮挡等场景计入既定真值。

| 录像目录 | NPZ SHA-256 | 窗口 | 实际/预期 | 软件越线 Track ID@frame |
|---|---|---|---|---|
| `wo_touching_pair_motion_20261024_03` | `0b55c6e1d5a43a4c69702dd89ef14be09c1673cdbc7b8d22b7bf5b8fbe40ce9e` | 0–900 | 2/2 | T1@834、T2@834 |
| `wo_touching_three_motion_20261024_02` | `7edb8fcc077a0592b957f485ae6d5b99989bdf61b2d0c17cff7d71afaa2ad9fa` | 0–99 | 3/3 | T3@34、T4@37、T5@37 |
| `wo_touching_three_motion_20261024_04` | `c534ad4334afa725337a03d5ed849e5ae81857b5c8f5bf1c1686b465884ed59d` | 0–79 | 3/3 | T5@25、T6@28、T7@28 |

这是 **CAMERA_GRAY8_SOFTWARE_THRESHOLD_GT128_NOT_FPGA_BINARY8**。共 1,081 帧，每帧保存原始 CCL 数、拆分后/聚类后数量、每个 Detection 的面积/质心/bbox、轨迹快照、crossing event、原录像时间戳及主机计数耗时。上述 Track ID 仅为算法输出，尚未由独立观察者映射到每颗实体。

连通性失败帧：首段三颗为 5、6、19、20、23、25、27、53、54；再录三颗为 21、23、30。计数一致与连通性诊断分列，`summary.json` 总体返回 FAIL，不把几何问题隐藏在总数中。另一次早期 `>=128` 历史口径回放仍为 2/3/3，但几何失败帧为 7/3；它不能替代本轮与 main 光学路径匹配的 `>128` 结果。

两组单颗圆形/六角形空心合成序列各 41 帧、计数各 1、误拆各 0。真实单颗、真实重复通过和独立实体准确率仍 NOT_VERIFIED。主机耗时只标注 host counter mean/median/p95；板端 FPS、DMA 状态和 CMA 均为空，禁止据此填写板测数据。

本机产物根目录：`C:/Users/POP/Documents/Codex/repo-audits/wo0017-main-board-prep-20261024/`。最终使用 `final-bundle/`、`final-replay/`、`final-main-source.xml`、`final-tools.xml`、`final-startup-rollback.json`、`fixed-receipt.json`。逐帧 JSON 及 SHA-256 清单在 `final-replay/summary.json`。这些大日志/包不随 Git 同步；本报告、工具及账本随 Git 保存。

## 三、CMA 分配与释放审查

`TX_BYTES=230432`、`RX_BYTES=76800`，两个 TX 加两个 RX 为 **614464 B = 600.0625 KiB**。若逐缓冲区按 4 KiB 页取整，示例总计 608 KiB；这不是实际分配器粒度测量，也不能保证 CMA 连续性。32768 KiB 是分配后的保留水位，不能把“当前稍高于 32768”当作一定能分完四块。

有限采集路径先检查 CMA，在分配前及每次分配前后再次检查。物理标定初始准入要求严格 `CmaFree > 32768`，部分后续通用检查为 `<32768` 时拒绝。后续验收维持更严格的标定要求；不能调整数值，也不能在失败后自动再次启动。

主机替身故障注入直接提取 main 对应函数，未导入 PYNQ，未分配真实 CMA：

1. 第二次 allocator 调用故意失败。此前第一块只有 `freebuffer()`，共享 helper 的 `.close()` 回滚没有释放它，显式释放调用数 0。
2. 第一次分配前注入 40000 KiB、分配后注入 10000 KiB。callback 在把新缓冲区交回登记前抛错，未调用 `api.free()`，显式释放调用数 0。

这是**异常回收契约失败**，不是当前板卡泄漏的证明；真实 PYNQ buffer API、GC 和当前全局 CMA 尚未检查。必须先补齐显式回收及逐次失败注入测试，再评审修改，不能靠 GC 或重启成功一次就宣告问题已解决。本轮只定位并封存该阻塞，不改写 DMA/释放逻辑。

采样开始后的规则不同：若 DMA 可能仍活动、状态读取失败、有错误位或通道不空闲，必须 FAIL_HOLD 并保留缓冲区和进程引用。不能执行 free/reset/reload/retry。正常退出必须先停止新帧、排空在途 DMA、确认两通道无错误且 idle，才可显式释放。

历史报告曾记录旧 Boot `53ba...` 的 CMA 为 17964/4352/15776 KiB；另一次 Boot `cf2ea...` 正常停机后记录 69864 KiB。这些记录不是本轮遥测。用户报告的 Boot `cf2ea1a2-f00e-4769-82bb-4f3e2a9e4d76` 只作为待核实预期，不能据此复用旧许可。见 `live-monitor-deployment-blocked-20261024.md` 与 `live-monitor-board-20261024/SESSION1-CLOSEOUT.md`。

## 四、部署包与安全上传步骤

计划板端目录为 `/home/xilinx/wo0017/split-validation-20261024-59eea586`。**本轮未连接板卡，也未在板端创建它。** 旧 `/home/xilinx/wo0017/live-monitor-20261024-ee55267e`、历史 Manifest、Stage 1、Guard、Permit 和证据保持原样。

本机可立即执行的哈希检查和上传预览：

```powershell
$audit = 'C:\Users\POP\Documents\Codex\repo-audits\wo0017-main-board-prep-20261024'
$r = Get-Content -Raw "$audit\final-bundle\build-receipt.json" | ConvertFrom-Json
.venv\Scripts\python.exe scripts/wo0016/verify_split_package.py --root $r.payload --manifest-sha256 $r.manifest_sha256
$sshTarget = Read-Host '填写已确认的 SSH user@host（此步仅预览）'
.venv\Scripts\python.exe scripts/wo0016/stage_split_validation.py --archive $r.archive --archive-sha256 $r.archive_sha256 --manifest-sha256 $r.manifest_sha256 --host $sshTarget --directory $r.planned_deployment_directory
```

只有另行明确授权上传后，才在最后命令加 `--authorize-upload-only`。上传器使用已知主机密钥和 BatchMode，先本地校验 ZIP/Manifest 哈希；板端创建新目录和每个文件均独占写入，拒绝覆盖、目录穿越和符号链接。只执行源码校验与语法编译，不运行 counter、不导入 PYNQ、不创建许可。失败保留部分目录并停止；不能重用该目录盲重试。

构建器可用新本机目录重新生成相同候选；不要复用已有输出目录：

```powershell
.venv\Scripts\python.exe scripts/wo0016/prepare_split_board_validation.py --source-ref 59eea586d9cfd8289ed423e33d4ede4c00ffa20a --output C:/Users/POP/Documents/Codex/repo-audits/wo0017-main-board-prep-20261024/rebuild-unique-id
```

## 五、授权后的只读准入与采样模板

**下列板端命令本轮均未执行。** 源码的 CMA 回收与子块连通性阻塞解决后，必须重新生成包与哈希；再获得明确的板端准入/采样授权。当前包不能用于下面的启动步骤。

### 5.1 当前状态重新核验

只读核验也需在操作者授权的后续会话执行。保存原始输出、主机接收时间、板端 Boot 及各文件哈希，不凭板端墙钟判断证据新鲜度：

```bash
cat /proc/sys/kernel/random/boot_id
grep -E '^(CmaTotal|CmaFree|MemAvailable):' /proc/meminfo
fuser /dev/video0
ss -ltnp | grep -E ':(8080|8081|8082)\b' || true
v4l2-ctl -d /dev/video0 --get-fmt-video --get-parm --get-ctrl=auto_exposure,exposure_time_absolute,brightness
/usr/local/share/pynq-venv/bin/python3 -c 'import sys,numpy,cv2; print(sys.executable,sys.version,numpy.__version__,cv2.__version__)'
grep -E '^(CmaTotal|CmaFree|MemAvailable):' /proc/meminfo
```

`fuser` 返回 0 表示设备被占用，必须停止准备，不自动 kill 旧监控；返回 1 才表示无拥有者，其他状态为检查失败。保持相机原安装高度和 640×400 MJPG@60、manual exposure 50、brightness 0；任何不符都停止，不自动调参。

在新部署 `scripts/wo0016/` 中，用同一个绝对 worker Python 和 `XILINX_XRT=/usr` 执行 `calibration_runtime_gate.verify_worker_environment(worker, script_dir=..., wo15_dir=...)`，核对 PYNQ/Pydantic/NumPy/OpenCV 路径与版本。该现有 probe 不加载 Overlay、不打开 MMIO，但会导入依赖和读取环境；前后都记录 CMA。导入导致的全局变化不是泄漏结论。

新准入还必须核对：真实 Boot 与预期一致；BIT/HWH 的实际字节 SHA；Manifest 中 executor/source/地址/时钟；历史 Stage 1 原件 SHA、Boot、BIT/HWH 绑定；当前 FPGA manager、进程占用、已加载签名、AFI1 和两 DMA 状态。MMIO 读取须在这次准入授权范围内执行。旧 Stage 1 只可在重新验证后作为历史输入证据，不能当新运行 Permit；不得重用其已消费 guard。无法证明当前加载状态时记 NOT_VERIFIED 并停止，不自动重载 Overlay或再跑 Stage 1。

所有新验证报告、Manifest 和 Stage 1 复核记录写入新的私有目录；原件只读。不能通过手改 Boot 字段或设置 `ready_for_board=true` 伪造新授权。缺少新的合法准入材料时不调用下一步。

### 5.2 轮次设计与独立真值

每轮独立 300 帧，分别测单颗/两颗贴合/三颗紧邻，各重复三次。先只做授权的一轮，确认完整、安全释放和人工复核后才考虑下一轮；不写自动循环。另设空带轮次及同一 300 帧内两组通过的附加轮次（预期数按独立观察确认）。若完整通过无法落在 300 帧内，该轮 NOT_VERIFIED；不临时放大帧数绕过固定契约。

包内 `round-plan.template.json` 已准备九轮的期望数量、300 帧限制、独立观察字段、实体映射、DMA/FPS/CMA/错误计数和证据哈希列。实际数量及所有实测字段均为 null，真值为 UNANNOTATED、采样授权为 false。使用时另存至新轮次，不能把模板期望值当作实测真值，也不要改写包内受哈希保护的模板。

每个实物使用 `round_id + entity_id`，同一实体再经过使用新的 pass_id；记录放入顺序/时间、首末可见帧、是否完整过线、独立视频/观察者参考。不能从算法输出倒推实际数量。实物从画面外进入，移走上一轮物料后才准备下一轮；触碰相机/曝光/高度或中途遮挡须单独记录并重新判定准入。

### 5.3 新锁与单次操作入口

以下为已核对 main 解析器的 Bash 参数模板；所有变量都必须来自新一轮经核实记录，不能使用旧 live-monitor 的启动参数。`PY` 必须是准入 probe 实际核对的解释器。`DEPLOY` 必须指向修复并重新验证后的新包。

```bash
PY=/usr/local/share/pynq-venv/bin/python3
# 操作者在授权后填写 DEPLOY、BOOT、ID、ROUND、MANIFEST、MANIFEST_SHA、STAGE1、STAGE1_SHA、N
# BOOT 必须等于刚读取的实际值；每轮 ID、ROUND 和锁目录必须从未存在。
LOCK=/var/lib/wo0017/calibration/$BOOT/$ID/calibration-lock.json
COMMON=(--mode board --physical-calibration --counting-profile full-width-mixed
  --max-frames 300 --camera /dev/video0
  --manifest "$MANIFEST" --manifest-sha256 "$MANIFEST_SHA"
  --expected-boot-id "$BOOT" --stage1-receipt "$STAGE1" --stage1-receipt-sha256 "$STAGE1_SHA"
  --calibration-id "$ID" --calibration-lock "$LOCK" --worker-python "$PY"
  --capture-dir "$ROUND/capture" --capture-max-bytes 134217728 --evidence-json "$ROUND/evidence.json"
  --stabilization-delay-s 3.0 --operator-prearm-delay-s 0.0
  --ground-truth-count "$N" --material-label nut_split_validation
  --host 127.0.0.1 --port 8082 --timeout-s 5
  --execute --authorize-stage wo0017-physical-calibration --approve-once)
# 仅在读回两通道都为0x1002且无错误、并明确选择该初始基线时追加：
# COMMON+=(--calibration-initial-dmasr 0x1002)
# 否则保持默认0x1；不为了通过检查而改写状态或盲猜基线。

sudo -n env XILINX_XRT=/usr "$PY" "$DEPLOY/scripts/wo0016/prepare_physical_calibration.py" --worker-python "$PY" -- "${COMMON[@]}"
sudo -n env XILINX_XRT=/usr "$PY" "$DEPLOY/scripts/wo0016/start_physical_calibration_standby.py" --attempt-dir "$ROUND" --worker-python "$PY" --control-port 8081 -- "${COMMON[@]}"
```

`ROUND` 必须先用 root 权限以 `mkdir(..., mode=0o700, exist_ok=False)` 新建；父 runs 目录也只首次独占创建，不能对历史目录使用 `mkdir -p` 后当作新轮次。`prepare` 读环境/状态并创建新的 Boot+ID 私有 READY 锁；standby 由操作者检查 READY/绑定的哈希后，通过 8081 一次性口令 Start，才消费该锁并开始采集。main 没有旧 monitor 的 zero-OTP 启动模式。需要隧道时只转发 8081/8082，口令和原始私有日志不提交 Git。

该路径不使用正式 B1/B2 Permit，也不得加 `--wo0017-profile` 或 `--no-consume-guard`。B1/B2 另有固定帧数/数量契约，不能拿其旧 Permit 代替本轮 300 帧单次锁。创建新锁也不是免除操作者采样授权。

## 六、采集后验收、文件与状态

每轮应有 300/300、0 drop、同 DMA slot 的 Gray8 输入与原始 S2MM Binary8；每帧相机序号/时间戳、DMA 完成时刻、slot、DMA/Packer 状态随元数据保存。Gray8 与 Binary8 的对应是软件调度/slot 配对，不宣称硬件回传了 echoed frame ID。标定路径还保存 18 帧 640×400 原始参考图。

必须等待 capture manifest 为 COMPLETE、原始 NPZ/sidecar 和 frame-records 哈希校验通过。原始资料封存；独立标注从 `ground-truth.template.json` 另建新的 `ground-truth.json`，不要覆盖 template、原录像或 sealed 数据。真实漏计用独立确认的空 Track ID 列表记录，不能为了通过而省略该实体。

```bash
"$PY" "$DEPLOY/scripts/wo0016/replay_capture_dataset.py" "$ROUND/capture" --output "$ROUND/pl-binary8-replay.json"
"$PY" "$DEPLOY/scripts/wo0016/diagnose_touching_capture.py" "$ROUND/capture" --output "$ROUND/pl-ccl-diagnosis.json"
"$PY" -c 'import json,sys; r=json.load(open(sys.argv[1])); print(r["entity_report"]); sys.exit(0 if r["entity_report"]["status"]=="PASS" else 2)' "$ROUND/pl-binary8-replay.json"
```

注意现有 replay CLI 的退出 0 只表示完成回放；实体结论必须读取 `entity_report.status`。诊断器比较聚类候选，不是硬件验收器。独立 GT 需 CONFIRMED、观察者/独立证据、窗口实际数量、实体完整过线帧区间和人工审核的 Track ID 映射，否则 NOT_VERIFIED。

每轮最终表必须填写：

| 字段 | 来源/判定 |
|---|---|
| 预期/实际数量、漏计、重复、错误合并/误拆 | 独立 GT + 每实体事件/轨迹对应；总数相等不等于逐颗通过 |
| 每颗 Track ID、crossing frame、area/centroid/bbox | frame-records、原始 PL Binary8 回放；同 round_id 关联 |
| 原始 CCL、拆分后与聚类后数量、子块连通性 | 对保存的原始 Binary8 逐帧诊断，不用 Gray8 软件阈值替代 |
| DMA 状态 | 两通道每帧状态、错误位、结束时 idle/安全释放；错误或不确定即 FAIL_HOLD |
| FPS | steady-stream 边界内 completed frames / elapsed；相机请求60、预览FPS、主机回放FPS分别列示 |
| 耗时 | capture、mapping、encode、DMA round trip/sync/copy、ROI、预览交接/后台编码的样本数和 mean/median/p95；未测 N/A |
| CMA | 导入前/后、分配前/逐次分配后、运行末尾、释放前/后及延时检查；全局变化不单独证明泄漏或释放成功 |
| 证据 | 新轮次绝对路径、主机接收时间、Git/模块/Manifest/Stage1/GT/原始数组/记录/报告 SHA-256 |

若需要判定性能达标，须在采样授权前明确目标 FPS/时延阈值；未给阈值时只报告测量值。保留安装高度记录，但不能用名义传送带宽度或假定 mm/px 宣称实测尺寸/速度。

- `OFFLINE_PASS`：仅相应离线测试/计数窗口/合成对照通过。
- `HARDWARE_PASS`：已授权真实 PL 采集完整、哈希/配对/DMA/安全退出通过，并且独立逐实体审核确认预期数量、漏计/重复/误拆均符合事先约定。不能由本轮离线结果赋值。
- `FAIL`：有明确计数、几何、DMA 或安全契约失败，保留失败记录并停止。
- `NOT_VERIFIED`：未执行、输入或独立 GT 不完整、当前环境未核实。不得以历史许可、空字段或总数一致替代。

本轮不创建新就绪标签、不推送、不更改上一轮候选标签。账本分别保留离线计数证据及几何/启动回收失败。仍需操作者确认：板卡 SSH 目标及主机密钥、独占设备使用权、当前 Boot/Overlay/Manifest/Stage1 复核、worker 环境与 CMA、修订源码/包哈希、实物布局和每轮数量/重复次数、独立观察者、完整通过窗口，以及新的上传/只读准入/采样授权范围。
