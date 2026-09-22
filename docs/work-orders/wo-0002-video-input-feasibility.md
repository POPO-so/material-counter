# WO-0002 · PYNQ-Z2 Video Input Feasibility & Pin Budget Verification

> **Historical work order notice**：本文记录的是 WO-0002 当时的执行范围和证据状态，原始任务边界保持不变。当前作品正式名称与机械/测速基线由后续 Controlled Errata 和当前 ADR 规定；本文中“不得修改项目名称 / MASTER_PLAN”的限制仅适用于当时工单，不应覆盖后续正式变更。本文的 HDMI/DVP 可行性结论仍保持原样。

| 项 | 内容 |
|---|---|
| **状态** | `OPEN` —— 待 Codex 执行 |
| **发起** | Claude（Planning / Review Agent） |
| **执行** | Codex（Implementation Agent） |
| **前置** | Controlled Errata #2 已提交（`2c84ba5`）；MASTER_PLAN P0 任务 ③ |
| **依据** | MASTER_PLAN C2 / C3 / H1 / R1；`docs/00_scope.md`；`docs/02_interfaces.md` 接口 A |
| **性质** | 前置可行性核实。**不冻结最终视频源路线。** |

---

## Planning 阶段审查结论（先查后做）

审查了 `MASTER_PLAN` C2/C3、`hw/pin_budget.md`、`docs/adr/adr-0001-video-source.md`、`docs/02_interfaces.md`：

| 对象 | 实际状态 |
|---|---|
| `hw/pin_budget.md` | **纯骨架** —— 4 个接口表全空、"合计可用 I/O ____"、XDC 块是 `# 待填`。**零实测** |
| `docs/adr/adr-0001-video-source.md` | 状态 `Proposed`；C2 四项 + C3 **全部 `☐ 未做`**；"决策"、"后果"两节**待填** |
| `MASTER_PLAN` C2/C3 | 框架完整（四项待核实 + 默认假设），但**默认假设未经任何验证** |
| `docs/02_interfaces.md` 接口 A | 定义了 TDATA/TUSER/TLAST/TVALID/TREADY，但明确标注"具体源侧接口参数取决于 C2/C3 实测结论" |

**结论：C2 与 C3 目前没有任何一项被真正验证过。** 本工单的任务是**从零建立证据**，不是"补全已有结论"。

### 执行环境（已勘察）

| 项 | 实际 |
|---|---|
| 本机 Vivado / Vitis | **未安装**（常见路径与全 C 盘浅层搜索均无） |
| 已有 Vivado 工程痕迹（`.xpr`/`.hwh`/`.xsa`） | **无** |
| PYNQ-Z2 板卡 | **有，随时可用** |
| 联网查 AMD 官方文档 | **允许** |

**因此**：需要 Vivado / 板卡才能得到的结论，必须列为**本工单的实际 verification step**，并在执行前先确认工具链可用（见 执行前置）。

---

## Objective

为后续真实视频输入路线选择提供**工程证据**：完成 C2（HDMI IN 工程集成条件）与 C3（扩展引脚预算）的前置核实。

**本任务不冻结最终摄像头路线。**

---

## 证据分级纪律（本工单新增，必须严格遵守）

本工单所有结论必须带下列标签之一：

| 标签 | 含义 | 硬性要求 |
|---|---|---|
| **`[Measured]`** | 实际在本机工具链或板卡上跑出来 | **必须附可复现的命令/操作 + 观察到的实际输出**（命令回显、截图、Tcl 控制台输出）。**没有输出就没有 [Measured]** |
| **`[Documented]`** | 官方 schematic / manual / reference design / AMD 文档明确支持 | **必须附出处**：URL + 章节号或 PDF 页码。**无出处不得标 [Documented]** |
| **`[Hypothesis]`** | 尚未实际验证的工程推断 | 必须写明"缺哪一步验证才能升级" |

**三条铁律：**

1. **不得把 `[Documented]` 写成 `[Measured]`。** 文档说支持 ≠ 在本机跑通。
2. **不得仅凭 AI 既有知识写"可行"。** 任何"可行/不可行"结论都必须落到 `[Measured]` 或 `[Documented]`，否则只能是 `[Hypothesis]`。
3. **本机实测不了的项，就老实留在 `[Documented]` / `[Hypothesis]`**，并写明是缺工具链还是缺板卡。

