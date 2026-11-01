# Camera-only 有界 RAM 连续采集模式（RAM-CAPTURE）

| 项目 | 内容 |
|---|---|
| 状态 | 已实现 `--format ram`；35 项相关测试通过；未接触板端/摄像头。**300 帧上限已由后续 450 帧提交取代（见 `camera-ram-450-20261102.md`）。** |
| 基线 | `ecd11d11`，分支 `codex/m8-camera-ram-20261102` |
| 范围 | 仅采集工具；未改相机 FPS/V4L2/解码、R5、release、FPGA、算法参数 |

## 1. 变更

| 文件 | 变更 |
|---|---|
| `scripts/wo0016/capture_touching_camera_only.py` | 新增 `ram` 输出格式：仅保存 640×400 Gray8 源帧 + 时间戳 + `read_ms`；采集期间不写任何数据文件；结束后一次性保存；`tail_post_ms` 消除末帧盲区；RAM 专用内存预算 |
| `scripts/wo0016/test_capture_ram.py` | 9 项 Fake VideoCapture 测试 |
| `docs/verification/camera-ram-capture-20261102.md` | 本文件 |

- 复用现有 npz 的 RAM 预分配路径；**ram 模式不分配 320×240 缓冲，也不做实时 resize**。
- 保留会话状态、`drop_start/drop_end/stop` 事件、`baseline_end`、`tail_frames`、真值模板、manifest、
  SHA-256、部分保存与 INCOMPLETE 语义。
- 诊断（`--diagnostics`）在 ram 模式记录 `read_ms`、`decode_ms`、`ram_write_ms`、`frame_total_ms`。

## 2. 内存预算与准入门槛（300 帧）

| 项 | 值 |
|---|---|
| 源帧 | 300 × 400 × 640 = **76 800 000 B（73.2 MiB）** |
| 时间戳 + read_ms | 300 × 20 B ≈ 6 KB |
| 诊断数组（可选） | 4 × 300 × 4 B = 4.8 KB |
| 运行时余量（Python/OpenCV/numpy/临时） | **128 MiB** |
| 估算峰值 | (76.8 MB + 128 MiB) × **1.25** ≈ **263.8 MB（257 604 KiB）** |
| 准入 | `MemAvailable ≥ 257 604 KiB`；板端当前约 420 MB → 通过，余量约 156 MB |
| 磁盘准入 | 预计文件 ×2 + 256 MiB ≈ 410 MB；板端 4.8 GiB → 通过 |
| 帧数上限 | **300（v1 硬上限）**；超过直接报错，**不静默截断** |

## 3. 采集期间是否完全避免磁盘写入

- 采集循环只写 RAM 数组；不调用 `open/np.savez/flush/fsync`；
- `truth-template.json` 在预检阶段写入，`manifest.json` 在结束后写入；
- 测试用 FakeCamera 在录制第 4 帧时枚举输出目录，确认**没有 `.npz`/`.npy` 数据文件**；
- 保存阶段在循环结束后一次性执行，并记录 `save_ms`/`hash_ms`/`manifest_ms`。

## 4. 时间戳、事件与数据完整性

- 每帧保留 `monotonic_ns`、`wall_ns`、`read_ms`；`timing` 从保存的时间戳计算；
- `tail_post_ms`：从末帧时间戳到循环结束（含释放相机前）的耗时，写入 manifest；
- 事件：`events/drop_start`、`drop_end`、`stop`、`baseline_end` 与帧号；
- 部分数据：read/解码失败或保存失败时保存已采集前缀，`status=INCOMPLETE` + `failure_reason`；
- 数据文件 SHA-256 可复算，`np.load` 可重读。

## 5. 模拟测试（35 passed）

`test_capture_ram.py`（9 项）：

- 完整 300 帧内的正常会话、事件、哈希、`tail_post_ms`；
- 采集循环内无数据文件；
- 内存准入按运行时余量计算（200 MB 拒绝、300 MB 通过）；
- 301 帧拒绝且不截断；formal 模式拒绝 ram；
- 中途 read 失败 → INCOMPLETE + 部分 npz；
- 保存失败 → INCOMPLETE + `save_failed`；
- drop 事件与尾部帧计数；
- 时间戳单调。

并保留既有 `test_capture_hardened.py`（14）、`test_capture_diagnostics.py`（9）、
`test_offline_threshold_semantics.py`（3）。

## 6. 第一次 RAM 空载短测建议（未执行）

```sh
sudo -n systemd-run --scope -p MemoryMax=512M -- \
  nice -n 19 timeout 180 /usr/local/share/pynq-venv/bin/python3 -B \
  /home/xilinx/capture-tools/capture_touching_camera_only.py \
  --output-dir /home/xilinx/captures/ram-empty-<UTC> \
  --frames 300 --start-delay-s 5 --format ram --diagnostics
```

准入同上（服务 inactive/disabled、无持有者、MemAvailable ≥ 258 MB、磁盘 ≥ 1 GiB、dmesg 干净）。
核验：`status=COMPLETE`、300 帧、`camera_ram_capture.npz` 仅含源帧/时间戳/read_ms、
`tail_post_ms` 非空、`timing` 非空、诊断四个数组长度 300、哈希可复算、摄像头释放。

## 7. 300 帧窗口是否足以覆盖真实投料

**仅够用于空载与短窗口冒烟，不足以作为正式物料采集窗口。**

- 300 帧按实测 47–55 fps 约 **5.5–6.4 s**；一次投放 + 清空尾部可能接近或超过该窗口；
- 工具**不会静默截断**：请求超过 300 帧会直接报错；
- 最小扩容候选：**450 帧**（源帧 115.2 MB；估算峰值 ≈312 MB，板端 420 MB 可用，理论可通过），
  或 600 帧（估算 ≈360 MB，余量过小，不建议）。扩容需单独评审并在板端实测 cgroup 峰值，
  本轮不自动提高上限。

## 8. 未解决风险

- 未在板端运行；实际 RSS 与 cgroup 峰值未实测；
- 300 帧窗口可能不足；
- 采集结束的一次性保存约 77 MB，可能产生数秒落盘时间（不影响采集完整性，但影响操作节奏）；
- 离线 320×240 预览需由源帧生成，且软件二值化不等价于 PL；
- HDMI close FAIL_HOLD 与本路径无关，但限制任何走 R5 的联合验证。