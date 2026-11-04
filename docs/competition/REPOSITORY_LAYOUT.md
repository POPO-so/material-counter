# 仓库目录与清理记录

[作品首页](../../README.md) · [文档目录](../README.md)

## 目录混杂的原因

仓库在多个阶段合并了 RTL、PS 软件、板端启动器、离线依赖、实验输出、交接记录和设计文档。它既用于开发，也用于保存可追溯证据。文件夹名称看起来杂乱，不代表其中内容都可以删除。

## 本轮已清理

| 删除范围 | 数量与原因 |
| --- | --- |
| `Users14173AppDataLocalTempmaterial-counter-g0-red/` | 22 个文件、856 B；错误 Windows 临时路径下的 pytest 模拟 proc/sys/PYNQ 文件，测试会在自己的临时目录重建，没有项目外部引用 |
| `Python脚本/material-counter-startup-status.json` | 1 个文件、54 B；无时间戳的过期状态副本，启动器实际读写 `.runtime/` 下的同名文件 |

仅删除上述 23 个文件，并增加精确忽略规则；未删除历史板测和构建证据。旧字节仍保存在 Git 提交 `7e3f01da`，逐文件 blob 和大小见[清理记录](../verification/repository-cleanup-20261104.json)。

清理后运行 `scripts/standalone/test_probe.py` 与 `scripts/wo0016/test_camera_only_preview.py`，48 项测试通过，使用独立临时输出目录，未访问开发板。

## 保留的内容与原因

| 目录或文件 | 原因 |
| --- | --- |
| `.agents/skills/` | 包含被 Vivado 构建引用的辅助库，不只是界面配置或缓存 |
| `skill/` | AMD 指南要求的团队可复用技能交付入口，原先占位已补充实际内容 |
| `.codex/handoffs/` | 历史交接、运行状态与来源线索；如需归档，应保留旧版本引用 |
| `Python脚本/` 的启动器和部署配置 | 被代码/测试引用，配置含 manifest 与 BIT 身份，不能按“重复 Python 目录”直接删除 |
| `material-camera-benchmark-debs/` | 两个离线安装包有[板端安装记录](../../data/results/camera-format-benchmark-20261027/README.md)，属于历史复现依赖 |
| `文档/`、计划及工程索引 | 包含设计与验收线索，有链接及哈希绑定，不能批量删除 |
| `data/results/` | 原始构建、测试与发布证据，与正文结果和工件身份绑定 |

## 中文路径与后续迁移

清理前在已跟踪文件中发现 41 个含非 ASCII 字符或空格的路径；删除无用状态副本后剩 40 个，其中 `文档/` 32 个、`Python脚本/` 6 个、`data/` 2 个。这与团队保存的[AMD 指南](CONTEST_REQUIREMENTS.md)英文路径要求存在差距。

下一轮迁移需要先为每个文件确定英文目标，再核对入口、相对链接、启动器配置、测试、发布清单和历史哈希的处理方式。原始证据保持原字节，旧工件记录不能为了路径变动重写成新的“已验收”快照。本轮不冒充已完成全部英文路径迁移。

已为全部 40 个路径生成[英文目标与逐文件 SHA-256 清单](../verification/english-path-migration-20261104.json)，目标之间无重名。当前暂未执行改名：六个启动器/配置关联现有入口，两份 `data/results/` 文件属于历史证据，其余包含图示及生成器的相对引用。提交以队友 V13 为主线，应先核对该版本入口和包内路径，再统一迁移，不能单改目录名后继续沿用旧发布结论。

## 当前阅读方式

评审先从首页和[文档目录](../README.md)阅读；开发者由 [PROJECT_INDEX](../../PROJECT_INDEX.md)定位实现。首页已按 AMD 推荐的 `src/sim/build/board/data/skill/report` 给出目录对照，无需通过删除证据来缩短根目录。
