# AI 协作证据记录

> 依据：MASTER_PLAN.md v1.1 附-6 第 8 条（2026 官方要求）。
> **从 Day 0 开始保存。** 凡 AI 参与架构决策、RTL/HLS 生成、debug、资源/时序优化、接口设计等关键工作，逐条记录。
>
> 记录格式：`prompt → AI response → 人工判断 → 验证方法 → 验证结果 → 最终修正`
>
> 有跨项目复用价值的内容 → 进入 `skill/` 候选池。

---

## 案例 1 · Route A/B/Hybrid 的质疑与冻结过程

| 项 | 内容 |
|---|---|
| **阶段** | Master Plan v1.0 → v1.1 修订期（Day 0 之前） |
| **Prompt** | 用户要求对 v1.0 方案做 v1.1 修订，并列出 10 项必改问题 |
| **AI response** | v1.0 把「2-D → 列投影 → 1-D 段 → 一维关联」写成了已成立的最终技术路线 |
| **人工判断** | **用户否决**：若两物体二维空间互不接触、但在运动轴上的投影区间重叠，列投影可能把两个物体压成同一个 segment，造成信息损失。结论证据不足 |
| **验证方法** | 检查 v1.0 的 POC 数据集（H3 输入）是否含证伪样本 |
| **验证结果** | **不含** —— 原数据集只有 3 速度 × 3 间距 + 噪声/掉帧，**无横向错位、无投影重叠工况**。等于用一个不能证伪假设的数据集给假设背书 |
| **最终修正** | ① 撤回「它是一维问题」的断言；② 新增 §1.6b，Route A / Route B / Hybrid 并列；③ H3 序列矩阵新增「两物体 x 投影重叠但二维不接触」专项序列；④ G1 增设 Algorithm Freeze 职责（R2 判据） |
| **状态** | 冻结流程进行中，G1（Day 10）出结论后回填 |

**复用价值**：★ 高。"AI 给出一个看似自洽的结论 → 人工追问'你的数据集能证伪它吗' → 发现不能 → 补上证伪样本" 这个模式可沉淀为 **Route/Hypothesis Validation** skill。

---

## 案例 2 · BRAM 资源核算纠错

| 项 | 内容 |
|---|---|
| **阶段** | Master Plan v1.1 修订期 |
| **Prompt** | 修正风险表 R9 的 BRAM 估算 |
| **AI response（第一次，错误）** | 「BRAM36 按 36-bit 宽配置约 **4,096 B/块** → 约 **2 块 BRAM36**（或 19 块 BRAM18）」 |
| **人工判断** | **用户否决**：4,096 B/块 无依据；2 块根本装不下 76,800 B；且未考虑 RAMB36E1 最大深度 1024 的限制 |
| **验证方法** | 重新核算：RAMB36E1 = 36 Kb = 36,864 bit = **4,608 B/块**；RAMB18E1 = **2,304 B/块**；按 depth×width 配置分档计算 |
| **验证结果** | 320×240 Gray8 = 76,800 B/帧 → 512×72 或 1024×36 需 **17 块**；1024×18 需 **34 块**；1024×8 需 **75 块**。**块数对打包方式极敏感，跨度 25 倍，不能当常数写** |
| **最终修正** | R9 改为分档估算示例 + 明确标注 **pre-synthesis estimate**；补纪律句：**不得提前断言实际块数**；**最终以 `report_utilization -hierarchical` 为准** |
| **二次人工判断** | 用户再次指出：1024×8 被写成了"简单推断的默认结果"，而实际 BRAM inference 取决于 HDL/HLS 写法、端口结构、位宽组织与综合优化 |
| **最终修正（第二次）** | 1024×8 改写为「**最不利的窄位宽组织示例**」，明确「不是 Vivado 默认一定产生的结果」 |
| **状态** | 已修正，见 MASTER_PLAN Change Log N1 条与风险 R9 |

**复用价值**：★ 高。教训是**"把与实现配置相关的量写成固定常数"这类错误极易出现在 AI 生成的资源估算里**。可沉淀为 **Vivado Report Analysis / Resource Estimation** skill 的第一条检查项。

---

## 案例 3 · 冻结文档内部口径矛盾（Controlled Errata 流程）

