# material-counter

Agent 项目导航：[PROJECT_INDEX.md](PROJECT_INDEX.md)。

**《高速小型物料智能视觉计数系统》**——面向全国大学生嵌入式芯片与系统设计竞赛 2026 FPGA 创新设计赛道（AMD 自主选题初级组）。

平台：PYNQ-Z2（Zynq-7000 / XC7Z020）。开发环境：Vivado / Vitis / Vitis HLS 2026.1、Windows、VS Code。

仓库名保持为 `material-counter`，不随作品正式名称变化而重命名。

## 正式作品简介

本项目面向垫圈、螺母等小型物料连续输送过程中的高速计数需求，基于 PYNQ-Z2 搭建实时视觉处理系统。系统利用传送带单向运动、相邻帧目标位移较小以及编码器可获取实际带速等场景约束，目标是由 FPGA 侧完成图像采集、区域裁剪、滤波、二值化、形态学处理、轻量区域/连通域分析及目标特征提取，将原始图像转换为目标位置、面积和边界框等信息；处理器端根据相邻帧目标的位置变化建立跟踪关系，并结合运动方向、编码器带速、速度预测和目标状态管理实现稳定越线计数，减少重复计数和短时漏检。

系统采用全局快门摄像头和固定光源降低高速运动造成的拖影，通过振动盘/供料器配合单条可调速主输送带实现连续供料，并通过供料节拍与输送速度形成不同物料密度工况。实时画面及计数结果计划通过显示屏输出，可同步显示累计数量、输送速度、处理帧率和当前运行状态。

上面是目标系统口径，不代表所有模块已经实现。PR #10 已于 2026-10-18 合并到 `main`（merge commit `5926820`），其中包括 WO-0015 S2MM Binary8 帧回传和 WO-0016 Stage A 连续流优化。WO-0015 记录了单帧、三帧及一次 OV9281 相机帧回传通过；WO-0016 Attempt 8 达到 15.157 FPS，但测量对象是空载 30 帧基线。Stage B 动态物料计数、计数准确率和整机验收仍未完成。具体证据与限制见下方“当前状态”。

## 系统主链路

```text
振动盘/供料器
  → 单条可调速主输送带 + 编码器
  → 全局快门摄像头 + 固定光源
  → PYNQ-Z2
  → PL：图像采集 / ROI / 滤波 / 二值化 / 形态学 / 轻量区域特征提取
  → PS：编码器带速 / 跨帧关联 / 运动方向 / 速度预测 / 目标状态管理
  → 越线计数
  → 显示实时画面、累计数量、输送速度、处理帧率和运行状态
```

当前不采用“独立双传送带差速机构”作为正式机械方案。摄像头的具体 HDMI、DVP 或其他接入路线仍需工具链和硬件验证，不得把候选路线写成已完成。

## 系统做什么

| | 内容 |
|---|---|
| **输入** | 连续运动物料的视频流 |
| **主体处理** | PL 图像前端 → 目标区域/特征提取 → PS 跨帧关联与持续跟踪 → 编码器带速辅助 → 跨线计数 |
| **主体输出** | 当前计数 · 目标轨迹 / ID 状态 · 位置 · 编码器带速 · 系统运行指标（帧率 / 端到端延迟 / FPGA 资源占用） |
| **后期探索** | 利用实时运动状态进行送料停止时机预测与控制（见下 Exploratory 层） |

**主体是“视觉感知 + 跟踪 + 计数”，不是简单的单帧检测加一。** 送料停止预测属于后续探索，不是 Core/MVP 的完成前提。

## 功能层级

| 层 | 内容 |
|---|---|
| **Core / MVP —— 必须完成** | ① 连续运动物料视觉感知 / 目标区域提取　② 跨帧关联　③ 持续目标跟踪　④ 跨线计数　⑤ 防止同一物料重复计数　⑥ 获取目标位置、速度等运动状态　⑦ 在 PYNQ-Z2 上形成完整可运行系统　⑧ 对计数准确率、处理帧率、端到端延迟、FPGA 资源占用进行实际测量 |
| **Robustness —— 主体增强** | 验证：不同物料间距 / 不同运动速度 / 位置错位 / 相邻目标接近 / 有限程度的短时遮挡与临时合并 / 间歇漏帧。工作包线以 H5 实测为准；**"通用遮挡跟踪"不属于本项目的必须解决问题** |
| **Exploratory / A2-lite —— 非主体硬要求** | 主体跟踪与计数稳定后，探索利用目标位置、速度、在途物料数量进行送料停止时机预测与控制，并与普通计数停止方式对比。**失败不影响主体项目成立** |

