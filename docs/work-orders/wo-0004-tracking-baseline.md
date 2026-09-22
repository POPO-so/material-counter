# WO-0004 · Tracking Baseline

| 项 | 内容 |
|---|---|
| **状态** | `OPEN` —— 第一轮并行开发任务，待负责人执行 |
| **负责人** | 成员 B |
| **前置** | `MASTER_PLAN.md`；`docs/00_scope.md`；现有 `data/synthetic/` GT；现有 Python 测试环境 |
| **性质** | 1～3 天的 PC Python 基线；不接 FPGA、不做复杂遮挡算法 |

## Objective

在 PC Python 环境中，根据现有 synthetic dataset 的逐帧目标位置建立最小跨帧跟踪和越线计数 baseline，验证“位置关联 + track ID + 状态管理 + 防重复计数”这条 PS 侧思路是否能被自动化测试复现。

本 WO 不实现图像检测。跟踪器接收的是目标特征/检测记录；在端到端 GT 测试中，必须去掉 GT 的 `object_id` 后再交给关联器，避免把真值 ID 偷渡成算法输入。

## Background

现有 GT 位于 `data/synthetic/<dataset>/<condition>/ground_truth.json`，包含逐帧目标的 `centroid_x`、`centroid_y`、边界框、可见性等字段。当前系统口径要求 PL 输出低数据量目标特征，PS 再做跨帧关联、运动状态管理和越线计数；`encoder_speed` 目前仍是 Planned，不应被假设为已经可用。

第一版基线只利用相邻帧位移有限这一场景约束，采用可解释、无第三方重型算法依赖的确定性方法。它的价值是提供一个可以被 synthetic GT 和回归测试检查的参考行为，不是最终抗遮挡跟踪器。

## In Scope

- 从逐帧检测记录读取中心点和必要的边界框信息。
- 基于上一位置或简单匀速预测的最近邻一对一关联，并设置最大允许位移阈值。
- 确定性的 `track_id` 分配、匹配、未匹配和结束状态管理。
- 可配置的竖直 crossing line，例如 `line_x`，以及明确的运动方向判定。
- 每个 track 最多产生一次 crossing event，支持防重复计数。
- 使用小型人工 fixture 和现有 GT 的自动化测试。
- 记录误匹配、短时单帧缺失等简单边界行为；不引入复杂遮挡恢复。

## Out of Scope

- 图像读取、分割、滤波、形态学、连通域或 FPGA RTL。
- Kalman filter、Hungarian algorithm、外观特征、深度学习和复杂遮挡/合并/拆分处理。
- 使用 GT `object_id` 作为 tracker 的输入或匹配依据。
- 摄像头实时流、PYNQ-Z2 部署、DMA、显示、编码器测速和性能宣称。
- 修改现有 `python/synth/` 生成器、PNG、GT 或既有 synthetic regression 语义。

## Deliverables

负责人在自己的分支提交以下文件，集中在 `python/refmodel/`，避免与 PL 和硬件 POC 冲突：

1. `python/refmodel/tracking_baseline.py` 或同等清晰命名的纯 Python 实现。
2. `python/refmodel/tests/test_tracking_baseline.py`，包含单元测试、边界测试和 GT 驱动测试。
3. `docs/verification/wo-0004.md`：输入字段、参数、状态转移、测试命令和结果摘要。
4. 必要的最小 `__init__.py` 或 fixture；不得复制整个 dataset，也不得提交运行生成的缓存。

## Step-by-step Tasks

1. 从当前 `main` 创建建议分支 `feat/tracking-baseline`，先检查 GT 的真实 schema 和现有 pytest 约定。
2. 定义 tracker 输入和输出的最小 Python 数据结构。至少写清楚 `frame_id`、位置、`track_id`、track 状态、`crossed`/event 和计数结果的含义。
3. 实现确定性的一对一最近邻匹配：按距离和稳定的 tie-break 顺序处理；超过最大位移阈值的检测不得强行匹配。
4. 实现最小生命周期，例如 `tentative`、`confirmed`、`lost`、`finished`；只允许在规定的前向穿越条件成立时生成一次 event。
5. 编写不依赖 GT ID 的人工 fixture：单目标穿线一次、目标未穿线、两个目标不应互相抢占、连续多帧在穿线后不重复计数。
6. 编写简单一帧缺失测试。测试应明确这是短时漏检边界，不得把它宣传为通用遮挡恢复。
7. 编写 GT adapter：读取 `ground_truth.json`，只保留可见目标的几何字段，显式丢弃 `object_id` 后送入 tracker。
8. 对至少三个现有 condition 运行 GT 驱动测试；根据 GT 轨迹独立计算期望穿线事件，比较总计数，并重复运行一次检查结果确定性。
9. 记录线位置、方向、最大位移、确认帧数和丢失容忍度等参数；不要偷偷加入 `encoder_speed` 或未来复杂预测逻辑。
10. 只提交 `python/refmodel/` 和本 WO 验证文档，不能改动 `python/synth/`、GT、图片或另一成员负责的目录。

## Acceptance Criteria

- [ ] `pytest` 能在干净的 Python 环境中运行该 WO 测试，全部通过并有明确输出。
- [ ] 关联器不读取或使用 GT `object_id`；测试中有断言或代码审查证据证明输入已去掉该字段。
- [ ] 至少覆盖：小位移匹配、track ID 稳定、未匹配/结束状态、前向穿线和穿线后的防重复计数。
- [ ] 两个检测不会在同一帧被分配给同一个活动 track；匹配规则和 tie-break 可复现。
- [ ] 人工 fixture 能验证一次穿线只计数一次，未穿线目标不计数。
- [ ] 至少三个真实 GT condition 的期望计数与 tracker 输出一致；若某 condition 没有穿线，必须把 0 作为明确结果而不是跳过。
- [ ] 连续运行两次输出完全一致，测试不修改现有 GT、PNG 和 synthetic generator。
- [ ] 验证文档明确标出该实现为 Baseline/Implemented，仅限 PC Python；复杂遮挡、实时部署和 `encoder_speed` 仍为 Planned/Out of Scope。

## Tests

建议运行：

```text
pytest python/refmodel/tests -q
pytest python/synth/tests -q
```

验收者还应检查：

- 测试是否真的从 GT 读取轨迹，而不是把 GT ID 直接喂给 tracker。
- `git diff --check` 是否通过。
- 测试前后 `data/synthetic/`、`python/synth/` 无意外变化。
- 失败时能指出是关联、状态机、线穿越还是 GT adapter 问题，而不是只输出一个最终总数。

## Risks

- 若把 GT `object_id` 传入实现，测试会产生虚假的“跟踪成功”；这是本 WO 的首要审查点。
- 线位置和方向若未记录，计数结果无法复现；所有参数必须显式配置。
- 最近邻在目标极度接近时可能交换 ID；这是 baseline 的已知限制，不应在本 WO 内扩展成复杂算法。
- synthetic GT 的 `visible=false` 只用于构造简单漏检边界；不能据此声称已经解决遮挡。

## Dependencies

- 现有 `data/synthetic/h3_full/` 目录和 `ground_truth.json`。
- 现有 `python/synth/tests/test_generate.py` 及当前 pytest 环境。
- 不依赖 Vivado、PYNQ-Z2、摄像头、编码器或 WO-0003/WO-0005 的代码。

## Suggested Branch

`feat/tracking-baseline`
