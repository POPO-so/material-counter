# 首组 M8 密集贴近采集协议（M8-FIRST-CAPTURE-PROTOCOL）

| 项目 | 内容 |
|---|---|
| 状态 | 协议与事件演练准备完成；450 帧窗口可行性已确认；正式投料未执行。 |
| 基线 | `fc8f8b9f`，分支 `codex/m8-first-capture-protocol-20261102` |
| 范围 | 仅新增采集状态行与协议文档；相机/解码/RAM 策略/450 上限未变 |

## 1. 450 帧预算（必须相加 ≤ 450）

| 阶段 | 帧数 | 约合时间（59 fps） |
|---|---|---|
| 空载基线 | **60** | ≈1.0 s |
| 投放窗口 | **300** | ≈5.1 s |
| 尾部（全部物料离开后） | **90** | ≈1.5 s |
| 合计 | **450** | ≈7.6 s |

- 投放必须在第 60 帧后开始、在第 360 帧前结束（否则尾部不足 90 帧）；
- 工具在非 formal 模式不会强制尾部帧数，必须人工核对 `frames_after_drop_end ≥ 90`；
- 不允许把一次过程拆成多个 session（会产生时间断档）。

## 2. 事件机制与时序

- 标记文件位于 `<session>/events/`：`drop_start`、`drop_end`、`stop`；存在即触发。
- 采样位置：每个录制帧的**帧边界**（读取/解码/写 RAM 之后）检查一次；精度约一帧（≈17 ms）。
- 一次性记录：首次看到标记时记录帧号与 `monotonic_ns`，后续帧不再重复记录；
  标记文件持续存在不重复触发。
- 每个 session 使用全新目录，旧 session 的标记不会串入；**不要复制或预先创建标记**。
- `baseline_end` 由 `--baseline-frames` 按帧号触发，不依赖文件。
- 新增 stdout 状态行（仅用于协议协调，不改变数据）：
  ```
  CAMERA_READY {...}
  RECORDING_STARTED frame=0 monotonic_ns=...
  BASELINE_COMPLETE frame=59 monotonic_ns=...
  DROP_START_OBSERVED frame=... monotonic_ns=...
  DROP_END_OBSERVED frame=... monotonic_ns=...
  STOP_OBSERVED frame=...
  ```
  只有看到 `BASELINE_COMPLETE` 才允许开始投放；`CAMERA_READY` 仅表示相机已打开。

## 3. 权限

- 采集以 `sudo -n` 运行，session 目录为 `0700 root`，普通用户不能在其中创建标记；
- **最小权限方案**：使用已有的 `sudo -n touch <session>/events/drop_start`（无需 chmod 777、无需改 unit）；
- 读取 manifest/数据同样通过 `sudo -n`；不修改目录权限。

## 4. 现场操作口令（正式采集时使用）

1. **准备**：固定相机/光照/带速；清空传送带；物料放在投放位但不要越线。
2. **Claude**：执行预检并启动采集，等待并报告 `CAMERA_READY` 与 `RECORDING_STARTED`。
3. **Claude**：看到 `BASELINE_COMPLETE frame=59` 后报告"READY，可以投放"。
4. **用户**：开始投放；投放完成的瞬间说"投放完成"。
5. **Claude**：收到后立即 `sudo -n touch .../events/drop_end`；同时确认 `DROP_START_OBSERVED`/
   `DROP_END_OBSERVED` 的帧号与时间。
6. **用户**：确认视野内无残留物料；Claude 核对 `frames_after_drop_end ≥ 90`。
7. **Claude**：等待采集自然结束并保存；核验 `status=COMPLETE`、450 帧、哈希、事件与真值。
8. 只有上一组核验通过（文件、哈希、事件、真值、尾部帧）后，才可以开始下一组。

## 5. 事件演练结果

见本轮报告（同一协议、无物料、模拟 `drop_start/drop_end`）。