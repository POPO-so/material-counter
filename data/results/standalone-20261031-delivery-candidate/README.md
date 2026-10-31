# 交付证据候选目录映射（2026-10-31）

**用途：候选证据目录映射。不是最终提交包，不代表 G3 或 G4 放行。** 本文件只定位当前可见证据、记录中的身份及缺口；不复制制品，不补造缺失文件或哈希。状态以各证据原文和实际目录为准。

**2026-10-31 当前包身份、部署与用户测试分工：** G4 当前候选包为 [v2](../standalone-20261031-g4-offline-package-candidate-v2/README.md)，37 个运行模块来自提交 `6cab07f244b8a0d0fb35d6eb4a2f9a293b4ee5ed`，索引 SHA-256 `c4c73cbb7c8df5ff2adb0f9df8be52ff1919faf38516747b039ee6b7910f2e31`，外置环境配置绑定相同索引。v2 [源码对应报告](../standalone-20261031-g4-offline-package-candidate-v2/source-correspondence-20261031.json)和 [板端部署快照](../standalone-20261031-g4-offline-package-candidate-v2/deployment-after-20261031.json)记录当前身份；首版包及其报告只作历史保留。板端 `current` 已指向 v2 release，service loaded/enabled/inactive/dead，[安装位置 56 文件完整性核对](../standalone-20261031-g4-offline-package-candidate-v2/installed-integrity-20261031.json) PASS，但没有启动 G4 业务，硬件安全终态仍未证明。完整离线重建闭包与自启动未证明。用户负责最终现场测试；五次冷启动、连续 ≥60 分钟运行及故障恢复测试已豁免。G3/G4 均未放行；关键制品路径与本地 SHA-256 见[机器可读清单](artifact-sha256.json)。

## 门禁状态与证据入口

| 门禁 | 状态 | 证据位置与范围 |
|---|---|---|
| G2 | 已放行 | [G2 集成报告](../standalone-20261030-123000-g2-integration/README.md)。构建与板端复验、告警说明、BIT/HWH/manifest 摘要均见报告；具体文件在该目录的 `export/`。板端准入、相机冒烟和 RLE+HDMI 像素冒烟原始记录在 `board/`。 |
| G2.5 | 已放行 | [G2.5 并发压力报告](../standalone-20261030-g25-concurrency/README.md)。moving/high_runs off/scan、dynamic、高游程像素检查及 180 秒追加轮数据在同目录 JSON、控制台、`summary.json` 和 `post-long-readback.json`；初始轮 runner 快照与来源说明见 `source-provenance.json`、`g25_run-before-memory-diagnostics.py`。追加 180 秒轮的源码 SHA 有记录，但此目录未保存该轮对应源码快照。 |
| G3 | **未放行** | [G3 部分报告](../standalone-20261030-g3-functional/README.md)。当前源码身份回放、协作式绘图退出、遥测冻结等报告在该目录；[人工真值执行前协议与空白模板](../standalone-20261030-g3-functional/manual-truth-protocol.md)仅定义准备和记录口径，尚未执行样本或确定通过标准；[离线核对工具](../../../scripts/standalone/manual_truth.py)已写、未运行/未验收，且只核对声明与已保存文件。正式验收还需独立封存人工作业原件及其哈希/时间证据；工具不审定物理真值，也不定义阈值。本地最新干净检出回归为 376 passed，见下文复验记录；G3 报告目录中的 `local-pytest-20261031.txt` 是较早 372 passed 历史记录。旧固定回放的 on/off 成对 JSON、控制台也在该目录，但其 Python runner 身份无法由旧报告唯一复原。 |
| G4 | **未放行** | [v2 候选包报告](../standalone-20261031-g4-offline-package-candidate-v2/README.md)。当前索引 SHA-256 为 `c4c73cbb7c8df5ff2adb0f9df8be52ff1919faf38516747b039ee6b7910f2e31`，源码对应报告为 [v2 报告](../standalone-20261031-g4-offline-package-candidate-v2/source-correspondence-20261031.json)。板端 `current` 已指向 v2，service loaded/enabled/inactive/dead，安装位置 56 文件只读完整性核对通过；不证明离线依赖闭包、自启动、运行或安全停止验收。摘要见[机器可读清单](artifact-sha256.json)。 |

