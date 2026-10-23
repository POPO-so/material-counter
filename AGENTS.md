# material-counter Agent 约定

- 先用 [PROJECT_INDEX.md](PROJECT_INDEX.md) 定位项目上下文；按任务读取必要文件，避免无目的全仓扫描。Worker 只读取完成所派任务所需的最小上下文。
- 判断“已实现”时核对实际代码、交付文件和验证结果；计划性描述与 Work Order 的预期路径不能充当实现证据。
- Route 和视频输入等候选方案未获证据与正式决策前，不得写成已冻结方案。
- Scope、接口、架构或受保护文档的重大修改由 Worker 升级给 Manager 决定，不自行扩大任务范围。
- Worker 完成后简要报告 changed files、tests、risks、unresolved；Manager 核对实际 diff、测试结果和项目约束后作最终验收。

## Milestone Tagging & Benchmark Ledger Protocol (Mandatory)

本节为仓库级持久工作约定；`CLAUDE.md` 同步本节与原有项目规则。每次 UI/功能升级、算法优化、DMA/FPS 优化或板端验证阶段收尾，Agent 必须执行以下 Definition of Done，不依赖用户再次提醒。

1. **先核验证据，再追加账本。** 读取该轮最新 pytest 日志/JUnit、离线回放或已授权板测报告；核对被测 Git SHA、源码哈希、运行日期、环境、帧数、统计口径、失败/跳过项及证据路径。记录 FPS、计数/人工真值与准确率口径、CmaFree 各时点（KiB）、关键延迟及 pytest 通过/失败/跳过数。没有实测的字段写 `N/A`，不同轮次或环境分行；绝不把主机回放 FPS、预览 FPS、相机请求 FPS 写成板端端到端 FPS，也不把总数一致写成逐颗身份验收。
2. **账本只追加。** 更新 `docs/benchmarks/optimization_ledger.md`，严禁覆盖、重排或删除历史行；更正错误时追加勘误行并引用原记录。失败、未达标及 NOT_VERIFIED 结果也必须保留。较新的 pytest 不回填到历史板测行；全局 CmaFree 变化仅为遥测，不独立证明泄漏、释放成功或硬件准入。关键延迟放摘要中，注明均值/中位数/分位数及处理阶段。
3. **优先使用辅助脚本。** 先调用或参照 `python scripts/record_milestone.py record --help`。脚本 `record` 支持显式指标及 `--pytest-junit` 自动统计，`--dry-run` 不落盘；补录历史运行必须用 `--date YYYY-MM-DD` 指明实际测量日期。相对证据/JUnit 路径统一相对仓库根目录解析。记录时需有干净的已测源码提交；脚本只追加账本，不提交任何文件。人工传入 pytest 计数时必须显式给出 `--pytest-passed`、`--pytest-failed`、`--pytest-errors`，保留对应日志证据及明确测试范围；`--verification passed` 是 Agent 经证据审查后的声明，不能用于绕过失败门禁。脚本不执行测试，也不能自动证明任意报告属于当前提交，调用者必须先完成绑定核验。
4. **先源码、后账本、再标签。** 先提交经验证的功能源码；在固定提交上执行必要测试，确认运行前后 HEAD/相关源码哈希未变；读取证据并 `record`；审查且只提交本轮账本行；再运行 `python scripts/record_milestone.py tag --tag v1.1-fps-opt --dry-run`。脚本会拒绝脏工作区、失败/未知验证、零通过测试、证据变化、重复标签以及测试后除账本以外的源码变更。历史人工行没有机器收据时，必须追加新验证行，不能伪造历史收据。
5. **阶段目标通过才锁定。** 所需阶段目标和约定测试均通过、失败/错误为零且 `git status --porcelain=v1 -uall` 为空时，Agent 必须主动提议一个语义明确的附注标签；本会话已授权本地打标签时可执行。例：`v0.9-baseline`、`v1.0-hmi-ready`、`v1.1-fps-opt`；不得移动或覆盖已有标签。附注必须包含被测源码与标签提交、环境、pytest 数/范围、FPS、准确率、CMA/延迟、证据哈希与已知限制。跳过项逐项解释；任何必需验收项被跳过时不得宣称该阶段通过。限定源码范围通过时可提出明确标为 offline/source 的标签；根目录收集或第三方技能测试失败必须披露，不能宣称全仓全绿。
6. **远端留存明确授权。** 标签后必须提示是否已推送；有当前会话的推送授权时才使用 `tag ... --push --remote origin`，否则给出该标签的单独推送命令。禁止隐式推送所有标签/分支、强制推送、自动提交无关改动或凭据；推送失败保留本地标签并报告，不盲目重试。
7. **证据与硬件边界不变。** 优先引用已提交的脱敏报告以便换电脑追溯；本地日志记录路径与 SHA-256，并说明未随 Git 同步。不得改写 sealed 历史记录、Guard、Permit 或采样数据；不得为了更新账本自动连接板卡、启动相机/传送带、加载 Overlay、访问 MMIO/DMA 或重测。任何此类操作仍需独立的明确授权。最终回复包含 changed files、验证结果、账本新增行、标签/推送状态与未完成门禁；不满足条件时保留记录并明确阻塞原因。
