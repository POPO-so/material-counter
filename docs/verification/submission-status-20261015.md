# 竞赛提交进度核验与交付清单 · 2026-10-15

> 历史快照：本文件记录 2026-10-15 的仓库与交付状态。后续 PR、main 基线及 WO-0015/WO-0016 证据已更新，当前状态请见[2026-10-18 提交状态](submission-status-20261018.md)。下文的 PR 开放状态和剩余任务仅用于追溯，不作为当前状态。

本记录按当时的源码、原始报告、哈希与 GitHub 状态核验。历史 Work Order 中的“未提交”“等待批准”“尚未实现”保留为当时记录；它们不覆盖后续提交和证据。本轮未连接 PYNQ，未执行 Overlay、MMIO、DMA 或电机动作。

## Git 基线与交付关系

原工作区：`C:\fpga\material-counter`；分支 `feat/pl-stream-skeleton`，HEAD `7a868c0`。原暂存区为空；大量未跟踪实验、技能和缓存保留。所有整理与修复均在独立工作树完成。

GitHub 默认分支 `main` 为 `4398f64`，只有 synthetic 实现，没有这些后续 PL/PS 模块。PR #1 为 OPEN、非草稿，head `0e276cf`、base main，交付流骨架、RGB→Gray、Gray→Binary。PR #2 为 OPEN、草稿，head `f0fee44`、base `feat/pl-stream-skeleton`，29 文件差异为真实 ROI 裁剪和证据。两者均未合并、无已上报 CI checks，不能把无 CI 等同验证通过。

从 fetch 后的远端 Git 对象读取 #1 的 RGB/Binary 与 #2 ROI source manifests，15 条源码/测试/参考脚本记录全部与对应 PR head blob 的 SHA-256 相符，详见 `pr12-review.json`；不因本地额外阈值脚本修改而误判远端证据版本。

本轮没有推送原 `feat/pl-stream-skeleton`，因此 #1 的 diff 不会被十个本地后续提交扩大；#2 继续依赖 #1，不改 base、不合并、不改写历史。新增交付链为：

