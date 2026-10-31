# 视频处理链路低开销统计设计

日期：2026-10-31

## 意图

在不明显影响计数性能的前提下，为现有 640×400 Gray8/RLE 视频链路提供可持续观测的统计：摄像头实际帧率、输入与输出分辨率、各流水线处理阶段的周期数或延迟、图像处理模块吞吐量、实时丢帧数、PS 特征处理耗时和整体端到端延迟。

当前显示器最上方是“本次累计计数、显示 FPS、处理延迟 / RLE + 计数”。本次设计明确替换顶部显示口径：累计计数作为业务核心值保留；“显示 FPS”和“处理延迟 / RLE + 计数”不再作为顶部主指标。顶部改为链路性能区，直接呈现实测链路指标。七类指标不能在原来三个小框里同时清晰显示，因此顶部采用“关键三值 + 链路摘要”，完整明细通过 BTN3 分页查看。

## 现状与问题

- 相机线程只保留最新帧 `(index, gray, captured_monotonic)`；消费慢时旧帧被覆盖，但没有覆盖/丢帧累计值。相机只请求 60 FPS，未回读协商帧率。
- 主循环的 `transfer_ms` 从相机读取之后开始，包含事件队列 drain 和 DMA 传输，不只是 transport；`count_ms` 包含控制处理和 `RleCounter.update_rle()`；`snapshot_ms` 是过线快照观察；`publish_ms` 包含 metadata 构造、栅格化和预览发布。
- 绘图子进程已有 `scale_ms`、`draw_ms`；输出共享帧已有 `compose_ns` 字段但发布时没有传入。VideoPump 已有 `copy_submit_ms`，只在 writeframe 成功后增加 `submitted`。
- 顶层 `display_fps` 是成功 HDMI 提交量的窗口速率，不是摄像头实际帧率，也不是显示器扫描帧率。顶层 `processing_ms` 是最近最多 30 帧的 `transfer_ms + count_ms + snapshot_ms` 均值，不是采集到提交的端到端延迟。
- 已有 PL 聚合计数器：50 MHz 时钟域下的 `total_cycles`、`active_cycles`、`input_beats`、`output_beats`、`input_stall_cycles`、`output_stall_cycles`。它们由 `snapshot_counters()` latch 后读取，约每 2 秒随报告样本采集一次。
- 当前没有相机协商帧率、相机覆盖丢帧、共享 latest-frame 跳过量、pump 独立 skip/failure 计数、每帧采集时间戳到 HDMI 提交的闭环、PL 各模块独立周期。

## 目标

1. 产出实测而非配置推断的相机输入 FPS，可区分相机覆盖丢帧。
2. 产出输入 640×400、处理预览 800×640、显示输出 1280×720 的运行时来源与时间点。
3. 产出各已有边界的窗口均值、最小值、最大值、p50/p95/p99，以及处理吞吐率。
4. 产出端到端延迟，定义为同一帧 `t_capture_monotonic_ns → HDMI writeframe 成功返回` 的耗时。
5. 产出分类实时丢帧：相机覆盖、预览发布失败、绘图图像端旧帧跳过、pump 提交失败/跳过量；无法测量时明确 unknown。
6. 在 PS 特征处理已有 `rle_regions_ms`、`track_ms` 基础上，区分检测/区域分析与跟踪关联耗时；不臆造拆分或其他未单独计时的子阶段。
7. 为 PL 各流水线模块的独立周期/吞吐预留稳定扩展点；第一阶段不改 RTL，不虚构不存在的信号。
8. 替换顶部旧口径：保留 `本次累计计数`，移除 `显示 FPS` 和 `处理延迟 / RLE + 计数` 作为主指标；顶部新增链路性能区，完整覆盖上述七类指标。

## 非目标与反目标

- 不改变计数算法、阈值、区域/跟踪参数、控制行为或计数结果。
- 不将请求的 60 FPS、HDMI 的 60 Hz、显示提交 FPS 冒充摄像头实测 FPS 或屏幕扫描 FPS。
- 不在热路径逐帧写日志、编码 JSON、计算 SHA、访问文件系统或轮询 MMIO。
- 不为每个 OpenCV 调用、每个 run、每个像素或每个跟踪操作增加计时。
- 不把“采集→writeframe 成功返回”表述为“采集→屏幕扫描完成”。
- 不在本阶段承诺或模拟 PL 模块级计数器的具体寄存器、模块名称或位宽。
- 不为了显示统计而牺牲计数主路径的正确性、故障保持或资源释放边界。

## 约束

