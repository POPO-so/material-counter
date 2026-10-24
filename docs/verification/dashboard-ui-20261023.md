# 控制看板 UI 审计与实施记录

## Phase 1：审计（修改前）

- 分支 `feat/wo0017-preview-hud`，HEAD `5ecc239`；最近提交依次为 `5ecc239`、`9fee709`、`d2f0dee`、`3e88de7`、`deca7b6`。
- 原有工作区改动：`PROJECT_INDEX.md`、三个未跟踪 UI Skill 目录、`文档/`。保留这些内容。
- 原生 Python `ThreadingHTTPServer`，页面原先内嵌在 `scripts/wo0016/mjpeg_preview.py`，不是 React 服务。
- `DoubleBufferStream` 使用双缓冲，Gray8→RGB 包→MM2S→PL→S2MM Binary8→缓存同步→MaskCounter。`stream_counter.py` 中区域提取、关联、越线去重与总计数不修改。预览每四帧抽样，后台线程编码 JPEG quality=70，只保留最新帧；无流客户端不编码。
- 实板入口 `run_stream_counter.py` 原先未给预览服务传入状态 provider；计数只进入最终 evidence 和可选 capture。
- `camera_only_preview.py` 是真实相机 + **软件阈值及软件跟踪**，不能标为 FPGA 输出；已有 `/state.json` 与受 loopback、Origin、JSON 限制的 `/api/calibration`。
- 独立 `operator_gate_launcher.py` 提供 `/status` 和一次性 `/start`（默认控制端口 8081）。不合并或旁路许可、Boot-ID、Guard、物理标定锁；没有通用停止/清零 API。
- 原始日志分布在 milestone、capture、memory-health、最终 evidence；没有统一日志路由。看板事件应标明是有界状态事件，不伪称完整硬件日志。
- 历史实板证据：Stage A 15.157 FPS，ROI 17.657 ms 超过 2.5 ms 目标；Stage B-2 Recovery-2 165 帧、4/5、22.132 FPS，准确率未通过。WO-0017 Attempt-07 READY 未开始采样，后续源码未部署。以上不是本次 UI 的实板验证。

| UI 元素 | 数据来源 | 修改前是否支持 | 实施策略 |
|---|---|---|---|
| 顶栏设备状态 | 完成帧回调、既有 DMA/Packer 校验 | 内部可观测，未统一公开 | 仅报告最近完成帧的证据；超时失效，不新增寄存器读取 |
| 预览 | `/stream.mjpg` | 支持 | 保留原 HUD + Binary8 PIP；清楚标注 FPGA/软件来源，停帧遮罩 |
| 累计计数 / 区域数 | FrameResult.total_count / detections | 内部支持 | 只读快照，不改计数器；无数据为 null/`--` |
| 处理 FPS / 时长 | 完成帧数 / monotonic | 最终报告有吞吐统计 | 新增会话均值、最后帧年龄；与预览 FPS、相机标称 FPS 区分 |
| 预览 FPS / 尺寸 | hub 实际 publish | 部分内部可测 | 仅从真实发布事件测量，不写死参考图数字 |
| 系统状态 | 会话状态 / 原始 provider | 不统一 | `/status.json` 聚合；保留 `/state.json` 兼容 |
| 开始 | 独立门禁 `/start` | 受限支持 | 在原门禁页操作；看板按钮禁用并解释 |
| 停止 / 清零 | 无 | 不支持 | 禁用，禁止 UI 假成功或后台 reset DMA |
| 标定控制 | `/api/calibration` | 条件支持 | 保留接口，按能力显示，操作确认及响应反馈 |
| 系统事件 | 会话状态及真实变化 | 无统一路由 | 30 条环形事件队列、1.5 秒刷新，明确不是完整日志 |

## Phase 2–3：设计与实施计划

使用 `frontend-design`（视觉方案→实现→截图自审）、`ui-ux-pro-max`（设计检索、交互/可访问性校验）、`web-design-guidelines`（获取官方规则，审查交付页面）。额外使用 brainstorming、writing-plans、test-driven-development、verification-before-completion 和 webapp-testing 组织实现及验证。项目 UI Skill 不触发后端硬件行为。

设计检索返回了偏暗色玻璃风的建议，与参考图不符，不采用其配色、模糊和外部字体；采用其数据时效、暂停预览、可访问性建议。颜色：主蓝 `#1467d8`、浅底 `#edf4fc`、白 `#ffffff`、深字 `#142640`、成功 `#08754b`、异常 `#b42318`。使用本地微软雅黑/PingFang/系统字体和等宽数字，不下载字体或图标。左右 60/40 布局，窄屏单列。

