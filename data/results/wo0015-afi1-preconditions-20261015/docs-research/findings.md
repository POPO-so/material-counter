# WO-0015 AFI1 前置条件：UG585 官方文档复核

日期：2026-10-15。范围：只读官方文档研究。没有连接板卡、读取或写入寄存器、运行 Vivado，也未修改 RTL、BIT/HWH、验证脚本或历史结果。

## 裁决

AMD 文档门户当前可验证的 UG585 版本为 **v1.15 English（2026-02-06）**。AMD 官方 FPGA_RST_CTRL 搜索结果将 bit 21 列为 `reserved`，reset value 为 1，并标注 `Reserved - always write with 0`；但该字段片段的版本标签是 `All`，没有单独锁定到 v1.15。因此，“索引片段显示 reserved”是 **VERIFIED**，“该片段在版本上明确属于 v1.15”只能 **INFERRED**（基于当前门户上下文）。AMD 官方完整历史 PDF UG585 v1.10（日文，2015-02-23）在 FPGA_RST_CTRL 字段表中对 bit 21 给出相同定义。两份官方资料均未赋予 bit 21 `FPGA_AXDS1_RST` 的字段名或可读取的 reset 状态语义。

因此，**读取 FPGA_RST_CTRL bit 21 不能作为 HP1/AXDS1 reset 已解除的权威证明**。`reset value=1` 是文档列出的寄存器复位值；它不等于该保留位是可观测 reset 状态，也不能据此把运行时读到 0 判为解除、读到 1 判为断言。

同一 UG585 v1.10 与当前在线 v1.15 的 AFI RDCHAN_CTRL、WRCHAN_CTRL 详情都要求在访问 AFI 控制寄存器前，先将 `FPGA_RST_CTRL.FPGA_AXDSN_RST` 写为 0；但对应 FPGA_RST_CTRL 字段表没有这个字段，bit 20–24 全部列为保留位。此为**文档内部冲突**。现有官方证据不能判断 AFI 前置文字是否过时、字段名是否指向另一个未说明的实现，或还存在其他遗漏。未用推测消除此冲突。

UG585 的 SLCR_LOCKSTA 定义说明：SLCR 锁定时写入会被忽略，读取仍返回有效寄存器值。因此读取 0xF8000240 无需解锁，且按只读 MMIO 访问不会写寄存器；但这只确认访问保护语义，不会给 bit 21 增加状态含义。文档没有明确陈述该寄存器的读取副作用；本审查也未对板端执行读取。

结论状态：

- **VERIFIED**：AMD 当前索引片段（版本标签 `All`）与官方 v1.10 PDF 均把 bit 21 列为保留位、reset value 1、写入要求为 0。
- **INFERRED**：当前 FPGA_RST_CTRL 索引片段归属于门户最新可验证的 v1.15 文档上下文；字段片段本身没有版本绑定。
- **VERIFIED**：两版 AFI RD/WR 控制详情包含 `FPGA_AXDSN_RST` 写 0 前置说明；同版 FPGA_RST_CTRL 字段表未列出该字段。
- **VERIFIED**：SLCR 锁定状态下读值有效、写入忽略；读取该 SLCR 地址不需要先解锁。
- **INFERRED**：保留位没有可依赖的字段语义，不能将其读值用作 reset deasserted 的安全准入判据。
- **UNKNOWN**：实际板端 bit 21 值、其硅实现别名、`FPGA_AXDSN_RST` 的确切位域/HP 对应，以及 AFI 前置说明与字段表冲突的原因。
- **UNKNOWN**：AMD 门户以外是否存在更新版本；v1.15 是本次检索时门户可验证的最新版本，不声称它是绝对最新版本。
- **CONFLICT**：AFI 页面要求先写 `FPGA_AXDSN_RST=0`，同版 reset 字段表却没有该字段并将 bit20–24 标为 reserved。

AMD v1.15 revision history 的 1.15 更新清单没有列出 FPGA_RST_CTRL 或 AFI reset 前置条件变更。它也没有提供从早期版本到当前版本逐位字段映射的完整差异记录。AMD Answer Record #56642 修正的是旧版文档中 bits 24、17、16 的描述，没有涉及 bit 21；它不能解决本项冲突。早期非官方镜像中出现的不同字段名不作为本裁决依据。

## 官方证据定位

完整历史资料已保存在本目录：

