# MJPEG 流式硬解码与联合资源优化实施计划

> 执行方式：Manager 决定接口和验收边界，原生 luna_worker 在互不重叠的目录实现并验证；禁止二次委派。用户于 2026-11-01 批准开始推进。候选实验不冻结正式 Route，不改运行服务。

**Goal:** 在 XC7Z020 上验证 USB MJPEG 经 PS 接收、AXI DMA 送 PL、JPEG 硬解码、二值处理与 PL HDMI 输出的联合实现路径。

**Architecture:** JPEG 输入为 32-bit AXI4-Stream；仅保存最多 4096 B 的头部供固定版本解析器构表，其后熵流顺序消费。解码亮度通过 8×640 B 条带重排为既有 Gray8 packet，直接接 PL 前处理。区域统计改用同步 BRAM，保留现有容量、FRG1 和 PBO1 行为。正式 HDMI 扫描仍独立连续运行。

**Tech Stack:** Vitis/Vivado 2026.1，XC7Z020CLG400-1，处理目标 50 MHz；HLS C++、Verilog、XSim、Python 验证。AMD JPEG 源固定 commit `1204c74cf86ff1e54e0933849328cb9ef7ae7fa6`。

## 工作区和已有证据

- 工作区：`.runtime/worktrees/jpeg-probe-20261101`；分支 `test/验证MJPEG硬解码前提`，启动 HEAD `880303f`。已有未跟踪探针和原始样本保留，不清理、不自动纳入提交。
- 原始相机 120 帧的文件与清单 SHA/长度一致；640×400 Baseline 4:2:2，DRI=40，每帧 49 个 RST。
- 旧 AMD C 仿真两帧 Y 输出与 OpenCV 最大差 1；本轮前置复核的阈值128结果相同，不作为物料准确率证据。
- 原始 AMD 全核 OOC 综合：19,484 LUT / 14,597 FF / 19 BRAM tile / 40 DSP，未布局布线。
- offload 参考 HEAD `c5a86807c93e6ed572e2f75e13a6f9c7f1320321`。其 placed 总 LUT=39,297；route OOM，无可交付 BIT。该 worktree 另有并行修改，只读取明确快照。
- 区域模块原 OOC：16,497 LUT / 19,397 FF / 0 BRAM；大规模分布式 RAM 为优先优化目标。

## 接口与错误边界

- 输入 API：`jpeg_axis_gray(in, out, jpeg_size, frame_id, status)`（公共头 `fpga/experimental/jpeg_axis/jpeg_axis_gray.h`）；in/out 均 `hls::stream<ap_axiu<32,0,0,0>>`，标量经 AXI4-Lite；不得出现 `m_axi` 端口或完整帧片上数组。
- 输入接受连续低位有效 TKEEP；帧字节数、TLAST、SOI/EOI、头部结构、640×400/422/3分量必须校验。长度上限512000 B，头部上限4096 B，超界报告错误。
- 输出复用 Gray8 packet：头部 magic `0x47525938`、`(400<<16)|640`、256000、frame_id；尾部 `0x454e4438`、frame_id、像素字节和(mod2^32)、`0xc0dec018`。TKEEP=15，TLAST仅最后一字。
- status 非0帧不得进入计数或成为有效显示帧。已识别输入错误必须 drain 当前 packet，并验证后继好帧；坏熵流/缺失生产者可能触发外部 watchdog，未覆盖前不得声称任意错误自动恢复。
- vendor RST/DC reset保留；不能用删标记替代正确解码。严格 DRI cadence 检查若未实现须在交付说明中单列。
- 连续显示需保持现有 mask双缓冲和帧边界提交；不得因 decoder stall 中断 HDMI 像素时钟。坏帧的 display commit 依赖必须实际核查。

## 执行任务与验收