- [x] 先验证无数据、软件来源、陈旧帧、provider 失败、计数绑定、控制拒绝行为。
- [x] 新增 `dashboard_state.py`：常数空间状态快照和事件，不读硬件，不写证据。
- [x] 新增 `dashboard.html`：中文蓝白看板、1.5s 单请求轮询、超时/断线清空、预览暂停与重试、真实标定控制。
- [x] `mjpeg_preview.py` 扩展页面与轻量状态路由，保留原始接口、流及编码机制。
- [x] 将正式帧完成回调和相机软件预览接入状态，保持 DMA 时序与计数算法。
- [x] 跑离线回归、记录已执行及未执行的浏览器检查；更新项目树。

## Phase 4：验证

### 已执行的离线检查

使用现有 `C:/Users/14173/AppData/Local/Programs/Python/Python312/python.exe`（带 pytest、NumPy、OpenCV 4.10）运行；PATH 中的 LibreOffice Python 没有这些依赖。

```powershell
& 'C:/Users/14173/AppData/Local/Programs/Python/Python312/python.exe' -m pytest scripts/wo0016 -q
& 'C:/Users/14173/AppData/Local/Programs/Python/Python312/python.exe' scripts/delivery/verify_delivery.py
git diff --check
git diff --exit-code -- fpga scripts/wo0016/stream_counter.py
```

- WO-0016 离线回归：**292 passed, 4 skipped**。其中三个是原测试跳过，一个是可选 Playwright 模块因依赖不可用而跳过，不能将其写为浏览器自动化通过。
- 新增 telemetry / HTTP 用例覆盖无数据、真实结果绑定、软件来源、过期失效、30 条有界事件、provider 失败返回 503、原始 `/state.json` 兼容、标定能力、未支持控制拒绝、实际 JPEG 发布指标和闲置流断开释放。
- 原 HUD 字体测试在 HEAD 原版上也失败：抗锯齿文字峰值为 251，原断言要求 255。仅把断言改为识别接近白色的中性文字（>=230，通道差<=4），没有改动原绘制函数。
- `verify_delivery.py`：**PASS**，319 个清单文件、5 批次，hardware_actions=0；历史 `FAIL_STOPPED` 保留。
- JavaScript 提取后 `node --check` 通过。运行源码 diff 中没有 FPGA 或 `stream_counter.py` 修改。

### 实际浏览器检查及边界

通过已连接 Edge 检查实际本地 HTTP 服务，而非直接打开静态 HTML：

- 1440、1024、768、375、320 五种宽度均无横向溢出；截图人工检查了桌面及窄屏布局。
- 无 provider 时计数、FPS、尺寸显示 `--`，相机/FPGA/DMA 显示未连接；支持能力不会凭空出现。
- 单独的临时离线夹具调用真实 `MaskCounter` 处理人工生成帧，再通过实际 Python HTTP 和 MJPEG 服务验证计数、区域数、处理 FPS、尺寸与预览发布 FPS 的绑定。页面事件与图像明确标注 OFFLINE TEST；它不是相机或板端证据。
- 软件模式显示“非 FPGA 输出”，FPGA/DMA 显示未启用；暂停预览移除图像请求，恢复后能够看到最新图像。
- 浏览器原生确认框使自动化连接阻塞，随后 computer-use 工具因无法可靠确定窗口 URL 而停止本轮电脑操作。已改为原生 HTML `<dialog>` 页面内确认框，并更新可选浏览器用例；**该最终确认框的端到端浏览器验证 NOT VERIFIED**。
- 断线/停帧的服务端语义由离线测试覆盖；完整浏览器断线恢复、后台标签页恢复、减弱动画与键盘焦点流程仍 **NOT VERIFIED**。相关实现及可选测试已交付，不将脚本存在视为执行通过。

可选浏览器测试（另需本地 Playwright 和 Edge，不属于板端依赖）：

```powershell
python -m pytest scripts/wo0016/test_dashboard_browser.py -q
```

本次隔离 `.venv` 的 Playwright 下载没有完成，已停止安装并清理在项目根目录生成的 pip 临时目录。

### 新界面与参考图的差异

