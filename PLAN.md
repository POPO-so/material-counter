# 计数运行链路实施状态

当前脱机集成状态（2026-10-30）：G0/G1/G1.5/G2/G2.5 已通过；G3 只完成固定回放显示开/关一致性及该回放的 30 秒窗口性能子项，绘图启动故障时计数继续与有界重试代码已补入但未验证；GPIO/HDMI 实机恢复场景仍待后续统一验证，完整 G3 未放行。G4 尚无冷启动或有目标脱机验收。Route/接口保持候选，物料准确率未验收。当前证据见 [G2](data/results/standalone-20261030-123000-g2-integration/README.md)、[G2.5](data/results/standalone-20261030-g25-concurrency/README.md)、[G3 部分记录](data/results/standalone-20261030-g3-functional/README.md)。更早段落属于历史阶段快照。

## 脱机灰度 HDMI 实施（2026-10-29）

用户已授权按 [计划.md](计划.md) 逐阶段实施。G0 环境/依赖核对见 [记录](data/results/standalone-20261029-g0/README.md)；G1 正在实施，未完成显示或脱机验收。G1/G1.5 通过前不投入完整 RLE 集成。新接口见候选 ADR-0005，正式 Route/接口状态不变。

## 桌面 RLE 接入修复（2026-10-29）

- [x] 查明旧桌面仍使用软件计数和旧静态报告，独立 RLE 入口此前未接入 GUI。
- [x] 默认改用固定哈希的 RLE 专用配置；640×400 不缩放，PL 游程、PS 原生区域分析/跟踪，MCF1 v3 同帧预览和实际资源报告。
- [x] 隔离内容 286 项回归通过；最终实板 570 帧无无效帧，368 组显示掩膜投影校验一致，暂停恢复、安全释放通过。
- [ ] 30 FPS 和物料准确率仍待验收；正常窗口处理速率约 16.6 FPS，实际展示均值约 14.6 FPS。

见 [桌面验证记录](data/results/rle-desktop-20261029/README.md)。下方“桌面 GUI 未切换”是 2026-10-28 独立候选链完成时的历史状态。

## 640×400 RLE 优化（2026-10-28，候选链已板测）

用户已批准按 [RLE 设计](docs/superpowers/specs/2026-10-28-rle-design.md) 开始实现。Manager 负责接口、RTL、集成及验收，边界明确的软件模块和既定测试交给 Luna Worker；不切换分支，不改既有历史证据。

- [x] 协议与黄金模型：新增 `scripts/rle/protocol.py` 及失败先行测试；输入 256032 B、输出上限 67744 B；验证无损、错误帧及溢出。
- [x] PS 原生游程 8 连通分析：新增 `scripts/rle/native/rle_components.cpp`、Python 包装与 OpenCV 对照测试；只重建复杂候选局部掩膜。
- [x] PL 原分辨率 ROI/灰度映射/阈值/可选形态学与游程输出；新增 `fpga/experimental/rle/`、`fpga/sim/rle/` 和自检 XSim 入口。
- [x] 构建与板端入口：新增 `scripts/rle/` 构建、制品校验、变长 DMA 接收、计数与独立运行入口，验证实际接收长度及 FAIL_HOLD。
- [x] 运行协议/原生/集成回归、RTL 仿真、实现及可用板端验证；记录真实结果和未完成项。
- [x] 同步项目树、接口增量与运行说明，审查本次 diff，仅提交本次文件。

测试命令统一使用可用 CPython，`-B -m pytest -p no:cacheprovider --basetemp <系统临时目录> scripts/rle -q`，避免再次在仓库生成测试临时目录。XSim 使用 Vivado 2026.1 后端；MCP 启动若失败，记录错误后使用配置中同一 Vivado 的 batch 入口，不更换后端。板端未通过前不替换桌面 GUI 的已验入口。

中间验证：RTL 八组完整 640×400 黄金向量通过；首次板测发现投影 RAM 奇数地址丢失，综合后自检已复现并以显式独立读口修复，修复后的综合后自检通过。首次／第二次位流不作为交付验收。系统临时目录写入受限，测试临时文件转到独立 `data/results/rle-20261028-validation/` 下，避免污染源码。Vivado MCP 配置未改，后续启动和 Tcl 执行均恢复成功，初次异常根因未能确认。最终位流和板端结果继续记录。

