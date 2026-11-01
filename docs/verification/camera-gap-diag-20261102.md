# Camera-only 采集：memmap 时间统计修复与分段诊断（GAP-DIAG）

| 项目 | 内容 |
|---|---|
| 状态 | 已实现；memmap `timing` 修复；新增可选分段诊断；主机测试 26 passed；未做板端采集。 |
| 基线 | `23ff3f73`，分支 `codex/m8-camera-gap-diag-20261102` |
| 范围 | 仅采集工具观测能力与测试；未改相机参数、V4L2 缓冲、解码、memmap 写入策略或同步策略 |

## 1. 修复内容

- `manifest.timing` 现在从**实际保存的时间戳**计算：
  - npz：`monotonic_ns` 数组；
  - memmap：`monotonic_ns.npy[:frame_count]`（此前恒为 null）。
- 时间戳定义不变：在 `read → decode → resize` 之后、数据写入之前采样。
- 新增可选 `--diagnostics`：
  - 每帧分段计时（ms）：`read_ms`（camera.read）、`decode_ms`（imdecode）、`resize_ms`、
    `source_write_ms`、`preview_write_ms`、`frame_total_ms`；
  - 预分配 `float32` 数组，无逐帧日志；
  - 结束后写 `diagnostics.npz`，并在 manifest 中登记文件与 SHA-256。
- manifest 新增 `diagnostics`（enabled/file/anomalies/anomaly_limit）与 `finishing`
  （flush_ms/save_ms/hash_ms/manifest_ms）。
- 异常间隔归因（最多 64 条）：对每个超过 `max(33 ms, 2×中位数)` 的间隔，记录
  相邻帧号、`gap_ms`、前一帧的 read/decode/resize/write/frame_total、后一帧的 read/decode/resize，
  以及 `unexplained_ms = gap − 前帧时间戳后工作 − 后帧时间戳前工作`。
- 兼容性：`capture()` 旧接口与返回键不变；历史 npz/manifest 格式不变；诊断默认关闭。

## 2. 对旧 300 帧数据的只读分析（可确认的事实）

- 时间戳在 `read+decode+resize` 之后、写入之前采样；
- `g[56] = 414.221 ms`（帧 56→57）；`read_ms[57] = 9.3 ms` → **≥404.9 ms 未被 read/decode 解释**；
- 全段 `read_ms` 合计 3069.6 ms，占 6401.5 ms 总间隔的 48.0%；剩余 **3332.2 ms** 落在
  resize、memmap 写入、标记检查与循环开销等**未计时阶段**；
- `read_ms` 最大值 39.84 ms（帧 61），**单次相机读取无法解释 414 ms**；
- 该次运行没有分段计时，**无法确认**停顿发生在 memmap 写入、resize 还是内核回写/调度；
  不能据此把 414 ms 归因于 eMMC。

## 3. 测试

`test_capture_diagnostics.py`（9 项）+ `test_capture_hardened.py`（14 项）+ 阈值语义（3 项）= **26 passed**：

- memmap `timing` 非空且与保存时间戳一致；npz/memmap 语义一致；
- 诊断文件与 manifest 登记；
- 注入 read 延迟 45 ms → 归因到后一帧 `read_ms_next`；
- 注入 decode 延迟 45 ms → 归因到 `decode_ms_next`；
- 注入 memmap 写入延迟 40 ms → 归因到前一帧 `source_write_ms_prev`；
- INCOMPLETE 会话仍有 timing 与诊断；
- 诊断关闭时不产生诊断文件；
- 既有的相机打不开、read/解码失败、磁盘/内存不足、保存失败、stop、时间戳异常、
  重复目录、formal 规则等测试全部保留通过。

## 4. 探针开销

- 主机 300 帧 RAM-only（npz）对比：关闭 / 开启中位耗时 0.081 / 0.079 s（差异在噪声内）；
- 内存：6 个 `float32` 数组 = 24 B/帧（300 帧 7.2 KB，3600 帧 86 KB）；
- 异常记录上限 64 条；无逐帧同步写日志；
- ARM 上的额外开销未实测，估计为每帧数十微秒（相对 17 ms 帧间隔 <0.5%），仅为估算。

## 5. 下一次最小板端诊断方案（未执行）

```sh
sudo -n systemd-run --scope -p MemoryMax=512M -- \
  nice -n 19 timeout 180 /usr/local/share/pynq-venv/bin/python3 -B \
  /home/xilinx/capture-tools/capture_touching_camera_only.py \
  --output-dir /home/xilinx/captures/gapdiag-<UTC> \
  --frames 300 --start-delay-s 5 --format memmap --diagnostics
```

准入条件与上一轮相同（服务 inactive/disabled、无持有者、MemAvailable ≥300 MiB、磁盘 ≥1 GiB、
dmesg 干净）。分析 `diagnostics.npz` 与 `diagnostics.anomalies`：

- 若 `source_write_ms_prev` 占主导 → 写回/存储路径；
- 若 `read_ms_next` 占主导 → 相机读取；
- 若 `unexplained_ms` 仍很大 → 调度、页错误或内核回写，需要更底层观测。

## 6. 是否仍需优化采集架构

**暂不。** 先用一次带诊断的 300 帧 memmap 空载测试定位停顿来源，再决定是否改为分块写入或调整同步；
本轮按要求没有加入周期性 `flush()/os.sync`，也没有改变 V4L2/分辨率/FPS/解码/写入策略。