- [x] 核查隔离工作区、固定源版本、现有资源和接入点；无需合并 LJ-01。
- [x] T1 建立验收测试：`scripts/experiments/jpeg_axis_verify/`；输入尾mod4、keep/last错误、坏header、后继恢复、有效帧全像素与packet完整性。红证据已保留；冻结版各独立Csim入口退出0。
- [x] T2 实现 `fpga/experimental/jpeg_axis/`：vendor最小依赖、来源和许可清单；header缓存、entropy AXIS feeder、8行Y重排、Gray8封包。冻结cpp SHA `d695aac91915f3bf8b4d4357d7b2ca539929197720b2f2390cfb5a2472108f7d`。已核对顶层接口无m_axi及dataflow进程间完成token依赖；硬件正确性仍待T5。
- [x] T3 冻结版真实frame1/98与vendorY逐像素相同；120帧与OpenCV最大差1、阈值128翻转0，`d695-snapshot-batch120-20261101`为FLOW_PASS。样本在该阈值下均无前景，不构成物料准确率证据。熵流截断独立Csim确认错误status、无效checksum及后继好帧，仍不保证任意坏熵流恢复。硬件背压不能由C仿真代替。
- [x] T4 实现 `fpga/experimental/jpeg_integration/rle_region_features.v` 的BRAM版本；512/320容量及端口语义不减。33个行为用例通过，冻结源码SHA `19439a2c666696d48521b112705763df6ac48012cfacd9476fcd6850a3551911`；统计表4 BRAM36、行表1 BRAM36，OOC为1244 LUT/514 FF/3 DSP。本地提交`f953885`，不是联合route证据。
- [x] T5 JPEG HLS CSim、CSynth、RTL co-sim：receipt `hls-green-recovery-d695-20261101T140606.json` 绑定源码 SHA `d695aac9…2108f7d` 与导出 component SHA `0bdfce50…7246725d5`；三项检查均 PASS。端口映射含 AXIS、AXI-Lite 与控制口，无 m_axi；官方正常帧及 entropy-cut→good2 随机背压 RTL co-sim PASS，错误恢复两 transaction 共 314358 cycles、稳定性错误 0。120 帧 CSim 对 OpenCV maxdiff 1、阈值128翻转0。结论仅限独立 JPEG HLS/RTL；不表示联合 BD、全系统 route 或板端验收。详见 `data/results/jpeg-evidence-audit-20261101-151459/README.md`。
- [x] T6 联合候选构建：从offload固定快照读取模块，以优化区域核心替代原核心，在 MM2S 和 image_pipeline 之间接JPEG IP，补AXI-Lite控制、时钟/复位、DMA长度位宽。不合并其他分支、不覆盖其工作文件。j9 recovery3：Vivado batch exit 0；BD validation、JPEG架构/地址读回、保留接口/时钟/地址比对、save/readback、live source association均PASS；`joint-connected.tcldict`为`CONNECTED_NOT_BUILT`。T6不代表联合综合、route、BIT或板端通过；XPR仓库迁移路径修复只作用于隔离构建副本，原始失败收据保留。收据：`.runtime/j9/evidence/connect_recovery3/`，摘要`t6-finalization.json`。
- [x] T7a 真实 `jpeg_axis_gray` RTL→image_pipeline→RLE/FRG→二值预览/帧身份 XSim；候选相机 JPEG 单帧 PASS，receipt `fad0dba6…daf4bf2`。这是 RTL/数字仿真证据，不代表联合系统实现或实体 HDMI。
- [ ] T7b 联合综合、CDC/约束审查及布局布线；报告资源、各时钟WNS/TNS/WHS/THS、DRC/CDC和 BIT 状态。route 未完成不得标记 ready_for_board。
- [x] T8 新增隔离的 PS MJPEG→JPEG DMA 实验运行模块：摄像头原始 MJPEG 字节直接进入 512000 B 对齐 CMA 缓冲，通过 MM2S 和 JPEG AXI-Lite 控制交给 PL；等待 `status_ap_vld`/status、RLE 与 FRG 同帧有效后才允许 `mask_accept`。不修改现有服务入口；离线 mock 覆盖长度/对齐/缓存同步/最终 TKEEP 对应长度/错误状态/超时/帧号和禁止坏帧接受。管理端复验 transport 与联合读回门禁共 50 项通过、25 个子用例通过；真实候选 BIT、USB 摄像头和 HDMI 板端运行仍单独验收。
- [ ] T9 按本次用户授权进行一次性候选板端部署与相机验证：仅在T6/T7及部署前置门禁通过后使用独立实验路径，不替换正式release/current、不注册或启用常驻服务；记录候选BIT/HWH/manifest哈希、boot ID、DMA/VDMA与camera owner状态，并以新摄像头单帧验证解码/RLE/FRG身份及PL二值HDMI显示。部署后按检查结果恢复测试前相机格式并安全释放资源。
- [ ] T10 更新本计划、PROJECT_INDEX、文档/项目树及实验使用说明；只提交本任务文件，本地Commit；保留分支/worktree，不push/merge。

