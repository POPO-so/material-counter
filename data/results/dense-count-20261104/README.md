# 密集计数软件优化验证（2026-11-04）

任务分支 `perf/密集计数优化-20261104`，基线 `5438e0e1f737fc426224f285a4c8b2454d225efc`。这是源码优化、本地 CPU A/B 和合成逐件回归记录；没有部署、重启板端服务、重载 PL 或进行实物准确率验收。

## 已落实的修改

1. `components.local_mask` 批量解码并生成密集 run 掩膜，保留少量 run 的轻量路径；不改原生库 ABI。
2. 大于 4 个种子使用一次 `distanceTransformWithLabels`；2–4 个保留原有 tie 行为。递归分支已证明连通后，外层保留面积/覆盖/重叠门禁，消除重复的子块 CCL。RLE 集成边界仍验证最终子块。
3. `field-v1` 增加最多 2 次完整拆分、65536 父框像素和 8 ms 累计调用准入预算。计时含掩膜、拆分、子块验证和失败后的父团孔洞分析。靠近线与等待年龄共同决定优先级；超预算未知父团保留为拒绝/未解析候选，不猜成一件。
4. 0.15 秒重试窗口内，只在父团互为唯一匹配、当前平移归一化 run 序列逐字节相同时复用已验分区统计或孔洞证据。缓存不存子掩膜，不跨歧义归属复用；形状变化和清零均使缓存失效。超预算而形状仍精确相同的已验统计可以继续使用，同时明确记录延后。
5. 父团速度、预测、连续确认和寿命统一采集时间，支持有界帧号断档；0.5 秒最大关联间隔、当前 1.2 秒父团寿命仍有界。无时间模式维持帧号行为。正常计数与补计仍共用 `counted_event` 去重。
6. 快照保存 `split_stats`、`split_diagnostics`，预算延后可独立触发限频证据；修复将正常等待上下文误判为写入 backlog、从而产生额外快照的问题。
7. 既有 `performance_replay` 支持快照 v1/v2、完整逐帧耗时与采集间隔汇总、父团恢复诊断及可选逐件真值核对；标注和源码均绑定 SHA，冷窗口边界保持明确。

## 同输入 CPU A/B

环境：Windows AMD64，CPython 3.12.10，NumPy 2.3.5，OpenCV 4.10.0；基准进程固定 OpenCV 单线程。前后复用同一个原生库、同一份基准驱动和相同输入，报告中的 SHA 已逐项核对。

| 指标 | 原版 | 优化后 | 变化 |
|---|---:|---:|---:|
| 真实静态帧 5298，完整拆分 P50 | 3.353 ms | 2.409 ms | 降低 28.2% |
| 真实静态帧 5307，完整拆分 P50 | 3.452 ms | 2.531 ms | 降低 26.7% |
| 16 孔压力样本，完整拆分 P50 | 9.984 ms | 5.205 ms | 降低 47.9% |
| 合成密集序列，区域处理 P95 | 6.111 ms | 1.624 ms | 降低 73.4% |
| 合成密集序列，区域处理最大值 | 10.355 ms | 6.943 ms | 降低 33.0% |
| 合成密集序列，跟踪 P95 | 0.517 ms | 0.512 ms | 基本持平 |
| 合成密集序列，跟踪最大值 | 0.661 ms | 1.116 ms | 单次尾部样本变大，未声称跟踪提速 |

静态样本各测 20 次。两个真实父块均拆为 14 块，逐子块二值 SHA 与旧版相同；16 孔压力样本前后都保守保留 6570 像素父块，返回掩膜 SHA 也相同。此等价性只覆盖这三个固定输入，不代表所有多种子形状的边界均不变。

合成序列共 125 帧：5 波，每波 10 个双环父团，合计 100 个有身份的合成物料，采集时间步长固定为 100 ms。旧版和优化后均为 100 个事件、100 件唯一匹配，每件恰好一个事件，没有靠总数相等代替逐件核对。优化后每帧最多 2 次完整拆分，包含 640 次任务延后（包括已验几何仍可复用的延后）。这不是实物 100 件验收，也不能用于推导板端 FPS 或相机丢帧。

原始证据：[原版](baseline.json)、[优化后](current.json)、[比较汇总](comparison.json)。输出保留每帧、每件及每个事件明细。

## 验证与已知基线失败

组合回归实际结果见 [pytest.txt](pytest.txt)：870 passed、1 skipped、3 failed，退出码 1。跳过项为 Windows 无法创建 symlink 的打包测试。三个失败均在本次未修改的原分支上单独重跑并复现：

- `test_dashboard_prefers_fresh_video_telemetry_and_marks_stale_unknown`：旧仪表盘字段预期不符。
- `test_preview_and_input_backend_cli_defaults_and_candidate_values`：旧测试期待 software，当前默认为 pl。
- `test_camera_read_after_is_a_repeatable_peek_without_consumer_state`：`__new__` 夹具缺少 `_decoded`。

本任务修改与新增测试最终定向集为 **125 passed**，退出码 0，见 [targeted-pytest.txt](targeted-pytest.txt)。基准 CLI 必须在逐件合成核对失败时返回非零，而非仅输出一个总数。不能将上述组合回归写成全绿。关键不变量另经只读 Worker 走查，未发现 Critical/Important 问题；该走查不替代测试。

组合命令：

```powershell
python -m pytest scripts/rle scripts/standalone scripts/wo0016/test_guarded_multiline_counter.py scripts/wo0016/test_predictive_association.py scripts/wo0016/test_split_touching_optimizations.py scripts/wo0016/test_split_connectivity.py scripts/wo0016/test_time_motion.py scripts/wo0016/test_run_stream_counter.py -q --tb=short -rs
```

复测优化版本（Windows）：

```powershell
python -c "from scripts.rle.components import build_native; build_native('.runtime/dense-rle.dll')"
python -m scripts.standalone.dense_benchmark --native-lib .runtime/dense-rle.dll --output .runtime/current.json --repeats 20
```

原版 A/B 用 `git archive 5438e0e1` 导出 `scripts/rle`、`scripts/wo0016`、`scripts/standalone`、`scripts/gray8` 到独立临时目录；用该目录作为 Python import 根，通过 `runpy.run_path` 执行本分支同一份 `dense_benchmark.py`，复用同一原生库。不可只切换配置来冒充原版对照。

## 风险和未覆盖范围

- 本轮没有板端 FAST 部署；只有显式现场验证任务才切换运行应用。本地改动不会自动改变正在运行的板端版本。
- 时间预算只能限制下一次完整拆分的准入，无法抢占一个已开始的 OpenCV 调用。父块硬上限、像素预算和次数预算共同约束；8 ms 等参数尚需 ARM 实测标定。
- 存在拆分候选时仍保留整帧原生 RLE CCL 回退，未在本轮引入候选裁剪与 PL 特征混合关联，以避免父框重叠造成错误归属。性能收益主要来自拆分内部和重复工作的减少。
- 并发父团 4 个、可见孔数最多 16 个等观测边界仍在。持续超载、完全遮挡或无法唯一关联仍可能留下未解决项，不能根据孔数猜计。
- 当前没有带逐件标注的连续实物密集录像；板端处理帧采集间隔 P95/最大值、真实漏计/重复率和持续吞吐仍待现场对照。回放输出已能保存这些分析所需的时间与事件证据。
- 多种子标签方法在未覆盖形状的距离并列边界可能与旧版不同；保留连通、覆盖和形状门禁，并由实物真值决定参数是否可放行。
