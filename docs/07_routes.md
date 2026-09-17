# 07 · Route 对比与冻结记录

> 来源：MASTER_PLAN.md v1.1 §1.6b / H3 / G1。**Day 3 起填充。**
> **Status: Pre-POC —— 三条路线均未冻结。**

## 三条候选路线

| | **Route A · 1-D Projection** | **Route B · Lightweight 2-D Feature Extraction** | **Hybrid** |
|---|---|---|---|
| **流水线** | gray → background subtraction → threshold → **column projection** → segment extraction → 1-D association | gray → segmentation → **lightweight object/region extraction** → centroid / bbox / area → constrained 2-D association | 列投影分流 + 判据触发时局部二维兜底 |
| **已知优势** | PL 实现简单（一趟扫描 + 累加器）；资源与延迟可预测 | 保留二维空间信息；两物体二维不相邻但同列时不混淆 | 兼顾两者 |
| **已知风险** | **两物体二维互不接触、但运动轴投影区间重叠时，列投影可能把两个物体压成同一个 segment → 信息损失** | PL 实现复杂度显著更高 | 判据设计本身是新风险 |
| **POC 阶段在哪实现** | Python Reference Model（即将用于 golden 向量） | **仅 Python / PS。POC 阶段不要求 FPGA 实现** | 仅 Python / PS |
| **FPGA 承诺** | 是（P2 落地） | **否**。若胜出，PL 侧实现方案需在 P2 内先出一份 ADR | 否 |

> **Route B ≠ 必须实现完整 FPGA 2-D CCL。** 完整连通域标注是本项目禁区。
> Route B 指**轻量**二维目标提取 —— POC 阶段以 `scipy.ndimage.label` 级别的复杂度在 Python 上实现。

**Hybrid 不是第三套实现**，而是判据叠加，工作量约为 Route B 的 30%。

---

## 判决矩阵（H3 六维，待填）

| 序列工况 | Route A | Route B | Hybrid |
|---|---|---|---|
| 单列正常间距 | | | |
| 小间距 | | | |
| **横向错位** | | | |
| **两物体 x 投影重叠但二维不接触** ← Route A 的证伪样本 | | | |
| 速度变化 | | | |
| 短暂停顿 | | | |

**对比维度**：counting accuracy · ID switch · missed count · duplicate count · FPGA implementation complexity · resource estimate · latency

> ★ **序列矩阵必须包含「两物体投影重叠但二维不接触」这一条。**
> 缺了它，R2 判据不成立 —— 等于用一个不能证伪假设的数据集给假设背书。

---

## 冻结结论（G1 / Day 10 填写）

| 项 | 内容 |
|---|---|
| 判决日期 | |
| 冻结 Route | |
| 依据 | |
| ADR | `docs/adr/ADR-00xx-route-freeze.md` |
| 三人确认 | ☐ A ☐ B ☐ C |

---

## 时间纪律

- Route A / B / Hybrid 三路对比必须在 **Day 10 前出结论**
- 若 Day 7 发现跑不完 → **有损降级：砍 Hybrid，只做 Route A vs B**（见 R18）
- **不要为了做完三路而推迟 G1**