| 批次 | 草稿 PR / base | 精确文件清单 | 限制 |
|---|---|---|---|
| 01 扩展阈值验证与形态学 | [#3](https://github.com/POPO-so/material-counter/pull/3) / #1 head | `data/results/submission-audit-20261015/batch-01-morphology.json` | 模块离线成果；WO-0008 morphology 与 #2 的 WO-0008 ROI 名号冲突仅作标注，不重命名历史记录 |
| 02 PS 参考与候选研究 | [#4](https://github.com/POPO-so/material-counter/pull/4) / #3 | `batch-02-ps-reference.json` | WO-0011 失败与 WO-0012 Proposed 状态完整保留 |
| 03 WO-0013 | [#5](https://github.com/POPO-so/material-counter/pull/5) / #4 | `batch-03-wo0013.json` | resetfix 单次 MM2S→Monitor；无 S2MM |
| 04 WO-0014 离线与 OOC 优化 | [#6](https://github.com/POPO-so/material-counter/pull/6) / #5 | `batch-04-wo0014-offline.json` | 原始脚本保留历史根路径；新跨目录入口见 `scripts/delivery/REPLAY.md` |
| 05 WO-0014 硬件依赖与精选证据 | [#7](https://github.com/POPO-so/material-counter/pull/7) / #6 | `batch-05-wo0014-hardware.json` | 纳管此前遗漏的 wrapper、构建/导出脚本、两对 BIT/HWH、所需 fixture 与最小 AMD helper；不带 project/cache/DCP/个人配置/现场调度档案 |
| 06 本轮审计、复现入口与生成器安全修复 | [#8](https://github.com/POPO-so/material-counter/pull/8) / #7 | `support-files.json`；主修复提交 `a3fa67c` | 16 个明确文件；修复非空输出污染，不改变 H3 数据规则；跨目录准备不等于新 RTL PASS |

五份 inventory 共 319 条文件记录，逐条记录原文件 SHA-256、大小、原提交或未跟踪快照来源。原提交 `da44709` 至 `7a868c0` 不被重写；精选交付产生新提交，旧 SHA 是来源标识，不是假定 clean clone 能取到的祖先。新工具不依赖旧 ca6 提交存在。

inventory 是成果文件快照清单；提交还包含 inventory 自身、`.gitattributes` 字节保留规则和第三方许可等支撑文件。Batch05 包含一项已由 Batch03 清单覆盖的 wrapper 换行字节修复。每个 PR 的完整实际 diff 路径与提交 SHA 另见 `data/results/submission-audit-20261015/delivery-revisions.json`，不以 inventory 条目数代替提交文件数。

每批已提交并推送，均未合并。先审查依赖再落地主批次；不要将多个草稿直接合入 #1 工作分支。原 #1/#2 与本链关系见上表。

## 实际功能与证据等级

等级分别记录：代码实现、离线软件、RTL 仿真、OOC 综合/OOC 时序、完整布线时序、实板通信、真实输入物料计数。它们不能互相替代。源码确切字节见各批 inventory，关键历史源绑定由 `scripts/delivery/verify_delivery.py` 校验。

| 功能 | 源码位置 / 来源版本 | 验证证据与等级 | 提交/推送/合并 | 剩余阻塞 |
|---|---|---|---|---|
| deterministic H3 | `python/synth/`；原规则在 main，安全修复为最终审计批次 | 原接受记录；本轮 9 tests；新旧生成器 fresh 输出 530 文件逐字节相同。487 PNG 是 504 时隙减 17 缺帧 | 原生成器已合并；拒绝非空目录修复另交草稿 | 合成输入不能替代真实输入 |
| stream / RGB→Gray / Gray→Binary | `fpga/rtl/axis_{stream_skeleton,rgb2gray,gray2binary}.v`；#1 `0e276cf` | PR #1 handoff；RGB 两种模式各 230912 像素；Binary 12 组 2774016 像素；模块仿真与综合 | 已提交/推送 #1，未合并 | 独立模块报告没有完整 I/O 时序约束；后续系统时序见 WO14 |
| ROI | `fpga/rtl/axis_roi_crop.v`；仅 #2 `f0fee44` | `roi-handoff-20261012`：24 有效、8 非法参数、3 联合入口；独立仿真/综合 | 已提交/推送 #2，未合并；不在原本地 HEAD | 尚未串入本次板测链；依赖 #1 |
| morphology | `fpga/rtl/axis_morphology3x3.v`；来源 `25f597c` | `morphology-acceptance-20261013/manager-verification.json`：84 组、2806344 像素零差异；当前源与 synth source-hashes 匹配，lint RTL hash 匹配 | 已提交/推送 #3，未合并 | 未串入 WO14 板测 BIT；无该模块完整实现时序。lint 目录包含旧 TB hash，TB 不参与 lint；最终 TB 用 synth manifest 钉住 |
| 区域→跟踪→越线 PS 参考 | `python/refmodel/ps_reference/{regions,tracking,evaluate_h3}.py`；`db552ea` | `wo0010-reference-final-20261013/manager-verification.json` 源 hash 匹配；H3 image TP19/FN12，qualified recall 61.29%；离线参考 | 已提交/推送 #4，未合并 | 无缝接触失败保留；不是板上 PS 服务，没有 DMA receiver/显示 |
| bounded feature / codec 候选 | `python/refmodel/experiments/wo0012_{bounded_runs,protocol}.py`；`390d4cb` | WO12 summary/预算/单元测试；仅 PC 候选模型 | 已提交/推送 #4，未合并 | ADR-0003 Proposed；正式 PL 特征引擎/ABI/Route/G1 未通过 |
| WO13 Binary8 DDR→MM2S→PL Monitor | `fpga/experimental/wo0013/`、`scripts/wo0013/`；`4710333` | resetfix BIT `47e6cd65…`、HWH `a31d71f8…`；一次 76816B、19204 拍、1 TLAST、checksum/首末字一致，健康检查通过；真实板通信 | 已提交/推送 #5，未合并 | 不是 RGB、无 S2MM；实测入口为 evidence 内 remote-send-once，不是会 reset/reload 的 generic helper |
| WO14 RGB 包→Gray→Binary→checker | `fpga/experimental/wo0014/`；基线 `ca6a68c`、优化 `6264b32` | 15 XSim 场景、487 帧/37401600 像素零差异；7 种坏包恢复；优化后 OOC100 WNS +2.354ns。`input-hashes.json` 匹配当前 adapter | 已提交/推送 #6，未合并 | 错包前缀必须作废；packet_done 不等于排空；checker 不是通用异常帧守卫 |
| WO14 完整 Zynq 实现 | BD wrapper、`scripts/wo0014_hw/`；原先未跟踪源快照 | 50/100 pair-manifest 各 6 份 RTL 与当前源全部匹配；完整实现 WNS +9.526/+1.167ns，WHS +0.068/+0.066ns，TNS/THS=0 | 遗漏依赖本轮提交/推送 #7，未合并 | DRC 14 warning、2 advisory 和 LUTAR-1 methodology 风险保留；时序通过不消除这些告警 |
| WO14 50MHz 通信里程碑 | diagnostic50、closeout 源与历史 source pins 匹配；来源 `5a153c4`、`7a868c0` | 单帧 PASS；另有 3 帧 DMA/checker/Golden 聚合统计 PASS；外层 CMA 门导致 FAIL_STOPPED，原前后数值未持久化 | 精选证据/源已提交推送 #7，未合并 | 不能断言泄漏或忽略门控；100MHz 板测全部 NOT_VERIFIED；无像素逐项回读 |
| 真实输入、PL→PS 回传、板上计数显示 | 尚无可验收主链实现 | 无真实摄像头/物料闭环证据 | 未交付 | 见下方三项缺口 |

最新 WO13 `wo0013-batch-20261014` 是另一对未板测产物（BIT `84c1229d…` / HWH `99f5b5b6…`），其 manifest 的建 BD Tcl `bad14ac6…` 与当前 `19378c47…` 不匹配。本轮选择源绑定一致的 resetfix 交付；不把另一批次称作完全可复现，不用它替换 Level2 板证据。旧 WO14 offline source-hashes 是优化前 adapter；优化后证据使用 timing/input-hashes 与两个硬件 pair manifest。

## 此前审计问题的逐项处置

| 项 | 实际状态 / 证据 | 安排 |
|---|---|---|
| 接口序列化 | WO12 codec 具备小端 16B 记录/帧尾、长度/保留位/计数校验；`test_wo0012_protocol.py`。正式接口 B 仍只有字段语义，草案不能当生产 ABI | P0：在 PL→PS 回传方案中给字节/EOF/实际长度/拒帧原子性具体布局；接口变更走 PR+三人确认+ADR |
| DMA 包边界 | WO14 输入 RGB packet 为 230432B，18-bit DMA；仅尾 trailer 拍 TLAST；输出 TLAST=EOL；adapter/TB/坏包恢复与板上输入统计证明这个实验边界 | P0：S2MM 回传需 EOF 桥，禁止将每行 EOL 接成整帧 DMA 完成；现有 BIT S2MM=0 |
| 字段溢出 | PC codec/bounded 模型检查面积16-bit/坐标/容量并拒帧，有测试；没有生产 PL 特征输出实现 | P0：生产接入前验证全帧 76800 前景会超过 16-bit area 等边界；不得靠截位继续计数 |
| timestamp 回绕 | 32-bit/100MHz 约42.95s；草案描述 modular delta，codec 只打包，tracker 仅用 frame_id，无实际 delta/epoch 重置测试 | P0（使用真实时间戳前）：先给半周期有效窗/重启边界方案；当前 frame_id 离线参考不冒充已解决 |
| generator 旧帧残留 | 原 `generate_dataset` 可向非空目录继续写，缩短序列/移除条件会留旧 PNG。本轮在任何写入前抛 FileExistsError；空目录、新路径保留 | 已修复；RED→GREEN、原文件完全保留、fresh 530 文件与原版一致，见 generator-checks.json |

## 本轮验证与复现

本轮不重复既有完整 XSim、OOC 或实现；复查源码哈希、RTL/产物对应关系，并在精选依赖布局上执行软件测试，以暴露遗漏依赖。结果：synth 9 passed；PS reference/WO12/WO13 软件 120 passed；diagnostic50 144 passed；closeout 与单次 helper 24 passed、5 subtests passed。初次诊断回归 143 passed/1 failed，缺 dmesg fixture；精选原始两份日志补入后 144 passed，未放宽测试。

跨目录准备工具从固定 spec 重建的 487 张 PNG 与原输入逐帧 SHA 全匹配，非空目录拒绝生效，16 项准备源 hash 与清单匹配。文档完整比较命令用 golden 副本作 actual 自检，487 帧零差异；这只证明路径/比较器可用，不是新增 RTL PASS。初轮发现比较器还需绑定 image_vectors.ROOT，命令已修复并复核；结果见 replay-preparation-checks.json。

执行 `python scripts/delivery/verify_delivery.py` 检查五批 inventory、PS/morphology/optimized source pins、WO13 artifact、两对 WO14 BIT/HWH/RTL、执行器 pins 和保留的 FAIL_STOPPED 状态。`scripts/delivery/REPLAY.md` 提供 fresh 目录下重建固定 H3、向量、跨目录 Tcl 和独立 Python 比对命令；默认不访问设备。历史报告/脚本中的绝对路径保留为来源，不是当前路径承诺。

软件依赖快照为 `scripts/delivery/requirements.txt`（本轮实际环境：CPython3.12.10/WindowsAMD64，NumPy2.5.3、Pillow12.3.0、pytest9.1.1、opencv-python-headless5.0.0.93）。复现时在自己的虚拟环境执行 `python -m pip install -r scripts/delivery/requirements.txt`；本轮没有修改全局 Python 或安装配置。验证器本身仅使用标准库。

完整工程复建前提：Vivado2026.1、xc7z020、PYNQ-Z2 board preset；使用已钉住的最小 AMD helper，MIT 许可见 fpga/scripts/THIRD_PARTY_NOTICES.md。示例离线构建（在新输出目录）：

```powershell
vivado -mode batch -source scripts/wo0014_hw/run_batch.tcl -tclargs C:/fresh-build/wo0014-hw-50mhz C:/board-repo 50
```

`finalize_build.py` 的历史输出路径/commit 门绑定旧实验；交付完整性用新 verifier。新构建必须产生新报告，不能把现有报告改成新构建 PASS；bitgen 时间戳等因素也不保证重建字节相同。本轮未重新运行完整工程。

## 距离最小竞赛闭环的三项缺口

1. **真实输入可验收**：指定摄像头/光源/物料采集，验证实际模式、曝光、持续帧率、时间戳/缺帧及可重复短片；当前没有此证据。
2. **PL 输出到 PS 的完整接收**：当前 MM2S+统计实验后增加经评审的 S2MM 帧回传/特征序列化方案，整帧边界、缓存同步、长度、坏帧拒收与 source identity 可核验；同时先解决 CMA 健康门的取证缺口。PL 特征候选与临时 Binary8→PS 路径均不能未经决策冒充正式架构。
3. **板上 PS 计数与基础显示**：将已验证参考接到真实接收输出，持续跟踪/越线去重，显示总数/帧状态/错误；以同一实物视频和人工真值测准确率、FPS/延迟/资源，不把前景像素数当物料数。

下一项最小任务：形成 **P3 输出回传桥的离线设计与验收向量包**，先给 S2MM 容量、Binary8 打包/尾拍TKEEP、EOF TLAST、先收后发、PS sync_from_device 与整帧提交/拒收具体方案；明确其为隔离实验，提交接口/ADR评审后才能实施。验收为320×240单帧和连续三帧边界模型、欠长/超长/坏包整帧拒收样例、零跨帧混入及可逐像素Golden比对；不需要板卡即可评审。正式 PL 特征方案仍由团队决定。

## 时间安排与板端待批准范围

| 截止 | 优先交付 | 准入/停止原则 |
|---|---|---|
| 10/15–10/20 | 审查上述批次、CMA只读取证、输出回传与真实输入的具体方案/团队决策 | 只推进一个主链阻塞；不扩展复杂遮挡或A2 |
| 10/21–10/27 | 经批准的最小回传→PS参考→越线→基础显示，获取真实输入证据 | 每次板测另批准、失败即停；优先保留已有50MHz可用证据 |
| **10/28 功能冻结** | 一条可运行主链、版本/产物/数据/测量边界固定 | 未验证的100MHz、复杂算法与送料控制不作为交付依赖 |
| 10/29–**11/03 交付包完成** | clean-checkout复现、固定输入/演示、源码/报告/视频/运行指南、目录映射与SHA清单 | 不将离线或统计证明改写为实际计数性能 |
| **11/04 提交** | 提交已核验的冻结包与必要比赛材料 | 只引用可追溯实测数值 |

下次如申请 CMA 取证，精确候选范围为：**一次**已认证 SSH 会话运行一个只读 OS 脚本，不导入 PYNQ、不做 mmap、不创建/释放缓冲、不删除/更换 Guard、不触发 Overlay/MMIO/DMA。执行前核对固定源码SHA、boot、已有Guard/停止状态与板身份；采样3次 `/proc/meminfo` 的 CmaTotal/CmaFree（0/1/2秒），每次持久写UTC/monotonic时间、进程生命周期/持有者摘要与内核连续性，保存原始值和脚本hash。身份或boot不符、不可审计持有者、内核异常、写证据失败或超时即停止且不重试。已有CMA严格门不放宽；仅据这次OS采样仍不能证明DMA释放正确或恢复硬件准入。本轮不申请或执行此操作；需要新人工批准。
