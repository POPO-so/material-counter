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
- [ ] T5 JPEG csynth、RTL co-sim：显式CFG/argv，独立HLS工作目录；检查是否仍有m_axi、FIFO死锁、背压和有效长度。保留失败日志，不把csynth等同于RTL通过。
- [ ] T6 联合候选构建：从offload固定快照读取模块，以优化区域核心替代原核心，在 MM2S 和 image_pipeline 之间接JPEG IP，补AXI-Lite控制、时钟/复位、DMA长度位宽。不合并其他分支、不覆盖其工作文件。
- [ ] T7 XSim接口链路验收及联合综合/布局布线；报告资源、各时钟WNS/TNS/WHS/THS、DRC/CDC和BIT状态。route未完成不得标记ready_for_board。
- [ ] T8 更新本计划、PROJECT_INDEX、文档/项目树及实验使用说明；只提交本任务文件，本地Commit；保留分支/worktree，不push/merge/部署。

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