---

## 执行前置（开工第一步）

先确认工具链，再决定哪些项能升到 `[Measured]`：

```bash
# 1) Vivado 是否可用
which vivado || ls /c/Xilinx/Vivado 2>/dev/null || echo "Vivado NOT FOUND"

# 2) 若可用，记录版本与已装器件
vivado -version
```

- **若 Vivado 不可用**：所有依赖 Vivado 的步骤（IP catalog 检查、XDC 校验、工程生成）**保持未完成**，在报告中明确列出"因缺工具链未验证"，不得跳过或代以推断。
- **若需先安装 Vivado**：安装本身**不在本工单范围内**；报告为 blocker 并停止相关步骤，其余（C3 文档分析、板卡上电观察、XDC 草案撰写）照常推进。

---

## C2 · HDMI IN 实际可集成性

> 板上存在 HDMI IN/OUT 是**已知事实**（AMD 2026 官方指南）。**本任务不是重新确认物理接口数量**，而是验证 HDMI IN 的**工程集成条件**。

### C2-① 实际实现路径

核实 PYNQ-Z2 HDMI IN 在当前开发环境下有哪些**实际**实现路径。至少覆盖：

- 板上 HDMI IN 连接器是否**电气连接**到 Zynq PL（还是仅连接到某个桥接芯片 / 未连接）
- 若已连接：走哪些引脚、哪个 I/O bank、什么电平
- 是否存在官方或社区 reference design 可直接复用
- 若未连接：**这就是本项的结论**，须明确写出

**来源要求**：PYNQ-Z2 官方 schematic + PYNQ 官方文档。标 `[Documented]` 附 URL/页码；若本机装了 Vivado 并能读到 board file，可补 `[Measured]`。

### C2-② 所需 IP / Overlay / Vivado 组件

列出把 HDMI IN 变成可用视频流所**实际需要**的组件，逐个给出可用性证据：

- 需要的 IP（HDMI RX、色彩空间转换、AXI4-Stream Video 相关等），**逐个**在 Vivado IP catalog 中确认**是否存在、版本号**（`[Measured]`）
- 需要的 PYNQ overlay / 驱动
- 需要的 license 或额外安装包

**★ 若 Vivado 可用，必须实际执行**（示例，具体命令按实际版本调整）：

```tcl
# 在 Vivado Tcl console 或 -mode batch 下
get_parts
get_ipdefs -filter {NAME =~ "*hdmi*"}
get_ipdefs -filter {NAME =~ "*v_tc*" || NAME =~ "*axis_vid*"}
get_board_parts -filter {NAME =~ "*pynq*"}
```

把**实际返回结果**贴进报告。**不允许凭记忆写"应该有 XX IP"。**

### C2-③ 能否接入我们自己的 AXI4-Stream PL 处理流水线

判断 HDMI IN 视频流**能否**接到接口 A（见 `docs/02_interfaces.md`）：

| 接口 A 字段 | 要求 | HDMI 路径能否满足 | 证据 |
|---|---|---|---|
| `TDATA` 24 bit RGB888 | 源侧像素格式 | | |
| `TUSER[0]` SOF | 帧首 | | |
| `TLAST` 行末 | 行末 | | |
| `TVALID`/`TREADY` | 严格反压 | | |

另外必须评估三项**工程约束**（这三项决定它能否用于比赛）：

1. **分辨率与帧率**：HDMI IN 输出分辨率能否降到 **320×240 @ ≥30fps**（MASTER_PLAN 当前工程目标）？不能的话，PL 侧是否需要额外的缩放级？成本多大？
2. **像素时钟**：HDMI IN 的像素时钟是多少、与接口 A 约定的 **100 MHz** 是否一致？不一致需要什么（异步 FIFO？时钟域转换？）
3. **延迟与确定性**：HDMI RX 路径是否引入**不可控缓冲**（帧缓冲、去隔行等）？这对 MASTER_PLAN 的"确定性延迟"叙事是否构成威胁？

**本项结论必须是四选一**：`可直接接入` / `需额外 IP 或转换级` / `可行但代价高（列出代价）` / `不可行（列出原因）`。

### C2-④ 是否适合作 Competition Version 的最终真实视频输入

综合 C2-①~③，给出**适合性评估**。必须回答：

