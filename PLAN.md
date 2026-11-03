## 2026-11-04 密集计数软件优化（软件完成，现场未验收）

用户授权逐项落实本会话提出的五项改进。基线 `5438e0e1`，任务分支 `perf/密集计数优化-20261104`。

目标：减少局部掩膜、拆分及重复校验开销；限制每帧工作量，超限保留未解析父团；减少不变失败候选的重试；父团统一采集时间预测和恢复；提供逐帧耗时、间隔、事件和真值核对入口。

边界：仅 PS 软件，不更改 RTL、BIT/HWH、FRG ABI 或冻结方案。不合并、不 Push。未明确要求现场应用切换，本轮先做隔离 CPU 回放与测试，不能宣称完成 100 件实物验收。

- [x] A：`scripts/rle/components.py` 向量化局部掩膜。Worker 仅修改该文件与对应测试，Manager 检查等价性和耗时。
- [x] B：`split_touching.py` 评估单次多种子标记；保留连通与覆盖门禁，记录与旧分区的差异；消除已证明冗余的验证。
- [x] C：RLE 拆分调度增加次数、像素与时间预算；靠近计数线优先并考虑等待年龄；跳过候选不得假定为单件。缓存仅在唯一匹配、当前归一化 RLE 完全相同下复用统计，不存旧子掩膜；形状改变或身份歧义失效。
- [x] D：父团预测、超时、确认使用采集时间；无时间戳调用保持历史行为；补计继续严格唯一归属并共用 counted_event 去重。
- [x] E：扩展现有 CPU 回放，记录完整 split/region/track 耗时、采集间隔统计、预算/恢复原因和逐件事件；未知真值不得标为准确率通过。
- [x] F：定向回归、真实静态掩膜及合成密集序列 A/B；更新项目树、结果记录与 Git 提交。

验证顺序：先新增失败回归，再最小修改，再运行相关测试；全部阶段通过后运行 RLE/计数/快照/回放相关回归。基线定向集已实测 `36 passed`。时间预算是候选间准入预算，不能中断正在执行的 OpenCV 调用；单次父块硬上限和工作量上限同时保留。

结果：[密集计数验证记录](data/results/dense-count-20261104/README.md)。同输入 Windows CPU A/B 中，16 孔压力 P50 9.984→5.205 ms；合成密集区域 P95 6.111→1.624 ms，前后均逐件对应 100 个合成事件。最终定向集 125 passed；组合回归 870 passed、1 skipped、3 个已在原分支复现的既有失败，不能宣称全绿。现场版本未切换，板端持续密集吞吐与实物 100 件真值没有验收。

## 2026-11-02 SW1 快照保存开关（G2 本地构建通过，板端验收暂缓）

用户已指定以 SW1 bit5 控制快照，并保留 BTN1 诊断模式和 `--crossing-snapshots` CLI 门控。设计记录见 [SW1 快照控制设计](docs/superpowers/specs/2026-11-02-sw1-snapshot-control-design.md)。实现分支为 `feat/材料计数SW1快照开关`；核心实现提交 `a90d2c1`、专项测试提交 `cd4d063`、G2 构建门禁修复提交 `a4fa257`。本轮集成目标为 `LJ-01`。

