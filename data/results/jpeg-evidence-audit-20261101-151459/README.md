# JPEG 已完成证据独立验收

检查时间：2026-11-01（见目录时间戳）。范围：只读检查既有 HLS、OOC、区域 BRAM、packet/video XSim 证据；未启动 Vivado/HLS/XSim、未接触板卡。

## 验收矩阵

| 项目 | 判定 | 证据与范围 |
|---|---|---|
| HLS 正常帧 + 熵流截断后 good2 恢复 | **PASS** | `hls-green-recovery-d695-20261101T140606.json` 状态 PASS，冻结 C++ SHA-256 `d695aac9…2108f7d`，export `component.xml` SHA-256 `0bdfce50…7246725d5`；检查 CSim/CSynth/Cosim 均 true，normal 与 entropy cosim 均 PASS。新 recovery receipt 的 34 个引用文件哈希全部匹配。原正式随机背压 normal cosim 日志/RPT显示 PASS；recovery cosim 正式 Verilog PASS 两 transaction，合计 314,358 cycles（entropy transaction 157,260；good recovery transaction 157,083；独立 normal transaction 157,260），实际输入/输出停顿且稳定性错误 0。错误帧 status=101、checksum invalid，后继同 stream frame_id=21 输出逐像素 maxdiff=0。较早 `hls-green-smoke1...140124.json` 只覆盖 normal，里面 `entropy_cosim=pending`；它是不可变历史 receipt，不能覆盖 14:06 新 recovery receipt。verify README 的 `entropy_cosim=pending` 描述旧 normal-only receipt 本身；该不可变 receipt 未改写，后续 recovery receipt 是独立的新 PASS 证据。 |
| JPEG CSim 回归 | **PASS** | 同一 recovery receipt 绑定两 vendor-Y 帧 bit-exact、120 帧 OpenCV maxdiff=1、threshold128 翻转 0、4 种 APP15 mod-4 对齐、7 类 header 拒绝及 entropy same-stream recovery。仅为算法/接口参考仿真。 |
| JPEG 实际 HLS IP OOC 综合 | **PASS（仅 OOC area）** | `fpga/experimental/jpeg_integration/jpeg_ooc.json` 与 `C:/w/jpo1101a/results.json` 绑定 D695/0bdf，结果和 7 项 OOC 产物 SHA 匹配；Vivado exit 0。17,358 LUT、13,758 FF、28 BRAM36 等效（2 RAMB36 + 52 RAMB18）、34 DSP48E1；20 ns OOC estimate WNS +4.964 ns、WHS +0.174 ns。78 warning、0 critical warning、0 error。日志注明无 `HD.CLK_SRC`，且 OOC DRC 不覆盖全部连接检查；未 placement/route，`board_ready=false`。 |
| 区域核心 BRAM 优化 | **PASS（仅 OOC/模块回归）** | 当前 `rle_region_features.v` SHA-256 与冻结 `19439a2c…a3551911` 完全一致；region receipt 的 18 项 evidence SHA 均匹配。12/12 边界/差分及 reference checks PASS，Vivado 日志有 `REGION_BRAM_PASS`。统计表 4 BRAM36、行表 1 BRAM36、stats LUTRAM=0；1,244 LUT、514 FF、5 BRAM36、3 DSP。`routed=false`。 |
| Gray8 packet→pipeline XSim | **PASS_FOR_PINNED_JOINT_CANDIDATE（固定 c5a8680 snapshot）** | 5/5 案例 PASS，覆盖 synthetic good、checksum 错、frame_id 错、后继 good recovery、vendor-Y；输入/输出背压、有效帧显式 accept 门控都有日志。receipt 36 项 evidence SHA 和 11 个输入 source/snapshot 项均匹配固定 `c5a8680` 联合候选；`axis_dma_rle_pipeline_bd.v` SHA `7cfbdde5…d50f261` 与 `C:/w/m/snapshot/fpga/experimental/rle/axis_dma_rle_pipeline_bd.v` 及 `.runtime/jpeg-joint-snapshot-20261101` 对应文件一致。联合候选有意使用该固定快照，不使用 worktree 根旧文件 SHA `2addeae7…09cee49f8`。testbench SHA `3fed9133…e24852e4` 与当前一致，region SHA `19439a2c…a3551911` 与当前一致。receipt 明确 `jpeg_rtl_decode_tested=false`：没有把真实 JPEG decoder RTL 接入此 packet 仿真。 |
| 连续视频 XSim | **PASS（固定 c5a8680 snapshot 的数字仿真）** | 5 个 1280×720 完整帧、6,187,500 拍控制时序比较、4,608,000 active pixels、20 个 RGB probe；跨帧 producer pause、拒绝坏身份、VSYNC 边界更新 ID 等覆盖。7 项 evidence SHA 及 6 个输入 source/snapshot 项均匹配。TB、runner、region SHA 与当前文件一致；display overlay RTL 来自显式声明的 `c5a8680` 固定快照。它不覆盖物理 TMDS/HDMI、板上时序或 JPEG decoder。 |
| 灰度预览 / PL 累计 glyph | **不适用或本轮未纳入验收** | 灰度预览已移出需求；JPEG 灰度仅为解码内部中间数据。PL overlay 显示二值 mask、线和框；累计 glyph 未实现，但不属于本轮资源接入验收项，因此不判测试 FAIL。 |
| 联合 BD、全系统综合/P&R、route、板端 | **未验收 / T6、T7 pending** | 最新联合候选未完成稳定接入及全系统 route。已归档前次 `refresh_region` capture fail-closed，未连接 JPEG IP、联合综合未运行；不得把 JPEG/region OOC 或 XSim 等同整机 route/HDMI/板测。 |

## 哈希检查

- HLS normal receipt：13/13 evidence 哈希匹配；HLS recovery receipt：34/34 匹配。两者均绑定 D695/0bdf。
- Region：18/18 匹配；packet：36/36 匹配；video continuity：7/7 匹配。
- packet 输入 manifest：11/11 source 与快照均存在且哈希匹配其 c5a8680 snapshot；video 输入 manifest：6/6 source 与快照均存在且哈希匹配其 c5a8680 snapshot。
- OOC `results.json` SHA 与源端 receipt 一致，7/7 声明的 OOC artifacts SHA 匹配。

## 决策边界

T5 的 HLS CSim/CSynth/官方 RTL co-sim 已验收。T6 joint BD 接入与 T7 XSim/系统综合布局布线仍未完成；当前没有整机 route、实体 HDMI 或板端验收证据。保持候选，不声称 ready for board。未运行任何工具构建或测试；未改 RTL、Tcl、Route、接口或运行实现。
