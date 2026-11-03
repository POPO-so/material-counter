# B 版 PS 绘制界面实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按已审阅的 B 版调整 PS 仪表盘和二值预览，完成必要的板端 FAST 部署，不改 PL。

**Architecture:** 所有画面改动集中在 `DashboardComposer` 的 Gray8 绘制路径。静态卡片由 chrome 绘制，动态数据在对应区域更新；状态点独立按 1 秒周期刷新，预览边框在每帧写入图像后绘制。

**Tech Stack:** Python、Pillow、NumPy、OpenCV、SSH。

---

## 全局约束

- 目标预览 viewport 为 `(384, 146, 512, 320)`；当前代码是 `(398, 154, 460, 288)`，本次只扩大 PS 侧缩放目标。
- 输出仍为 1280×720 Gray8；不改阈值、计数/跟踪算法、PL、bitstream、HWH、DMA/VDMA 参数或接口。
- 侧栏为 x=38/900、宽 342 px；左侧卡片高 104/55/148 px，右侧累计/按钮卡片高 213/102 px。
- 顶部四卡 x=24/336/648/960、宽 296 px；标题和值居中，最右端对齐 x=1256。
- 侧栏内容行按 20 px 行距居中；顶部 RUNNING 28 px 右对齐，指示点留 15 px 间隔并每 0.5 秒切换一次。
- 不新增或运行测试；使用 Python 语法检查、渲染预览、人工查看图像及板端最小健康检查。
- 部署只同步本次修改的 `compositor.py`，只重启对应项目应用；不加载 bitstream，不重启整板。

## Task 1: 修改 PS 仪表盘绘制

**Files:**

- Modify: `scripts/standalone/compositor.py`
- Test: 不新增或运行测试；执行下列语法和渲染检查。

- [x] **Step 1: 更新预览目标缓冲和边框**

将 `DashboardComposer.VIEW` 改为 `(384, 146, 512, 320)`，并把可复用 `_work` 缓冲改为 `np.empty((320, 512), dtype=np.uint8)`。二值图仍用最近邻缩放；在图像与叠加内容写入后、帧身份标记写入前，在 viewport 内绘制 2 px 灰色边框。

- [x] **Step 2: 更新卡片几何并居中绘制**

顶部指标使用 x 坐标 `(24, 336, 648, 960)` 和宽 296。左栏卡片位置/高度为 `(38,145,342,104)`、`(38,256,342,55)`、`(38,318,342,148)`；右栏为 `(900,145,342,213)`、`(900,364,342,102)`。居中绘制各卡片标题和信息行；标签和值合成一行，多行基线间距 20 px，并在内容区垂直居中。

顶部目标数量使用现有 `len(boxes)`；顶部其余三项使用 dashboard 的相机 FPS、端到端延迟和丢帧值。右侧累计卡显示 `dashboard['count']` 和单位“件”，数值中心落在卡片几何中心；按钮卡集中显示 BTN0–BTN3 与 SW0 的说明。移除曝光范围/步进行、预览链路卡和左侧重复按钮说明。

- [x] **Step 3: 对齐运行状态并加入独立闪烁**

RUNNING 使用 28 px 拉丁字体，文字右边缘在 x=1256；白点与文字垂直居中，圆点右缘到文字左缘约 15 px。按 `perf_counter_ns()` 的 500,000,000 ns 半周期切换点的亮灭状态。通过单独的小区域和每个输出槽的状态缓存更新圆点，避免状态闪烁触发整张 chrome 重建。

- [x] **Step 4: 做局部静态检查**

运行 `python -m py_compile scripts/standalone/compositor.py`，预期退出码 0；运行 `git diff --check`，预期无空白错误。不要运行 pytest 或其他测试。

## Task 2: 渲染并检查最终画面

**Files:**

- Render to: `%TEMP%\material-counter-dashboard-b-preview\hdmi-resource-dashboard.png`（临时制品，不加入 Git）。

- [x] **Step 1: 生成硬件无关预览**

从仓库根目录运行：

```powershell
python -m scripts.standalone.render_dashboard_preview --out-dir "$env:TEMP\material-counter-dashboard-b-preview"
```

预期生成 PNG 和 HTML；用图像查看器检查对齐、行距、按钮说明、计数居中、状态点和预览边框。确认输出仍为 1280×720 Gray8。

## Task 3: 对板端执行 FAST PS 部署

**Files:**

- Board: 当前运行 release 中的 `scripts/standalone/compositor.py`，由只读的 current symlink 与进程命令行确认实际路径。

- [x] **Step 1: 用 SSH 确认活动部署目标**

运行只读检查：

```powershell
ssh -o BatchMode=yes -o ConnectTimeout=5 xilinx@192.168.2.99 "hostname; readlink -f /opt/material-counter/current; systemctl list-units --type=service --all --no-pager 'material-counter*'; pgrep -af 'integrated_run|g1_run|board_entry|material-counter' || true"
```

依据活动进程的命令行和 service `ExecStart` 确认实际代码路径及受影响应用。若 SSH 仍超时，按项目约定尝试 COM4 串口诊断，再恢复 SSH 通道。

- [ ] **Step 2: 同步一个 PS 文件并重启受影响应用**

用现有可靠的增量传输方式只同步 `scripts/standalone/compositor.py` 到已确认的活动 release。仅在该应用活动时停止并重启它；不重载 PL，不改 `current`、package manifest、service 配置或其他文件。

- [ ] **Step 3: 做最小健康检查**

确认板端文件已更新、目标应用重新运行且日志没有本次改动相关的启动错误。记录传输和应用重启耗时；不把进程运行单独表述成画面验收通过。

部署检查记录（2026-11-03）：SSH 经链路本地地址可连接；当前 `material-counter-integrated.service` 为 active/running，运行报告为 `FAIL_HOLD`，原因是相机读取线程未能停止，且 VDMA 仍报告运行中。按项目硬件资源保护约定，本次未覆盖活动发布目录、未停止/重启进程，也未触碰 PL。待板端恢复到可安全停止状态后再执行 FAST 文件同步与健康检查。