- [x] PL 源码：G2 GPIO Channel 1 扩为六位；BTN0–BTN3 保持 bit0–3，SW0/SW1 经两级同步后占 bit4/bit5，接入 PYNQ-Z2 开关约束，并加入构建/HWH 门禁。
- [x] PS：按 HWH 宽度设置 PYNQ channel；只有 GPIO 宽度至少 6 位时才启用 SW1 bit5 快照门控。四位基线和五位 SW0 候选均使 SW1 不可用并停采；五位路径仍保留 SW0 bit4。只有 SW1 ON、诊断模式开启、CLI on 时采集；关闭时 flush 并重置快照上下文，报告开关状态。
- [x] SW1 专项测试：`test_sw1_snapshot_control.py` 的 17 项均通过，覆盖 SW1 OFF、诊断模式关闭、CLI 关闭、SW1 ON，以及四位旧 HWH 的不可用门控。
- [x] HLS 前置构建：PYNQ v3.1 锁定源码核验通过；Vitis 2026.1 对 `color_convert` 与 `pixel_unpack` 的 CSim、csynth 和 IP 导出均通过。结果在 `.runtime/builds/sw1-video-ip-20261102`。
- [x] G2 BD 创建：新工程读回 `btns_gpio` 宽度 6，BTN0–BTN3/SW0/SW1 位映射和 PYNQ-Z2 开关管脚与设计记录一致。
- [x] G2 完整综合、实现与 BIT/HWH 导出：Vivado 2026.1 完成，最终 setup WNS +0.052 ns、hold WHS +0.023 ns，TNS/THS 均为 0，路由网线全部完成；HWH `C_GPIO_WIDTH=6`，BD 读回 SW0=bit4、SW1=bit5。构建和报告见 [G2 SW1 构建结果](data/results/standalone-20261102-033000-g2-integration/README.md)。
- [x] CDC 报告人工审查：两条 CDC-6 已分别按 BTN0–BTN3 独立控制语义和 v_tc 6.2 厂商文档/约束审查；1,703 条 CDC-15 已核对为 19 组握手式 bundled-data 通路，payload 有 datapath-only 最大延迟约束。人工处置接受当前设计，不创建 Vivado waiver，原始 Warning 保留。逐类依据、路径和复位前提见 [CDC 复核记录](data/results/standalone-20261102-033000-g2-integration/cdc-review.md) 和 [原始报告](data/results/standalone-20261102-033000-g2-integration/reports-g2-integrated/cdc.rpt)。
- [x] 路径保护：Vitis HLS 输出目录落在 PYNQ 工作区内时，Tcl guard 实测拒绝；合法外置输出路径的完整 HLS 构建已成功。
- [ ] 板端 PL 重载与 SW1 实物验证：用户明确暂缓。本轮没有部署或重载 bitstream，SW1 实物开关效果尚未验证。

SW1 专项测试命令退出码为 0（17 项通过）。另一次相关文件组合回归为 88 passed、3 failed；失败位于未改动的遥测分页默认值、预览后端 CLI 默认值、以及 `LatestCamera.__new__` 测试夹具缺少 `_decoded` 三处，与 SW1 改动无关。完整 G2 构建由本机 Vivado 2026.1 batch 完成；Vivado MCP 控制端点未连接。BIT/HWH 为候选构建产物，尚未部署。CDC 人工复核已完成且原始告警保留；板端验收仍按用户要求暂缓。

## 2026-10-31 板端吞吐优化（源码与本轮实测已完成）

源码固定于 `e2fb5952bfc4f36c3eacd53a8bdb6bdeec6c4fe2`，独立 worktree 分支 `perf/board-performance-20261031`。运行链优化和诊断元数据开销修复完成，指定本地回归 488 项通过；CPU 同输入回放与微基准保留在证据目录。

- [x] v7 旧包与 v8 新包各完成 600 帧现场相机运行。v7 total_count=4；v8 收到 600×2,208 bytes、全零 runs、total_count=0。现场场景不同，不作严格同输入 FPS 因果比较。
- [x] v8 新包固定 index `e127d991371f5077bd270b2ed037508d2e76c5dfdd9c9573940fdfb2a4d1707e`；43 个模块包字节匹配，服务切换后 current 指向 v8，service active/running、PID 18172、自启动仍 disabled。
- [x] 运行态快照显示 RUNNING、processed=16461、invalid=0、recoveries=[]、field-v1；32 个实际加载 Python 源哈希与 index 对应项逐项一致，诊断触发/错误/丢弃为 0。
- [x] 保存原始报告、启动/admission/activation 记录和 FPS 汇总；后半程窗口按固定 processed≥300 帧边界保留全部 fresh 样本（包括显示 0 FPS），末三窗口降至处理 15.00、13.52、12.65 FPS。
- [ ] 有物料真值准确率及密集持续吞吐验收仍未完成。600 帧 v8 是空场景，不能证明识别准确率；运行核验未隔离诊断、SSH等开销，不作严格性能 A/B；低窗口原因未单独定量确认。