## 验证命令与产物归属

- Python：`C:/Users/14173/Documents/material-counter/.venv/Scripts/python.exe`；已有基线 `-m pytest scripts/experiments/test_jpeg_probe.py -q -p no:cacheprovider` 为6通过。
- HLS：`C:/AMDDesignTools/2026.1/Vitis/bin/vitis-run.bat --mode hls --config <explicit.cfg> --work_dir C:/w/mc-jpeg-axis-hls-20261101 --csim/--cosim`；C synthesis用同目录 `v++.bat --compile --mode hls ...`。
- Vivado 优先使用专属 MCP Tcl session；不得接管其他会话。本轮MCP后续启动失败而同一安装的独立batch启动验证成功，故联合阶段使用专属batch进程及真实`-log`文件，保留MCP失败记录。生成工程、DCP、日志放任务构建目录，小型来源清单和验收摘要归档到 `data/results/jpeg-axis-20261101/`。
- 实现Worker只写 `fpga/experimental/jpeg_axis/`；测试Worker只写 `scripts/experiments/jpeg_axis_verify/`；区域Worker只写 `fpga/experimental/jpeg_integration/region*` 与该目录的 `rle_region_features.v`。联合BD与总体验收由Manager安排后续Worker。

## 当前进度与下一步

2026-11-01：计划/索引/项目树已本地提交 `dd39c66`。公共 `.h`、Apache vendor子集和来源清单已准备；测试入口的红证据为 `C:/w/mc-jpeg-axis-hls-20261101/logs/initial-red-link-fail-20261101T054010.log`（缺少 jpeg_axis_gray 实现的 link fail），Manager已验收并授权继续实现。工具安装内 `utils/x_hls_utils.h` 仅作外部依赖，不随Apache源码提交。

联合构建的固定21文件快照由 `fpga/experimental/jpeg_integration/prepare_snapshot.py` 成功准备到本worktree的 `.runtime/jpeg-joint-snapshot-20261101`，来源 `c5a8680`；不读取参考worktree的未提交修改。`C:/w` 新建快照目录遇ACL拒绝，改用工作区内忽略目录，不影响源快照。只有快照准备完成，尚未联合构建。

三个实施任务继续执行，当前未完成绿验证。JPEG实现 Worker `01a193e3-532e-7d42-bcc2-7133f0107f08`；区域BRAM Worker `01a193e4-9b10-7c63-b7f7-cfe554cde962`；TB/HLS Worker `01a193e4-d87c-78c1-b417-706f418d6294`。Manager下一步复核第一次csim输出、header/熵流负例及BRAM映射，然后安排联合BD接入。新stream错误帧必须经PS确认JPEG status及RLE/FRG有效后才能提交mask_accept；此记录不表示实现或RTL/板端验收通过。

2026-11-01 13:44恢复：原生Worker曾受额度限制而中断，按用户要求恢复原三个角色，未切换模型或编排方式。最终HLS综合在06:31已独立完成（`d695-snapshot-csynth-final-20261101`，source-bound synthesis-state PASS），但尚无RTL协仿绿证据。区域T4已验收提交。恢复分工：Kant补RTL随机背压及HLS验收摘要；Copernicus补联合克隆路径、门控和脚本自测；Linnaeus新增Gray8 packet→pipeline的640×400好/坏帧及背压XSim验收。Manager保留CDC/约束审查与联合实现决策。

2026-11-01 14:01进展：