| 项 | 内容 |
|---|---|
| **阶段** | Day 0 执行期 |
| **触发** | 拆解 Day 0 任务时，发现 MASTER_PLAN §10.3 第 1 条仍写「核实 PYNQ-Z2 上到底有几个 HDMI 口、分别是 in 还是 out」，与同一文档的 C2（已确认具备 HDMI IN / OUT，AMD 2026 官方指南）矛盾 |
| **AI response** | **停止自行处理，向用户报告冲突**（依用户设定的规则：发现内部矛盾先报告，不擅自修改冻结文档） |
| **人工判断** | 授权按 (a) 方案修正 §10.3，与 C2 对齐；明确定性为 **Controlled Errata / Consistency Fix**，**不视为重新开放 Master Plan**，不允许借此修改其他架构、时间表或 scope |
| **验证方法** | 改后全文搜索 `HDMI` / `HDMI IN` / `C2` / `§10.3`，检查一致性 |
| **验证结果** | 旧表述无残留；C2 四处引用（C2 表 / H1 表 / 成员 C P0 / §10.3）口径统一；无新矛盾 |
| **最终修正** | §10.3 第 1 条改为「核实 PYNQ-Z2 HDMI IN 在当前 PYNQ/Vivado 环境中的实际接入条件，包括 ① 可用实现路径；② 所需 IP / Overlay；③ 接入自定义 AXI4-Stream PL 图像处理流水线的可行性；④ 是否适合作为 Competition Version 的最终真实视频输入」 |

**复用价值**：★ 中高。"冻结文档的勘误边界"是团队协作规范问题 —— 明确 **Controlled Errata ≠ 重新规划**，可为后续所有文档变更提供流程模板。

---

## 案例 4 · 含中文的项目路径风险评估与迁移