证据目录：`data/results/standalone-20261031-performance/`，现场原始记录：`data/recorded/performance-20261031/`。本轮不修改下一代架构计划。

## 2026-10-31 下一代架构实施计划（新分支）

依据两份架构评审文档，在 `nextgen-architecture` 分支（基线 `c248244`）按计划逐步推进，范围、验收与委派规则见 [架构评审实施计划](文档/架构评审实施计划_20261031.md)。本节计划不冻结 Route/接口；现场漏计优化 v7 计划保留在下文。

# 计数运行链路实施状态

## 2026-10-31 现场漏计优化实施计划（执行中）

用户已授权制定计划并委派 Worker 执行。依据为 [59 组现场分析](data/results/standalone-20261031-crossing-snapshots/ANALYSIS.md)，目标是修复已确认的换 ID 漏计、同排 ID 串接和双螺母单框；不以未知投入数计算总体准确率，不冻结 Route/接口。

采用原生 `luna_worker`（固定 gpt-6-luna/high）承担明确的回放工具和后续局部采集优化，Manager 负责运动关联设计、粘连参数决策、集成和验收。各任务文件所有权互斥；Worker 禁止二次委派、修改范围外文件或操作板卡。优先沿用当前工作区并保留用户既有未提交文档，不创建重复任务卡。

方案选择：单纯将 48 放宽到 120 已证明有部分效果，但不能防止已计数 ID 串接，也不能拆分合框；本轮选择可配置的时间/速度预测关联与同排顺序约束，加上基于现场尺寸标定的既有局部拆分。既有算法默认行为保持兼容，脱机入口单独启用已验候选配置。诊断保存保留原始同帧数据和可核对完成标记。

### A. 现场回归基线（Worker，Manager验收）

- [x] 新增 `scripts/standalone/crossing_replay.py`、`test_crossing_replay.py`、`fixtures/crossing-regressions-20261031.json`，从现有已提交元数据抽取五个连续窗口，固定初始track/参数/总数、每帧检测和单调时间；不补造缺失帧。
- [x] 先失败测试，再实现。原配置逐帧复现现场事件ID/帧号/累计数；120仅作隔离变量对照，三个单体0→1、同排1→2、合框仍1。
- [x] 时间基初始化只使用可证明的历史，未知速度保持None。输出明确标注“关联层重放”，不得描述为完整图像/真实准确率回放。

### B. 运动关联（Manager）

- [x] 新增明确开关的预测关联：按实际采集间隔限制最大速度，所有连续帧使用预测位置，严格横向范围，同排采用保持前后次序的一对一分配；有歧义或时间间隔过大时不凭空补计。
- [x] 保留暂停、清零、方向、滞回、确认、无效帧和已有去重语义。覆盖同排两物体、不同排、长时间断档、倒退、晚出现目标与重复计数负例。
- [x] 运行A中基线用例及新增失败先行测试，要求三个单体和同排连续窗口计数正确；不把有缺片的2536/2537当作完整计数验收。

### C. 局部粘连拆分（Manager决策，明确部分可交Worker）

- [x] 从本机原始样本提取2487/2606双体与独立单体的精简二值fixture，保存来源帧和摘要；实际单体约240–340、双体445–664，重新评估单体/拆分门限。
- [x] 复用既有 `split_touching_component`，仅对超过单体尺寸的局部区域运行。两双体应拆成2个连通子区域，覆盖守恒无重叠；现场单体及合成圆环不得误拆。不做全帧软件替代PL。
- [x] 真实RLE输入经过区域分析→拆分→跟踪的连续窗回归；接受拆分需检查子区域最小面积和组件尺寸，并记录实际生效参数。

