# 输送速度与目标面积可视化：Windows Offline 验证

日期：2026-10-24（Asia/Shanghai）。环境：Windows-11-10.0.26200-SP0、Python 3.12.10、仓库 `.venv`、Playwright / Microsoft Edge headless。基线：`a745f9f1e2fef8d7892f40d3b79c4c856c116ff8`。本轮仅使用合成数据及注入测试替身；未连接摄像头、PYNQ-Z2、SSH、MMIO 或真实 DMA。

## 实现范围与统计口径

- 功能改动仅在 `live_monitor_state.py`、`run_live_monitor.py`、`mjpeg_preview.py`、`dashboard.html`；测试仅改对应的三个文件。`split_touching.py` 和 `stream_counter.py` 均未改动。
- 从 `result.tracks` 读取 `forward_velocity`，排除 missed/finished 轨迹及未知、非有限、负值；每帧有效轨迹先取均值，再在 `history_limit` 有界窗口内取有效帧均值。空帧也占窗口位置；当前帧无有效速度时返回 null。静止的 0 速度仍有效。
- `telemetry.px_per_second = px_per_frame * processing_fps`；`cm_per_second = px_per_second * mm_per_pixel / 10`。FPS 使用已完成帧的滑动时间窗口，过期（超过 5 秒）或非 running 状态不显示速度。首次无 FPS 时可显示 px/帧。
- `--mm-per-pixel` 默认 0.75，必须有限且大于 0；页面注明“按 ... mm/px 估算”。这只是输入比例，并非新增实物标定，也不证明真实输送带速度。mock FPS、预览 FPS 和板端端到端 FPS 不混用。
- 每个活动轨迹使用独立检测面积绘制 `T1 135px` 格式标签和边界框；双目标渲染回归验证各自面积与框。该测试证明显示层处理两个子目标的能力，不新增粘连拆分算法准确率结论。
- 越线日志附轨迹 ID、像素面积、px/帧；`inferred_gap=True` 时包含“触发速度预测跨帧恢复”。事件有界为 30 条。累计卡片显示最近越线面积；清零按原帧边界执行，并清除上一批面积。缺少对应轨迹时明确为 N/A。

## 验证证据

1. TDD：新增遥测/参数用例先出现 11 个预期失败，再全部通过；面积标签与浏览器速度卡也分别验证了新增断言在实现前失败。
2. 首轮监控/HTTP/预览/浏览器：55 passed。扩展的九文件回归：83 passed、0 failed、0 errors、0 skipped（75.05 s），见 `preliminary-rerun.xml`。
3. 审查后加强 30 秒采样断言，并修正面积文字对比度；当前源码的两个浏览器测试：2 passed、0 failed、0 errors、0 skipped（42.69 s）。注入硬件契约测试另有 8 passed；只使用替身，不导入 PYNQ 或打开设备。
4. 固定提交的最终统一验收以[优化账本](../benchmarks/optimization_ledger.md)本阶段记录为准；最终 JUnit 位于本地 `C:\Users\POP\Documents\Codex\repo-audits\conveyor-visualization-20261024/final.xml`。代码与报告先提交，再在固定 HEAD 上运行下列十文件命令；通过后只追加并提交账本。

```powershell
.venv\Scripts\python.exe -m pytest scripts/wo0016/test_live_monitor.py scripts/wo0016/test_live_monitor_browser.py scripts/wo0016/test_live_monitor_http.py scripts/wo0016/test_mjpeg_preview.py scripts/wo0016/test_dashboard_state.py scripts/wo0016/test_dashboard_http.py scripts/wo0016/test_dashboard_browser.py scripts/wo0016/test_camera_only_preview.py scripts/wo0016/test_stageb1_calibration_preview_metrics.py scripts/wo0016/test_live_monitor_board.py -q --junitxml=C:/Users/POP/Documents/Codex/repo-audits/conveyor-visualization-20261024/final.xml --basetemp=C:/Users/POP/Documents/Codex/repo-audits/conveyor-visualization-20261024/final-tmp
```

### 30 秒本地 mock 浏览器运行（审查后源码）

使用参数 `--mode mock --no-auth --port 0 --mock-fps 30`，仅访问 127.0.0.1。交互后单独开始 30 秒采样，持续 30.672 s，采集 30 次状态；完成帧从 88 增至 783。滑动处理 FPS 范围 21.307–30.992；这是 Windows 合成处理速率。有效估算速度 4.267–8.823 cm/s；最近越线面积 178 px。每条有效样本都断言像素速度与处理 FPS、比例换算一致，至少 25 个状态样本、至少 15 个有效速度样本；完整可见阶段观测到 4 px/帧，边缘裁剪阶段质心速度允许降低。

验证清零、binary 视图切换、停止服务、有效 MJPEG 解码；1440/768/375/320 px 无横向溢出。额外浏览器用例验证 px/帧回退、比例说明、过期与断连时清除旧速度。页面 JavaScript 错误：0。已检查桌面与移动端截图。

