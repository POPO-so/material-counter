# LJ-01 + 算法移植集成纪要（2026-10-30）

> 本纪要记录 `integration/lj01-plus-algo` 分支的基线、移植内容、验证结果和未关闭风险。
> 其中的仿真、主机微基准和历史板端证据均**不等于**整合版本的板端验收；整合版本尚未进行实板资源、RLE 端到端或真实物料准确率验收。

## 1. 基线与整合关系

| 项 | 值 |
|---|---|
| 架构基线 | `origin/LJ-01` = `5b554617793566b39dd3bc1e04d919f1aae34095`（“合并分屏画面刷新到LJ-01”） |
| 主工作区基线 | `main` = `55f8f0ee`，工作区含未提交的算法/诊断改动；审计快照见 `C:\Users\POP\material-counter-snapshots\20261030-stage0` |
| 集成分支 | `integration/lj01-plus-algo`，基于 `5b554617` |
| 工作区 | `C:\fpga\mc-integration`（独立 git worktree） |
| 原工作区 | `c:\fpga\material-counter` 未切换、未重置、未覆盖；快照校验 5269 个文件 SHA-256 全部一致 |

整合方式是**选择性移植**，不是 merge 或 cherry-pick：LJ-01 提供 640×400 RLE、原生 run-based CCL、DMA 安全路径和板端验证过的 Gray8 架构；主工作区中经审计的计数可观测性、有界轨迹管理和出生语义候选按 LJ-01 的接口重写后落盘。两套 320×240 Gray8 线格式不兼容的问题由 [ADR-0006](../adr/adr-0006-gray8-active-protocol.md) 记录决策，不通过代码合并解决。

## 2. 五个集成提交

| 提交 | 优先级 | 内容 | 主要文件 |
|---|---|---|---|
| `e30fce34` | P0 | 在 LJ-01 跟踪器上移植可选计数可观测性 | `scripts/wo0016/stream_counter.py`、`scripts/wo0016/test_count_observability.py` |
| `a86e1819` | P1 | 已完成轨迹改为 128 条有界保留 | `scripts/wo0016/stream_counter.py`、`scripts/wo0016/test_stream_counter.py` |
| `a668df41` | P3 | 320×240 实料标定知识与 640×400 重标定流程（纯文档） | `docs/verification/real-material-calibration-transfer-20261030.md` |
| `ac23e300` | P0 | 把可观测性通道接入 `RleCounter` | `scripts/rle/counter.py`、`scripts/rle/test_counter.py` |
| `3c6a9df2` | P2 | 默认关闭的 `band_gate` 出生语义 + 单变量 A/B | `scripts/wo0016/stream_counter.py`、`scripts/wo0016/birth_semantics_ab.py`、测试、A/B 结果与说明 |

本阶段新增的提交（文档、基准、测试修正）见第 6 节，不计入上述五个移植提交。

## 3. 移植内容、来源、作用与风险

### P0 · 计数可观测性（`e30fce34`、`ac23e300`）

来源：主工作区未提交的 `scripts/wo0016/stream_counter.py` 和 `scripts/rle/counter.py`；按 LJ-01 的 `FrameResult`、`RleCounter` 接口重写。

跟踪器侧（`scripts/wo0016/stream_counter.py`）：

- `_begin_frame_observability()`、`_diagnostics_snapshot()`：每帧开始/结束维护诊断快照。
- `_detection_record()`、`_track_record()`：把检测框、轨迹状态和出生标记转为可序列化记录。
- `_record_rejected_component()`：记录被 `min_area`、`max_area`、`max_bbox_width/height` 拒绝的分量及原因。
- `_record_outside_roi_components()`：记录 ROI 外/边界观测。
- `_finish_track(track, reason)`：轨迹结束时记录原因（`missed_frame_budget_exceeded` 等）。
- `FrameResult.diagnostics`：输出 `rejected_components`、`split_attempts`、`ambiguous_associations`、`association_candidates`、`boundary_observations`、`lost_tracks`、`associations`。
- `stage_timings_ms`：Gray8 路径输出 `t_ccl`、`t_split`、`t_track`、`split_calls`。

RLE 侧（`scripts/rle/counter.py`）：

- 每帧调用 `_begin_frame_observability()`；只有 `observability=True` 时才以 `analyze_runs(..., min_area=1)` 运行 CCL，然后在 Python 侧按 `min_area` 过滤并记录被拒分量，保证检测集合与默认路径一致。
- 记录 ROI/画面边界裁剪、拆分尝试及 `split_rejected_reason`（`None` / `no_valid_partition` / `invalid_partition`）、关联候选和丢失轨迹。
- 保留队友原有 `roi_subtimings_ms`（`rle_regions_ms`、`track_ms`）；新增 `stage_timings_ms`，不替换既有键。