G3 未完成项包括实物 GPIO 边界、HDMI 拔插/硬件恢复、真实相机有目标输入及人工物料真值、缩放黄金验收；`scripts/standalone/test_integrated_run.py` 中 Process 创建/启动失败对应单元用例已包含在 2026-10-31 的本地回归中（干净检出复验 376 passed；G3 目录的 372 passed 日志是较早历史记录），但板端故障注入/运行证据尚缺。G4 候选 release、外置 env 与 systemd unit 已部署，service 当前记录为 enabled/inactive；仍未完成既有板端环境下离线依赖闭包/准入的现场确认、自启动及安全停止验收。wheelhouse、OS 镜像等干净环境重建材料尚未形成，属于更强的重建目标。原计划的五次冷启动、≥60 分钟运行和故障恢复已由用户豁免，不再作为代理待执行测试，由用户负责最终现场测试。不得用单项回归、候选索引或历史独立显示结果替代这些门禁。

## 固定输入、制品与演示材料

| 项目 | 当前可定位状态 | 身份/哈希的证据来源 |
|---|---|---|
| G2 BIT | 存在：`../standalone-20261030-123000-g2-integration/export/g2-integrated.bit`；G4 v2 包内副本：`../standalone-20261031-g4-offline-package-candidate-v2/package/export/g2-integrated.bit`。 | 本次清单直接对 G2 export 文件计算 SHA-256；值为 `79609f9a60c3e727b5f2f54a8d639a464a098715fb5eb28d294646a9735948e0`。G2 README 和既有记录相符。G4 包内副本 SHA 未列入本清单，不据此推及板端文件。 |
| G2 HWH | 存在：G2 `export/g2-integrated.hwh`，G4 包内 `package/export/g2-integrated.hwh`。 | 本次清单直接对 G2 export 文件计算 SHA-256；值为 `986de17a030540adc9e892cb6cd7b146ead7b5ddcf0aab2da80a9c0bf5027243`。不据此推及板端文件。 |
| Manifest | G2 `export/display-manifest.json` 与 G4 v2 包内 `package/export/display-manifest.json` 均存在。 | 本次清单直接对 G2 export 文件计算 SHA-256；值为 `2872e1598b3c3ab7ccb5d383f5cee6a8822dbd91eb0f198098eca067489a2966`。G4 当前 package index SHA-256 为 `c4c73cbb7c8df5ff2adb0f9df8be52ff1919faf38516747b039ee6b7910f2e31`；不要把 index SHA 当成 manifest SHA。 |
| G3 固定缩放输入/候选输出 | 存在于 G3 `scaling-inputs/`、`scaling-expected-candidates/`；旧比较在 `scaling-comparison/`，当前源码本地 AREA 候选比较及 actual raw/PNG 在 `scaling-current-20261031/`。这些材料尚不是板端黄金图。 | 各输入/候选 SHA-256 与生成方法见 G3 README；当前源码报告绑定 HEAD `b28e676`、runner/compositor 哈希，常量与 AREA ramp 全区域匹配、二值 nearest 遮挡外匹配。旧 LINEAR ramp 有 640/207,360 像素差异，灰度策略未正式冻结。 |
| G2.5 固定压力输入 | 报告称 moving 与 high_runs 由 `scripts/standalone/pressure.py` 固定生成；运行结果 JSON 和 runner 在 G2.5 证据目录。目录没有独立列出的原始输入文件。 | 输入定义/生成入口见 G2.5 README 和 `scripts/standalone/pressure.py`；不要将运行报告误称为独立保存的原始输入。 |
| G3 固定回放/命令输入 | 旧 v2 on/off 结果及命令摘要在 G3 目录；原始命令文件未保存。`replay-command-schedule-prepared.json` 是由旧输出事件重建的后备输入，不是旧运行原始输入。当前源码回放 JSON 已记录 package/index 等身份。 | 对应 SHA 与身份边界见 G3 README。旧 v2 不记录 Python runner commit/SHA；当前源码回放的报告摘要和 source identity digest 见 G3 README 及各 JSON。 |
| 报告/演示视频 | 各门禁报告、日志和 JSON 已按上表定位。上述 G3 目录未见 `video/` 或 `demo/` 子目录；本次范围内未找到独立视频/演示材料路径。 | 本映射不为缺少的演示材料生成哈希。 |
| G4 候选部署包 | v2 `package/` 存在，含 `deployment-index.json`、`package-source.json`、`board-runtime.json`、启动配置、systemd 文件、BIT/HWH/manifest、native library、字体及列入包的运行源码；[部署观察](../standalone-20261031-g4-offline-package-candidate-v2/deployment-after-20261031.json)记录板端 `current` 已指向 v2。 | v2 index、源码对应、安装位置完整性及离线运行时快照的 SHA-256 见[机器可读清单](artifact-sha256.json)。第三方依赖未形成完整离线闭包；板端 service enabled/inactive，G4 未放行。 |

