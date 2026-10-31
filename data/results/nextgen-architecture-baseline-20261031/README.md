# nextgen-architecture P1 基线盘点（2026-10-31）

- 分支：`nextgen-architecture`，基线提交 `c248244`（自 `LJ-01` 创建）
- 依据：[架构评审实施计划](../../文档/架构评审实施计划_20261031.md) P1
- 性质：P1 基线盘点记录。只读核查＋文档记录；未运行软件回归、仿真、综合或板端任务。

## 1. 候选入口清单（Worker 只读核查，Manager 复核）

| 入口 | 路径 | 角色 |
|---|---|---|
| RLE 候选链（当前优先复用） | `scripts/rle/run_counter.py` | 640×400 Gray8→PL RLE→PS 原生区域分析与计数；离线/合成板测/相机模式 |
| RLE 模块 | `scripts/rle/{protocol,transport,counter,components,artifacts}.py` | 协议校验、DMA 传输、计数、原生区域组件、BIT/HWH/RTL 制品核验 |
| Gray8 运行链（当前唯一运行链路） | `scripts/gray8/run_gray8_counter.py` | 320×240 Gray8 packet 阈值化路径 |
| WO-0016 tracker | `scripts/wo0016/stream_counter.py`、`motion_association.py` | Binary8 消费、ROI 越线计数、横向顺序一对一运动关联 |
| standalone G4 候选 | `scripts/standalone/boot_integrated.py`、`integrated_run.py`、`delivery.py` | G4 候选启动/集成/包索引；已部署但 service inactive，未验收 |
| G1 显示入口 | `scripts/standalone/g1_run.py`、`boot_autostart.py` | 有限显示服务，不具备 G2 RLE 联合验收能力 |

入口状态边界：根 README 桌面入口走 GUI/RLE；Gray8 与 WO-0016 文档指向 Gray8 运行链；RLE 脚本是独立候选、未替代桌面 GUI；G4 v2 已安装但 inactive。仓库文档不能据此断言板端此刻运行哪个进程。

## 2. 位流与构建配置

- RLE 构建：`scripts/rle/build.tcl` 引用 `scripts/gray8/create_bd.tcl`，产物为 `export/rle.bit`/`rle.hwh`（复制 Gray8 wrapper），无 `.xclbin` 引用。
- standalone 构建：`create_display.tcl`/`build_display.tcl`/`create_g2_integration.tcl`/`build_g2_integration.tcl` 引用 `display.bd`、`display-pins.xdc`、`display_timing.xdc`、`monitor-cdc.xdc`，产物 `.bit`/`.hwh`。
- RLE RTL：`fpga/experimental/rle/axis_rle_encoder.v`、`axis_dma_rle_pipeline_bd.v`；standalone 视频监测 RTL：`fpga/experimental/standalone/video_frame_monitor.v`。
- G4 候选包模块清单来源：`data/results/standalone-20261031-g4-offline-package-candidate-v2/package/package-source.json`，37 个模块，由 `scripts/standalone/delivery.py` 校验。

## 3. 证据版本边界

- G4 v2 包：`CANDIDATE_NOT_BOARD_ACCEPTED`，来源提交 `6cab07f244b8a0d0fb35d6eb4a2f9a293b4ee5ed`，37 模块**不含** `scripts/wo0016/motion_association.py`（该模块为 v7 预测关联工作新增）。候选包清单落后于仓库算法变更，不能把 v2 包清单当作包含该模块的证据。
- 关键证据目录均存在：`standalone-20261031-g4-offline-package-candidate-v2`、`standalone-20261031-crossing-snapshots`、`standalone-20261031-count-optimization`、`rle-20261028-board`、`gray8-camera-20261027-122633`。
- `PROJECT_INDEX.md` 未引用 G4 v2 记录路径；`scripts/standalone/README.md` 不存在（说明位于 `DEPLOYMENT.md`）。
- 历史证据（G2/G2.5/G3/Gray8/RLE board）保持原样，未重写；本轮未改算法或部署。

## 4. 核查与验收记录

- Worker：`luna_explorer` 只读核查，未改文件、未运行测试。
- Manager：复核入口路径、构建脚本引用、G4 包 `package-source.json` 状态/来源提交/模块数，确认 37 模块不含 `motion_association.py`。
- 范围：P1 基线盘点完成；P2（输入能力与真值）尚未开始。

## 5. 下一步（P2）

- 核实相机模式/曝光/持续到达与各段耗时；建立含粘连、倒退、停顿、丢帧的真值集。
- 本阶段需要板端输入与独立真值，不能仅凭离线回放推断。