详细边界见 [docs/00_scope.md](docs/00_scope.md)；层级映射见 MASTER_PLAN §3.0b。

## 当前状态

| 状态 | 内容 |
|---|---|
| **Implemented / Offline verified** | `python/synth/` 确定性合成数据与回归；`fpga/rtl/` 流骨架、RGB→Gray、Gray→Binary、ROI、3×3 形态学；`python/refmodel/ps_reference/` 区域/跟踪/越线参考；WO-0014 RGB 包流水线及 WO-0015/WO-0016 软件链路。相关离线回归记录为 244 passed、1 skipped；测试输入清单由现存 H3 PNG 重建，原始清单的规范哈希来源未复核。离线验证不等于上板验收。 |
| **Board-tested / 有限通信证据** | WO-0013 单次 MM2S→PL Monitor 已通过；WO-0014 50 MHz 单帧及三帧聚合统计有板上记录，但三帧任务最终为 `FAIL_STOPPED`；WO-0015 单帧、三帧和一次相机帧的 Binary8 S2MM 回传记录为 PASS；WO-0016 Attempt 8 通过 30/30 帧 Packer 校验，空载端到端吞吐 15.157 FPS。 |
| **Synthesized / Implemented timing** | WO-0014 的 50/100 MHz 完整工程均有综合、布线时序及 BIT/HWH；100 MHz 板测为 `NOT_VERIFIED`。ROI、形态学的独立 OOC 综合不能当作完整系统时序闭合。 |
| **Planned / Proposed** | 正式 PL 特征提取与生产 ABI、持续板端 PS 跟踪/越线计数和显示、Stage B 动态 M6 物料验收及准确率、编码器带速验收仍待完成。Stage A `avg_roi_count_ms=17.657 ms`，高于 2.5 ms 子目标；Stage B 150 帧动态投料尚未运行。WO-0012 特征/序列化仍为候选草案。 |
| **Exploratory** | 结合计数、在途量和实际带速进行送料停止时机预测与控制；失败不影响 Core/MVP。 |

当前已完成若干隔离 POC，**Architecture / Route Not Frozen**。Route A、Route B、Hybrid 以及真实视频输入路线仍需正式决策；PR 合并不代表架构冻结或硬件验收。

### 合并范围与验证依据

本节以合并后的源码及已有验收证据为准。PR #10 将 WO-0015 S2MM Binary8 帧回传和 WO-0016 Stage A 连续流计数实现合入 `main`；当前核验基线为 merge commit `59268209148888c135ff7ff5c0aa7dba8ddbaf66`，与 `origin/main` 一致。历史交付批次及提交来源见 [2026-10-15 历史审计](docs/verification/submission-status-20261015.md)，当前汇总见 [2026-10-18 提交状态](docs/verification/submission-status-20261018.md)。

[2026-10-15 提交审计](docs/verification/submission-status-20261015.md)是历史快照，其中 PR 待合并、草稿链等文字只描述当时状态；当前 Git 基线及 WO-0015/WO-0016 证据见[最新提交状态](docs/verification/submission-status-20261018.md)。ROI 与 morphology 的历史 Work Order 都使用 WO-0008 编号，按文件名区分。