### D. 降低诊断开销（独立Worker执行）

- [x] 以兼容默认的轻量保存模式保留灰度+RLE+元数据，重复上下文源帧只写一次，离线生成标注/二值图；有界队列、磁盘预留、错误/丢弃统计和原子完成标记继续有效。
- [x] 重叠事件和慢盘测试证明不重复编码、引用可校验；相机恢复代次不能共用帧身份。主循环不进行PNG编码或SD等待。
- [x] SIGUSR1大报告使用有界后台导出，取得一致的不可变快照，禁止后台线程直接遍历会被主循环修改的deque。若本轮不能可靠覆盖该共享状态，则保留明确缺口，不在过料期间导出大报告。

### E. 集成验收与部署候选（Manager）

- [x] 汇总实际diff与Worker证据，运行 `scripts/standalone`、`scripts/rle` 与跟踪/拆分相关测试；必要性能比较只对固定相同输入开展。
- [x] 维护项目树、索引及结果，准备固定提交/文件摘要的新包；板端仍运行v6，候选放行前不替换当前owner。
- [x] 受控更新时先正常停止并检查DMA idle、VDMA halted、无错误、相机/缓冲及锁释放，再切换环境/包摘要。新会话累计从0开始，旧最终报告保留。
- [x] 最终报告区分本地回归、板端兼容/运行证据与人工真值；现场准确率需有已知数量独立试验，不由稀疏窗口推定。

当前执行：A–D代码与本地验收已完成，481项相关回归通过；板端纯CPU现场样本重放通过，v6安全停止后已切换v7，PID9860运行快照876帧/invalid0、19组全部保存无丢弃/错误；实拍5帧组下载核验及离线渲染通过。独立只读审查遇额度限制未产生结论；Manager已执行实际diff/边界验收，物料准确率待独立已知真值验证。测试使用 `.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider --basetemp .runtime/<本任务临时目录>`，避免根目录测试缓存。

### 最新候选包状态（2026-10-31）

G4 候选包 37 个运行模块来自提交 `544a6d44af74708ae17ab88af6419093a45859cb`，重建索引 SHA-256 `96b4661b8e00f27de35be53236d108717aec80b23d9ff26ee63265e77afa3088`，外置 `package.env.candidate` 已同步；55 个包文件建索引、56 个文件通过内建完整性核对。候选运行器灰度缩放选用 `INTER_AREA`，二值图 `INTER_NEAREST`。G3/G4 均未放行；板端安装、自启动及最终现场测试未做，第三方离线依赖闭包未建立。最终测试由用户执行，五次冷启动、≥60 分钟有目标运行和故障恢复测试不再要求。下方较早日期条目保留为历史快照。

### 最新代码阶段状态（2026-10-30）

G0/G1/G1.5/G2/G2.5 状态不变且已通过；G3/G4 均未放行。既有 346 项回归和 AREA ramp 比较来自此前代码版本。本分支集成绘图从 LINEAR 改用 AREA，但本次代码未测试或板验；此前固定 ramp 的本地 AREA 比较匹配，不代表本分支验收，灰度正式策略和黄金验收仍未冻结。运行报告新增有界原始 GPIO 采样变化、总数和截断字段，实物 GPIO 未测。G4 启动前增加对已观察 8 个 dpkg 包状态/版本的只读准入，仍非完整离线依赖闭包。候选包 37 模块源码指向 `bf817ca98b3a1032f83ac732424314d926e3da52`、分支 `feat/统一灰度缩放策略`；本地 `delivery.py --build-index` 退出码 0，索引覆盖 55 个包文件，内建 `verify_package` 核对 56 个文件，索引 SHA-256 `63216dc2014621bc5f03e5201fcdc87dd33e4b620fe420317d660d266f09d56d` 与 `package.env.candidate` 一致；此项仅为文件完整性核对和候选包准备，未运行软件测试、仿真或板端任务，G3/G4 仍未放行，离线依赖闭包或自启动未证明。Route/接口仍为候选，物料准确率未验收。