- 继承蓝白配色、两列卡片、大数字和中文层级；状态内容按实际能力降级，没有复制参考图中的分辨率、FPS、533 或18等指标。
- 保留现有 Gray8 HUD 主图 + Binary8 PIP，而非改写 JPEG 渲染为参考图中的全屏纯二值图；主图等比例居中，不拉伸。标题和来源按真实采集路径区分。
- “当前帧检测数”改为“当前帧区域数”，准确对应 `FrameResult.detections`，不宣称使用目标检测网络。
- 系统日志改称“系统事件”，最多30条；完整日志仍在原证据文件中，不伪造初始化成功消息。
- 未支持的停止/清零禁用；开始继续使用原独立门禁页。预览服务在有界采集流程内创建，不提供再次启动或绕过许可的通道。
- 新增数据时效、断线遮罩、预览暂停、标定响应与页面内确认。完成/停止后显示带“最终结果”标签的最终计数；网络失联则清空实时数值。

### 接口绑定及部署

| 接口 | 用途与限制 |
|---|---|
| `GET /`、`/index.html` | 启动时读取同目录 `dashboard.html`，之后从内存服务，不逐请求读磁盘 |
| `GET /status.json` | 新增只读轻量 HTTP 预览遥测；schema=1；不改变生产 FPGA 特征 ABI 或受保护接口文档 |
| `GET /state.json` | 保留原 provider 输出语义，现可附带 dashboard 快照 |
| `GET /stream.mjpg` | 原异步 MJPEG；最新帧优先；fps 指标为服务端 JPEG 发布速率，**不是客户端实际显示帧率** |
| `POST /api/calibration` | 保持原 loopback、同源、JSON 和后端标定权限；UI 等待响应才报告成功 |
| 独立门禁 `/status`、`POST /start` | 保留原服务与一次性鉴权，不在预览接口中代理；实际端口取现有启动参数 |

同步代码时必须包含 `dashboard.html` 和 `dashboard_state.py`，不能仅复制旧文件或只复制 `*.py`。`formal_runtime_sha256()` 已包含新增运行文件，因此新源码不能沿用旧源码绑定的标定锁/Permit。**本次没有部署、修改或消费板端锁，也没有访问板端 OS、相机、MMIO 或 DMA。**

### 资源和性能评估

- 不新增前端框架、字体 CDN、外部图标或板端依赖。页面使用本地字体和内嵌 SVG。
- 非视频指标每1.5秒轮询，单请求完成后再排下一次；4秒超时。隐藏标签页停止请求并移除 MJPEG，返回后查询最新数据。
- 状态更新复用既有 preview callback 的线程调度，没有为每帧添加新的 `asyncio.to_thread`；只取现有完成结果，不读寄存器或重新提取区域。
- 保留原后台编码、每四帧抽样和 quality=70；无客户端继续不编码。发布时额外记录最多60个时间戳；事件队列最多30条。慢客户端发送超时，空闲流检测断开以释放客户端计数。
- `[Measured: dashboard-host-qa-20261023]` Windows 主机一次10000次同计数帧状态更新均值约0.35微秒；简短快照JSON约507字节。这只是本机微基准，不包括 JPEG、计数、HTTP 或 PYNQ 开销，不能外推板端性能。
- **PYNQ CPU、DMA吞吐、计数FPS、多个客户端、内存与长稳影响均 NOT VERIFIED**；没有证据可声称本次 UI 对实板性能“零影响”。FPGA RTL、BD、bitstream、DMA核心时序和核心计数算法未改。

### 修改文件

- 新增：`scripts/wo0016/dashboard.html`、`dashboard_state.py`、`test_dashboard_state.py`、`test_dashboard_http.py`、`test_dashboard_browser.py`。
- 修改：`scripts/wo0016/mjpeg_preview.py`、`run_stream_counter.py`、`camera_only_preview.py`、`test_mjpeg_preview.py`、`test_run_stream_counter.py`。
- 文档：本记录与 `文档/项目树.md`；未修改用户已有 `PROJECT_INDEX.md` 和三个 Skill 的内容，未修改受保护计划、架构及历史验收记录。

审查依据：[Web Interface Guidelines](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md)。已落实语义按钮与标签、可见焦点、缩放、等比例图像、文本安全插入、有界列表、失败反馈和减弱动画；未完成的实际浏览器检查按上节保留。

新看板实板部署、真实摄像头、DMA 吞吐、长稳、物料准确率：**NOT VERIFIED**。历史准确率失败结论保持不变。