- JPEG冻结候选已提交`816a079`，三个实验目录的原始字节保存规则已提交`5173189`。Git索引与25项已跟踪实验文件逐字节相同，6项vendor来源SHA检查通过；未修改工作树源码。测试源码和来源清单不会因换行转换在新checkout中失配。
- Gray8 packet链路XSim为5/5 PASS，本地提交`a8d8f81`：128万像素、输入stall624478拍/输出stall2353拍；坏checksum和错frame_id不accept，后继好帧恢复，3次显式accept。FRG为推测性暂存，必须联合RLE有效状态决定使用。
- 独立视频连续性XSim为5帧PASS，本地提交`63e88e3`：1280×720、总1650×750、pixel周期13.468ns，6187500拍时序比较，4608000有效像素，生产暂停跨2帧；2次接受均于pixel域VSYNC边界切换。只证明数字RTL，不包含物理TMDS或板端时序。
- 首次正式协仿停滞已保存为失败/中断记录；同一RTL独立重放完成64008输出beat，VCD确认输入/输出stall且稳定性错误0。正式关闭trace viewer的随机背压协仿于14:01:24 PASS，报告正常frame1 latency=157260cycles（50MHz约3.1452ms），源仍为D695；尚未把截断熵流的RTL后继恢复标为通过。
- `C:/w/jj1101d`原生batch安全probe通过：实际关联的文件均在clone/批准IP目录，原baseline架构/时钟/地址保留；该clone无HLS green，因此本身不能启动connect/synth/route。联合脚本已补原始region receipt字段兼容及Windows路径长度检查。
- Manager允许以不可变的正常帧RTL通过receipt推进隔离联合BD连接及资源综合，与错误恢复RTL验证并行；route前仍须实际错误恢复结果、资源与CDC/约束审查。单核新JPEG实际OOC资源也在独立验证，不能沿用旧AMD核19484LUT或HLS估计。

## 显示与计数能力边界（固定c5a8680代码核查）

- `video_binary_overlay_bd.v`中的`cfg_count`/`feature_count`分别是框数量（最多16个）和区域记录数，`capture_count`是捕获帧次数；均不是物料累计计数。固定PS `pl_preview.py`写入的是`len(boxes)`。
- 当前PL overlay实际绘制二值mask、计数线和框，没有累计总数输入或数字字形逻辑。累计总数仍由PS跟踪/计数路径管理，不能把当前候选描述为“累计数字已由PL绘制并与mask原子同步”。
- mask和框配置通过独立mailbox在VSYNC边界生效；仅`id_p == displayed_frame_id`时绘制框，身份不匹配时抑制框，不是两个mailbox联合原子提交。mask域公开displayed_id在pixel域切换后的CDC ACK到达后更新。
- 本轮packet/连续视频验收证明的是上述现有功能，未新增总数字形、联合提交寄存器、PS MJPEG运行驱动或板端部署。需要这些功能时应单独设计、实现与验收，不能由JPEG解码/资源结果推断它们已经实现。

2026-11-01 15:15 JPEG 证据复核：Manager验收 T5。正常帧与 entropy-cut→good2 recovery 官方 RTL co-sim 都 PASS；D695 C++/0bdf component 绑定有效，CSim/CSynth/Cosim receipt 引用文件 SHA 全匹配。单核实际 HLS IP OOC 为 17,358 LUT/13,758 FF/28 BRAM36 等效/34 DSP，尚未布局布线。区域 BRAM 19439 SHA 与当前文件一致，5 BRAM36 映射及区域回归 PASS（OOC）。packet XSim 对固定 c5a8680 joint-candidate snapshot 5/5 PASS；pipeline SHA 7cfbdde5…d50f261 与 `C:/w/m/snapshot` 及 `.runtime/jpeg-joint-snapshot-20261101` 一致，候选不使用 worktree 根旧 2adde 文件；测试未包含 JPEG decoder RTL。video continuity XSim 对同一固定 snapshot 的五帧数字视频 PASS，不是物理 HDMI/板端证据。灰度预览已移出需求；PL累计 glyph 未实现，但不在本轮资源接入验收范围。T6 joint BD 与 T7 联合实现/route/板端仍 pending。

2026-11-01 本轮连接门禁修复：仅对 image_pipeline 的 refresh_region、Tcl code=0 且明确负 WARNING 计数差新增独立日志区间审查，原 FAIL receipt 保留；overlay refresh 不继承此例外。Manager复验15项测试及1项subtest通过，既有JPEG探针6项通过，固定21文件快照dry-run通过；`C:/w/m` 源码和green gates核对通过，两份region替换哈希均为19439。新clone尚未启动connect/synth；另一G2 Vivado正在运行、物理内存低于1 GiB，本轮不并发启动构建。恢复命令、资源快照及未验收项见 [修复交付记录](../../../data/results/jpeg-axis-integration-followup-20261101-151831/README.md)。后续仍为live BD readback→联合综合→Manager审查CDC/约束→route；没有BIT或上板结果。