| 模块 / 实验 | 已有证据 | 适用边界 |
|---|---|---|
| [公共前端](docs/verification/frontend-handoff.md) | 流骨架、RGB→Gray、Gray→Binary 的模块仿真、独立像素比对与综合；后续阈值回归见审计记录。 | 模块功能通过不代表真实摄像头输入或完整系统验收。 |
| [ROI](docs/verification/wo-0008-handoff.md) | `axis_roi_crop.v` 实现真实裁剪、重建 SOF/EOL；24 组有效测试、8 组非法参数拒绝及 3 组联合入口检查，独立比对零差异，Lint/OOC 综合完成。 | 半开窗口、空 ROI 无输出；未接入 WO-0014 板测链，未上板或校准实物窗口，无完整系统时序证据。 |
| [3×3 形态学](docs/morphology-verification-20261013.md) | `axis_morphology3x3.v` 支持旁路/腐蚀/膨胀、零背景边界；84/84 仿真用例、2,806,344 像素独立比对零差异，Lint/OOC 综合完成。 | 行接收/行输出交替，不能宣称持续每周期输入一像素；未串入 WO-0014 板测 BIT，无该模块板测或完整实现时序证据。 |
| [PS 计数参考](docs/verification/wo-0010-ps-reference.md) | PC 离线 Binary8→8 连通区域→关联跟踪→越线去重。`[Measured: wo0010-reference-final-20261013]` 原 PNG 通路在 31 个可确认事件中 TP=19、FN=12，召回率 61.29%。 | 无缝接触导致的分割漏计仍存在；GT 身份不进入关联器。不是板端 DMA 接收服务，不能把 GT 几何诊断或精确率当作整机准确率。 |
| [WO-0013 DMA](docs/verification/wo-0013-level2-accepted-20261014.md) | `[Measured: wo0013-pagemap-policy-20261014-120205]` PYNQ-Z2 单次发送 76,816 字节，PL 接受 19,204 拍、1 次 TLAST，首末字和校验和一致；测试后 Ping/SSH/Linux 健康检查通过。 | 仅 Binary8+元数据的 DDR→MM2S→Monitor，不是 RGB 图像处理；无 S2MM、无整帧回读、无持续运行或整机计数验收。使用记录中的 resetfix BIT/HWH，不可随意替换产物。 |
| [WO-0014 离线链路](docs/verification/wo-0014-offline-integration-20261014.md)与[优化时序](docs/verification/wo-0014-timing-acceptance-20261014.md) | RGB 包→adapter→Gray→Binary→checker；15 项 XSim，487 帧、37,401,600 像素独立比对零差异，覆盖 7 种坏包及恢复；后续优化完成 OOC 时序验收。 | ROI/形态学未串入该链。错误帧前缀必须整帧作废；`packet_done` 不表示输出已排空，checker 不是通用异常帧守卫。 |
| [WO-0014 完整实现](docs/verification/wo-0014-hw-integration-20261014.md)、[50 MHz 单帧板测](data/results/wo0014-pg021-ready-20261015/report.md)与[三帧收官](data/results/wo0014-closeout-20261015/report.md) | 50/100 MHz 完整实现时序通过，BIT/HWH 与源码有哈希绑定；50 MHz 单帧 PASS，另有三帧 DMA/checker/Golden 聚合统计 PASS。 | 三帧任务最终因 CMA 健康门为 `FAIL_STOPPED`；100 MHz 板测全部 `NOT_VERIFIED`。聚合统计不能替代逐像素回读；DRC 的 14 Warning、2 Advisory 及 LUTAR-1 methodology 风险仍保留。 |
| [WO-0015 S2MM Binary8 帧回传](docs/work-orders/wo-0015-s2mm-binary8-frame-return.md)与[最终板端记录](data/results/wo0015-final-20261017/acceptance-report.md) | 单帧、三帧及一次 OV9281 相机帧回传记录均为 PASS，逐字节差异为 0；当前 `main` 中两个相关执行脚本的 SHA-256 与板端结果记录一致。 | 这些证据证明 Binary8 实验回传，不代表生产特征接口或持续计数闭环；测试输入清单来源限制见离线回归说明。 |
| [WO-0016 Stage A](docs/work-orders/wo-0016-async-double-buffer-stream-counter.md)与[板端验收记录](docs/verification/wo-0016-board-acceptance.md) | Attempt 8 完成 30/30 帧，Packer 校验 30/30，空载吞吐 15.157 FPS；本机 HTTP 回环收到 8 帧，5.921 FPS。 | 空载 0/0 不是物料准确率验收；ROI 计时 17.657 ms 高于 2.5 ms 子目标；Stage B 动态投料 150 帧仍为 `NOT RUN`。 |

### DMA / Overlay 安全限制

