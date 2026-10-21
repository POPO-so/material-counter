# WO-0016 主工作区资产归档与合并准备 · 2026-10-22

本记录只涉及本地 Git、文档、SHA-256 与资产保全。Stage B-2 结论保持 **165/165 帧、4/5（80%）、22.132 FPS，物料准确率验收未通过且不再重测**。Stage A ROI 平均 17.657 ms，高于 2.5 ms 目标，CCL 为 30/30 帧。本轮硬件动作 0，未消费 READY Guard。

## 原始状态与完整保全

原主线 HEAD 为 `59268209148888c135ff7ff5c0aa7dba8ddbaf66`，收口基线为 `6a845a01f73e44ac6237c47233b42d95a9e9c943`。原 4 个修改项和 145 个未跟踪项共 149 个文件、20,378,337 字节，已在仓库外 `C:/fpga/material-counter-asset-archive-20261022/original-files` 逐字节备份。所有原文件按原路径提交于归档分支 `archive/wo0016-stageb-raw-evidence-20261021`，提交 `180741b41bf3c7a6654fed48ca26edb90f2d74e6`；149/149 committed Git blobs 与原文件及外部副本的字节数和 SHA-256 一致。归档分支专用 attributes 也经 checkout 过滤模拟复核，无字节转换。

索引：[全部 149 项](workspace-state.json) · [144 个实验文件](evidence-manifest.json) · [归档提交复核](archive-commit-verification.json)。manifest 同时记录原绝对路径、仓库相对路径、字节数及 SHA-256。归档分支内同目录另保存原始 `main-original.diff.patch`、NUL 分隔的原始 status 和当时 refs；四个修改文件的原始字节也在原路径保存。

[独立恢复验证](archive-bundle-verification.json)：外部 `original-workspace-archive.bundle` 已在一个全新裸仓库中恢复归档分支，再次核验原 149 个文件的 Git blobs，全部一致。恢复过程没有向共享仓库导入对象或修改其 refs。

| 原未跟踪目录 | 文件数 | 字节数 |
|---|---:|---:|
| `wo0016-phase110-20261019` | 6 | 39,538 |
| `wo0016-stageB-20261019-215537` | 7 | 68,005 |
| `wo0016-stageB-20261019-221949` | 5 | 150,641 |
| `wo0016-stageB-20261019-222926-repeat2` | 11 | 199,492 |
| `wo0016-stageB-20261019-224057-confirmed-idle` | 13 | 596,002 |
| `wo0016-stageB-20261019-225948-hud-preview-handoff` | 6 | 51,007 |
| `wo0016-stageB-20261020-010723` | 13 | 556,757 |
| `wo0016-stageB2-prep-20261020-6f13f0f0` | 83 | 18,673,567 |

8 个目录共 144 个文件、20,335,009 字节；`submission-status-20261018.md` 是第 145 个未跟踪文件，作为历史状态报告一并保全。没有按重复性、后缀或缓存名称丢弃任何一项。

## 文档迁入与交付清单

WO-0015 验收报告的 helper 源码哈希修正完整迁入；两个 helper 在 main、收口工作树及祖先 `b1cf74a` 中的 SHA-256 已复核一致。10 月 15 日的历史标注及 10 月 18 日状态报告已迁入，两份报告增补当前状态提示，正文当时的 NOT RUN、旧测试和清单失配陈述仍作为历史记录保留。README 保持 `6a845a0` 字节；PROJECT_INDEX 保留 Stage B-2 最新状态并添加本记录入口。原 README/索引字节保存在归档提交中。

详见 [文档迁移映射](document-migration.json)。`support-files.json` 的 17 条旧记录中 5 条同步为当前哈希，并加入验收报告和 10 月 18 日状态报告，共 19 条；[清单修订记录](support-reconciliation.json) 保留原值和历史 Git 引用。`verify_delivery.py` 的 319 项、5 批次核验 PASS，另行复核 19 条支持文件 PASS。原始实测 CSV、JSON、图像及二进制产物没有被改写。

## 83 个准备文件的重复性

在归档分支创建前，83 项中 38 项与 `6a845a0` 树中的资产逐字节相同，41 项在当时所有 branches/remotes/tags 的可达历史中有相同内容，42 项未找到匹配。只在历史中匹配的 3 项是三个 runtime 副本中的旧 `prepare_vectors.py`。完整对应路径和 Git blob OID 见 [重复性清单](prep-deduplication.json)；全部副本仍保留。

## 三个 dangling commits（只读）

作者均为 `material-counter team <material-counter@local>`，日期为 Asia/Shanghai。完整 changed paths、作者/提交日期、说明见 [机器报告](dangling-commits.json)；原始 `git show --stat --format=fuller` 输出保存在 `dangling-stat/`。

| 提交 | 提交时间 | 内容与保全结论 |
|---|---|---|
| `71c3917` | 2026-10-18 04:38:47 +08:00 | stash 结果树与可达分支相同，但第三父 `04b3b8e` 含 16,100 个历史未跟踪文件（按路径 1,423,514,013 字节）。其中 15,050 条路径的 blob 内容不在原 refs 中，涉及 7,456 个不同 blob OID；含源码、报告、实验产物及大量构建缓存，具有历史保全价值，未重新验证实验有效性。 |
| `a6cc823` | 2026-09-18 08:17:02 +08:00 | H3 扩展历史提交，54 个 changed paths，52 个独有 blob：43 个 H3 JSON、6 个实现/spec/测试/工具文件、2 个文档和 `.gitignore`。保留为旧实现与测试材料，不作为当前规范夹具或验收证明。 |
| `45eb152` | 2026-10-14 12:22:48 +08:00 | WO-0013 DMA 历史提交，50 个 changed paths 中 49 项 payload 已在其他 refs；仅旧 `.gitattributes` 字节独有，其保护规则已由当前版本覆盖。 |

stash 第三父中的历史材料代表包括 `scripts/wo0014_hw/diagnostic50_implicit_audit/` 下的源码与测试、`docs/verification/wo-0013-phase-a-report.md`、`data/results/wo0014-diagnostic50-hardening-20261014/` 的原始诊断输出。它们具有来源追溯价值；本轮没有运行这些历史脚本，也没有重新认定历史实验有效性。

三者完整可达对象图已从共享对象库只读复制到仓库外 `dangling-history/complete-reachable-history.pack` 与 `.idx`，共 11,700 个不同对象；严格 index-pack 与 verify-pack 均通过。没有创建救援 refs，也没有导入、重写或清理共享对象。[外部包校验记录](dangling-pack-verification.json) 记录 SHA-256；完整树清单留在外部备份中，路径和哈希载于机器报告。

## 主工作区清理与合并门禁

归档完成后的证据见 [清理前复核](verification-before-cleanup.json)。只有 archive committed blobs、外部备份和待清理源文件全部匹配，且收口修正已提交后，才恢复 main 的四个受控文档并逐项移除已存档的 145 个未跟踪路径。Ignored 资产与 `.git/objects/**/tmp_obj_*` 不在清理范围。实际清理结果另由 `main-cleanup-receipt.json` 记录；在该回执存在前，本记录不表示主工作区已清理。

本轮不合并 main，不推送远端，不改写历史，不运行 clean/reset-hard/gc-prune。每次 Git 写操作显式设置 `gc.auto=0` 与 `maintenance.auto=false`。本地快进条件与最终工作区状态以清理后回执为准；远端状态只使用本地现有引用，未联网刷新。