2026-11-01 恢复推进：用户明确要求继续关闭主要缺口，将原先“不改运行服务”扩大为仅新增隔离 PS 实验运行模块；既有 `run_counter.py` 和正式服务不重构。联合综合仍以可用物理内存≥3 GiB 且无竞争 Vivado 构建为启动门槛。staged HLS component 和生成 Linux driver 给出 AXI-Lite 寄存器映射；板端真实 HWH/BD 仍须在 T6 live readback 中复核。

2026-11-01 T8 完成管理端复验：`scripts/rle/test_jpeg_transport.py` 与 `fpga/experimental/jpeg_integration/test_joint_readback_offline.py` 合并运行，50 passed、25 subtests passed；`git diff --check` 退出码 0；重建短别名后 staged manifest 源码哈希与 HLS/region 前置门禁均输出 PASS。T8 已完成 fake-MMIO/DMA 控制逻辑；live BD、联合 XSim/实现和 HDMI 板端没有因此验收。

2026-11-01 一次性摄像头模式探测：板端UVC枚举识别为`DECXIN CAMERA`，`/dev/video0`支持MJPG 640×400（枚举帧间隔0.005/0.008/0.017/0.033/0.050/0.100秒），另支持YUYV 640×400；视频节点当时无打开进程。实际JPEG帧头/SOF采样和DRI未取得。一次性`v4l2-ctl`设置640×400 MJPG并尝试单帧流期间SSH中断；用户手动重启后boot ID变更，后续只读节点查询恢复成功。中断可能与另一板端部署会话有关，未归因于相机或采集命令。测试前读得设备模式为1280×800 MJPG；640×400临时模式是否保留、`/tmp/decxin-oneframe-bF4f9c`是否由EXIT trap清除，待板端可稳定访问时复核。曝光及其他UVC控件未修改。

2026-11-01 部署范围更新：用户明确要求按项目部署规则部署当前JPEG实验候选，并限定为本次测试。该指令替代早先T9的“不得部署”边界；新增T9一次性部署，仍须先通过T6/T7及板端安全准入，不能用旧生产BIT/standalone包冒充JPEG候选。当前仓库根`AGENTS.md`没有安装/切换步骤，`scripts/standalone/DEPLOYMENT.md`仅覆盖standalone/G4包流程，不能直接作为JPEG部署脚本。当前 `.runtime/j1`、JPEG结果目录未发现联合 `.bit/.hwh`，所以尚无可部署JPEG制品。

2026-11-01 T6 资源决策：用户明确要求“直接开始T6 用虚拟内存”。这一指令仅覆盖T6连接/读回阶段的既有3072 MiB可用物理内存门槛；启动前实测可用虚拟内存12430 MiB，pagefile分配34816 MiB/当前使用5202 MiB，无其他Vivado进程，且新staging源码哈希与green gates通过。按此证据启动一次T6 batch连接/读回；此决策不自动放宽T7综合或布局布线的资源门槛。主要代价是分页增多导致构建缓慢或桌面响应下降。

2026-11-01 T6 recovery3验收：复用现有j9工程，没有重新导出JPEG IP或创建新快照。修复构建副本XPR中三条已迁移外部IP仓库绝对路径、移除重复JPEG仓库项后，重跑一次连接/读回成功；原始T6失败收据保持未改。启动前物理可用内存1.819 GiB、虚拟可用14.079 GiB；T6使用虚拟内存继续。T7整机综合门槛仍为可用物理内存至少3 GiB且无竞争Vivado，当前尚未满足；不得把T6授权延伸成T7资源门槛豁免。

2026-11-01 T7 packet边界XSim复跑未验收：固定21文件源快照入口已确认，xvlog通过，xelab在生成xsim_2.c对象时以[XSIM 43-3409]失败，没有功能PASS标记；错误日志位于.runtime/j9/xsim-packet/20261101-222726-959409/sim/。该测试仍明确不含JPEG解码RTL。运行时可用虚拟内存14146 MiB，xelab自动启用18个子编译；同一脚本旧候选有独立5/5 PASS证据，但不能代替本次失败重跑。依据AMD [UG900](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Vivado-Simulator-Elaboration-Options)，xelab -mt可限制子编译并行度；在检测到另一个Vivado Tcl进程运行且物理可用内存581 MiB时，未并发启动减线程复测。T7仍pending，当前没有RTL行为失败结论。

