# WO-0015 AFI1 访问前置条件与 AXDS1 定义：离线澄清报告

研究开始：2026-10-15；完成：2026-10-16（Asia/Shanghai）。目录沿用开始日期。最终状态：**STOPPED_PRECONDITION_UNVERIFIED**。

本轮没有连接板卡、读写硬件寄存器、加载Overlay、复位、DMA、Vivado执行或重新生成Bitstream。只新增独立结果、采集器和离线测试；未改既有RTL/BIT/HWH、生产执行器或历史报告，未提交、未合并。

主要进展：已取得AMD官方历史全文及当前门户资料；确认bit21不是可通用依赖的复位状态位。生成代码还揭示silicon分支差异。读取FPGA_RST_CTRL的保护语义与“能用bit21证明AFI安全”是两个不同问题，不能相互替代。

## 1. 标签定义及核心裁决

| 标签 | 本报告含义 |
|---|---|
| VERIFIED | 来源直接支持所述事实；若来源是文件或历史快照，只验证该层事实 |
| INFERRED | 有条件推断或间接对应，尚无相应实机证明 |
| UNKNOWN | 没有足够证据，不能按PASS处理 |
| CONFLICT | 同一适用范围内的说明未协调，存在不能自行裁决的定义冲突 |

| 问题 | 分类与结论 |
|---|---|
| bit21位置/掩码 | VERIFIED：位置21，掩码0x00200000；位置本身不赋予运行语义 |
| 当前AMD寄存器索引中bit21定义 | VERIFIED：reserved，复位默认值1，写入要求为0；没有AXDS1状态语义 |
| 索引片段严格属于哪个PDF版本 | INFERRED：门户当前v1.15上下文；片段自身标version=All，未拿到v1.15完整PDF |
| 官方历史UG585 v1.10字段表 | VERIFIED：bit21也是reserved，默认1，写入要求为0 |
| AFI控制页的前置说明 | VERIFIED：RD/WR均引用FPGA_AXDSN_RST写0前置；并非只针对传输，也包含访问控制寄存器 |
| AFI前置文字与reset字段表如何对应 | CONFLICT：同一历史全文已同时存在“命名复位字段前置”及“该位置reserved且无此字段”的说明 |
| 实板silicon分支、当前bit21/AFI值 | UNKNOWN：本轮无实测，历史也没有这些寄存器读数 |
| bit21读0是否证明HP1 reset解除 | 不能作为权威证明；这是从缺少有效字段语义得出的INFERRED安全裁决，不能反推实际硅状态 |
| 是否允许进入AFI读取 | 否，继续STOPPED_PRECONDITION_UNVERIFIED |

不能把reserved默认为read-as-zero。文档对另一些保留范围明确规定读零，但bit21行不是该类表述；其默认值为1也不等于有可用的复位断言状态。文档要求写0不构成本轮写权限，亦不能解释成应现场写0来“满足”只读条件。

## 2. 文档版本对比与冲突定位

| 来源/版本 | bit21定义或有关说明 | 页码/章节 | 证据等级及边界 |
|---|---|---|---|
| AMD官方UG585 v1.10，2015-02-23，日文全文 | FPGA_RST_CTRL bit21为reserved，默认1，写入要求0 | 附录B，System Level Control Registers，地址p.1555，字段表p.1556 | VERIFIED，官方完整PDF已下载并提取 |
| 同一v1.10 | AFI RD控制要求相关FPGA_AXDSN_RST预先写0；HP1读控制0xF8009000 | 附录B，AXI_HP Interface (AFI)，p.745 | VERIFIED，同一全文中的前置文字 |
| 同一v1.10 | AFI WR控制有相同前置；HP1写控制0xF8009014 | 同章p.748–749 | VERIFIED，与上行共同构成内部定义冲突 |
| AMD门户UG585 v1.15，2026-02-06 | Revision History列出的当前版本 | Revision History，Version 1.15条目 | VERIFIED为本次门户可核验版本；不声称掌握门户以外所有版本 |
| 当前AMD FPGA_RST_CTRL索引 | bit21仍为reserved/默认1/写0，无FPGA_AXDS1_RST字段 | Register FPGA_RST_CTRL Details | 内容VERIFIED；该片段标version=All，归属v1.15仅INFERRED |
| 当前AMD AFI RD/WR详情 | 仍引用FPGA_AXDSN_RST写0前置；32BitEn位0：1为32、0为64 | Register AFI_RDCHAN_CTRL / AFI_WRCHAN_CTRL Details | VERIFIED为所获内容；RD可访问页面含v1.15元数据，WR依赖索引 |
| AMD AR#56642 | 修订旧UG585 v1.5的bits24/17/16，未涉及bit21 | 官方支持记录Question/Answer摘要 | VERIFIED为摘要范围；不能作为bit21的勘误或解冲突依据 |
| 早期第三方v1.0镜像 | 曾出现AXDS1活动字段名称 | 非AMD一手版本，未纳入本轮裁决 | UNKNOWN适用性，不用它覆盖官方全文/索引 |