- 它是否是**真实摄像机视频**（而非合成/回放）？作为比赛现场输入是否成立？
- 相比 DVP / USB，它的**工程代价**与**风险**是什么？
- 若 C2-① 结论是"未电气连接"，本项直接判 **不适合**，并说明后续应以哪条路线为主。

> **★ 结论自由原则**：若证据表明 HDMI IN 不可行或代价过高，**如实写"不可行/不推荐"是完全合格的结果**。本工单不预设 HDMI IN 必须成功。**不得为了迎合预期而把结论拔高。**

---

## C3 · 扩展引脚预算（DVP 候选）

### 不要写"约 15 根线够不够"

必须给出**逐信号**的完整链路：

```
required signal → candidate physical pin → FPGA package pin → I/O standard / voltage → conflict → feasibility
```

### 必做步骤

**C3-① 可用扩展接口盘点**（填写 `hw/pin_budget.md` 现有骨架表）

对 Pmod ×2 / 双排 Pmod / Arduino header / RPi header，逐个记录：

- 可用 GPIO 数
- **I/O bank 归属**
- **I/O voltage**（3.3V / 1.8V — 必须逐 bank 确认，不同 bank 可能不同）
- **clock-capable pins**（PCLK 需要）
- **与板载外设的冲突**（哪些引脚已被 HDMI / Ethernet / SD / LED / 按键占用）

**来源**：PYNQ-Z2 官方 schematic（`[Documented]`，附页码）+ 板卡目视核对（`[Measured]`，附照片或逐针描述）。

**C3-② DVP 需求清单**（逐信号）

| 信号 | 根数 | 方向 | 备注 |
|---|---|---|---|
| D[7:0] | 8 | in | |
| PCLK | 1 | in | **必须 clock-capable** |
| VSYNC | 1 | in | |
| HREF / HSYNC | 1 | in | |
| XCLK | 1 | **out** | 摄像头主时钟，需 PL 侧时钟输出能力 |
| SCCB SDA / SCL | 2 | inout / out | 需要上拉 |
| RESET / PWDN | 1–2 | out | 视摄像头型号 |
| **合计** | **15–16** | | |

**C3-③ 完整映射表**（本工单核心产物）

| 信号 | 候选物理排针 | FPGA 封装引脚 | I/O Bank | IOSTANDARD | clock-capable? | 冲突 | 可行性 |
|---|---|---|---|---|---|---|---|
| D0 | | | | | n/a | | |
| … | | | | | | | |
| PCLK | | | | | **必须 yes** | | |

**★ 封装引脚号必须来自官方 schematic / board file**，不得凭记忆填。无出处的格子留空并标 `[Hypothesis]`。

**C3-④ I/O voltage / bank / clock 约束检查**

- 同一 bank 内混用电压是否合法
- PCLK 所在 bank 是否有可用 `create_clock` 资源
- Arduino / RPi 排针中哪些引脚**不**是普通 I/O（如专用时钟脚、模拟脚）

---

## XDC 草案（候选，非已验证实现）

对 DVP 候选接口给出 XDC 草案，写入 `hw/pin_budget.md`。**至少覆盖**：

```tcl
# 1) package pin —— 逐信号 set_property PACKAGE_PIN
# 2) IOSTANDARD —— 逐信号 set_property IOSTANDARD
# 3) PCLK —— create_clock（周期按摄像头上限估算，并注明是估算）
# 4) 输入延时 —— set_input_delay（若可估算）
```

**★ XDC 的两种状态必须分清：**

| 状态 | 标签 | 条件 |
|---|---|---|
| 语法与引脚合法性**已验证** | `[Measured]` | **实际在 Vivado 中加载过这份 XDC 并确认无引脚/IOSTANDARD 错误** |
| 仅草案 | `[Hypothesis]` | 未在 Vivado 中加载过 |

**不得把未加载过的 XDC 说成"已验证"。** 若 Vivado 可用，**必须实际执行**（示例）：

```tcl
create_project -in_memory -part xc7z020clg400-1
read_xdc <草案路径>
# 观察是否报 package pin / IOSTANDARD 错误
```

---

## Expected Outputs

**主要更新（仅这两个）：**