2026-11-01 T7真实JPEG RTL→pipeline验证准备：新增 `tb_jpeg_pipeline.sv` 与 `tb_jpeg_pipeline_run.py`，将真实 `jpeg_axis_gray` RTL、冻结的640×400 pipeline、优化region及二值预览/overlay身份检查串联；runner现由独立raster oracle生成RLE/FRG期望向量，并检查全部JPEG→Gray8→RLE/FRG/预览结果。输入预检通过：HLS component SHA `0bdfce50…7246725d5`、146文件集及历史相机帧/Csim golden哈希匹配；Python AST、参数对齐、oracle向量生成和`git diff --check`通过。首轮batch在RTL编译前因Vivado Tcl `set_property`参数写法失败；已改为`set_property -dict`，但尚未复跑。当前外部G2 Vivado PID 57024及其synth子任务仍在运行，故未启动第二个Vivado/XSim。

2026-11-01 本轮板端只读核验：当前boot ID `6ca5a640-c3f0-4fd8-b3cf-883066534609`，DECXIN `/dev/video0`为640×400 MJPG@60；板端OpenCV 4.5.4。`/dev/video0`由手动启动的`integrated_run.py` PID 7592占用，运行standalone v12、`--input-mode raw`、`--preview-backend pl`，systemd服务inactive；未停止进程、未改相机设置。仓库原有120帧实采记录经V4L2/MJPG/CONVERT_RGB=0取得13420字节JPEG，SOI/EOI/SOF/熵流解析通过，并有HLS Csim golden。该证据没有发现PS采集器把解码像素误作JPEG的故障；真实PL串流仍待新候选部署后验收。此前SSH断开是否与别的板端会话相关仍未知。

2026-11-01 证据交叉核验：旧 `data/results/jpeg-axis-integration-20261101-143535/joint_connect.stdout.log` 是早期失败；当前 T6 状态以 `.runtime/j9/evidence/connect_recovery3/t6-finalization.json` 为准，其 `status=PASS`、Vivado exit 0，并列出 IP repository、BD validation、architecture readback、save BD、live sources 和 preserved readback 收据。重新计算 manifest、`joint-connected.tcldict`、最终 XPR/BD SHA 均匹配 finalization，architecture readback 为 `status OK`。它的 `board_ready=false` 仅表示尚未综合/部署；不在本轮重跑 T6。

同一真实相机 JPEG 仍存在于板端 `/home/xilinx/jpeg-probe-20261101/camera-120-frames/frame_000001.jpg`，现场 `sha256sum` 与本机 fixture 一致（13,420 bytes，SHA `34e34e90690fb1b50262d2d585406cb23f34ecdae5ac4e6d6ae1408efdf5fd0`）。因此候选 bit/HWH 可用时，可在不抢占当前摄像头的前提下先将此压缩样本经 PS DMA送入 PL；实时摄像头占用与独立实时采集仍是另一项现场状态。`scripts/rle/jpeg_transport.py` 当前只有传输类和 V4L2 生成器，仓库没有板端调用入口；一次性板测入口、frame-monitor/HDMI 结果核验仍待补齐。

2026-11-02 继续推进 T7：未重跑T6。AMD Vivado MCP client info为ROSS 2026.10.1、活动会话为空；按项目已记录的托管启动故障使用 Vivado 2026.1 batch。第一次真实pipeline runner在`create_project`阶段因`tclapp::load_apps`退出，未进入xvlog/xelab。短路径`S:`下的独立工程创建探针（工作目录在C盘项目根，日志/Journal用绝对S盘路径）通过。runner已修正以保留SUBST别名、将日志/Journal改绝对路径，并从项目根启动；输入、HLS component及流水线哈希预检和Python AST通过。最后一次失败运行仍使用旧工作目录/相对日志命令，故这些修正尚无完整T7绿验证。另一独立G2恢复综合 Vivado PID 52500仍活动；最新快照可用物理内存约1.05 GiB，不并发启动下一次Vivado。后续先等待该构建终止，再用当前runner复跑T7；本条不将启动失败归因到JPEG RTL。

