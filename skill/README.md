# 可复用技能包

[作品首页](../README.md) · [AMD 指南核对](../docs/competition/CONTEST_REQUIREMENTS.md) · [协作与纠错记录](../docs/competition/AI_COLLABORATION.md) · [V13 包核验](../docs/competition/V13.md)

本目录现有三项可复用方法。`overlay-artifact-admission` 与 `dma-vdma-safe-status` 来自队友提供的 V13 r2 归档；`pynq_artifact_audit` 是单独补充的离线文件核验工具，不属于这份队友归档。公开仓库目录与本地比赛 RAR 的技能集合因此明确区分。

## 技能目录

| 技能 | 来源 | 适用场景 | 方法与边界 |
| --- | --- | --- | --- |
| [Overlay 工件准入](overlay-artifact-admission/skill.md) | 队友 V13 r2 | 上板前核对 BIT/HWH/manifest | 使用项目校验器检查哈希、路径、HWH 地址与声明能力；只完成离线准入，不代表成功加载或板端验收 |
| [DMA/VDMA 安全状态](dma-vdma-safe-status/skill.md) | 队友 V13 r2 | 读取指定运行的 DMA/VDMA 状态并判断停止证据 | 依据项目已记录的寄存器接口和 owner 协议；未知、错误或运行中状态均停止，不提供通用复位或重配置方案 |
| [PYNQ 离线工件哈希审计](pynq_artifact_audit/README.md) | 竞赛准备期间补充 | 对经独立审阅的 manifest 做文件字节、路径与哈希检查 | 仅用 Python 标准库；不验证 BIT/HWH 格式、硬件拓扑、依赖闭包或板端运行 |

队友技能的 `skill.md` 给出适用范围、使用流程、失效条件、验证作用及来源案例。PYNQ 工件审计的命令和测试在它自己的 README 中。此目录不要求安装到 Codex 全局环境。

## V13 RAR 包含范围

队友提供的 `material-counter-5438e0e1-complete-r2.rar` 中，`skill/` 仅含前两项队友技能。该 RAR 的运行模块仍有 5 项未与 V13 源码提交对齐，状态见[V13 包核验记录](../docs/competition/V13.md)；这两份技能文档可作为团队方法公开，不能据此把 RAR 认定为最终 V13 运行包。`pynq_artifact_audit` 没有合并进该 RAR。

技能可复用性和通用 PYNQ 加分由评审决定，本页不预判分数。公开归档中的技能说明和验证记录不能替代队友缺失的原始模型问答。
