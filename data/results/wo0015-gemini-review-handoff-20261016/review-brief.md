# 请 Gemini 审核：WO-0015 当前进度与证据边界

日期：2026-10-16，Asia/Shanghai。本材料供用户转交，未自动向 Gemini 发送。

请独立审核以下事实、推断与下一步方案，重点指出证据链漏洞和最小补证路径。
**审阅不构成新的硬件授权，请勿建议直接试写寄存器、重载Overlay或重跑DMA。**

## 1. 目标与当前状态

项目为 PYNQ-Z2 / Zynq-7000 的 Binary8 单帧回传实验，WO-0015。静态设计为
50MHz、320×240，每帧S2MM回收76800B（19200个32-bit word）；三帧合计230400B。

**当前：STOPPED_PRECONDITION_UNVERIFIED。** 数据差异根因未证实，WO未通过最终
上板验收。用户已断开板卡；没有安排自动重连或上电。近期各轮均为离线研究，
没有新的MMIO/Overlay/复位/DMA。原开发目录与worktree保持未提交、未合并，
RTL、BIT、HWH和历史失败证据均保留。

历史授权boot_id：`822a9c29-37c0-438c-81c4-da998d57073b`。这不是当前会话已实测
仍相同的声明；断开也不必然等于断电。下次若boot变化，必须停止并另行重授权。

锁定硬件SHA-256：

```
wo15.bit a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420
wo15.hwh bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6
```

## 2. 历史实际板测推进到哪里

多轮早期尝试因boot绑定、目录权限、CMA来源兼容、原始字节采集、初始DMA Halted
及CmaFree算术差值等门控停止，历史结果分别保留。后来经用户逐次授权完成
Run-only初始化：两通道DMACR=0x00010003、DMASR=0，未重复加载Overlay。

最近一次实际DMA发生于`wo0015-runonly-resume-retry2-20261015`：

| 项目 | 历史保存结果 |
|---|---|
| Step2 | 单帧传输完成，但Golden比对失败，立即熔断 |
| MM2S / S2MM DMASR | 均0x00001002：Idle=1、Halted=0、错误位0 |
| S2MM长度 | 76800B |
| Checker | pixel_count=76800，SOF=1，EOL=240，packet_done=1，错误计数/错误码0 |
| Checker输出checksum | 571200，按Binary8前景值255推导2240个前景像素 |
| Packer | accepted=76800，sent_beats=19200，DONE=2，error=0 |
| Golden | 39520字节差异，首差偏移4 |
| Step3 | 未执行 |

Checker没有独立fg_count寄存器；2240是实测checksum的派生值，不是新实测计数器。
Packer没有独立输出EOF事件计数器，不能把输入TLAST计数当作输出EOF计数。

最关键取证缺口：**历史RX原始字节没有保存，不能补造actual字节或从已释放缓冲
区追回。** 当前只能证明DMA/流控完成及统计量，不能声称Binary8数据正确。

## 3. 39520差异的数学关系：吻合但不唯一

Golden单帧76800B，前景2240个，奇/偶32-bit word中的前景各1120个。

假设偶word全零、奇word残留A5：偶word差1120B，奇word差38400B，合计39520B，
首差恰为4。但离线已构造另一种非奇偶Poison模式，也产生相同两个摘要数。

因此“偶零/奇A5”仍是**假设**，不能判为实际RX，更不能单凭该数学关系断言
32/64-bit步进、Cache或Packer根因。

已审计历史脚本：RX全部预填A5，非偶零/奇A5初始化；有CPU→device与device→CPU
同步调用，缺方法时回退flush/invalidate。但历史没有记录实际采用的分支、
coherent元数据及同步前后内容，不能据静态调用排除Cache问题。

## 4. 已完成的离线取证增强

Golden失败时，在释放前独占落盘完整RX/Golden .bin、hash、前64B对比、逐字节
差异样例，以及奇偶word差异/全零/Poison统计。落盘或校验失败进入证据HOLD，
不先free丢失数据；该逻辑已离线测试，**没有部署或上板捕获新RX**。

历史全量离线结果459 passed + 5 subtests；其中新增诊断定向测试11项。旧WO-0014
40ms墙钟用例曾出现收集顺序相关抖动，失败日志保留；未放宽timeout或宣称该旧
抖动已修复。后续76项与本轮43项是各自定向验证，不能相加当成互不重叠全量。

## 5. 静态位宽与运行时AFI证据