### 保留的失败与修正

- 系统 Python 缺少 pytest；使用既有 `.venv`，未联网安装依赖。
- 扩展首跑 81 passed / 2 setup errors：本地证据父目录不存在，导致 tmp_path 准备失败。补建目录后原范围 83 passed；原 XML 保留。
- 加强采样断言的首次浏览器运行 1 passed / 1 failed：错误地假定边缘裁剪时观测质心始终为 4 px/帧，实际 3.2。修正测试为按实际观测值核验换算关系，并保留完整可见阶段 4 px/帧断言；未更改跟踪逻辑。原 XML 保留。

### 源码 SHA-256（本报告提交前工作文件字节）

| 文件 | SHA-256 |
|---|---|
| `scripts/wo0016/live_monitor_state.py` | `642bb10d453682d42f0c8a6b263f3fab5b7422f471660e135b41911f7b6ff461` |
| `scripts/wo0016/run_live_monitor.py` | `e715b934152a7221e05df324941e85c90235e4cde1667bf8fe54ed4fa850b074` |
| `scripts/wo0016/mjpeg_preview.py` | `05b1b0127a6b6f678e0b77ab7710316ac784717de0b62c64caeb5d17ae31b246` |
| `scripts/wo0016/dashboard.html` | `e83b5d826cbd370f1c08df034a9283ff9d8a4a38b2ee2e8eacef544976ddc49f` |
| `scripts/wo0016/test_live_monitor.py` | `dfdb74ced0819740f1eb4ac46db5f1cb6258b983ca6468f4d556b68d10897163` |
| `scripts/wo0016/test_live_monitor_browser.py` | `51c9283ad0514175838da10c9fd0817b7b0c88270ed15347df49d2bdf2d3a29a` |
| `scripts/wo0016/test_mjpeg_preview.py` | `fd4ecb2e153639a8896282816d76f25ef07621024c92dd516c29b28c5facc757` |
| `scripts/wo0016/split_touching.py` | `f6404ad6673dc6e4ba5d47a5a09f3aa6d5fccbecef877269407a787fd84ae8cb` |
| `scripts/wo0016/stream_counter.py` | `438a9432513f16224774050f97acc28a7c46f85af22461b28b4d389976a7b6f0` |

### 本地证据 SHA-256

以下原始日志/截图保留在本机，未随 Git 同步；本报告随 Git 保存。最终固定提交的 JUnit、30 秒状态和前后哈希收据由账本单独记录哈希。

| 本地路径 | SHA-256 |
|---|---|
| `C:\Users\POP\Documents\Codex\repo-audits\conveyor-visualization-20261024\preliminary.xml` | `2c4a11c8b1452634434e555ed257cb0e67bc05862efd27f0f15d6626a354505e` |
| `C:\Users\POP\Documents\Codex\repo-audits\conveyor-visualization-20261024\preliminary-rerun.xml` | `f7de2eb5dc47b0235bb48515828d5566767a0aa8122a9f6d0a05f21b47fe53d3` |
| `C:\Users\POP\Documents\Codex\repo-audits\conveyor-visualization-20261024\browser-reviewed.xml` | `e576f0510d5f1328ae49eebbe692ac553bf55af082cae003d4c1b6b3e7de8362` |
| `C:\Users\POP\Documents\Codex\repo-audits\conveyor-visualization-20261024\browser-reviewed-rerun.xml` | `33765bad6147345e9a33737669c15bb9a4aad66b046c52da78c0e06264531bc7` |
| `C:\Users\POP\Documents\Codex\repo-audits\conveyor-visualization-20261024\browser-reviewed-rerun-tmp\test_mock_browser_reset_view_h0\mock-30s.json` | `bdb1bbf56102e14dc685526eadb0d4a59ae6927117ef72bd655982c7ca688511` |
| `C:\Users\POP\Documents\Codex\repo-audits\conveyor-visualization-20261024\browser-reviewed-rerun-tmp\test_mock_browser_reset_view_h0\live-monitor-desktop.png` | `c910b9e33922b994fcc67425f5143a61538d19426e5cfadf8a5980d14f842117` |
| `C:\Users\POP\Documents\Codex\repo-audits\conveyor-visualization-20261024\browser-reviewed-rerun-tmp\test_mock_browser_reset_view_h0\live-monitor-mobile.png` | `82d443d5ed88faeb19c51196936401856231dc62ea06db8acc69bb3d9df28ac8` |

## 限制与标签

本次仅验收监控显示层及所列离线测试；未跑根目录全仓收集、第三方技能测试或实物验收。CMA 各时点、板端 FPS、真实物料准确率、处理分阶段延迟：N/A。速度依赖轨迹观测与输入比例；物理标定仍未在本轮测量。建议通过固定提交验收后使用 `v1.0-conveyor-visualization-offline`；本轮仅执行 tag dry-run，未获创建标签或推送的明确授权，不创建标签、不推送。