当前脱机集成状态（2026-10-30）：G0/G1/G1.5/G2/G2.5 已通过。已有 G3 集中验证记录：相关软件回归 346 项通过；同输入显示开/关各 2,400 帧的轨迹与计数一致；新报告源码身份完整；遥测过期与协作式绘图退出场景已覆盖。缩放候选比较中常量灰度、AREA ramp 和 nearest overlay 外像素匹配，但 LINEAR ramp 有 640/207,360 像素差 1，插值策略未冻结。G3 仍未放行：实物 GPIO 消抖/浮空/长按与帧边界竞态、HDMI 热插拔和硬件恢复、Process.start 创建/启动异常的执行证据、真实相机有目标与人工真值仍缺；已新增对应启动异常用例，尚未执行。G4 尚未开始。候选包已刷新 37 个应用模块，运行环境预检和包源码清单校验代码已补齐；本地 `delivery.py --build-index` 退出码 0，索引覆盖 55 个包文件，内建 `verify_package` 核对 56 个文件，索引 SHA-256 `63216dc2014621bc5f03e5201fcdc87dd33e4b620fe420317d660d266f09d56d` 与 `package.env.candidate` 一致；此项仅为文件完整性核对和候选包准备，未运行软件测试、仿真或板端任务，G3/G4 未放行，离线依赖闭包和自启动未证明。Route/接口保持候选，物料准确率未验收。当前证据见 [G2](data/results/standalone-20261030-123000-g2-integration/README.md)、[G2.5](data/results/standalone-20261030-g25-concurrency/README.md)、[G3 部分记录](data/results/standalone-20261030-g3-functional/README.md) 与 [脱机部署缺口](scripts/standalone/DEPLOYMENT.md)。更早段落属于历史阶段快照。

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

## G3 缩放候选比较（2026-10-30；策略未冻结）

- 固定 C row-major uint8 输入、四份目标尺寸候选数组、compositor 实测 raw 和 PNG 已整理在 data/results/standalone-20261030-g3-functional/scaling-comparison/，比较绑定 OpenCV 4.10.0、runner SHA-256 2629d39f843ce02c3c9a654b0a2af63f4465117607262152e83ccdad8a4a2bd9 和源码身份 digest d169e6841cf14935f2fdb378460921ee8c7d57a0554734dc77e501ce9b3204a8。
- 常量灰度在 AREA/LINEAR 下均 207,360/207,360 像素匹配；灰度 ramp AREA 为 207,360/207,360 匹配；二值 nearest 在七列计数线 overlay 之外 204,840/204,840 匹配，完整 crop 的 overlay 区域有 360 个差异。
- 灰度 ramp LINEAR 有 640/207,360 像素相差 1，差异集中在输出列 0、6、9、12、15。内部舍入/量化原因未确定；AREA 和 nearest 结果也只是本轮候选比较，均未据此冻结运行时策略。JSON 状态为 comparison_complete_with_candidate_mismatch，候选不是黄金验收或 G3 放行证据。

## G3 新运行报告源码身份（2026-10-30；已实测）

- integrated_run.py 在创建运行计数器（触发懒加载项目模块）后、进入运行循环前，为新报告生成 source_identity：schema material-counter.project-source-identity.v1、项目相对路径到 SHA-256 的映射、规范化排序 JSON 整体 digest、file_count、范围说明、complete 与 errors。
- 本次 on/off 两份新报告的 source_identity 均 complete，覆盖 25 个项目 Python 源文件，digest 为 69d2f9996c3bfd4e0ff5fddc610dc355d57b59fd53789fb822efe5eda017815a。范围仅包含 runner 及指定项目目录内 Python 源，不能追补旧 v2 记录，也不等于依赖/ABI/位流身份。
- 相关软件回归使用项目记录的 CPython 3.12.10，scripts/standalone、scripts/rle、scripts/gray8 共 346 项通过。该软件回归和源码身份证据不代表 G3 整体放行。