锁定HWH/BD表明：DMA S2MM memory=32、stream=32；经hp1_fabric接PS S_AXI_HP1=32。
内部auto_pc为AXI4→AXI3协议转换，未发现该路径auto_us/auto_ds位宽转换。
MM2S的64-bit memory/HP0是另一条路径。阈值128为静态参数，与仿真一致，
未发现上板漏配threshold/ROI运行时寄存器的证据。

这只证明静态设计，**不证明AFI1运行时已配置32位**。

- AFI1_RDCHAN_CTRL：0xF8009000；WRCHAN_CTRL：0xF8009014。
- 32BitEn为bit0，1=32位、0=64位。
- **两个地址均尚未实测读取。** “运行时仍64位”是优先候选，未证实异常，
  更未证实它导致历史39520差异。

## 6. 为什么尚未读取AFI：文档与前置条件未协调

官方历史UG585 v1.10全文（附录B，reset字段p.1556）和当前AMD字段索引均把
FPGA_RST_CTRL[21]列为reserved、默认1、写入要求0。不能把reserved默认当成
read-as-zero，或把其读值当作HP1复位状态。

同一历史手册的AFI RD/WR页（p.745、748–749）仍要求访问前先处理相关
FPGA_RST_CTRL.FPGA_AXDSN_RST，但reset表没有这个有效命名字段，适用关系未解释。
当前门户可核验版本为UG585 v1.15（2026-02-06）；reset索引片段自身标version=All，
其归属v1.15仅为门户上下文推断，未声称拿到v1.15完整PDF。

SLCR_LOCKSTA说明锁定时读值仍有效，因此读取FPGA_RST_CTRL本身无需解锁。
但这不增加bit21的状态语义，也不能据其读零自动继续AFI。文档冲突没有通过猜测
或写0消除。当前硬件访问准入仍UNKNOWN。

## 7. 本轮新发现：本地启动镜像与FSBL

找到普通主机文件：
`C:/Users/POP/Desktop/FPGA赛/pynq_z2_v3.1.1/pynq_z2_v3.1.1.img`

大小8337309696B，完整SHA-256：
`4a6ac1cdbb413d768fcb059b7182e1f53aa7f728b898b7f5061cbb0b7cc93aab`。
没有伴随供应商校验、下载回执或刷卡记录。文件名不证明实际发行版或历史启动身份。

未挂载，采用审阅过的数据解析器只读MBR/FAT。启动文件BOOT.BIN大小1165852B，SHA：
`3bf9c24025a489926fd76872754977c5954e2ab25c91957373386e1f20915af6`。
启动头、分区头校验及边界通过，实际有3个分区：

| 头内名称 | 原始载荷字节 | 注释 |
|---|---:|---|
| zynq_fsbl.elf | 98320 | 提取的是原始装载数据，不是完整ELF |
| u-boot.elf | 1041824 | 嵌入U-Boot 2024.01版本字符串 |
| system.dtb | 19740 | 本地候选DTB载荷 |

原始FSBL载荷SHA：`16081243af5ed1d5b908b0c038c13c3e205c131b31d538690786a530dba82161`。
原始ELF、BIF、map、源码提交与构建参数仍UNKNOWN；不能从头内文件名重建源ELF身份。
BOOT头的Register Initialization Table没有有效地址值对。

本地镜像根分区UUID与历史内核日志同为：
`63b8b04d-af08-419a-a9dc-16f374e283dc`。
匹配本身VERIFIED；共同镜像/克隆谱系INFERRED；**历史实际启动绑定UNKNOWN**。
克隆会保留UUID，同一根分区也可配不同BOOT文件，不能以此认证完整镜像。

## 8. PS初始化调用链的新证据与缺口

既有WO-0015生成代码：ps7_init()不调用ps7_post_config()；独立post_config三分支
预计写FPGA_RST_CTRL=0并配置AFI1 RD/WR bit0=1。但生成表存在不证明曾执行。
silicon1.0/2.0分支把bit21定义为AXDS1 reset，3.0分支为reserved；这是已证实的
版本分支差异，不能直接当作同条件矛盾。实际silicon分支仍未知。

本机2026.1 SDK FSBL模板进一步显示：

- main调用ps7_init；加载后FsblHandoff中，post_config受BitstreamFlag、
  PS7_POST_CONFIG及NON_PS_INSTANTIATED_BITSTREAM条件控制。
