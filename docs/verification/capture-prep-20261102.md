# Camera-only 连续帧采集工具加固（CAPTURE-PREP）

| 项目 | 内容 |
|---|---|
| 状态 | 主机侧实现与模拟测试完成；未接触板端、摄像头或任何硬件路径。 |
| 基线 | `1c1ee54f`，分支 `codex/m8-capture-prep-20261102` |
| 范围 | 仅 camera-only 采集工具、其测试与离线阈值语义；未改算法、RTL、systemd、release |

## 1. 变更与语义

| 文件 | 变更 |
|---|---|
| `scripts/wo0016/capture_touching_camera_only.py` | 加固会话路径：预检、状态机、标记事件、有界窗口、部分保存、时间统计、真值模板、memmap 长窗口；保留旧 `capture()` 接口与返回键 |
| `scripts/wo0016/test_capture_hardened.py` | 14 项 Fake VideoCapture 测试 |
| `scripts/wo0016/offline_threshold.py` | 新增 `inclusive` 参数：默认保持 `cv2.THRESH_BINARY`（`>`），`inclusive=True` 使用 `>=` 匹配 PL 规则；默认行为不变 |
| `scripts/wo0016/test_offline_threshold_semantics.py` | 3 项阈值语义测试 |

无 PYNQ/DMA/VDMA/MMIO/Overlay/HDMI 依赖。

## 2. 会话状态与交互流程

```
PRECHECK → CAMERA_READY → RECORDING → FINISHING → COMPLETE
                                        ↘ INCOMPLETE / FAILED
```

- **PRECHECK**：参数合法性、输出目录必须不存在（不覆盖历史）、磁盘（预计字节 ×2 + 256 MiB）、
  MemAvailable（预计峰值 ×1.5）、formal 规则（禁止 compact、最低 600 帧）。
- **CAMERA_READY**：打开 V4L2、确认 640×400/MJPG、读取 2 帧预热；打印 `CAMERA_READY`。
- **RECORDING**：按帧读取、解码、写入；每帧检查 `events/` 下的标记文件。
- **FINISHING**：落盘、计算统计与哈希、写 manifest。
- **COMPLETE / INCOMPLETE / FAILED**：分别对应完整窗口、部分数据/提前停止、预检或相机打开失败。

**标记事件（本地文件，不依赖聊天时间）**：`events/drop_start`、`events/drop_end`、`events/stop`；
在帧边界检测，记录帧号与 `monotonic_ns`。`baseline_frames` 到达时记录 `baseline_end`。
formal 模式下缺少 `drop_end` 或尾部帧不足 → `INCOMPLETE`。

## 3. 数据格式、连续性统计与哈希

- **npz 模式**（默认，有限窗口）：
  - `source_gray640x400` `[N,400,640] uint8`（非 compact）
  - `input_gray320x240` `[N,240,320] uint8`
  - `monotonic_ns[N]`、`wall_ns[N] int64`、`read_ms[N] float32`
- **memmap 模式**（长窗口，formal 默认）：同名字段写为独立 `.npy` 文件，逐帧写入，内存占用与窗口长度无关。
- manifest 记录：session_id、status、state_history、failure_reason、format、compact、formal、
  frames_requested、frame_count、baseline_frames、tail_frames、frames_after_drop_end、events、
  camera 设置、elapsed_s、effective_fps、timing（median/max gap、超过阈值的间隔、非正间隔）、
  预检结果、data_files（大小 + SHA-256）、archive/archive_sha256/archive_bytes。
- `timing.gap_threshold_ms = max(33 ms, 2×中位数)`；**只能说明应用可见的间隔，不能证明 V4L2 内部零丢帧**。
- `raw_mjpeg_saved = false`：只保存解码后的 Gray8；原始 MJPEG 未保存，manifest 如实记录。
- `decoded_frames = saved_frames = frame_count`（本工具无独立处理阶段）。
- 真值模板 `truth-template.json`：material/nominal_count/placement_mode/belt_speed/lighting_state/
  camera_settings/drop_start/drop_end/operator_count/clear_confirmed/archive_sha256/notes 全部 `UNKNOWN`。

## 4. 内存与磁盘预算

| 模式 | 每帧 | 300 帧 | 1800 帧 |
|---|---|---|---|
| npz 非 compact（源 400×640 + 320×240） | ≈256 KB | ≈100 MB RAM | 不允许（上限 300） |
| npz compact（仅 320×240） | ≈77 KB | ≈23 MB | ≈138 MB RAM |
| memmap 非 compact | ≈256 KB | — | ≈598 MB 磁盘、低 RAM（上限 3600） |

预检要求：磁盘 ≥ 预计字节 ×2 + 256 MiB；MemAvailable ≥ 预计峰值 ×1.5。
board 当前 5.0 GB 可用、MemAvailable 约 415 MB：formal 1800 帧 memmap 需约 1.45 GB 可用，
通过；npz 600 帧非 compact 不在允许范围（改用 memmap）。

## 5. 模拟测试（Fake VideoCapture）

14 项采集测试 + 3 项阈值测试，全部通过（与既有 camera-only preview 测试合计 54 passed）：

- 完整会话：事件、baseline/drop 帧号、尾部帧、状态历史、npz 形状、哈希、真值模板；
- 相机打不开 → FAILED manifest，无数据文件；
- 中途 read 失败 / 解码失败 → INCOMPLETE + 部分 npz（前缀帧）；
- 磁盘不足 / 内存不足 → PRECHECK 失败，不打开相机；
- 保存失败 → INCOMPLETE + `save_failed`，manifest 仍可写；
- 停止标记 → INCOMPLETE + `stopped_by_marker`；
- 时间戳非单调 → `non_positive_gaps` 记录；
- 输出目录已存在 → 拒绝且不覆盖；
- formal + compact / formal 窗口过短 → 拒绝；
- formal 缺 drop_end → INCOMPLETE；
- memmap 文件与哈希；
- 旧 `capture()` 接口返回旧键；
- 阈值：默认 `>`、`inclusive=True` 使用 `>=`、ROI 外为 0、输入不被修改。

## 6. 板端空载短测的准入要求与命令（未执行）

准入：服务 inactive/disabled、无业务进程/job/设备持有者、MemAvailable ≥ 300 MB、
磁盘 ≥ 1 GB、`/dev/video0` 存在且无持有者、采集工具已 staging。

```sh
sudo -n /usr/local/share/pynq-venv/bin/python3 -B \
  /home/xilinx/capture-tools/capture_touching_camera_only.py \
  --output-dir /home/xilinx/captures/empty-<UTC> \
  --frames 120 --start-delay-s 5
```

（`xilinx` 不在 `video` 组，需 `sudo -n`；输出目录为 root 所有。）

核验：manifest `status=COMPLETE`、`frame_count=120`、`effective_fps≈60`、`archive_sha256` 可复算、
`timing.max_gap_ms` 与 `gaps_over_threshold` 合理。随后可跑一次 `--compact --frames 300` 验证长序列。

## 7. 尚未解决的风险

- 未在真实摄像头上运行；V4L2 驱动内部丢帧不可见，只能用时间戳间隔推断；
- 离线 Gray8→Binary8 阈值与 PL `>=128` 的差异已显式化，但软件二值化**不等价于 PL 实测**；
- formal 长窗口依赖 memmap 写入与磁盘吞吐，板端 eMMC 写吞吐未实测；
- 输出为 root 所有，后续离线分析需 `sudo` 或另行授权改权限；
- 采集工具不在正式 release 中，需单独 staging；
- HDMI close FAIL_HOLD 与关机阻塞风险与本路径无关，但仍限制任何走 R5 的实时验证。