## 干净检出本地核验（2026-10-31）

- 首次在干净检出 `d059b9d` 上运行：372 项本地软件回归通过；旧 `verify_delivery.py` 因历史 Gray8 RTL 快照末尾 CRLF 与当前 LF 源码的原始字节 SHA 不同而失败，后续核对按停止条件未运行。原始命令、输出和退出码见 [首次失败记录](clean-checkout-20261031-003608-771.json)。历史快照自身与 manifest 的 SHA 一致；三份不一致源码在换行规范化后与快照内容相同。
- 修复核验器后，在干净检出 `16b0e91` 上重放：`376 passed`、Gray8 `verify_delivery.py` 为 `PASS`（6 份中 3 份原始字节一致、3 份仅 CRLF/LF 等价）、G4 候选包 37 个应用模块来源核对成功。命令、完整输出、退出码及检出状态见 [复验记录](clean-checkout-rerun-20261031-004544.5324828.json)。该记录通过 Windows 控制台捕获的来源输出中，中文分支名发生转码损坏；[工具直接写入的 UTF-8 来源报告](clean-checkout-source-correspondence-20261031.json)保留正确分支名及全部 37 文件摘要。此处所指的 G4 `source-correspondence-20261031.json` 是当时刷新前的历史报告，不是当前包报告；当前包对应见 [v2 报告](../standalone-20261031-g4-offline-package-candidate-v2/source-correspondence-20261031.json)。
- 上述步骤没有安装或下载依赖，也没有将 Windows 环境隔离为离线依赖环境；因此只完成干净检出本地核验，未完成 Task 6 的离线环境重放、板端验收或最终提交包冻结。

## 来源与身份边界

- G2/G2.5 板端记录使用 boot ID `e48ff153-bd85-4dd6-98f6-438bd51da08d`；G3 当前源码回放记录的 boot ID 及临时包/index 身份见 G3 README。不同阶段的运行报告不能拼接成同一次端到端验收。
- G2.5 初始六轮 runner 来源提交和快照见 `source-provenance.json`；追加 180 秒轮只记录 runner SHA，没有对应快照。G3 旧 v2 回放缺 runner 身份；G3 后续当前源码回放记录 25 个 Python 文件的 source identity，但该摘要只覆盖项目内 Python 源。
- 当前 G4 [v2 源码对应报告](../standalone-20261031-g4-offline-package-candidate-v2/source-correspondence-20261031.json)记录 37 个应用模块与提交 `6cab07f244b8a0d0fb35d6eb4a2f9a293b4ee5ed` 的 Git blob 内容匹配，并绑定 v2 索引；它只证明源码对应范围，不是运行验收。首版和更早包的源码报告与索引仅作历史保留。
- G3 目录中的本地 372 passed 日志与命令记录是较早历史回归；后续干净检出复验为 376 passed，不能把旧日志当成本次复验记录。`scripts/standalone/test_integrated_run.py` 的 Process 创建/启动失败对应单元用例已运行，但板端故障注入证据仍缺。现有记录没有提供可与 25 文件板端 source identity 等同的源码快照/摘要。不得据此声称它覆盖板端回放代码身份或 G3 全部验收项。
- 清单文件 [artifact-sha256.json](artifact-sha256.json) 记录本次直接对现存 G2 BIT/HWH/manifest、当前 G4 index/source report、release 完整性报告与 offline runtime snapshot 计算的 SHA-256 和仓库相对路径；不存在项会以 `missing` 状态明确记录，不推测哈希。清单不替代包内 index，也不代表门禁通过。

## 交接限制

本文件按 Task 6 的“目录映射”目标整理候选证据，不完成 Task 6 的最终包冻结、干净检出离线重放、SHA 清单验收、部署回退说明或状态文档更新。候选/缺失材料、源身份边界及未完成门禁均须由 Manager 审核；在相关证据补齐并核验前，不得将此目录标记为最终提交包。