风险：诊断模式会改变 CCL 的 `min_area` 参数并在 Python 侧多做一次过滤，分量数会增多；默认关闭时行为不变。RLE 诊断路径未在板上测量 CPU/内存，也未做端到端实料 A/B。

### P1 · 有界轨迹管理（`a86e1819`）

来源：主工作区未提交的 `stream_counter.py`；在 LJ-01 的 `_finish_track` 上实现。

- 构造参数 `max_completed_tracks=128`；`_completed_tracks = deque(maxlen=...)`。
- 轨迹完成时从活跃表移入有界保留队列；计数、历史计数和活跃跟踪语义不变。
- 诊断的轨迹视图由“保留队列 + 活跃轨迹”组成。

风险：只保留最近 128 条已完成轨迹，更早轨迹不再出现在诊断中；不影响计数。原基线 `_tracks` 无界增长（20k 帧后 1127 条），本项修复了该缺陷。

### P2 · Tracker 语义候选（`3c6a9df2`）

来源：主工作区未提交的 `stream_counter.py`；作为可切换候选移植。

- 新参数 `birth_semantics="baseline" | "band_gate"`，默认 `baseline`（与 LJ-01 完全一致）。
- `band_gate` 允许带内出生轨迹 armed，但必须观测到沿运动方向离开迟滞带（`band_exit_observed`）后才计数；带内出生与过线区出生分别记录 `born_in_hysteresis_band`、`born_past_line`、`born_counted_without_event`。
- A/B 使用 7 个合成场景、单变量控制；结果见 `docs/verification/birth-semantics-ab-20261030.md` 和 `data/results/birth-semantics-ab-20261030-01/summary.json`。

风险：合成场景中 `band_gate` 绝对误差较小（3 → 1），但在“真实件越线后带内出现碎片”场景产生 1 个**确定重复计数**，另有一个场景本身不可判定。**不得据此宣称优于基线；默认保持关闭。**

### P3 · 实料标定知识（`a668df41`）

来源：主工作区的 M6/M8 面积带、ROI/阈值标定方法和实料证据；只保留方法与证据等级，不移植数值。

- 320×240 的面积/距离参数不能直接用于 640×400（面积约 3.33×，横向距离约 2.0×，纵向约 1.67×）。
- 文档给出 640×400 的 7 步重标定流程和 A/B 限制；未生成任何 640×400 实料数值。

风险：纯文档，无运行时影响；真正的 640×400 标定尚未执行。

## 4. 回归测试

### 命令

```bash
cd /c/fpga/mc-integration
/c/fpga/material-counter/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider \
    --basetemp /c/Users/POP/mc-snapshots-tmp/pt-integration-final \
    scripts/wo0016 scripts/rle scripts/gray8 -q
```

### 结果

| 环境 | 结果 |
|---|---|
| 队友原始基线 `5b554617`（`git archive` 导出，独立目录） | **1 failed, 653 passed, 4 skipped in 99.07s**；唯一失败为 `test_live_monitor_browser.py::test_mock_browser_reset_view_health_and_stop` |
| 集成分支（修复后 HEAD） | **797 passed, 4 skipped in 123.48s**，0 failed |
| 集成分支（修复前，仅作历史记录） | 796 passed, 4 skipped, 1 failed（同一浏览器测试） |

新增测试覆盖：可观测性字段与默认关闭、ROI 裁剪与阶段计时、RLE 拆分/丢失轨迹、有界轨迹、`band_gate` 默认值与 birth 记录、7 场景 A/B 钉死。

### 剩余测试问题

1. `test_live_monitor_browser.py::test_mock_browser_reset_view_health_and_stop`
   - 根因：测试断言 `#rss` / `#cma-free`，但 `dashboard.html` 在 `9d5e1aa6`（2026-10-27，“增加板端资源监视开关与详细模式”）已把“DMA 与内存健康”面板替换为默认关闭的“板端资源监视”面板，元素被移除；该提交更新了 `test_live_monitor_http.py`，遗漏了浏览器测试。
   - 基线复现：在 `5b554617` 的独立导出目录中用相同环境/命令复现，失败行为 `#rss` element not found，与集成分支一致。
   - 处理：最小改动仅替换测试断言为当前 UI 语义（`#resource-status` 为“采样已停止”、`#resource-content` 隐藏），不改页面或生产代码。
2. `test_wo0017_prelaunch.py::test_formal_prelaunch_rejects_permit_marked_consumed`
   - 一次历史基线全量运行中失败（`2 failed, 652 passed, 4 skipped`），实际错误为 `stage B-2 READY guard identity/status does not match this run`，早于预期的 permit 校验。
   - 隔离运行：`9 passed in 0.45s`；随后同一基线独立全量运行和集成分支全量运行均通过。
   - 结论：**间歇性/顺序相关问题，未定位根因，也未修复**；不能把“这次通过”当作已修复。后续若再次出现，应先复现并比对 guard 内容与重算 identity。