- 统计是诊断数据。异常不得改变计数路径结果，也不得让主循环因统计失败而停止。
- 热路径只允许 O(1) 计数、固定长度环形记录、`time.monotonic_ns()`/`perf_counter_ns()` 读取和必要的轻量字段拷贝。
- 聚合与格式化只在现有的低频采样点执行，建议约 2 秒一次，并使用现有 `TelemetryCache` 的 period/stale 语义。
- 相机 FPS 以相机线程实际产生的帧数除以其单调时间窗口计算。可用的帧还应按消费侧首次看到的时间窗口计算“有效输入 FPS”；两者必须分开命名，不能相互替代。
- 相机覆盖丢帧按相邻相机序号差值减去已取得帧数累计；序号重置或非单调必须显式标记异常，不能产生负丢帧数。
- 端到端延迟只在同一 `frame_id + session_uuid + capture timestamp` 能沿共享帧到 HDMI 提交完整传递时计入；缺字段的帧计为 unknown，不参与分位数。
- PL 模块级计数接口必须版本化和可选。缺少模块计数器时，读取方仍能处理现有聚合计数器并按 unknown 显示。
- 新增报告字段保持向后兼容：不删除、不重命名、不改变既有字段的类型、单位或含义；新增字段只承载诊断信息。
- 本设计文档不修改 `docs/02_interfaces.md` 中已授权/候选接口的状态。若后续决定改动 Interface B 的帧尾布局或 Interface C 的寄存器语义，必须按项目规则走接口 PR、三人确认和 ADR。

## 方案选择

### A｜纯 PS 低风险版

复用现有边界和 TelemetryCache，增加相机速率/覆盖、显示闭环和阶段窗口聚合。PL 只保留现有聚合计数器。风险最小，但无法得到 PL 各模块独立周期。

### B｜PS + PL 全模块扩展版

在 PS 统计基础上，为 PL 每个流水线模块增加 start/end/busy/stall 周期寄存器。信息最完整，但需要 RTL 修改、重新综合、时序/资源回归和新的 PL→PS 控制读取，风险与工作量最大。

### C｜混合分阶段（选定）

第一阶段按 A 落地低开销 PS 统计并完成顶部替换，在 Python 数据结构、报告 JSON 和显示详细页中预留 `pl_modules` 可选扩展点。第二阶段在明确每个 PL 模块名称、信号、时钟域和位宽后，再按受控接口流程实现模块级计数器。这样可以先量化统计本身的开销和现场价值，不把 RTL 变更与第一阶段绑死。

## 设计

### 1. 每帧身份与统一时间基准

新增一个轻量、不可变的帧统计记录，随现有 frame identity 传递，不新增队列或线程：

- `frame_id`：现有相机/RLE 单调递增序号。
- `capture_monotonic_ns`：相机线程产生该帧时的 `time.monotonic_ns()`。
- `consume_monotonic_ns`：主循环第一次取得该帧时的单调时间。
- `transfer_done_monotonic_ns`：DMA/RLE transport 返回后。
- `regions_done_monotonic_ns`：`analyze_runs` 完成后。
- `track_done_monotonic_ns`：关联/跟踪完成后。
- `publish_monotonic_ns`：输入预览发布成功或失败时。
- `compose_done_monotonic_ns`：绘图子进程完成 render 时。
- `submit_done_monotonic_ns`：HDMI writeframe 成功返回时。

时间戳使用 `time.monotonic_ns()`。阶段耗时统一以纳秒保存，报告/显示时再转毫秒，避免热路径浮点运算。已有 `FrameLease` 的 `scale_ns`、`draw_ns`、`compose_ns`、`published_ns` 保留；本设计补齐 `capture_monotonic_ns` 和 `submit_monotonic_ns` 的传递，不改变现有字段含义。

跨进程传递使用现有共享帧元数据。若现有固定元数据容量不足以容纳新增时间戳，采用固定长度二进制时间戳槽而不是扩大 JSON metadata；优先级为：扩展 `SharedFrames` 的固定 `RawArray` 元数据 → 在 preview metadata 中传递单个整型 capture 时间戳 → 若两者都不可行则端到端延迟明确为 unknown，而不是退回逐帧 JSON。

### 2. 相机实际帧率与丢帧

在 `LatestCamera._capture()` 维护：

- `captured_total`：相机线程实际取得的帧数。
- `last_capture_index`、`last_capture_monotonic_ns`。
- `camera_overwrite_drops`：两次消费间隔内，相邻 `index` 跳变量减去 1 的累计和。
- `capture_interval_ns_sum/min/max/count`：窗口聚合所需的最少状态。

主循环在成功 `camera.read()` 后统计“有效输入帧数”。低频采样时计算：

- `camera_fps = captured_delta / elapsed_s`。
- `effective_input_fps = consumed_delta / elapsed_s`。
- `camera_overwrite_drops` 增量。
- `camera_sequence_errors` 增量，用于序号重置/非单调等无法可靠计算丢帧的事件。

