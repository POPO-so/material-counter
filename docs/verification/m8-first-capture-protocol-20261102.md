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
## 6. 协议 v2（最终修订）

### 6.1 终端 A 顺序（不得颠倒）

```
BASELINE_COMPLETE
  → ssh sudo -n touch .../events/drop_start
  → 等待 DROP_START_OBSERVED（程序在下一帧边界记录）
  → 仅在两者都成功后才响铃并打印"现在开始投放"
```

- 任一环节失败（touch 非零、未出现 `DROP_START_OBSERVED`）→ **不发出投料信号**，打印失败标记；
- 同一 session 内重复 `BASELINE_COMPLETE` 只处理一次；
- 启动器使用 `set -o pipefail` + `PIPESTATUS[0]` 保留采集进程退出码；
- `CAMERA_READY`/`BASELINE_COMPLETE` 均不是采集成功证据；最终以 `CAPTURE_RC=0` 与
  manifest `status=COMPLETE` 为准。

### 6.2 终端 B（一次按键）

- 预先建立 SSH ControlMaster（`ControlPersist=900`），按键后仅发送 `touch drop_end`；
- 发送前检查 `drop_end` 是否已存在，存在则拒绝重复发送；
- 记录板端 `/proc/uptime` 作为触发时刻，可与 `DROP_END_OBSERVED.monotonic_ns` 计算真实延迟；
- 不依赖 R5/DMA/Overlay/HDMI。

### 6.3 450 帧时间预算（含延迟）

| 阶段 | 估计 | 说明 |
|---|---|---|
| baseline → drop_start 观察 | 0.2–0.5 s | 同步 ssh touch + 下一帧边界 |
| 观察 → 操作者响铃 | ≈0.01 s | 同一终端 |
| 操作者反应 | 0.3–0.8 s | 需现场计时 |
| 投放 10 颗 | 2.0–4.0 s | 需控制在 300 帧（≈5.05 s）内 |
| drop_end 触发 → 观察 | 0.2–0.4 s | 按键 + 常驻连接 + 下一帧 |
| 投放完成 → 全部离开画面 | 0.5–1.5 s | 依赖带速/投放点到视野出口距离 |
| 尾部空载 | ≥90 帧（≈1.5 s） | 必须在 450 帧内 |

**结论**：只有"投放 ≤4.0 s 且离开画面 ≤1.5 s"时，450 帧才能覆盖完整过程。
若超出，记录真实窗口限制，标记"不适合完整计数验收"，**保留全部已采原始帧**；
不得为凑 90 帧而虚标有效。

### 6.4 有效性判定（修正）

- 人工真值（10 颗）与算法检测数量**分开报告**；
- 不得因二值连通块数 ≠ 10 废弃密集粘连样本；
- 分别报告：人工真值、检测数量、轨迹计数、无法确认的遮挡；
- `drop_start`/`drop_end` 只是事件标记；`drop_end` 后需 ≥90 帧并**用实际画面确认 ROI 清空**；
- 窗口不足 → 标记不适合完整计数，但保留帧供局部拆分分析，不删除。

### 6.5 离线模拟结果

`build/simulate_protocol.sh`（不接摄像头、不连开发板）：

- 正常顺序：响铃仅在 `DROP_START_OBSERVED` 之后出现一次；
- touch 失败：无响铃，输出 `DROP_START_TOUCH_FAILED` / `DROP_START_NOT_ACKNOWLEDGED`；
- 无确认：无响铃；
- 重复 `BASELINE_COMPLETE`：只 touch 一次、只响铃一次；
- 采集退出码 3：启动器报告 `CAPTURE_RC=3` 并以 3 退出；
- `drop_end`：首次发送成功；再次执行被 `DROP_END_ALREADY_EXISTS` 拒绝。

结果：**18 项检查全部 PASS**。

## 7. 协议 v3：全自动现场控制器

### 7.1 现场提示可达性核查

- Claude Code 执行进程 `SessionId=1`，与 `explorer` 及交互用户 `BWIN\POP` **同一桌面会话**；
- 音频服务 `Audiosrv` 运行，存在可用声音设备；
- 未安装 BurntToast，因此提示采用 **WPF 置顶黄色大字窗口（12 s 自动关闭）+ 系统提示音**；
- 该提示只在**这台现场电脑**上可见/可听；若操作者不在该电脑旁，提示不可达，必须 BLOCKED。

### 7.2 自动化分工

| 环节 | 执行者 |
|---|---|
| 安全门禁（服务/进程/相机/内存/磁盘/dmesg/sudo/目录） | **控制器自动** |
| 启动 450 帧 RAM 采集 | **控制器自动** |
| 检测 `BASELINE_COMPLETE` → 创建 `drop_start` | **控制器自动** |
| 等待 `DROP_START_OBSERVED` | **控制器自动** |
| 现场提示（声音 + 置顶窗口） | **控制器自动**（本机桌面） |
| 投料 10 颗 | **用户** |
| `drop_end` | **控制器自动**：提示后固定窗口 4.0 s，记录 `source=ESTIMATED_FIXED_WINDOW` |
| 结束、退出码、manifest 核验 | **控制器自动** |

