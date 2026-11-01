# 视频遥测归因缺口补全实施计划

## Goal

补齐绘图输入发布至 HDMI `writeframe` 成功返回之间的同帧时间边界，并分别报告软件合成器输入、绘图输出、PL 预览接受和 HDMI 提交速率，使下一次板端快照可以区分等待、绘图和提交阶段。

## Architecture

- `PreviewInputs` 继续使用现有三槽共享帧池；输入帧的共享发布时间由 `PreviewLease` 暴露，并随对应绘图输出帧写入可选的 `source_publish_ns` 元数据。
- VideoPump 将该源发布时间、绘图输出发布时间和 `writeframe` 返回时间写入同一帧的 `FrameTelemetry`。现有 `compose_ns` 作为独立的绘图工作时长统计，不与包含排队时间的 `compose_ms` 混用。
- 主循环在现有低频遥测采样点以累计计数器增量计算合成器输入、绘图输出和 PL 接受速率。缺少 PL 预览或计数器/采样时间倒退时，速率显示 unknown。
- BTN3 的 PS/丢帧详情页使用明确名称展示四种速率；旧 `preview_submitted_fps` 和 `display_fps` 字段保持现有兼容语义。

## Tech Stack

Python 3、`multiprocessing` 共享数组、NumPy、现有 `TelemetryWindow` 与 `DashboardComposer`。不增加依赖、线程、逐帧报告或无界缓存。

## Spec

依据 [视频遥测归因缺口补全设计](../specs/2026-11-02-video-telemetry-attribution-design.md)。保持帧 session UUID / frame ID 配对和零时间戳 unknown 约定；不改变相机/面板发布频率、绘图内容、CPU 调度、计数算法、PL/RTL 接口或板端部署状态。

## Global Constraints

- 在隔离分支 `feat/video-telemetry-attribution` 的 worktree 中实施；保留工作区已有用户修改，不清理、覆盖或提交无关变更。
- 按项目 Manager–Worker 规则由一个 `luna_worker` 完成边界清晰的代码修改，Manager 检查 diff 与口径。
- 当前用户尚未要求运行测试或部署；本阶段不新增或执行测试，不部署板端。验证限于代码路径审阅、差异检查和静态检查（不运行测试命令）。
- 完成并经 Manager 验收后，仅提交本任务相关文件，保留分支和 worktree；不得 push、merge 或删除 worktree。

## Implementation Steps

1. **传播绘图输入发布时间**
   - 在 `scripts/standalone/shared_frames.py` 为 `FrameLease` 和每个共享槽增加可选 `source_publish_ns`；更新创建、pickle 状态、`publish()`、`acquire_latest()`，默认 0 并沿用 uint64 验证。
   - 在 `scripts/standalone/preview_inputs.py` 的 `PreviewLease` 暴露输入帧的 `published_ns`。
   - 在 `scripts/standalone/integrated_run.py` 的绘图子进程发布输出帧时传入 `lease.published_ns`。确保传递的是当前输入帧的发布时间，而不是输出帧生成时间。

2. **补全同帧阶段遥测与绘图工作时长**
   - 在 `scripts/standalone/video_telemetry.py` 的 `FrameTelemetry` 增加可选 `compose_work_ns` 时长字段；独立进行非负 uint64 校验，不将它作为单调时钟时间戳参与顺序比较。
   - `TelemetryWindow.sample()` 为有效的 `compose_work_ns` 生成 `compose_work_ms` 汇总，缺失/非法值不计入。
   - 在 `scripts/standalone/video_pump.py` 记录 `publish_ns=lease.source_publish_ns`、`compose_done_ns=lease.published_ns`、`compose_work_ns=lease.compose_ns` 与已有的 `submit_done_ns`。保持缺失时间戳不计入阶段统计的行为及 session/frame identity。

3. **补充分链路速率并显示**
   - 在 `scripts/standalone/integrated_run.py` 的两秒采样处，以覆盖每条 `inputs.publish()` 路径的独立累计成功发布数、绘图完成数 `drawing_counts[0]` 和新增的累计 PL 接受数计算三种独立 FPS；计数器重置/回退或时间无效时返回 unknown。保持报告中的 `published` 字段原义不变。
   - 保留目前约三秒更新的兼容字段 `preview_submitted_fps` / `binary_fps` 及其现有模式相关含义；新增字段不得复用或覆盖其语义。`display_fps` 继续表示 HDMI `writeframe` 成功提交率。
   - 在 `telemetry_page_lines()` 与必要的 dashboard 透传中，以 `合成器输入`、`绘图输出`、`PL 接受`、`HDMI 提交` 名称显示四种速率；维持已有队列丢帧信息可见，并把 HDMI 提交明确描述为软件提交完成率，不称作屏幕扫描率。

4. **Manager 验收与收尾**
   - 检查工作区差异，重点核对共享状态参数顺序、spawn/pickle 兼容、零值/unknown、相同帧的时间戳传递、速率累计计数器以及旧字段语义。
   - 按本任务约束不运行测试或部署；记录未验证的运行时效果。
   - 确认暂存范围只含本任务文件后创建本地中文提交，检查最终 Git 状态并保留 worktree。

## Review Focus

- 每个输出 lease 的 `source_publish_ns` 必须对应它消费的输入 lease，不能跨帧或跨会话串接。
- `compose_ms` 为输入发布到绘图输出发布的总墙钟延迟；`compose_work_ms` 仅汇总 `composer.render()` 的已有耗时。
- `FrameTelemetry` 的可选时长字段不参与时间戳先后约束；零发布时间不构造有效阶段样本。
- 新速率基于同一单调采样窗口与累计计数；PL 不存在时其速率为 unknown；兼容字段与 HDMI 统计定义不变。
- 修改不得超出已确认设计范围，且不触碰 RTL、部署资源或不相关用户改动。

## 实施判定

- `published` 仅累计有效预览分支；故障提示分支也会向 `PreviewInputs` 成功发布帧。因此 `compositor_input_fps` 使用独立累计量覆盖所有成功的 `inputs.publish()` 调用，避免将故障提示帧从实际合成器输入速率中漏掉；现有 `published` 报告口径保持不变。