不使用 V4L2 `CAP_PROP_FPS` 的请求值作为实测值。若后续单独增加 V4L2 协商回读，只作为 `camera_negotiated_fps` 独立字段。

### 3. 阶段耗时与吞吐量

复用已有边界，不新增逐操作计时：

- 相机读取等待：`consume_monotonic_ns - capture_monotonic_ns`。
- 帧进入主循环到 transport 返回：复用现有 `frame_start`、`transfer_end`，但报告名区分 `queue_transfer_ms`，避免继续把 `transfer_ms` 误读为纯 DMA。
- PS 总处理：`track_done_monotonic_ns - transfer_done_monotonic_ns`。
- PS 区域/检测：复用 `rle_regions_ms`。
- PS 跟踪关联：复用 `track_ms`。
- 快照观察：复用 `snapshot_ms`，并明确为可选诊断开销。
- 栅格化与预览发布：复用 `publish_ms`，后续可仅在有预算时再细分。
- 绘图合成：复用 `DashboardComposer.last_timings_ms.scale_ms`、`draw_ms`；新增 `compose_ns` 表示 render 总时长并写入现有 `FrameLease.compose_ns`。
- HDMI 拷贝与提交：复用 `VideoPump.timings_ms.copy_submit_ms`。
- 端到端：`submit_done_monotonic_ns - capture_monotonic_ns`。

吞吐量：

- 相机输入 FPS：相机线程帧数/墙钟。
- 有效输入 FPS：主循环取得帧数/墙钟。
- 处理 FPS：主循环完成 RLE 计数帧数/墙钟；沿用现有 `processing_fps` 但明确其口径。
- 绘图 FPS：drawing 成功发布输出帧数/墙钟。
- 显示提交 FPS：VideoPump 成功 writeframe 数/墙钟；保留现有 `display_fps` 含义，但不再作为顶部主指标。
- PL RLE 活动率：`active_cycles / total_cycles`。
- PL 输入/输出 beat 率：窗口内 beat 增量除以 50 MHz 周期增量。
- PL 输入/输出 stall 占比：对应 stall cycles / total_cycles。

### 4. 丢帧分类

每个类别独立累计，互不抵消，也不把不同原因的丢帧混成一个总数：

| 字段 | 含义 | 来源 |
|---|---|---|
| `camera_overwrite_drops` | 相机线程已产生但被更新帧覆盖，未由主循环取得的帧 | `LatestCamera` 序号差 |
| `preview_publish_drops` | 主循环无法发布 Gray8/二值预览 | 现有 `dropped`，报告名 `dropped_preview` |
| `drawing_output_drops` | 绘图子进程无可用输出槽 | 现有 `drawing_counts[1]` |
| `shared_input_skips` | `PreviewInputs` 消费端因 latest-frame 策略跳过的旧 READY 帧 | 需在 `SharedFrames.acquire_latest()` 增加累计，不返回旧数据 |
| `pump_submit_failures` | HDMI `writeframe` 抛错/超时导致 `DISPLAY_FAIL_HOLD` | VideoPump 失败点 |
| `pump_skips` | pump 因 latest-frame 策略没有提交的旧帧 | 可从 `FrameLease.sequence` 差额估计，但需明确不等于屏幕丢帧 |

`rle_invalid_frames` 保持现有 `invalid_frames` 语义，单独报告，不归入视频丢帧。

### 5. 顶部链路性能区与分页详情

顶部显示口径正式替换，规则如下：

1. **保留**“本次累计计数”，位置和业务含义不变。
2. **移除**“显示 FPS”和“处理延迟 / RLE + 计数”作为顶部主指标；这两个值如仍需诊断，只进入详细页或报告，不再占用顶部主框。
3. 顶部主区改为四个关键值：
   - 本次累计计数；
   - 摄像头实际帧率，单位 FPS，来源 `camera_fps`；
   - 整体端到端延迟，单位 ms，来源同一帧采集到 HDMI `writeframe` 成功返回；
   - 实时丢帧数，来源分类丢帧窗口增量，默认显示相机覆盖 + 显示链丢帧合计，详细页拆分。
4. 顶部主区下方增加一条链路摘要，直接覆盖分辨率、阶段、吞吐和 PS 特征耗时：
   - 输入/输出分辨率链：`640x400 → 800x640 → 1280x720`，并标明运行时实际值/配置值/未知；
   - 阶段周期或延迟：先显示 PL 聚合 `active/total cycles`、`transfer_ms`、`count_ms`、`compose_ms`、`submit_ms`；模块级周期在第二阶段前显示 unknown；
   - 图像处理模块吞吐量：显示处理 FPS、显示提交 FPS、PL beat 率，单位明确；
   - PS 特征处理耗时：显示 `rle_regions_ms` 和 `track_ms`，可给最近窗口均值/p95。