### 7.3 关键约束

- `drop_end` 是**自动估计**，不是人工确认；真值模板仍为 UNKNOWN，控制器事件日志记录来源与窗口；
- 若 `drop_start` 创建失败或未观察到 `DROP_START_OBSERVED`：**不发提示、不发送 drop_end**，本次无效；
- 提示窗口/声音不可达 → 禁止发出投料指令；
- 不重试、不启动第二组；失败保留原始数据与事件日志。

### 7.4 控制器与提示脚本

- `scripts/wo0016/operator_capture_controller.ps1`
- `scripts/wo0016/show_drop_cue.ps1`

运行（正式采集时）：

```powershell
pwsh -NoProfile -File scripts/wo0016/operator_capture_controller.ps1 `
  -SessionDir /home/xilinx/captures/m8-dense-20261102-01 `
  -CueMode Window -DropWindowSeconds 4.0
```

### 7.5 模拟测试（不接摄像头/不连板）

`scripts/wo0016/simulation/simulate_controller.ps1`，19 项检查全部 PASS：

- 正常：提示仅在 `DROP_START_OBSERVED` 后出现一次；`drop_start` 一次；`drop_end` 一次且来源为
  `ESTIMATED_FIXED_WINDOW`；观察到 `DROP_END_OBSERVED`；核验通过；
- touch 失败：无提示、无 drop_end、控制器失败退出；
- 无确认：无提示、无 drop_end、标记未确认；
- 重复 `BASELINE_COMPLETE`：只 touch/提示一次；
- 采集退出码 3：控制器保留并报告。

### 7.6 仍无法消除的人工因素

1. **物理投料**本身；
2. 提示必须在这台现场电脑的桌面/扬声器上被感知；
3. `drop_end` 是时间估计而非人工确认，分析时必须标注 ESTIMATED；
4. 人工真值 10 颗为操作者声明，工具无法核验。

## 8. 协议 v3.1：预热的桌面提示与 4 秒状态切换

### 8.1 桌面可达性实测（一次，不接摄像头）

在交互桌面（SessionId=1）独立运行 `show_drop_cue.ps1`：

| 项 | 实测 |
|---|---|
| 预热后、信号前窗口是否可见 | **False**（不会提前误报 READY） |
| 信号后窗口可见 | **True** |
| 信号 → 窗口可见延迟 | **307 ms** |
| 投料窗口内窗口保持可见 | True |
| 进程是否正常退出 | True（exit 0） |
| 从信号到关闭总时长 | ≈6.25 s（4 s 投料 + 2 s 关闭） |
| 提示音 | 开始时 `Exclamation`，4 s 后 `Asterisk`；**是否可闻需现场确认** |

### 8.2 提示状态

- 预热：采集开始即启动隐藏的 WPF 进程，加载窗口但不显示；
- `DROP_START_OBSERVED` 后写入信号文件 → 窗口在 **约 0.3 s** 内显示；
- 显示内容：**"现在开始投放 10 颗 M8 / 4 秒内完成"**（黄色置顶）；
- 4 s 后切换为 **"投料时间结束"**（灰色）并播放提示音，再 3 s 自动关闭；
- 窗口未能显示（1.5 s 内不可见）→ 不发出任何投料提示，发送 `stop` 标记，**不发送 drop_end**，控制器以 2 退出。

### 8.3 安全行为

- `drop_start` 创建失败或无 `DROP_START_OBSERVED`：不预热信号、不显示窗口、不发送 drop_end；
- 提示进程有界：预热最多等待 90 s 无信号即退出；控制器结束时清理提示进程与信号文件；
- 提示进程不阻塞采集读取（异步读 + 独立定时器），不影响采集退出码。

### 8.4 模拟复核

`scripts/wo0016/simulation/simulate_controller.ps1`：**24 项检查全部 PASS**，新增：

- 提示进程不可用（模拟失败）：无提示、无 drop_end、发送 stop、控制器退出 2；
- 正常流程仍只在 `DROP_START_OBSERVED` 后提示一次；
- `drop_end` 来源仍为 `ESTIMATED_FIXED_WINDOW`。

### 8.5 时间预算更新

| 阶段 | 实测/估计 |
|---|---|
| baseline → drop_start 观察 | 0.2–0.5 s |
| 观察 → 窗口可见 | **≈0.3 s**（实测 307 ms） |
| 操作者反应 | 0.3–0.8 s |
| 4 s 提示窗口结束 | baseline 后约 **4.7 s**（在 5.05 s 投放窗口内） |
| drop_end 触发 → 观察 | 0.2–0.4 s |
| 尾部空载 | ≥90 帧 ≈1.5 s |

### 8.6 仍需用户确认

1. 提示窗口与声音在你的现场桌面**实际可见/可闻**（我只能证明窗口可见与声音调用成功，不能证明你听见）；
2. 接受 4 s 提示窗口与"投料时间结束"切换；
3. 接受 `drop_end` 为 `ESTIMATED_FIXED_WINDOW` 自动估计；
4. 授权仅一组 10 颗 M8 采集。