- [UG585 v1.10 官方 PDF（日文）](ug585-amd-official-v1.10-ja.pdf)，SHA-256：`CD50BFF23C5F538AB8BB1286153C2FA1617A0E6A53BA0B6A1E18E4A860360EAA`。从 AMD Docs 官方内容 API 下载；完整 PDF。
- [UG585 v1.10 文本提取](ug585-v1.10-extract.txt)，SHA-256：`4EAC64773080226F72E80DEBD141A25BAEC7A7FBEDBCEAAB8CBD9E2FE3293492`。用 `pdftotext -layout` 从上述完整 PDF 提取，用于定位；表格版面以 PDF 为准。

| 文档版本 / 位置 | 可核验内容 | 获取方式与边界 |
|---|---|---|
| v1.10，附录 B `AXI_HP Interface (AFI)`，p.745，`Register AFI_RDCHAN_CTRL Details` | HP1 地址 `0xF8009000`；访问前置文字引用 `FPGA_AXDSN_RST` 并要求写 0；bit0 `32BitEn`：1=32-bit、0=64-bit。 | AMD 官方 PDF 全文；本地文本提取行 43631–43687。 |
| v1.10，附录 B `AXI_HP Interface (AFI)`，p.748–749，`Register AFI_WRCHAN_CTRL Details` | HP1 地址 `0xF8009014`；相同写 0 前置；bit0 `32BitEn` 编码相同。 | AMD 官方 PDF 全文；本地文本提取行 43822–43909。 |
| v1.10，附录 B `System Level Control Registers (slcr)`，地址条目 p.1555、字段表 p.1556，`Register FPGA_RST_CTRL Details` | 地址 `0xF8000240`；bit20–24 被分别列为 reserved，reset value 1，要求始终写 0；bit21 没有 AXDS 字段名。 | AMD 官方 PDF 全文；bit 字段表见本地文本提取行 85037–85079，bit21 为行 85065。 |
| v1.10，附录 B `Register SLCR_LOCKSTA Details` | lock status=1 时 SLCR 写入忽略，而读值有效。 | AMD 官方 PDF 全文；本地文本提取行 82937–82956。 |
| 当前 AMD 索引片段，`Register FPGA_RST_CTRL Details` | bit21 显示 reserved / reset value 1 / write 0；字段表无 `FPGA_AXDS1_RST`。 | 检索结果标签为 `version: All`，不是版本固定的 v1.15摘录；将其归属到门户当前 v1.15 上下文属于推断。未取得 v1.15 完整 PDF。 |
| 在线 UG585 v1.15，`Register AFI_RDCHAN_CTRL Details`；当前索引 `Register AFI_WRCHAN_CTRL Details` | 两条目均列出 `FPGA_AXDSN_RST` 写 0 前置；RD/WR `32BitEn` 位宽编码。 | RD 页直接获取到 v1.15 元数据；WR 搜索片段标记 `version: All`，该项版本归属同样未固定。 |
| 当前 AMD 索引片段，`Register SLCR_LOCKSTA Details` | 锁定保护的读写语义与 v1.10 相同。 | AMD 检索结果标记 `version: All`；没有下载 v1.15 全文 PDF。 |
| 在线 UG585 v1.15，`Revision History` | 门户列出的版本为 1.15，release date 2026-02-06；1.15 更新清单未列出 FPGA_RST_CTRL。 | AMD 官方页面响应共4495行；页面检索完整，但本地只保存定位行和有限摘录。 |
| AMD Answer Record #56642 | 修正范围是 bits 24、17、16；未提 bit21。 | AMD 官方支持知识库页面的索引摘录；不能据此推断 bit21 的含义。 |

在线来源链接列于 [official-sources.json](official-sources.json)，包括每页 URL、版本、位置、检索方式和获取完整性；[amd-online-captures.json](amd-online-captures.json) 保存当前检索的来源标签、页面行号与受限摘录。该在线捕获保留可审计的关键元数据和摘录位置，没有复制整页技术文档。AMD 官方页面： [UG585 revision history](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Revision-History)、[FPGA_RST_CTRL fields](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-FPGA_RST_CTRL-Details)、[AFI RD fields](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-AFI_RDCHAN_CTRL-Details)、[AFI WR fields](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-AFI_WRCHAN_CTRL-Details)、[SLCR_LOCKSTA fields](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-SLCR_LOCKSTA-Details)、[AR #56642](https://adaptivesupport.amd.com/s/article/56642?language=en_US)。

本次没有新的板端证据。历史结果中的运行时 AFI 与 reset 实测值仍为空；静态 PS 初始化脚本、生成 HTML 或历史软件回执均不能替代当前会话实测。本报告不授权任何寄存器访问或板端操作。