2026-11-02 T7真实 JPEG RTL→冻结图像流水线 XSim PASS：运行 `.runtime/j9/t7/jpeg-pipeline-20261102-012710-5ed6cb34`；receipt 与 SHA sidecar一致，SHA-256 `fad0dba6d25417f12a259b063cc1922cc8df23ed6865b9dff9d9ff629daf4bf2`，testbench/runner SHA 与当前分支文件匹配。`JPEG_PIPELINE_PASS frame_id=1 jpeg_bytes=13420 gray_beats=64008 preview_pixels=256000 rle_words=552 frg_records=0 displayed_id=1`。所有冻结 HLS/pipeline/region/输入清单 SHA 与 receipt 一致；该结论限于 XSim。联合 synth 已对 T6 固定快照启动，目前运行到 JPEG OOC；route 尚未启动，须在审阅 synth 资源/时序/CDC/DRC/约束报告并满足 route 前置门禁后再开始。

2026-11-02 T7 联合 synth 报告恢复（覆盖上条“尚待审阅综合报告”的状态）：候选根 `.runtime/j9/k`，T6 固定 BD/XPR 快照；`synth_1` 进度 100%、状态 `synth_design Complete!`，并生成综合 DCP。原 joint capture receipt 保持 `FAIL`（Vivado code 0、`JOINT_SYNTH_RUN_COMPLETE`，导出 210 warnings，而父日志审计仅计到 4 条 warning，另有 1 条 critical warning），不得记作联合 synth 官方 PASS。只读 recovery-r2 从该综合运行生成 CDC/DRC/Methodology/clock-interaction/timing 报告，Vivado exit 0，状态 `REPORTS_GENERATED_REVIEW_PENDING`；报告路径 `.runtime/j9/k/reports/synth/`，receipt `.runtime/j9/k/evidence/synth/synth-report-recovery-r2.tcldict`。综合顶层资源 42,184 LUT、35,955 FF、30 RAMB36 + 55 RAMB18（57.5 RAMB36 等效）、81 DSP，均在配置预算内；stats/row BRAM 映射门禁通过。`timing.rpt` 显示 HDMI 像素域 WNS −9.875 ns、TNS −250.892 ns、275 个 setup fail endpoints；WHS −1.957 ns、THS −627.507 ns、328 个 hold fail endpoints。最差 setup 路径在 `binary_overlay` 的 `line_or_box5` → `overlay_q_reg`，26 级逻辑、路径延迟 19.708 ns。`cdc.rpt` 有 320 个 CDC-13 critical（非 FD 原语跨域路径）及 1,686 个 CDC-15 warning；`methodology.rpt` 另报 320 个 large-hold violation、17 个 large-setup violation、TIMING-6/7 各 1 个 clock 关系 critical warning。跨域配置寄存器 `cfg_mb_boxes_reg[*]` 到 HDMI 组合坐标逻辑直接相关；clock-interaction 将部分跨时钟路径标为 `Partial False Path (unsafe)` 或 `Max Delay Datapath Only`，hold 未因此消失。加载约束时还发现 HDMI `SerialClk` 生成时钟无从 `hdmi_out_PXL_CLK_O` 到 master 的逻辑路径，`display_timing.xdc` 又覆盖同名时钟。`drc.rpt` 163 项仅有 Warning/Advisory，无 Critical DRC。当前应停在 T7 synth review；时钟源/覆盖、unsafe CDC 与像素域 setup 必须先厘清和修正，再重跑官方捕获/资源门禁并决定是否 route。Recovery-r1 暴露资源 parser 用 `current_design NAME=design_1` 查表，但 Vivado 报告真实顶层行名为 `display_wrapper`；官方资源 parser 也需要按报告实际 top 修复。此恢复不是 route、BIT/HWH 或板端证据。

PS 入口审计（源码当前态）：`scripts/rle/run_counter.py` 的 `camera_frames()` 调用 `decode_camera_frame()` / `cv2.imdecode` 得 Gray8，并以旧 `RleDmaTransport` 发送；`scripts/rle/jpeg_transport.py` 中隔离的 `iter_raw_mjpeg()` 和 `JpegRleDmaTransport` 尚未由该入口调用。因此 JPEG DMA 的 fake-MMIO/DMA 测试与 standalone XSim 已有，主运行器集成和一次性板端 JPEG 入口仍缺。板端 v12 进程 PID 7592 占摄像头是此前只读快照；本轮未停止进程、部署或重载 PL。当前无匹配的 JPEG 联合 BIT/HWH、无 route 结果，USB camera→PL JPEG→真实 HDMI 显示仍未验证。