- 已有证据不授权再次执行硬件操作。Overlay、MMIO、DMA 均需新的明确批准，限定产物、板身份和单次范围；失败即停，不自动重试、reset 或 reload。Overlay 加载成功、一次寄存器读取与 DMA 完成是不同证据等级。
- WO-0014 三帧任务外层状态必须保留为 `FAIL_STOPPED`。CMA 前后原始数值未持久化，既不能据此断言泄漏，也不能忽略健康门或宣称恢复准入。不得放宽 CMA 门、删除/更换 Guard 或绕过停止状态；只读 OS 取证也需单独批准。
- WO-0013 通用 `python/boardtests/wo0013/dma_replay_verify.py` 会加载 Overlay 并在错误时 reset/re-arm，**不是已验收单次实验的入口**，不可用于禁止 reset/reload 的授权。一次成功不代表可重复安全运行。
- [WO-0014 DMA 长度评估](docs/verification/wo-0014-dma-length-assessment-20261014.md)：320×240 RGB 实验包为 230,432 字节，硬件使用 18 位 DMA 长度字段，S2MM 未启用。输入包 TLAST 结束整个包，图像输出 TLAST=EOL；未经 EOF 回传桥设计，不能直接当作整帧 DMA 完成边界。

### 离线完整性与复现

从仓库根目录运行以下只读完整性检查（仅需 Python 标准库，不访问开发板）：

```powershell
python scripts/delivery/verify_delivery.py
```

它核对交付清单、关键源码/测试/产物哈希、受保护文件及 `FAIL_STOPPED` 状态，不能替代新仿真、综合或板测。跨目录离线输入准备、XSim 和独立比对步骤见 [scripts/delivery/REPLAY.md](scripts/delivery/REPLAY.md)，依赖快照见 [requirements.txt](scripts/delivery/requirements.txt)。生成输入及仿真输出必须使用新的目录，禁止覆盖已有数据或证据。

## 术语纪律

Route 尚未冻结。文档与代码中优先使用**视觉感知 / 目标区域提取 / 特征计算 / 跨帧关联**。
**不预设** YOLO / CNN / 完整目标检测网络 / 特定 CCL 或 segmentation 实现。"检测"可作为功能层概念使用，但不得因此提前冻结实现方法。

## 唯一权威文档

**[MASTER_PLAN.md](MASTER_PLAN.md)** —— Master Plan v1.1 及后续 Controlled Errata，作为当前计划与边界的权威记录。
本仓库的所有工作必须遵循该文件。它不在日常开发中被修改；如需勘误，走 Controlled Errata 流程并在本文档记录。

## 仓库结构

开发阶段使用当前结构；**最终提交前**需在本文档建立与官方推荐目录
（`src/ sim/ build/ board/ data/ skill/ report/`）的对应关系表。

```
docs/        文档（scope / 接口契约 / 风险 / Route 记录 / ADR / AI 协作记录）
python/      合成数据、图像参考模型、PS 离线计数参考及实验/测试
fpga/        PL RTL、仿真、实验模块、约束与复现脚本
scripts/     精选交付校验、离线复现与实验工程构建入口
hw/          硬件平台（BOM / 接线 / 装配 / 标定 / 引脚预算）
skill/       AI 协作 skill 候选池
data/        数据集与实验证据（synthetic / recorded / labeled / results）
```

## 命名纪律

目录名与文件名统一使用**英文小写字母、数字、`_`、`-`**；不使用中文、空格或特殊字符。
**Python 包与模块用 `_`**（避免 `-` 造成 import 问题）。

## 证据纪律

所有性能数字必须带以下前缀之一，见 MASTER_PLAN「记号约定」：

| 记号 | 用途 |
|---|---|
| `[Target]` | 工程目标，尚未验证 |
| `[Hypothesis]` | 实验假设，未经本项目实测 |
| `[Measured: <expid>]` | 实测结果，来自 `data/results/<expid>/` |
| `pre-synthesis estimate` | 资源类数字，未综合 |

**禁止提前把预期结果填成实际结果。** 论文 / PPT / 答辩只允许引用 `[Measured]`。

## 角色

- **Codex Manager**：确定任务范围与架构边界、处理歧义和风险，审查实际修改与验证结果，负责最终验收。
- **Codex Worker**：在明确的任务和文件边界内完成定向检索、实现或验证；发现范围或设计问题时升级给 Manager，并简要报告修改、测试和遗留问题。

## 运行现有合成数据测试

在安装 `python/synth/requirements.txt` 后，从仓库根目录运行：

```powershell
python -m pytest python/synth/tests -v
```