最终验收见 [RLE 板测报告](data/results/rle-20261028-board/README.md)：118 项相关软件测试通过，50 MHz setup +3.366 ns／hold +0.036 ns，LUT 10.92%／BRAM 1.79%。成功运行记录合计 989 帧，其中 87 帧完整像素对照通过、2 帧预期溢出拒计；最终原生库 v2 的 300 帧非零前景相机运行为 20.5651 FPS、4088–4176 B/帧。没有人工物料真值，准确率未验收；未达到 30 FPS，桌面 GUI 未切换入口。后续以真实物料标定、采集预取及阶段重叠为独立任务，不把本轮候选链描述为工业验收完成。

## PySide6 Fluent 桌面界面（2026-10-28）

- 桌面界面迁移至 PySide6/Qt Widgets，提供跟随系统、浅色、深色和高对比度主题；保留既有桌面会话、单连接同帧图传及安全停止逻辑。
- 相关软件回归 139 项通过；Windows 原生 Qt 平台在 100%、150%、200% 缩放和 720×600 逻辑像素窗口下检查各页面，无横向溢出。
- Qt 隐藏窗口实连板卡，验证灰度图与二值掩膜同帧、暂停恢复、调试和安全关闭；板端结果为 `STOPPED`、相机基准已恢复。该检查未提供物料真值，也未重新测量资源对比。

## 原生桌面 GUI（2026-10-28）

- 新增独立 Python/Tkinter GUI 与桌面会话，保留当前软件相机计数；灰度图、二值结果和计数由同一帧打包，经单一以太网连接传输，板端不进行桌面模式 JPEG 编码或叠加绘制。
- 板端同启动周期短测接收 253 组同帧图像，软件阈值逐像素比对不一致为 0；断开预览后处理继续，清零、相机亮度调整与基准恢复，以及安全停止均有实测结果。
- 空场景各 12 次资源采样：网页预览 CPU 均值 93.93%，桌面模式 57.56%；以太网发送均值分别约 0.50 MB/s 与 2.67 MB/s。两种 FPS 统计口径不同，具体限制、逐秒数据和代码哈希见 [对照记录](data/results/desktop-gui-20261028/README.md)。
- 隐藏窗口联调已验证真实 GUI 接收帧、暂停恢复、调试和安全关闭；无物料真值，不能据此认定计数准确率或生产工况通过。
- Windows 正常权限下板卡 SSH 可用；沙箱内网络或 DLL 受限不能直接判定板卡离线或程序依赖损坏。

## 网页启动器优化（2026-10-28）

用户已批准按风险核实结果开始优化。实现采用独立 JSON 部署配置、可独立加载测试的板端启动/停止脚本和真实本机 HTTP 代理测试；预期哈希继续由维护者明确更新，禁止自动信任板端哈希。保留现有停止所有权检查、FAIL_HOLD、不自动重试和历史实验入口约束。

- [x] 先添加配置校验、远端流程和目录命名的失败测试，再集中地址、路径、端口和预期哈希；配置错误必须在 SSH/停止服务前报错。
- [x] 提取远端启动/停止脚本，参数化部署依赖，保留旧已部署模块的目录兼容；新版模块使用无 attempt 编号的网页运行目录。
- [x] 补真实回环 HTTP POST/MJPEG 和注册表系统边界测试；保持本机端口和 Local 互斥锁范围，修正互斥锁句柄释放。
- [x] 运行相关回归，检查本次增量 diff、维护项目树及配置说明；相机模块、项目树和本文件按任务起始快照隔离增量，原有无关修改保留在工作区。

验证：`test_camera_only_preview.py`、`test_web_launcher_deployment.py`、`test_web_launcher_bridge.py`、`test_web_launcher_browser.py`、`test_web_preview_workflow.py`、`test_live_monitor_http.py` 共 126 passed（27.26 s，Windows 沙箱外，真实回环 HTTP；运行参数 `-B -m pytest -p no:cacheprovider ... -q`）。部署与板端入口的初始失败测试为 35 failed，缺少实现；实现后通过。2026-10-28 尝试只读 SSH 核对板端产物和脚本语法时，`192.168.2.99` 连接超时，未执行远端代码、未写板卡或停止图传；本轮不宣称真实板端启动通过。

用户于 2026-10-27 明确批准移除当前生产路径中的 Gray→RGB→Gray。当前运行入口统一使用 `GRAY8_PACKET_V1`；旧 RGB 编码器、RGB AXI-Stream RTL、旧 RGB 构建入口及对应回放代码已从活动实现删除，不保留运行期 RGB 回退。历史 `data/results/` 和旧设计说明作为原始证据保留，不改写成 Gray8 结果。

该授权只确定当前实现采用 Gray8 输入协议，不代表算法 Route、摄像头/机械路线、项目接口正式冻结或验收签字已完成。

## 当前协议和安全边界