## 5. 性能对照（主机微基准）

脚本：`scripts/benchmarks/tracker_bench.py`；结果：`data/results/tracker-bench-20261030-01/summary.json`。
环境：CPython 3.12.10，Windows 11，AMD64，24 逻辑核；1 次预热 + 3 次交错计时取最小值；输入固定。

| 场景 | 帧数 | 基线 µs/帧 | 整合 µs/帧 | 变化 | 基线 `_tracks` | 整合 `_tracks` / 保留 | 峰值 traced |
|---|---:|---:|---:|---:|---:|---:|---:|
| replay-2k | 2,000 | 38.60 | 44.54 | **+15.38%** | 114 | 3 / 111 | 136.0 → 162.3 KiB |
| replay-20k | 20,000 | 266.49 | 75.00 | **−71.86%** | 1,127 | 3 / 128 | 928.2 → 184.8 KiB |
| masks-2k | 2,000 | 348.15 | 359.71 | +3.32% | 114 | 3 / 111 | 328.9 → 333.0 KiB |

解读：

- 2k 回放的固定开销来自每条轨迹的 `_track_record` 调用，约 6 µs/帧；相对 25 FPS 帧预算可忽略，但确实存在。
- 20k 回放中基线 `_tracks` 无界增长并每帧全量扫描，整合版保留 3 条活跃 + 128 条已完成，耗时下降约 72%，峰值内存下降约 80%。
- 掩膜路径由 CCL 主导，差异接近噪声。
- **以上是主机 CPython 微基准，不是 PYNQ 板端吞吐、CPU 占用或 DMA 证据。** 脚本不属于 pytest 回归。

## 6. 本阶段新增提交

| 提交 | 内容 |
|---|---|
| `305fe3e9` | `docs(adr)`：新增 `docs/adr/adr-0006-gray8-active-protocol.md`；`docs/02_interfaces.md` 仅补充协议取舍与 ADR 链接 |
| `32ff6f59` | `perf(bench)`：`scripts/benchmarks/tracker_bench.py`、README、`data/results/tracker-bench-20261030-01/summary.json` |
| `87adcc2e` | `test(wo0016)`：浏览器测试两行断言更新为当前资源面板语义 |
| 本文件所在提交 | `docs(verification)`：本纪要 |

## 7. 协议决策与 ADR 编号

- 新架构 320×240 Gray8 活动协议为 `GRAY8_PACKET_V1`（Header `0x47525938`、Trailer `0x454E4438`、End word `0xC0DEC018`）；旧 `WO14`（`0x574F3134` / `0x454E4434` / `0xC0DEC014`）仅保留为历史实验依据。
- 本分支 ADR 编号为 0001–0005，新增 0006；未覆盖、删除或改写任何既有记录。
- **编号分叉风险**：`main` 的 `adr-0004-gray8-interface-a-baseline.md` 是状态为 Accepted/Frozen 的另一份 ADR，与本分支 `adr-0004-gray8-dma-input.md` 并存。合并 `main` 前必须先解决 ADR 编号与语义分叉，且不得覆盖冻结记录。
- 本轮未修改 RTL、DMA 协议实现、BIT/HWH 或板端环境；640×400 RLE 线格式不变。

## 8. 尚未执行的验收

- RLE 端到端（真实 RLE 帧输入 → `RleCounter` → 跟踪计数）及诊断对照。
- 实板资源（LUT/FF/BRAM/DSP、时序）、板端 CPU/内存、DMA 安全复测。
- 真实物料准确率（漏计/重复计数）、640×400 重标定。
- 任何板端 BIT/HWH 下载或运行。
- 整合分支的板端验收本身。

## 9. 合并 `main` 的验收门禁

1. 全量回归通过；基线已知失败要么已修复，要么有可复现根因和明确豁免决定，不能仅凭“基线也失败”豁免。
2. RLE 端到端回放完成，诊断开启/关闭的检测集合一致，阶段耗时无不可接受退化。
3. 板端验收完成：位流/HWH 未变且签名匹配；Gray8 与 RLE 路径 DMA 正常、fault=0、buffer 释放；记录真实帧率与资源占用。
4. 真实物料 640×400 标定与准确率验收完成（人工真值，分别统计漏计与重复计数）。
5. `band_gate` 保持默认关闭，除非现场 A/B 证明漏计收益且无新增重复计数。
6. ADR-0006 与 `docs/02_interfaces.md` 通过三人评审；`main` 的 Frozen ADR-0004 不被覆盖或静默改写。
7. 通过 PR 合并，禁止 force push、禁止直接修改 `origin/LJ-01`。