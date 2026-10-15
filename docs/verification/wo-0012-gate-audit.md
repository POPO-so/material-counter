# WO-0012 · G1 / Route / ADR 门控审计

审计日期：2026-10-13。本文只记录仓库中可核对的门控规则和证据状态；不构成 G1 通过、Route 冻结、接口批准或 PL 开工授权。本文没有修改四份受保护文档。

## 1. 项目正式 Route 与本轮三种架构候选

项目 Route 的正式名称是 **Route A（1-D Projection）**、**Route B（Lightweight 2-D Feature Extraction）** 和 **Hybrid**。Route A 做列投影与段分割；Route B 保留二维区域特征；Hybrid 以列投影分流，并在判据触发时采用局部二维兜底（`MASTER_PLAN.md:161-181`；`docs/07_routes.md:6-19`）。三者当前均为候选，尚未冻结（`MASTER_PLAN.md:3-5`；`docs/00_scope.md:52-55`；`docs/07_routes.md:3-4`）。

WO-0012 评估的 **完整 CCL、受限 PL 特征提取、PS 区域分析兜底** 是实现范围候选，不是 Route A/B/Hybrid 的别名：

| 实现范围候选 | 与正式 Route 的关系 | 当前约束 |
|---|---|---|
| 完整、无资源边界的二维 CCL | 不等于 Route B | Scope 明确排除此类通用 CCL；Route B 也明文“不等于必须实现完整 FPGA 2-D CCL”（`docs/00_scope.md:30-31`；`MASTER_PLAN.md:175-177`）。若要改变此边界，需另行处理 scope 与架构决策。 |
| 受限 PL 特征提取 | 可作为 Route B 胜出后的 PL 实现候选 | Route B 在 POC 阶段只要求 Python/PS；若胜出，PL 侧最小实现须另有 ADR，未决前不得开工（`MASTER_PLAN.md:170-177`；`docs/07_routes.md:13-17`）。 |
| PS 区域分析兜底 | 参考验证 / 工程兜底候选 | WO-0010 记录的批准限于“原则用途”；具体 DMA、驱动和生产接入未确认或实施（`docs/wo-0010-binary-frame-transport-proposal.md:3-5`）。它本身不完成 Route 比较或冻结。 |

## 2. G1 的正式验收条件

G1 有两项并行判据，**R1 与 R2 缺一不可**。Master Plan 明确指出 R1 过而 R2 未完成时 G1 不算通过；R2 缺失时不得进入 P2 后端（`MASTER_PLAN.md:395-402`；`MASTER_PLAN.md:1122-1123`；`MASTER_PLAN.md:1132-1139`）。

| 判据 | 文档规定 | 对应的必备材料 |
|---|---|---|
| R1 · 计数能力 | 合成数据 `[Target]` 计数准确率 ≥99%、重复计数 = 0、漏计率 ≤1%；录播数据 `[Target]` ≥97%（`MASTER_PLAN.md:397-400`）。H3 还列出准确率 ≥99%、重复计数 = 0、ID Switch ≤2%，并要求注入1–2帧间歇漏检仍成立（`MASTER_PLAN.md:511-523`）。 | 可复现、标注可信的合成与录播评测；H3 指定至少15条合成序列、至少6条专项序列、至少5条录播序列、累计物料数≥300（`MASTER_PLAN.md:515-521`）。 |
| R2 · Route 判决 | 在含横向错位与投影重叠的子集上，Route A/B/Hybrid 对比表完成，结论能由三人复述（`MASTER_PLAN.md:397-402`）。 | 同批序列上的 Route A/B/Hybrid 六维比较；强制包含“两物体 x 投影重叠但二维不接触”的 Route A 证伪样本（`MASTER_PLAN.md:515-520`；`docs/07_routes.md:23-37`）。 |

Master Plan 将 R2 和路线冻结称为“六维对比”，但其逐项列出了计数准确率、ID Switch、漏计、重复计数、FPGA 实现复杂度、资源估计、延迟七项（`MASTER_PLAN.md:519-520`；`docs/07_routes.md:34`）。应在 G1 checklist 中说明哪些项目合并为一个维度，避免验收时各自按不同维度数判定。

H3 还要求逐事件可追溯至帧号和位置（`MASTER_PLAN.md:519`）。Route 冻结产物应写入 Route ADR 并同步到 `docs/07_routes.md`（`MASTER_PLAN.md:181`）；Day 10 的验证段同样要求该对比表与 `adr-00xx-route-freeze.md` 存在（`MASTER_PLAN.md:1148-1152`）。

## 3. 当前仓库证据与签字状态

截至本次只读检查，**不能根据仓库证据认定 G1 已通过或 Route 已冻结**：

