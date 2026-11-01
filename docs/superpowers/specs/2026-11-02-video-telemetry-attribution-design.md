# 视频遥测归因缺口补全设计

日期：2026-11-02

## 意图

补齐 PL 预览运行时绘图与 HDMI 提交之间的遥测，让低提交率可以拆分为面板发布频率、绘图处理、输出等待和提交耗时。当前现场快照的 `compose_ms` 无有效样本；`preview_submitted_fps` 在 PL 模式记录的是 PL 接受帧率，不能代表软件绘图输入速率。

## 目标

1. 将输入共享帧实际的 `published_ns` 传到对应的绘图输出帧，并在 VideoPump 的同一帧遥测中写入 `publish_ns`、`compose_done_ns` 和 `submit_done_ns`，使 `compose_ms`、`submit_ms` 与 E2E 使用有效边界。
2. 将现有 `lease.compose_ns` 作为 `compose_work_ms` 单独汇总。`compose_ms` 表示输入发布至绘图输出发布的总延迟，包含输入等待；`compose_work_ms` 表示 `composer.render()` 实际墙钟耗时。
3. 在低频遥测样本中分别给出 `compositor_input_fps`、`drawing_output_fps`、`pl_preview_accept_fps` 和现有 HDMI `display_fps`。保留兼容字段 `preview_submitted_fps` 原有的模式相关含义，界面使用新字段的明确名称。

## 方案

- **A｜先补测量（选定）**：传递源帧发布时间，复用已有 `compose_ns`，并按现有低频采样点统计输入、绘图输出和 PL 接受速率。可直接判断时间花在发布等待、绘图还是 HDMI 提交；不改变运行策略。
- **B｜先优化绘图**：立即调整当前工作树的文字测量和仪表盘重绘。工作区的绘图源码已有未提交修改，而板端快照绑定的是已提交基线；旧候选短测只有一个绘图样本，无法证明当前稳态热点。
- **C｜改调度或发布策略**：调整 OpenCV 线程数、CPU 亲和性、面板 gate 或相机速率。会改变算力分配或面板刷新行为，现有数据尚不足以选择参数。

选择 A 后再依据新遥测决定是否推进 B 或 C。

## 实现边界

- 在 `PreviewLease` 暴露其输入 `FrameLease.published_ns`；绘图进程发布输出帧时，将该时间戳写入新的可选 `source_publish_ns` 槽位。默认值为 0，零值按 unknown 处理。
- VideoPump 使用输出 lease 的 `source_publish_ns` 与 `published_ns` 组成阶段遥测，并以 `compose_ns` 记录绘图工作耗时；帧身份继续使用现有 session UUID 与 frame ID。
- 在已有约 2 秒采样点按计数器增量计算速率，不新增逐帧 JSON、线程或无界队列。缺失时间戳或窗口计数无效时返回 unknown。
- 现有 `preview_submitted_fps`、`display_fps` 字段含义保持兼容。明确展示 PL 接受、软件合成器输入、绘图输出和 HDMI writeframe 提交四种速率；不把 writeframe 提交率描述为屏幕扫描率。
- 不改相机请求帧率、PreviewPublishGate、CPU 亲和性、OpenCV 线程数、显示内容、计数算法或 PL/RTL 接口；不在本阶段部署板端。
- 保留工作区现有未提交修改，尤其是 `integrated_run.py` 与 `compositor.py`。

## 验收条件

- 有效的输出帧能形成同帧 `publish_ns → compose_done_ns → submit_done_ns`，阶段统计仅在时间戳有效时计数。
- `compose_work_ms` 来源于已有 `compose_ns`；与 `compose_ms` 分开，能够区分绘图工作时间和从输入发布开始计算的等待/处理总时间。
- 四种速率有独立、明确的字段和界面名称；兼容字段不改名、不改变含义。
- 所有统计保持有界、低频聚合；本设计不承诺性能提升，也不据单窗数据调整运行策略。