本轮可直接比较的是官方v1.10全文与当前门户/索引，并未完成所有历史版本逐版差异追溯。当前revision history的更新清单没有明确解决该冲突；“未列出修改”也不证明从未更改。**无法确认AFI前置文字过时，亦无法确认它只是要求初始化保留位为零**，两种解释均不得用于放行。

官方入口：[v1.10完整PDF](https://docs.amd.com/api/khub/documents/wkAwefW0cDJrTXuZKWYZTA/content)、[当前Revision History](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Revision-History)、[FPGA_RST_CTRL字段](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-FPGA_RST_CTRL-Details)、[AFI RD字段](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-AFI_RDCHAN_CTRL-Details)、[AFI WR字段](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-AFI_WRCHAN_CTRL-Details)、[AR#56642](https://adaptivesupport.amd.com/s/article/56642?language=en_US)。

每条来源的获取方式、版本绑定限制、定位和全文hash在 `docs-research/official-sources.json`；详见该目录findings。Manager复核了本地官方PDF提取中的bit21表、AFI前置文字及SLCR读保护描述。

## 3. FPGA_RST_CTRL只读访问是否安全

**VERIFIED（文档规定）**：地址0xF8000240，32-bit，rw；[SLCR_LOCKSTA字段](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-SLCR_LOCKSTA-Details)说明锁定保护会忽略写入而读取仍有效。因此读取SLCR不需要先写unlock；不得把unlock作为读取步骤。历史全文附录B的SLCR_LOCKSTA表也给出相同保护语义。

**INFERRED（文档层面的普通只读取证）**：在已确认正确器件、正常PS环境、映射权限/内存属性和严格只读实现等条件下，一次32-bit FPGA_RST_CTRL读取可以作为原始寄存器取证候选。所获资料未给出该寄存器的read-to-clear等读取副作用，但“未说明副作用”不被提升成任何板端环境下绝对无风险的保证。本轮没有实际映射或读取，也没有实现/启用SLCR读取后端。

**UNKNOWN（当前实际准入）**：当前器件silicon revision、运行环境、访问映射条件和该次读取的实时准入尚未核验。**即使未来读到bit21=0或1，也只能记录保留位原始值，不能据此解除AFI阻断。** 读取SLCR与读取AFI属于不同目标和授权；前者的保护语义不自动授权后者。

结论：具备研究单次SLCR只读取证的文档依据，**不具备“靠bit21确认reset并继续AFI”的证据链，也未满足当前实机准入**。在冲突未解决前，新增该读取不能消除主要阻断，不作为推荐第一步。

## 4. Vivado 2026.1生成代码、PS配置与启动路径

文件根目录：`data/results/wo0015-hw-50mhz-20261015/project/wo15_50m.gen/sources_1/bd/wo15/ip/wo15_ps7_0/`。实现日志直接记载Vivado v2026.1；未重跑工具。

| 证据层 | 已查到内容 | 裁决 |
|---|---|---|
| 静态设计 | 锁定HWH的S2MM memory/stream均32，HP1=32，S2MM连接HP1；HP0读路径64 | VERIFIED为保存设计，不是实时AFI |
| 生成HTML/C silicon 1.0/2.0分支 | bit21名为FPGA_AXDS1_RST，描述AXDS1软复位 | VERIFIED为分支定义 |
| 生成HTML/C silicon 3.0分支 | 同位置名为reserved_FPGA_AXDS1_RST，HTML写Reserved/Do not modify | VERIFIED为分支定义 |
| 上述分支间差异 | 语义随silicon分支不同 | VERIFIED版本差异；不能仅因不同分支就判为同一条件下冲突。当前适用分支UNKNOWN |
| 生成post-config三分支 | 都生成FPGA_RST_CTRL全掩码写零，再将AFI1 RD/WR bit0置1 | VERIFIED为代码内容；仅在表被调用且执行成功时预计产生对应写入（INFERRED） |
| 生成ps7_init() | 依次配置MIO/PLL/Clock/DDR/Peripherals，无ps7_post_config调用 | VERIFIED |
| 生成ps7_post_config() | 独立函数/过程，按运行时silicon版本分派_1_0/_2_0/_3_0 | VERIFIED；不能为查看分支而执行该函数或其版本读取辅助代码 |
| PS7_POST_CONFIG宏 | 头文件供FSBL检测入口存在，非调用证据 | VERIFIED |
| 历史Overlay脚本 | 显式Overlay(download=False)→download；脚本未显式调用PS init/post-config | VERIFIED为保存源码；库内部/镜像启动行为不能由此排除 |
| 当前实际FSBL/BOOT.BIN/U-Boot来源及调用 | WO-0015保存材料没有相应启动镜像/ELF/源码或调用证据 | UNKNOWN；不是断言板上不存在这些文件 |
| 当前PS/AFI实际状态 | 无读数，无当前启动代码执行证明 | UNKNOWN |

核心定位：C `ps7_post_config` 6944–6960、`ps7_init` 6983–7038；Tcl独立分派529–545、init562–589；头文件68–69、103–106。3.0表FPGA_RST_CTRL写在C2132，AFI1 RD/WR写在2142/2147；bit21注释C2080–2082。HTML3.0表28269–28284、2.0表56919–56934、1.0表84930–84945。精确源码hash和其他分支定位见 `init-research/source-evidence.json`。

**不能从“Vivado是2026.1”推断板卡silicon是3.0；也不能把CPU core revision当作PS silicon revision。** 即使知道板卡silicon版本，生成文件是否与真实FSBL链接并被执行仍需独立证据。

## 5. 实时只读准入采集器：已完成的离线工作

新增 `readonly_admission_collector.py`，不修改上一轮候选或生产脚本。实现固定OS文件读取、原始字节SHA-256/base64、UTC采样、boot/uptime前后窗口、BIT/HWH和7个Guard锁、PYNQ缓存身份校验、proc占用/权限可见性和FPGA Manager前后状态检查。CMA仅遥测。不存在寄存器读取能力、PYNQ导入、传输、任意地址配置、写寄存器或自动清理Guard；CLI固定拒绝实机执行。

与上一轮不同，它直接采集和校验来源，不接收九个True作为通过凭据。**但它仍是待部署评审的OS证据模块，不是已认证的远端采集协议，也不是AFI准入放行器**：始终保留STOPPED、reset UNKNOWN、definition CONFLICT、afi_read_authorized=false。完整审计、可信路径/传输/重放边界见 `collector-audit.md`；独立Worker也审阅了这些限制。

本轮实际定向回归：**76 passed**，其中新采集器62项、上一轮最小读取核心14项。所有设备/OS后端均为离线fake或monkeypatched函数，未用真实后端进行采集。测试覆盖原始换行、身份/摘要/Guard、歧义JSON、boot与时间变化、占用/扫描不可见、只读flags、非regular设备拒绝、异常关闭、无重试和不放行AFI。先记录预期行为及重复JSON键的RED，再修复通过。上一轮459项+5子测试未在本轮重跑，不作为本轮新增通过数。

## 6. 下一阶段最小授权申请与未决风险

建议范围见 `next-step-approval-request.md`：**先只取OS文件层的板卡/启动来源证据，不读SLCR或AFI，不加载、不复位、不DMA**。启用前仍须完成固定传输、源码摘要、受控路径/挂载和节点实现的部署评审；任何不满足都停止，不能手填True或信任任意JSON。

同时准备了可供人工提交给AMD的澄清问题，见 `amd-clarification-question.md`，本轮没有向任何人/外部支持系统发送消息。需要权威解释AFI通用前置与reserved定义对不同silicon版本的适用性、允许的只读安全证明，以及正常已运行Linux下是否可只读AFI。

主要未决风险：文档字段映射未协调；silicon适用分支未知；实际启动链/库行为未知；软件缓存不等于当前硬件；仅扫proc不能排除后续竞争；采集器依赖可信父路径/挂载且未实机验证，challenge/来源需未来传输绑定；任何访问的实时环境仍未知。没有一个风险通过假设解除。

## 7. 交付与边界

本目录为新建独立结果 `data/results/wo0015-afi1-preconditions-20261015/`。来源及比较、代码审计、源码/历史摘要、测试结果和交付索引均随附。历史AFI读数仍为空，39520差异根因仍未证实；本轮只推进了文档与取证准备，不将配置假设升级为实测结论。