- 320×240 Gray8，MM2S TX 为 76,832 字节：四个小端头字（`0x47525938`、`(H<<16)|W`、像素数、frame ID）、逐像素 Gray8 payload、四个小端尾字（`0x454e4438`、frame ID、payload sum、`0xc0dec018`）。S2MM RX 为 76,800 字节 Binary8。
- 唯一硬件实现和构建入口在 `fpga/experimental/gray8/`、`fpga/sim/gray8/` 与 `scripts/gray8/`。复用 50 MHz PS、DMA、HP0/HP1 拓扑及现有 Binary8 Packer、PS MaskCounter。旧 RGB 位流与 Gray8 包不兼容。
- 相机采集读取 raw MJPEG 并直接 `imdecode(IMREAD_GRAYSCALE)`；不把 Gray8 复制成 RGB，也不提供 RGB 运行期回退。照明 LUT 仍在发送前处理 Gray8 强度，阈值仍由 PL 执行。
- 每帧板端运行会做 Binary8 逐像素验证；带该验证的报告 FPS 包含启动/预热、校验与 PS 计数，不是纯 DMA/FPGA性能值。本次相机图像无前景物料，未验证物料准确率或 YOLO 收益。
- 发生 DMA 所有权不确定时可能进入 `FAIL_HOLD`，保留 CMA 缓冲区并等待操作员恢复；不自动重试或复位。

## 已完成与剩余验收