- JTAG另有PCFG_DONE条件调用；不能套到SD路径。
- 候选BOOT没有PL bitstream分区。**若**采用该模板，SD路径可能不走上述post_config
  分支；候选二进制与此模板的源码身份未知，不能判实际已跳过。
- 候选FSBL原始数据中有3段形似默认post-config表的相同字节块，含reset写0，
  没有AFI1两绝对地址字面量。这不证明函数身份/可达/执行，也不排除间接寻址。
- 镜像BOOT.PY确有BaseOverlay("base.bit")调用；BOOT.SCR可按环境选择启动文件。
  它们是否实际执行、Linux/PYNQ内部具体配置仍UNKNOWN。
- 历史内核日志直接证明base/wo15的Linux FPGA-manager下载事件；没有FSBL调用
  trace或AFI寄存器值，不能替代PS初始化执行证明。

## 9. 当前建议的最小下一步，尚未获板端执行批准

只申请一次B1：6个proc文件的OS来源取证：

```
/proc/sys/kernel/random/boot_id
/proc/sys/kernel/osrelease
/proc/version
/proc/cmdline
/proc/uptime
/proc/self/mountinfo
```

固定源码/hash、Python -I -S -B、独占原始stdout/stderr落盘、fresh nonce、SSH主机
密钥核验、失败即停，无凭据模式回退或自动重试。B1即使完成也仍STOPPED，不自动
读BOOT或AFI。若boot变化，仅保留首项读数停止，不自动更新授权基线。

B2须另批单个/boot/BOOT.BIN，要求已存在固定ro挂载，且钉住fd前后验证readonly；
不remount、不mount、不修复。SSH/sudo可能写系统认证日志，不能承诺整张SD绝对
零写入。若要求后者，则继续离线。当前板卡保持断开，无自动重连。

## 10. 请重点审核的问题

1. 数据差异及奇偶Poison假设的证据分级是否正确？还有什么可离线排除或补证？
2. AFI前置字段与reserved定义有无可核验的AMD官方版本/勘误，足以解决适用性？
   请给准确来源，不以旧版镜像/论坛概括代替权威说明。
3. bit21读取可否作为有效reset证据？若不能，最低风险替代证据是什么？
4. BOOT头、原始FSBL、参考模板、共同UUID及历史日志之间是否存在过度推断？
5. 下一轮6文件B1的价值与白名单是否足够小？是否应先取得既有FSBL ELF/BIF/串口
   启动记录，避免无效板端采集？
6. 只读方案的路径钉住、atime、挂载竞争、sudo/PAM副作用、nonce/传输来源和
   失败即停是否还有遗漏？不要把文件只读等同于整机零写入。

请按“认可的直接事实 / 过度推断或冲突 / 缺失证据 / 建议的最小下一步”输出意见。
不要把审阅结论当成板端执行授权，也不要把当前根因候选写成已证实。

## 11. 本地证据目录与安全附件建议

共同根目录：
`C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/data/results/`

- `wo0015-runonly-resume-retry2-20261015/`：最近实际DMA失败证据。
- `wo0015-step2-mismatch-audit-20261015/root-cause-report.md`：寄存器、Cache、
  HWH/BD、数学反例和RX落盘增强。
- `wo0015-afi1-preconditions-20261015/verification-report.md`：UG585版本/字段冲突、
  生成PS路径和准入审计。
- `wo0015-boot-provenance-20261016/verification-report.md`：本轮启动来源完整报告。
- 同目录`command-audit-and-whitelist.md`、`next-step-approval-request.md`、
  `BOOT.BIN-analysis.json`、`init-chain/evidence.json`、
  `init-chain/rootfs-uuid-comparison.json`、`summary.json`：定向审阅依据。

Gemini不能直接读取本机路径，可先转交本摘要及上述普通报告/定向JSON。
**不要整包上传原镜像或restricted-raw目录**：REVISION原始材料含疑似构建凭据，
已受限保存；普通报告使用脱敏副本，凭据没有被使用。需要源码审阅时再附明确
的已审阅源码，勿把BOOT.PY/BOOT.SCR或FSBL作为可执行操作提交。

最新本轮43项离线测试通过（解析11、OS提案27、命令生成5）；前轮76项定向通过；
历史全量459项+5子测试属于不同阶段，不累计。最近完整性复核320个历史索引文件
一致，锁定BIT/HWH未变。当前实际启动身份与数据差异根因均未证实。
