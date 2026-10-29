# ADR-0006 · 320×240 Gray8 活动协议选择：GRAY8_PACKET_V1

| 项 | 内容 |
|---|---|
| **状态** | `Accepted` —— 项目负责人于 2026-10-30 决策；正式接口签核仍按 `docs/02_interfaces.md` 的 PR + 三人确认流程，本记录不冻结正式接口 |
| **日期** | 2026-10-30 |
| **决策者** | 项目负责人 |
| **关联** | ADR-0004（本分支 `adr-0004-gray8-dma-input.md`）、`docs/02_interfaces.md`、`scripts/gray8/`、分支 `integration/lj01-plus-algo` |

## 背景

仓库历史中存在两套互不兼容的 320×240 Gray8 DMA 线格式。二者的 Header、Trailer 与 End word 均不同，PL Checker 与 PS 校验互斥，同一时刻只能有一套路由生效：

| 格式 | Header | Trailer | End word | 证据状态 |
|---|---|---|---|---|
| `GRAY8_PACKET_V1` | `0x47525938` | `0x454E4438` | `0xC0DEC018` | 队友已在板端完成 963 帧（3 合成、960 相机）逐像素 Binary8 校验，DMA 空闲、fault=0；XSim 与 implementation 通过（ADR-0004） |
| `WO14`（旧） | `0x574F3134` | `0x454E4434` | `0xC0DEC014` | 历史实验格式；保留原始测试与结果，但不能作为新架构的兼容性或板端验收证据 |

`GRAY8_PACKET_V1` 的板端证据属于**历史板端证据**：它证明该协议在队友原始基线上可工作，不构成整合分支 `integration/lj01-plus-algo` 的板端验收。

## 决策

1. 新架构的 320×240 Gray8 **活动协议**为 `GRAY8_PACKET_V1`，固定使用 Header `0x47525938`、Trailer `0x454E4438`、End word `0xC0DEC018`。
2. `WO14` 保留为**历史实验依据**，不再作为新架构默认协议。历史 320×240 测试、回放与回归能力继续保留；不得删除证据或改写历史结果。
3. 640×400 RLE 继续作为主要优化路线。其协议与 320×240 Gray8 相互独立，本 ADR 不改变 RLE 线格式。
4. 本轮不修改已通过验证的 RTL、DMA 协议实现、BIT/HWH 和板端环境。本 ADR 只记录软件侧活动协议的选择。
5. 本 ADR 不改变任何既有 ADR 的状态，也不覆盖、删除或改写任何 Frozen/Accepted 记录。

## 编号与既有 ADR 的关系

- 本分支现有 ADR-0001 至 ADR-0005，ADR-0006 为未占用编号。
- `main` 分支的 `docs/adr/adr-0004-gray8-interface-a-baseline.md` 状态为 **Accepted / Frozen**，与本分支的 `adr-0004-gray8-dma-input.md` 是两份不同文件。本 ADR 不修改、不重命名、不覆盖该冻结记录。
- 两条分支的 ADR 编号已经分叉（`main` 为 0001–0004，本分支为 0001–0005）。合并 `main` 前必须单独完成文档编号与语义的核对；该核对不在本 ADR 范围内。

## 影响

- `docs/02_interfaces.md` 的“当前运行实现”节与本决策一致；本次只补充两套格式的取舍记录和 ADR 链接。
- 旧 `WO14` 代码与测试如仍存在，仅供历史回归，不得被新架构默认入口调用。
- 任何后续协议修改仍须提交 PR、三人确认并形成新的 ADR。

## 未验证项

- 整合分支尚未进行 RLE 端到端、实板资源或真实物料准确率验收。
- `WO14` 的历史结果只在其原始配置下有效，未与 `GRAY8_PACKET_V1` 做板端对照。
- 本决策不产生新的板端证据，也不能被引用为整合版本已通过板端验收。