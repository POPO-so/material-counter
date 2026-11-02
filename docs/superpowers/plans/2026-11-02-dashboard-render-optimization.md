# 仪表盘渲染优化实施计划

> **For agentic workers:** 按两个有序阶段实施；每阶段先写/更新测试，运行该阶段测试，再提交。不要部署板端。

**Goal:** 降低 `DashboardComposer` 面板更新与共享输出帧绘制开销，同时逐像素保持完整有效输出。

**Architecture:** 第一步以屏幕显示值构造缓存 key，加入命中率计数并把低层诊断折叠到现有 BTN3 PS/丢帧详情。第二步持久化静态 chrome，并按三个共享输出槽的版本延迟同步；每帧仍完整生成动态预览、叠加和身份标记。

**Tech Stack:** Python、NumPy、Pillow、OpenCV、multiprocessing 共享帧槽、pytest。

**Spec:** [仪表盘渲染优化设计](../specs/2026-11-02-dashboard-render-optimization-design.md)

## Global Constraints

- 保持 1280×720 输出尺寸、现有计数/跟踪/阈值/控制行为、PL/HDMI 接口不变。
- 不改遥测采样周期，不部署板端、不重启服务。
- 所有输出槽完整有效；帧标记与每帧动态预览按现有语义更新。
- 仅改本任务相关源码、测试及项目树文档；不触碰根工作区已有修改。

## Review Focus

- 显示值舍入边界、无效值与 unknown：key 命中时画面必须与上次相同；显示值变化必须更新。
- 三个输出槽首次写入和复用：必须先同步正确 chrome，不能残留未初始化或旧面板像素。
- 面板 key 更新后尚未轮到的槽：该槽下次使用时必须追上当前版本。
- PL 预览、二值预览、计数线、检测框、跟踪文字和帧标记必须保留既有像素行为。
- BTN3 页切换需显示对应 PL/PS 遥测；总览保留现场关键计数和状态字段。

## Tasks

### Task 1：按显示精度缓存并折叠低层诊断

**Files:**
- Modify: `scripts/standalone/compositor.py`
- Test: `scripts/standalone/test_compositor.py`
- Record: 相同 100 帧本地合成负载的 `draw_ms` 与 key 命中率

- [x] 添加显示精度 key、像素值归一化、绘制帧/key 命中/key 重建计数器的测试。
- [x] 在面板渲染中对屏幕显示一位小数的遥测值统一归一化后再构造 key 与绘制；保持 unknown 和非数值占位符。
- [x] 将 `page_lines` 接入右侧详情区；总览保留现场主指标，将 CPU 来源、队列、线程诊断收进 PS/丢帧详情页。
- [x] 运行本地测试并记录基准；`test_compositor.py` 为 68 passed、4 failed。4 个失败（字体度量缓存 2 项、过时 viewport 尺寸 2 项）也存在于改动前基线；相关新增/更新断言通过。`test_integrated_run.py` 为 53 passed、5 failed，失败在既有详情页返回类型、预览默认值、旧视口坐标和未初始化相机对象断言。
- [x] 同一 Windows 本地合成负载跑 100 帧：key 变化 4 次，命中率 96%；`draw_ms` mean/p50/p95 为 0.187/0.065/0.114 ms，对比基线 2.666/2.642/3.172 ms。
- [ ] 检查差异后提交第一阶段。

### Task 2：静态 chrome 持久化与输出槽版本同步

**Files:**
- Modify: `scripts/standalone/compositor.py`, `scripts/standalone/integrated_run.py`
- Test: `scripts/standalone/test_compositor.py`
- Record: 同一负载 `draw_ms` 与逐像素等价结果

- [ ] 添加全量渲染参考图、3 个输出槽首次使用/复用、key 更新后延迟槽同步、动态区域和 marker 更新测试。
- [ ] 将 claim 得到的槽位号传入 compositor；按槽位版本仅在必要时同步完整 chrome，稳定 key 命中时跳过整帧静态复制。
- [ ] 每帧完整重写 VIEW 与当前启用的叠加层、marker；保留 PL 预览清屏和无槽位参数的兼容安全路径。
- [ ] 运行 `scripts/standalone/test_compositor.py`；记录与基线和 Task 1 相同负载下的 `draw_ms`，用多槽像素对照验证完整输出。
- [ ] 检查差异后提交第二阶段。

## 基线

- 当前 `test_compositor.py`：57 passed、10 failed；失败涉及既有详情页文本、字体测量缓存、标签、CPU/队列绘制和过时 viewport 断言。阶段结果须和该基线区分，相关失败需在验收时明确说明。
- 合成 100 帧基准：99 次面板 key 变化，命中率 1%；`draw_ms` mean 2.666 ms、p50 2.642 ms、p95 3.172 ms（本机 Windows，PL 预览合成帧）。这不是板端数据。

**Task 1 结果（本地 Windows 合成负载，非板端）：**同一 100 帧输入下，key 变化 4 次、命中率 96%；`draw_ms` mean/p50/p95 为 0.187/0.065/0.114 ms。`key_stats` 运行计数包括 warm-up 帧，warm-up 后窗口命中率按 100 帧实测为 96%。