- WO-0010/0011 的 H3 图像评估为19/31个可确认事件，召回率61.29%；WO-0011 明确指出这不足以声称达到 G1 准确率目标（`docs/verification/wo-0010-ps-reference.md:67`；`docs/verification/wo-0011-segmentation-audit.md:3,165-166`）。这项严格事件协议的召回率不应未经统一指标定义便改写为正式 R1 的 `count_accuracy`。
- `docs/07_routes.md` 标记 `Pre-POC`，所有 Route 比较单元格为空，冻结日期、Route、依据、ADR 均为空，A/B/C 确认框未勾选（`docs/07_routes.md:3-4,23-49`）。
- 当前唯一的投影诊断文件 `data/results/wo0010-reference-final-20261013/g1-projection-diagnostic.json` 明确标记为“G1 diagnostic only, not full Route-A implementation or freeze”；它只对一条 `projection_overlap_noncontact` 序列逐帧记录20个二维区域与1个非零列投影段，160线的可确认真值为0。128线补充到4个真值，但没有完整 Route A 事件评测。因此该文件是反例线索，不是 R2 三路同序列比较。
- `docs/wo-0011-g1-feature-requirements.md:61-64` 与 WO-0011 报告 `:165-168` 列出尚缺的独立 Route A 完整比较、共同有事件窗口、真实录播人工真值、机械工作包线及 PL 资源/吞吐证据。当前 checkout 没有 `data/recorded/` 目录，因此仓库内看不到符合 H3 要求的至少5条录播及其人工真值；不据此推断仓库外是否存在素材。
- 门控扫描时 `docs/adr/` 只有 ADR-0001 视频源路线（状态 `Proposed`，视频源未冻结）和 ADR-0002 当前工作基线（状态为三人评审后生效）；没有 `adr-00xx-route-freeze.md`。本轮随后新增ADR-0003技术草案也不构成Route冻结或人工批准。ADR-0002 的状态见 `docs/adr/adr-0002-current-project-baseline.md:5-8`；ADR-0001 见 `docs/adr/adr-0001-video-source.md:1-8`。
- `docs/02_interfaces.md` 写明接口变更须 PR + 三人确认 + 一次 ADR，并且接口文档三人签字仍待 G0 完成（`:3-12`）；A/B/C 的签字日期和确认栏为空/未勾选（`:104-112`）。任何特征记录字段或语义的变更都不能由本轮候选设计自动批准。

Master Plan 的共同审查分工要求 Route 冻结结论由三人全体在 G1 确认（`MASTER_PLAN.md:684-694`）。附-6 要求架构性变更先有 ADR、ADR 由人撰写（AI 只能填技术细节），Route 冻结本身也是 ADR；接口变更仍需 PR 与三人确认（`MASTER_PLAN.md:1018-1032`）。因此“准备好方案”“一人同意”或已有 PS 参考代码均不等于正式审批。

Master Plan 的 Day 10 清单还列出录播端到端可视化、摄像头选型与采购、机械 BOM 与采购、三人分别运行官方 overlay 等产出（`MASTER_PLAN.md:1107-1116`）。它们涉及仓库外实体状态；本审计不将缺少仓库记录当作这些事项必然未完成，须由团队提交对应证据核验。

## 4. 分阶段审批边界

1. 先按 G1 checklist 核对 R1 与 R2 的完整证据，并由三人共同复述/确认是否通过；未满足时如实记录未通过或待补证，不把 WO-0010/0011 的局部结果写成 Route 决定。若建议条件推进，必须另有正式重基线审批，不能用“条件通过”替代原G1判据。
2. 若三人完成 R2 评审，将 Route A/B/Hybrid 的比较、选择依据、证伪结果和确认写入 Route 冻结 ADR，并同步 `docs/07_routes.md`；即使 R2 已形成结论，若 R1 未满足，仍不得标记 G1 通过或据此宣称可进入后端阶段。Route B 的选择只决定正式算法 Route；它仍不自动决定 PL 如何实现。
3. 若后续选择 Route B，再单独由人批准 PL 最小实现 ADR 和资源/吞吐边界，之后才启动 Route-specific PL 后端（`MASTER_PLAN.md:170-177`；`docs/07_routes.md:13-17`）。完整 CCL 是否可做仍受 Scope 约束。
4. 若需要修改接口 B，再按 PR + 三人确认 + ADR 处理；若沿用原字段，则仍需遵守当前接口签字与原子帧校验状态（`docs/02_interfaces.md:3-12,52-68,104-112`）。
5. PS 区域分析兜底的原则批准仅涵盖参考/工程兜底用途。具体 DMA/VDMA 选择、EOF/TLAST适配、元数据、缓存一致性和生产接入仍是独立设计/审批项（`docs/wo-0010-binary-frame-transport-proposal.md:3-5,13-30`）。

本审计的建议边界是分开确认 **G1 是否通过、正式 Route 选择、Route B 的 PL 实现 ADR、接口变更、临时 PS 帧通路的生产接入**。任何一项都不替代其余项，也不在此文件中代团队作出结论。
