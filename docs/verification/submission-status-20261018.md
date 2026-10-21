# 当前提交状态与验收边界 · 2026-10-18

本记录更新 2026-10-15 历史交付审计，依据当前 `main`、已合并 PR、工作区验收记录和来源哈希。此轮只检查仓库与文档，没有连接开发板或执行 Stage B。

## Git 与交付基线

| 项目 | 当前状态 |
|---|---|
| 主分支 | `main` |
| HEAD / `origin/main` | `59268209148888c135ff7ff5c0aa7dba8ddbaf66`，一致 |
| 合并记录 | [PR #10](https://github.com/POPO-so/material-counter/pull/10)，合并提交 `5926820` |
| 工作区 | 本次核验开始时干净 |
| 最新纳入内容 | WO-0015 S2MM Binary8 帧回传与 WO-0016 Stage A 连续流优化 |

PR 合并和 clean Git 状态证明代码已入主线，不等同于完整系统验收。

离线完整性入口 `py scripts/delivery/verify_delivery.py` 本次未通过：`batch-02-ps-reference.json` 为 `conditions.csv` 记录 SHA-256 `85ae53eb9af5fb264d5433207298b3f288c019667694d855062e4d886b0b234d`、6190 字节；当前 `main` 与 HEAD 中该受跟踪文件为 `dee73876cd4617f80fd15ac161f1421bf986298470739a8f271b48913c93a080`、6147 字节。文件无本地改动；该内容在 `db552ea` 与当前 HEAD 中相同。清单记录的哈希/来源需要单独核对，本次未改动该 CSV 或交付 inventory。

## 已有验收证据

| 工作包 | 已记录结果 | 边界 |
|---|---|---|
| WO-0015 S2MM Binary8 | Overlay-only 回执通过；单帧、三帧连续回传及一次 OV9281 相机帧回传均记录 PASS，逐字节差异为 0。 | 是 Binary8 实验回传证据，不是生产 PL 特征 ABI 或持续物料计数闭环。 |
| WO-0015 执行器来源 | 板端记录的 `camera_dma_oneshot.py` SHA-256 为 `5b9684f4b02988e8a822c36ef0ac7faec78c0a7980f2fca1574afd3027c9df28`；`board_preflight.py` 为 `f6556d811f2b4488b1ad471ea5d8ad565b74ae194b6db0b4356ae907e05de2d6`。当前合并 `main` 的两个文件哈希与之完全一致；相同内容可在祖先提交 `b1cf74a3934ea10a883e1707a87bf830706015b1` 找到。 | 旧验收报告提及的 `8ec0a624...` 和 `df0fb596...` 分别对应较早提交 `138cc2e` 和 `5091398`，不是当前合并树。已在 [WO-0015 验收报告](../../data/results/wo0015-final-20261017/acceptance-report.md)更正。哈希一致只覆盖这两个脚本。 |
| WO-0016 Stage A Attempt 8 | 30/30 帧完成，Packer 验证 30/30，端到端 15.157 FPS；本机 HTTP MJPEG 回环 8 帧、5.921 FPS。 | 输入是空载基线，计数 0/0 不证明真实物料准确率；`avg_roi_count_ms=17.657 ms`，未达到 2.5 ms 子目标。详见[板端记录](wo-0016-board-acceptance.md)。 |
| 离线回归 | 记录为 `244 passed, 1 skipped`。 | 测试使用按 487 张现存 H3 PNG 重建的临时输入清单，并与归档向量源哈希交叉核对；原始输入清单的规范哈希来源未复核。本次没有重跑测试。 |

## 尚未完成的最终验收

WO-0016 Stage B 的 150 帧动态 M6 螺母/垫片测试仍为 `NOT RUN`，计划人工真值为 `EXPECTED_COUNT=5`。完成前需要在当次开机后重新核对板身份和 Boot ID，建立同次启动的 WO-0015 Stage 1 PASS 回执、manifest 与一次性 guard，检查 DMA/CMA/摄像头/端口门禁，再由操作员按预定真值投放物料。任何前置门失败都应停止，不重试、不重载 Overlay、不绕过 guard。

本次环境未配置可确认的板端 SSH 目标，不能核验当前板卡开机状态、Boot ID、Stage 1 回执或现场物料。因而 Stage B 没有执行，也没有生成新的硬件结论。现有材料可用于提交代码和 WO-0015/WO-0016 阶段进展；若最终提交要求完整真实物料计数验收，应先完成 Stage B 并补入原始结果及来源哈希。
