# material-counter

**《连续物料视觉跟踪与计数系统》**（英文工作名：Continuous Material Visual Tracking and Counting System）—— 全国大学生嵌入式芯片与系统设计竞赛 2026 FPGA 创新设计赛道（AMD 自主选题初级组）。

平台：PYNQ-Z2（Zynq-7020，XC7Z020）。

## 系统做什么

| | 内容 |
|---|---|
| **输入** | 连续运动物料的视频流 |
| **主体处理** | FPGA / SoC 实时视觉处理 → 目标区域提取 / 特征计算 → 跨帧关联 → 持续跟踪 → 跨线计数 → 运动状态 |
| **主体输出** | 当前计数 · 目标轨迹 / ID 状态 · 位置 · 速度 · 系统运行指标（帧率 / 端到端延迟 / FPGA 资源占用） |
| **后期探索** | 利用实时运动状态进行送料停止时机预测与控制（见下 Exploratory 层） |

**主体是"跟踪 + 计数"，不是"定量送料"。**

## 功能层级

| 层 | 内容 |
|---|---|
| **Core / MVP —— 必须完成** | ① 连续运动物料视觉感知 / 目标区域提取　② 跨帧关联　③ 持续目标跟踪　④ 跨线计数　⑤ 防止同一物料重复计数　⑥ 获取目标位置、速度等运动状态　⑦ 在 PYNQ-Z2 上形成完整可运行系统　⑧ 对计数准确率、处理帧率、端到端延迟、FPGA 资源占用进行实际测量 |
| **Robustness —— 主体增强** | 验证：不同物料间距 / 不同运动速度 / 位置错位 / 相邻目标接近 / 有限程度的短时遮挡与临时合并 / 间歇漏帧。工作包线以 H5 实测为准；**"通用遮挡跟踪"不属于本项目的必须解决问题** |
| **Exploratory / A2-lite —— 非主体硬要求** | 主体跟踪与计数稳定后，探索利用目标位置、速度、在途物料数量进行送料停止时机预测与控制，并与普通计数停止方式对比。**失败不影响主体项目成立** |

详细边界见 [docs/00_scope.md](docs/00_scope.md)；层级映射见 MASTER_PLAN §3.0b。

## 术语纪律

Route 尚未冻结。文档与代码中优先使用**视觉感知 / 目标区域提取 / 特征计算 / 跨帧关联**。
**不预设** YOLO / CNN / 完整目标检测网络 / 特定 CCL 或 segmentation 实现。"检测"可作为功能层概念使用，但不得因此提前冻结实现方法。

## 唯一权威文档

**[MASTER_PLAN.md](MASTER_PLAN.md)** —— Master Plan v1.1，**已冻结**。
本仓库的所有工作必须遵循该文件。它不在日常开发中被修改；如需勘误，走 Controlled Errata 流程并在本文档记录。

## 状态

- Status: **Pre-POC / Architecture Not Frozen**
- Day 0 = 2026-09-18，Day 46 = 2026-11-03
- Route A（1-D Projection） / Route B（Lightweight 2-D） / Hybrid **均未冻结**，由 G1（Day 10）判决

## 仓库结构

开发阶段使用当前结构；**最终提交前**需在本文档建立与官方推荐目录
（`src/ sim/ build/ board/ data/ skill/ report/`）的对应关系表。

```
docs/        文档（scope / 接口契约 / 风险 / Route 记录 / ADR / AI 协作记录）
python/      算法与评测（synth 合成数据生成器、后续 refmodel / routes / metrics）
fpga/        PL 侧（rtl / constraints，后续 hls / sim / scripts / ip）
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

- **Claude** —— Planning / Review Agent：读 MASTER_PLAN、拆任务、查架构一致性、给 Codex 定义任务与验收标准、review 结果、报 blocker
- **Codex** —— Implementation Agent：创建/修改仓库文件、写代码与测试、执行命令、运行验证、报告实际结果