5. BTN3 进入链路性能分页，分页至少为：
   - P0 链路总览：七类指标摘要；
   - P1 PL 阶段与周期：聚合计数器、stage 窗口统计、模块级 unknown/第二阶段占位；
   - P2 PS 与丢帧：区域分析、跟踪关联、快照开销、分类丢帧明细。
6. 缺失、过期或非法统计值显示 `--` 或“过期”，不得显示 0 冒充有效值。未知的 PL 模块级周期必须显示 unknown，不能显示为 0。
7. 顶部替换改变现有可见布局，但不改变 1280×720 输出、帧身份标记、HDMI 提交和计数主路径；布局实现需回归文字、画中画、计数线和底部资源条区域不互相遮挡。

### 6. PL 模块计数器扩展点

第一阶段不改 RTL。新增模型和报告字段：

```text
pl_counters = {
  schema: "material-counter.pl-counters.v1",
  clock_hz: 50_000_000 | null,
  aggregate: { total_cycles, active_cycles, input_beats, output_beats,
               input_stall_cycles, output_stall_cycles },
  modules: [
    { module_id, stage, start_cycles, end_cycles, busy_cycles,
      input_beats, output_beats, input_stall_cycles, output_stall_cycles,
      clock_hz, valid, unknown_reason }
  ],
  coverage: "aggregate-only" | "partial" | "complete"
}
```

兼容规则：

- `modules` 缺省或为空表示未知/未实现，不表示模块周期为 0。
- 现有 `counters_before/after`、`samples[].counters`、`counter_delta` 保持原字段含义。
- 只有模块名称、寄存器映射、位宽、时钟域和并发 latch 语义全部确认后，才允许填入 `modules`。
- 若第二阶段的模块计数器需要改变 Interface B/C，按 `docs/02_interfaces.md` 走接口 PR、三人确认和 ADR，不在本设计内授权。
- 读取周期保持每 2 秒一次，禁止逐帧 MMIO。

### 7. 失败与边界行为

- 缺失、过期或非法统计值不参与聚合；标记为 unknown/stale。
- 统计计数器自身异常只影响该指标，不改变计数主路径的返回值。
- 相机序号回退/重置计为 `camera_sequence_errors`，不伪造丢帧数。
- 显示链未收到同一 frame identity 时，E2E 延迟记 unknown。
- 停机时报告仍输出已有字段；新增可选字段可缺省。
- 顶部显示替换失败或新指标未知时，保留累计计数和既有计数显示，不能因统计显示异常影响计数。

## 验收

1. 单元测试证明：相机覆盖丢帧、序号异常、窗口 FPS、显示闭环延迟、分位数、stale 行为都按本文口径。
2. 单元测试证明：没有有效 E2E 样本时不输出 0 或伪值；共享帧缺少时间戳时只影响该指标。
3. 显示测试证明：顶部已移除“显示 FPS / 处理延迟 / RLE + 计数”主口径，累计计数仍正确；四个关键值与链路摘要使用实测/unknown 数据源，布局无遮挡。
4. 分页测试证明：BTN3 可在链路总览、PL 阶段与周期、PS 与丢帧之间切换，七类指标均可达。
5. 回归测试证明：统计开启/关闭时计数结果不变；热路径没有逐帧 JSON、SHA、日志、文件 I/O 或 MMIO。
6. 性能验收：同一输入、同一场景下，统计开关做 A/B；统计不得造成明显的处理 FPS 或端到端吞吐回退。若板端无法隔离环境，只报告“未严格隔离”的实际观测，不作因果结论。
7. 现场验收：顶部和分页能读出相机实际 FPS、有效输入 FPS、输入/输出分辨率、PL 聚合/阶段周期或延迟、处理/显示吞吐、分类实时丢帧、PS 特征耗时和 E2E 延迟；PL 模块级周期在第二阶段实现前显示 unknown。
8. 兼容验收：旧报告消费者能忽略新增字段；既有字段名称、类型、单位不变；缺失新增字段时显示和报告仍可用。

## 留给实施者的决定

- 固定长度时间戳槽的具体共享内存布局与向后兼容策略。
- 窗口聚合的统计结构（环形缓冲或固定桶）和分位数算法；必须满足热路径 O(1)、低频 O(n) 且有界。
- 顶部四个关键值、链路摘要和 BTN3 分页的精确像素坐标、字号与换行；必须遵循现有 1280×720 几何和无遮挡验收。
- 第二阶段 PL 模块清单、寄存器映射与接口审批材料；不属于第一阶段实现前置条件。

## 审批状态

待用户批准。批准后下一步使用 `superpowers:writing-plans` 编写逐任务实施计划；在批准前不写产品代码、不修改 RTL、不部署板端。