1. `hw/pin_budget.md` —— 填满全部待填节，含 C3 完整映射表 + XDC 草案 + 结论
2. `docs/adr/adr-0001-video-source.md` —— C2 四项逐项结论 + 四类候选对比 + provisional recommendation

**允许的极少量 supporting artifacts**（不得扩展成新文档体系）：

- DVP 的 `.xdc` 草案文件（放在 `fpga/constraints/`）
- 验证命令的实际输出（贴在上述两份文件里，或放 `hw/` 下的单个证据文件）

**不得**新建文档目录、不得新建文档体系、不得改写 `docs/02_interfaces.md` 的接口定义（它明确等 C2/C3 结论，本工单只**提供**结论，不改接口契约）。

---

## ADR 要求

`docs/adr/adr-0001-video-source.md` 至少比较四类候选：

| 候选 | 是否最终真实视频输入 |
|---|---|
| **HDMI IN** | 是（若 C2 判定可行） |
| **DVP camera** | 是（若 C3 判定引脚可行） |
| **USB camera** | 是（但走 PS，非纯 PL 采集） |
| **file replay / TPG** | **否 —— development source**，不等同于最终真实视频输入 |

**★ 本阶段只能形成 `candidate assessment` / `provisional recommendation`。**

ADR 状态必须停留在 `Proposed`。**不允许 `final route frozen`。**
ADR 中必须显式写一句：**Video source route NOT FROZEN**（见 Acceptance criteria 8）。

最终视频输入路线仍由**后续真实验证**决定。

---

## Out of Scope

本任务**不得**：

- ❌ 购买摄像头
- ❌ 实现完整 HDMI pipeline
- ❌ 实现 DVP receiver
- ❌ 实现 tracking / counting
- ❌ 修改 synthetic generator（`python/synth/**` 冻结）
- ❌ 修改 Route A / B / Hybrid
- ❌ 冻结最终视频源
- ❌ 修改项目名称
- ❌ 修改 MASTER_PLAN 架构
- ❌ 开始 A2-lite

**不得修改**：`MASTER_PLAN.md`、`docs/00_scope.md`、`docs/02_interfaces.md`、`docs/05_risks.md`、`docs/07_routes.md`、`python/**`、`data/**`。

---

## Acceptance Criteria

工单完成时必须提供：

1. **C2 四项逐项结论及证据等级** —— 每项标 `[Measured]` / `[Documented]` / `[Hypothesis]`，`[Documented]` 附出处，`[Measured]` 附实际输出
2. **HDMI IN → custom AXI4-Stream pipeline 的实际可行性结论** —— 四选一（可直接接入 / 需额外转换级 / 可行但代价高 / 不可行），含分辨率·帧率·像素时钟·延迟四项评估
3. **DVP 完整 signal / pin budget** —— 逐信号 mapping，含 bank 与 IOSTANDARD
4. **I/O voltage / bank / clock constraint 检查** —— 含 clock-capable 确认与 bank 冲突
5. **candidate video source comparison** —— 四类候选并列
6. **XDC draft** —— 含 `PACKAGE_PIN` / `IOSTANDARD` / `create_clock`，并标明是"已加载验证"还是"仅草案"
7. **unresolved risks** —— 明确列出仍未验证的项，及各自缺什么才能验证
8. **显式声明：`Video source route NOT FROZEN`**

**另需报告：**

- 本机 Vivado 可用性（`[Measured]`：`vivado -version` 实际输出，或"未安装"）
- 若因缺工具链导致某些项未验证，**逐项列出**，不得用推断填补
- `git status` 与 protected files diff

---

## 执行命令（示例，按实际环境调整）

```bash
cd C:/fpga/material-counter

# 工具链检查
which vivado || echo "Vivado NOT FOUND"

# 若 Vivado 可用：IP 与板卡件盘点、XDC 加载验证（Tcl 见上文 C2-② 与 XDC 节）

# 板卡目视核对（拍照或逐针记录）
# 产出后
git status --short
git diff --stat -- MASTER_PLAN.md docs/00_scope.md docs/02_interfaces.md docs/05_risks.md docs/07_routes.md
```

---

## 交付后

由 **Claude 独立复核**：抽查 `[Measured]` 项是否真有可复现输出、`[Documented]` 项出处是否成立、`[Hypothesis]` 项是否被误升格。

**复核重点：有没有把没验证的东西写成验证过的。**