## G3 绘图退出与遥测过期验证（2026-10-30）

- 集中验证期间运行了协作式绘图子进程退出场景：注入后共处理 800 帧，STOPPED、count 67、invalid 0；frame 338 记录绘图退出事件，exit code 0，DMA/VDMA 及 RLE/显示缓冲区安全释放。
- 遥测冻结场景在 frame 300 注入、显示关闭，共处理 1,200 帧，STOPPED、count 100、invalid 0；性能、资源和 XADC 样本出现 stale 标记，最终样本全 stale。
- 当前代码仍包含绘图创建/启动异常捕获、process=None 恢复与 5 秒退避；本轮没有触发 Process.start() 创建或启动异常。协作式退出不代表 HDMI/VDMA 故障或热插拔恢复，实物 GPIO、HDMI 和真实相机有目标场景仍未完成，G3 未放行。

## G3 集中验证结果（2026-10-30）

- 使用 CPython 3.12.10 运行 scripts/standalone、scripts/rle、scripts/gray8 软件回归：346 passed in 15.27s。
- PYNQ-Z2 boot ID e48ff153-bd85-4dd6-98f6-438bd51da08d。隔离临时包基于源码提交 838f5ebb2a5562f72d7695b80d3c93c8265d9b76，package index SHA-256 12e4e93f553270c7a6028dd63e2f461fa5331e04597cf8fb95fa50740d31d70d，包 SHA-256 682c3f08521e7ad8145dcc25fb8762a37d32c965b63ebc499e2e9ec2d31492e6；包只在独立板端目录运行，没有系统安装、service 变更或 bitstream reload。
- 显示 on/off 共用输入 SHA-256 2d2656815ac70479f8e00eb27bf07684a3557c8a7ee1ebaec66d8b40817f4e95，各处理 2,400 帧，均 STOPPED、count 158、invalid_frames 0；trace SHA-256 同为 94249f6cbaaf69db09c61bfdfd38720b0d703ade7772204b52c4556c6efb7427，七个命令事件及源码身份 digest 均一致，transfer_consistency.valid 为 true。
- 四份板端报告：[显示开启回放](data/results/standalone-20261030-g3-functional/replay-on-current.json)、[显示关闭回放](data/results/standalone-20261030-g3-functional/replay-off-current.json)、[绘图退出](data/results/standalone-20261030-g3-functional/drawing-exit-current.json)、[遥测冻结](data/results/standalone-20261030-g3-functional/telemetry-freeze-current.json)。报告 SHA-256 和注入边界见 [G3 部分记录](data/results/standalone-20261030-g3-functional/README.md)。
- G3 未放行；G4 依赖 G3，尚未开始。真实 GPIO 边界、HDMI 拔插/硬件恢复、Process.start() 异常、相机有目标及人工真值仍是后续门禁。

## G3 绘图启动异常恢复实现（2026-10-30；启动异常路径尚未注入）

- `scripts/standalone/integrated_run.py` 现在捕获绘图子进程创建或启动异常，记录 `drawing_start_failed`、当前帧、错误和 monotonic 时间，并在确定没有子进程启动时保持 `process=None`；每次失败后等待 5 秒再重试。
- 主循环可从 `process=None` 恢复；仅在确认旧子进程已退出后 join/reclaim 其共享帧 lease。绘图子进程不可用时跳过预览发布，RLE transfer 与 `advance_count` 仍按原流程进行；VideoPump/HDMI 按既有生命周期清理。
- 本节记录实现时的代码状态。后续板端协作式子进程退出和遥测冻结场景已在上方集中验证记录中执行；Process.start() 创建/启动异常、实物 GPIO 与 HDMI/VDMA 恢复仍未覆盖，G3 未放行。
