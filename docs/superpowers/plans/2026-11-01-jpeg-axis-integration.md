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
- [ ] T1 建立验收测试：`scripts/experiments/jpeg_axis_verify/`；输入尾mod4、keep/last错误、坏header、后继恢复、有效帧全像素与packet完整性。先记录红证据，再运行新实现。
- [ ] T2 实现 `fpga/experimental/jpeg_axis/`：vendor最小依赖、来源和许可清单；header缓存、entropy AXIS feeder、8行Y重排、Gray8封包。新 dataflow 作用域需核对 hls-pragma-scope/hls-dataflow。
- [ ] T3 在真实frame1/98上 C simulation，再在120帧回放上比对；已知vendorY逐像素优先，OpenCV差异另外统计。硬件背压不能由C仿真代替。
- [ ] T4 实现 `fpga/experimental/jpeg_integration/rle_region_features.v` 的BRAM版本；512/320容量及端口语义不减。复用参考12cases并补跨帧、合并链、stall、容量边界；XSim通过后OOC综合，要求统计表实际映射为BRAM且明显降低LUT。
- [ ] T5 JPEG csynth、RTL co-sim：显式CFG/argv，独立HLS工作目录；检查是否仍有m_axi、FIFO死锁、背压和有效长度。保留失败日志，不把csynth等同于RTL通过。
- [ ] T6 联合候选构建：从offload固定快照读取模块，以优化区域核心替代原核心，在 MM2S 和 image_pipeline 之间接JPEG IP，补AXI-Lite控制、时钟/复位、DMA长度位宽。不合并其他分支、不覆盖其工作文件。
- [ ] T7 XSim接口链路验收及联合综合/布局布线；报告资源、各时钟WNS/TNS/WHS/THS、DRC/CDC和BIT状态。route未完成不得标记ready_for_board。
- [ ] T8 更新本计划、PROJECT_INDEX、文档/项目树及实验使用说明；只提交本任务文件，本地Commit；保留分支/worktree，不push/merge/部署。

## 验证命令与产物归属

- Python：`C:/Users/14173/Documents/material-counter/.venv/Scripts/python.exe`；已有基线 `-m pytest scripts/experiments/test_jpeg_probe.py -q -p no:cacheprovider` 为6通过。
- HLS：`C:/AMDDesignTools/2026.1/Vitis/bin/vitis-run.bat --mode hls --config <explicit.cfg> --work_dir C:/w/mc-jpeg-axis-hls-20261101 --csim/--cosim`；C synthesis用同目录 `v++.bat --compile --mode hls ...`。
- Vivado 使用专属 MCP Tcl session；不得接管其他会话。生成工程、DCP、日志放任务构建目录，小型来源清单和验收摘要归档到 `data/results/jpeg-axis-20261101/`。
- 实现Worker只写 `fpga/experimental/jpeg_axis/`；测试Worker只写 `scripts/experiments/jpeg_axis_verify/`；区域Worker只写 `fpga/experimental/jpeg_integration/region*` 与该目录的 `rle_region_features.v`。联合BD与总体验收由Manager安排后续Worker。

## 当前进度与下一步

2026-11-01：计划/索引/项目树已本地提交 `dd39c66`。公共 `.h`、Apache vendor子集和来源清单已准备；测试入口的红证据为 `C:/w/mc-jpeg-axis-hls-20261101/logs/initial-red-link-fail-20261101T054010.log`（缺少 jpeg_axis_gray 实现的 link fail），Manager已验收并授权继续实现。工具安装内 `utils/x_hls_utils.h` 仅作外部依赖，不随Apache源码提交。

联合构建的固定21文件快照由 `fpga/experimental/jpeg_integration/prepare_snapshot.py` 成功准备到本worktree的 `.runtime/jpeg-joint-snapshot-20261101`，来源 `c5a8680`；不读取参考worktree的未提交修改。`C:/w` 新建快照目录遇ACL拒绝，改用工作区内忽略目录，不影响源快照。只有快照准备完成，尚未联合构建。

三个实施任务继续执行，当前未完成绿验证。JPEG实现 Worker `01a193e3-532e-7d42-bcc2-7133f0107f08`；区域BRAM Worker `01a193e4-9b10-7c63-b7f7-cfe554cde962`；TB/HLS Worker `01a193e4-d87c-78c1-b417-706f418d6294`。Manager下一步复核第一次csim输出、header/熵流负例及BRAM映射，然后安排联合BD接入。新stream错误帧必须经PS确认JPEG status及RLE/FRG有效后才能提交mask_accept；此记录不表示实现或RTL/板端验收通过。
