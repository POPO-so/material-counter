# 性能基准（手动运行，不属于 pytest 回归）

`tracker_bench.py` 在本机 CPython 上对照原队友基线（默认 `5b554617:scripts/wo0016/stream_counter.py`）与当前集成分支的跟踪器，用于观察跟踪状态机的每帧开销和长时间运行后的轨迹保留量。

它不是板端吞吐测试：不测量 DMA、相机 FPS、PL 资源或 PYNQ CPU。板端性能仍需独立验收。

## 复现

在仓库根目录运行：

```bash
.venv/Scripts/python.exe scripts/benchmarks/tracker_bench.py
.venv/Scripts/python.exe scripts/benchmarks/tracker_bench.py --repeats 5 \
    --json-out data/results/tracker-bench-20261030-01/summary.json
```

对照其他基线代码：

```bash
.venv/Scripts/python.exe scripts/benchmarks/tracker_bench.py \
    --baseline-script /path/to/stream_counter.py
```

## 固定配置

- 输入：3 条固定检测框轨迹（10×10，100 px，4 px/帧纵向运动）与同构掩膜；分辨率 320×240，计数轴 `y`，计数线 `y=120`，全 ROI，`min_area=12`。
- 场景：`replay-2k`、`replay-20k`（检测回放）、`masks-2k`（二值掩膜）。
- 每个模块 1 次不计时预热；计时重复默认 3 次，交错执行取最小值。
- 计时范围只包含 `MaskCounter.update_detections` / `MaskCounter.update` 调用；输入构造与内存测量不计时。

## 结果解读

- `us/frame`：跟踪器每帧耗时，越低越好。
- `frames/s`：由计时区间换算，仅用于对照，不是相机或板端 FPS。
- `tracks`：运行结束后 `_tracks` 的长度。基线不清理已完成轨迹，长序列会持续增长；整合版只保留活跃轨迹。
- `retained`：整合版 `_completed_tracks` 的有界保留数量；基线无此字段，显示 `-`。
- `peak_KiB`：独立 `tracemalloc` 测量，有额外开销，不能与计时数字直接比较。

## 与 pytest 的关系

脚本文件名不是 `test_*.py`，也不会被 `scripts/wo0016 scripts/rle scripts/gray8` 的回归命令收集；请不要把它加入默认测试门禁，避免机器负载造成不稳定失败。