- [x] 用户授权 Gray8 唯一当前实现；保存旧结果但不再把旧 RGB 入口作为运行回退。
- [x] WO-0016、WO-0015 当前运行代码迁移到 `GRAY8_PACKET_V1`；活动编码使用唯一 `scripts/gray8/packet_codec.py`，缓冲区默认 TX 76,832、RX 76,800。
- [x] XSim adapter、320×240 wrapper、S2MM guards 三项通过。
- [x] Vivado 50 MHz Gray8 BIT/HWH 构建完成，`scripts/delivery/verify_delivery.py` 离线验证通过。
- [x] 新 BIT 已加载到 PYNQ-Z2。首次 DMA 启动检查曾误判状态值 0；修正后 `self-test-clean-3.json` 退出码为 0，3/3 合成 Gray8 帧的 Binary8 全像素比对通过，缓冲区安全释放。
- [x] 旧的较宽软件/离线套件记录：706 passed、8 skipped、2 个已知失败。失败分别为 ablation 仓库内临时路径断言与预览抗锯齿纯白像素断言；不能表述为全套全绿。当前 108 项相关软件集在 sandbox 外运行：108 passed、4.14 s，详见相机板测报告。
- [x] 同一板卡启动周期完成 30 帧相机短测、30 帧预热与三轮 300 帧实测；加上 3 帧合成测试共 963 帧，Binary8 全像素验证全部通过，DMA 空闲、fault=0、buffers 已释放。
- [x] 三轮 300 帧的吞吐为 19.1063、19.3684、20.1920 FPS；900 帧加权 19.5448 FPS。该值包括启动/预热、逐像素校验和 PS 计数。本次相机二值掩膜全黑、未检出前景，CCL执行0帧；它不是有目标场景吞吐或准确率结果。
- [x] 已在 boot `442a249b-1a20-41a1-bbc6-46debb0f4af8` 启动独立 camera-only 网页预览，地址为 [http://192.168.2.99:8080/](http://192.168.2.99:8080/)。HTTP 页面返回 200，MJPEG 5 帧成功解码且序号递增。它只运行相机采集、软件阈值预览和 `SIM COUNT` HUD；DMA 与 FPGA 计数均关闭。当前画面很暗，前景数为 0，因此软件显示不构成 FPGA 计数或物料识别验证。服务由本次会话的 Popen 启动，PID 9345；不是开机自启。部署入口为 `/home/xilinx/web-preview-20261027-124938/launch_viewer.py`，状态和日志见 [网页预览验证报告](data/results/web-preview-20261027-124938/README.md)。
- [ ] 后续运行任何 DMA/板端计数测试前，先核对 PID 9345 对应命令确为本次预览，再向该 PID 发送 SIGTERM，确认服务退出并释放摄像头；不要结束未经核对的 PID。停止步骤：`ps -o pid,uid,gid,args -p 9345`，确认是本次 `launch_viewer.py` 后执行 `kill -TERM 9345`。
- [ ] 使用有实物目标及人工真值的数据评估计数准确率，并测量目标密集/粘连场景的吞吐；不与旧 RGB 整机历史 FPS 直接比较，不声称 YOLO 收益。
- [ ] 按项目接口流程进行正式签核。当前 Route、机械/视频输入路线及接口冻结状态仍以 `docs/07_routes.md`、本 ADR 状态及正式审批记录为准。

清理范围、首次状态检查误判和合成测试见 [Gray8 清理记录](data/results/gray8-cleanup-20261027/README.md)。最新 boot ID 下的真实相机运行、像素校验、FPS、分阶段计时及限制见 [相机板测记录](data/results/gray8-camera-20261027-122633/README.md)。首次构建时序/资源见 [构建记录](data/results/gray8-dma-20261027/README.md)。历史 RGB 板测和仿真记录保持原样，仅作为其当时配置下的历史证据。

## G3 缩放候选参考数组（2026-10-30；候选未执行）

- 标准库生成器 `data/results/standalone-20261030-g3-functional/generate_scaling_candidate_sources.py` 生成三份 C row-major uint8、shape `[400,640]`、各 256000 bytes 的固定 raw 输入，以及四份 `[360,576]`、各 207360 bytes 的候选 expected raw：常量灰度、二值 nearest、灰度 ramp AREA、灰度 ramp LINEAR。
- `constant-gray-73.raw` SHA-256：`aa33e56924504144d0ea31c71ad74281064ba96c7572c2b303325091eea954af`；`gray-ramp-12.raw`：`87d33b08d68a971d3d0c86ddf7ce671f5059a2b6af19599cde9c42cb81476354`；`binary-nearest-rect-points.raw`：`467bf9002e72f182da0c3a65dfb5454d6c04a15dc5647c02f373d4c0e6bc5263`。
- 两份 ramp 输出按标准库 `Fraction` 精确计算。AREA 对每列用源覆盖 `[10*j/9,10*(j+1)/9)` 进行精确 overlap 加权平均；LINEAR 在 `(20*j+1)/18` 插值。round-to-nearest ties-to-even 是候选生成舍入约定：LINEAR 列 `j=19` 唯一精确 `252.5` 取 `252`，尚未与 OpenCV 比较，未冻结运行时行为；AREA 576 列无精确 half tie。输出 SHA-256：AREA `81e1988ffe0c929f88a500c88993e77cffb2e46e0b00f4f2c107e14ac92ae3f8`；LINEAR `56f24d8548a06030abd22ea9f6a6c875a24014979b63496507a230b3dc6c1b1c`。
- JSON 登记四份候选输出的源路径/SHA、公式、舍入约定、C row-major uint8、shape、字节数和输出 SHA。四份都不是 compositor 实测或黄金证据；没有 PNG、compositor comparison 或 runner hash 绑定。灰度策略未冻结，状态 `candidate_unexecuted`，G3 未放行。

## G3 新运行报告源码身份（2026-10-30；尚未验证）

- `integrated_run.py` 在创建运行计数器（触发懒加载项目模块）后、进入运行循环前，为未来新报告生成 `source_identity`：schema `material-counter.project-source-identity.v1`、项目相对路径到 SHA-256 的 `sha256_by_path` 映射、排序紧凑 JSON 整体 `digest`、`file_count`、范围说明、`complete` 与 `errors`。
- 范围为 runner 本身，以及当时已加载且位于项目根内 `scripts/standalone`、`scripts/rle`、`scripts/wo0015` 的 `.py` 源码；排除测试文件和根目录外路径。单文件摘要复用 `bundle.sha256`。不可读文件会记录错误并标记不完整，继续计数运行。
- 该字段只适用于此后生成的新报告，不能追补旧 v2 记录；只覆盖项目内 Python 源，不等于依赖/ABI/位流身份。代码尚未验证，G3 不放行。
- 本次只运行候选生成器并进行静态文件长度、SHA-256、JSON 绑定、首行前九列与 JSON 手算值核对；未运行测试、compositor、PNG 比较、仿真、构建或板端任务。

## G3 绘图启动失败恢复代码（2026-10-30；未验证）

- `scripts/standalone/integrated_run.py` 现在捕获绘图子进程创建或启动异常，记录 `drawing_start_failed`、当前帧、错误和 monotonic 时间，并在确定没有子进程启动时保持 `process=None`；每次失败后等待 5 秒再重试。
- 主循环可从 `process=None` 恢复；仅在确认旧子进程已退出后 join/reclaim 其共享帧 lease。绘图子进程不可用时跳过预览发布，RLE transfer 与 `advance_count` 仍按原流程进行；VideoPump/HDMI 按既有生命周期清理。
- 本次按要求未运行检查、测试、仿真、构建或板端任务。实现尚未验证；GPIO/HDMI 实机恢复场景仍待统一验证，G3 未放行。