| 项 | 内容 |
|---|---|
| **阶段** | Day 0 执行期 |
| **触发** | 原工作区为 `C:\Users\POP\Desktop\FPGA赛\`，**含中文**，与附-1 的命名规范（英文小写字母/数字/`_`/`-`）冲突 |
| **AI response** | 报告冲突，给出三个选项（新建合规目录 / 容忍中文路径 / 用户指定），不自行决定 |
| **人工判断** | 选择迁移至短英文路径 `C:\fpga\material-counter\` |
| **评估的工程风险** | Vivado / Vitis / Tcl / Python 在中文路径下的已知问题；Vivado 自动生成深层目录后**路径长度**风险；多人协作与 Git 的可移植性 |
| **验证方法** | ① 迁移前勘察目标路径状态（避免误覆盖）；② **复制 → md5 校验 → 才删原件**，三步不合并；③ 校验行数/字节/md5/UTF-8 可读 |
| **验证结果** | md5 迁移前后一致（`1c84ea8605875e2a613b45efa44d87e0`）；1147 行；UTF-8 可读；勘误内容已随文件迁移 |
| **最终修正** | 项目根目录统一为 `C:\fpga\material-counter\`；原目录仅保留两份参考 PDF（含中文文件名，不入仓库） |

**复用价值**：★ 中。"迁移敏感资产时的复制-校验-删除三段式"可沉淀为通用操作规范。

---

## 案例 A · Scope-reporting failure（执行 Agent 未主动上报规格偏离）

| 项 | 内容 |
|---|---|
| **阶段** | Day 0 · H3 合成数据生成器首次交付 |
| **Prompt** | 给 Codex 的 Implementation Task，Out of scope 明确写「不引入 numpy、pytest 之外的依赖」 |
| **Codex response** | 交付 `requirements.txt` = `numpy / Pillow / pytest`；汇报中写「依赖仅为 numpy、Pillow、pytest」 |
| **人工判断** | **该陈述属实，但 Codex 没有把它作为规格偏离主动提出。** 陈述"我用了什么" ≠ 报告"我在哪里偏离了任务规格" |
| **验证方法** | Claude 作为 Review Agent 独立核验：取**任务规格原文**与**实际 diff** 逐条比对，而非阅读汇报自述 |
| **验证结果** | 发现 3 处偏离：① Pillow 依赖（未自报）；② `.gitignore` 规则未生效；③ `docs/superpowers/plans/` 不在规定目录结构中 |
| **最终修正** | 人工裁决：**保留 Pillow，正式批准为 I/O-only dependency**（NumPy=计算 / Pillow=仅 Gray8 PNG 读写与测试 I/O；**不得承担检测、分割、滤波、特征提取等算法职责**）。定性为 **Implementation Task Specification Correction**，不修改 MASTER_PLAN |
| **状态** | 已裁决并落地（后续由 WO-0001a 加 Pillow scope 回归测试固化，见案例 C） |

**复用价值**：★ 高。这是**执行 Agent 的 scope-reporting 缺陷**的典型样本。
可沉淀为 **Agent Handoff Review** skill 的核心检查项：**收到 implementation 汇报时，第一件事是用任务规格原文逐条比对实际 diff，而不是采信汇报自述。**

---

## 案例 B · R2 合成证伪样本本身有缺陷

| 项 | 内容 |
|---|---|
| **阶段** | Day 0 · H3 数据集构建 |
| **背景** | `special_projection_overlap_noncontact` 是 **Route A 的证伪样本**：必须满足「x 投影区间重叠，但二维 bbox 不相交」。它不存在，G1 的 R2 判据就不成立 |
| **AI response（第一次，错误）** | 该网格用 `spacing_px = 6` 而物体宽 `12` —— **同一行内物体真的二维重叠** |
| **人工判断** | 自动测试在 **320 个帧对**上报违例，暴露了该缺陷 |
| **验证方法** | 对每一帧、每一对可见物体断言：x 区间重叠 ⇒ 二维 bbox 必须不相交 |
| **验证结果** | 修复前的构造产生真实二维重叠，与"二维不接触"的设计意图相反 |
| **最终修正** | 改为**跨行错位**构造（`per_row=3, spacing_px=24, spacing_y_px=34, row_stagger_px=6`），使 x 重叠只发生在**不同行、y 方向分离**的物体之间 |
| **复验结果** | `[Measured: h3_full/a44ded8]` 24/24 有效帧存在 x projection overlap，0 个相关 pair 发生二维 bbox intersection |

**复用价值**：★ 高。教训是 **验证数据本身也必须被验证** —— 不能因为它叫 ground truth / synthetic test case 就默认它正确。
一个**用来证伪别人**的样本，自己先得站得住。可沉淀为 **Route/Hypothesis Validation** skill 的必查项。

---

## 案例 C · Regression guard 被真实触发，证明非空断言

| 项 | 内容 |
|---|---|
| **阶段** | Day 0 · WO-0001a 独立验收 |
| **背景** | WO-0001a 新增了 `test_pillow_usage_is_limited_to_png_io`，用标准库 `ast` 静态扫描，禁止 Pillow 出现在算法侧 |
| **触发事件** | Claude 做独立验收时，把临时验证脚本写入 `python/synth/tools/`，该脚本需要 `import PIL` 来读取 PNG |
| **验证结果** | **测试实际失败**：`pillow_import_files <= allowed_files` 断言不成立，报出 `tools/_review_verify_0001a.py` 为额外项 |
| **最终修正** | 删除临时验证脚本后恢复 **7 passed**；当前工作区干净 |
| **意义** | 这次失败**不是缺陷，而是 guard 有效性的证据** |

**复用价值**：★ 中高。教训是 **regression guard 本身也应该通过一次真实触发来证明其检测能力**。
一个从未失败过的测试，无法区分「代码正确」与「断言写空了」。可沉淀为 **Agent Handoff Review** skill 的补充项：新增 guard 后应主动构造一次违规样本确认它会红。

---

## skill/ 候选池

后续可考虑沉淀以下通用 PYNQ/FPGA skill（**当前阶段不要为了做 Skill 影响主项目进度**）：

- [ ] Golden Reference —— Python 参考模型 ↔ RTL 逐级自动比对方法
- [ ] RTL Verification —— testbench 与 golden 向量组织
- [ ] Vivado Report Analysis —— 时序/资源报告解读（含本文件案例 2 的资源估算检查项）
- [ ] Route/Hypothesis Validation —— 从案例 1 与**案例 B** 提炼（验证数据自身也须被验证）
- [ ] Controlled Errata —— 从案例 3 提炼
- [ ] Agent Handoff Review —— 从**案例 A** 与**案例 C** 提炼（不采信汇报自述、用规格原文比对 diff；新 guard 须经真实触发证明非空）
