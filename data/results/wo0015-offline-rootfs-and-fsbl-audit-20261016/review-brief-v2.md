# 请 Gemini 复审：WO-0015 离线补证 v2

日期：2026-10-16，Asia/Shanghai。供用户转交，未向 Gemini 或其他服务发送本材料。当前状态 **`STOPPED_PRECONDITION_UNVERIFIED`**；B1/B2 为 **`NOT_APPROVED_DO_NOT_EXECUTE`**。本次仅增量收口 POSIX 边界；下列 A–C 取证沿用前轮成果，未重新全量读取/哈希大镜像，也未重新解码真实 FSBL 21 张表。板卡保持用户所报断开状态；本轮没有尝试连接、SSH、MMIO、复位、Overlay 或 DMA，没有挂载镜像或执行 ARM 载荷。不读取/复制 restricted-raw，不读取 REVISION；材料不包含凭据。

## 请优先检验的结论

1. **本地候选 PYNQ 不会在所识别的 PS7 Python 分支中按 HWH 写 AFI1 宽度。** `EmbeddedDevice.download` 确实进入 Linux FPGA manager sysfs，随后调用 `set_axi_port_width`；但该函数只有 `family_ps == "zynq_ultra_ps_e"` 的写寄存器分支，Zynq-7000 不进入。此结论不能扩展为内核没有复位/AFI 副作用：镜像 kernel 配置与 builtin 索引可取证，但缺少驱动 C 实现及运行绑定，底层行为仍 UNKNOWN。
2. **base 的 HP1 为禁用配置，HWH 宽度参数为 64。** 配套 base.tcl 的 HP1 enable=0，不能将 64-bit 参数解释为 base 曾使用 HP1 或历史运行值。base.bit SHA-256 `3239e9cc37ee4993c25f02ae6c97da0b3372f0e722e267d10073e20adc5d6717`；base.hwh `222898ef2c0ccc4a0c47608e99d8dc7c9c6cdfed5130964884ff35005827ef3a`。
3. **FSBL 正常无 PL 分区路径跳过 handoff-time post-config，是有条件静态推导。** 原始 98320B 载荷按 base=0 反汇编，入口清零覆盖 `0x193ED`；分区循环 `0xAC0` 再清零，只有 `0x20` 分区属性位路径在 `0xBAC` 置一；`0x12E4` 的非零分支才到 `0x1350` 调用 post-config。候选三个分区属性 `0x10/0x10/0x12` 均无该位。符号名与属性语义有 SDK 模板辅助，未取得原始 ELF/source identity；历史实际启动和 JTAG/PCFG_DONE 分支不能据此确定。
4. **所有被选择器引用的候选字节码表已完整解码。** 21 表、675 操作，其中 654 条候选写；AFI1 `0xF8009000`／`0xF8009014` 表目标参数各零命中，FPGA_RST_CTRL `0xF8000240` 三命中，分别位于三个替代表，每条 mask=FFFFFFFF、value=0。解释器的寄存器间接 STR 已绑定到表参数，不是只搜 literal。其他未穷尽控制流/U-Boot 的任意间接写仍 UNKNOWN。
5. **Golden 首 word 确为零，摘要仍不唯一。** Golden 偏移 0..3=`00 00 00 00`，74560 个 00、2240 个 FF，奇偶 word 的 FF 各 1120。偶零/奇 A5 得 39520 差异、首差4；连续偏移4..39523 XOR1也得到相同摘要。两者都只是假设反例，历史 RX 未保存。
6. **缓存调用的候选静态链已闭环，但未排除缓存问题。** 历史适配器会优先命中候选 PynqBuffer 的 sync_to/from_device，内部 flush/invalidate，最终 XRT bo.sync TO/FROM；并非缺方法 fallback，也非 sync_for_cpu/device。allocation 的 cacheable=0/bo.flags.normal，与对象 coherent=False/cacheable属性True不同。没有历史包哈希、对象元数据或实际维护效果证据。

## 候选镜像与历史环境边界

镜像 8337309696B 的完整 SHA-256 重新核实为 `4a6ac1cdbb413d768fcb059b7182e1f53aa7f728b898b7f5061cbb0b7cc93aab`；ext4 UUID `63b8b04d-af08-419a-a9dc-16f374e283dc`，clean=1、needs_recovery=0。不挂载、不回放 journal。镜像 PYNQ METADATA=3.1.1，173 条 Python RECORD 哈希匹配其本地安装，不能认证 upstream 或排除 vendor patch。

候选 BOOT.BIN SHA-256 `3bf9c24025a489926fd76872754977c5954e2ab25c91957373386e1f20915af6`，FSBL raw SHA-256 `16081243af5ed1d5b908b0c038c13c3e205c131b31d538690786a530dba82161`。名称 `zynq_fsbl.elf` 不能改变 raw 载荷无 ELF magic 的事实。

DTB 为 98 节点、478 属性，fpga-region→devcfg phandle 已解析；没有 afi 命名命中。DTS 是自编 FDT 解析器的可读渲染，dtc 不可用，未独立往返验证。U-Boot 仅导出 16 条白名单环境字符串；不证明所选环境或不存在其他寄存器初始化。指定三个工程 ZIP 中央目录共 482 项无 FSBL/BIF/MAP/串口日志候选；不读取 ZIP 成员内容，结论仅限该范围。

历史 Linux 日志加载过 base.bit/wo15.bit、最近失败为 Run-only 等事实仍沿用原始历史证据，未覆盖或升级为当前状态。镜像根分区与历史包/启动文件是否等同，仍 INFERRED/UNKNOWN。

## B1/B2 预案的安全门

- B1 共享核心首先检查 euid；root 在任何文件打开前拒绝。首个文件仍为 boot_id，与历史授权基线 `822a9c29-37c0-438c-81c4-da998d57073b` 比对，不一致直接失败并使冻结 wrapper 非零退出，其他五个 proc 不读。全部六个文件与首尾 boot_id 均统一 `O_RDONLY|O_NOFOLLOW` helper，`MAX_PROC_READ_BYTES=65536`，超限失败。
- 所有文件普通用户访问；冻结 wrapper 拒绝 euid=0，无 sudo，EACCES 停止。
- B2 先 `os.open("/boot", O_RDONLY|O_DIRECTORY|O_NOFOLLOW)`，再 `os.open("BOOT.BIN", O_RDONLY|O_NOFOLLOW|O_NONBLOCK, dir_fd=dir_fd)`；内容前完成 ST_RDONLY、普通文件、`0<size<=MAX_BOOT_BIN_BYTES`（32MiB）、dir/file同st_dev四项门控。任一前置失败均零内容读取并关闭双fd。`os.stat("/", follow_symlinks=False)` 只取元数据，将 `boot_dev_differs_from_root` 写入捕获结果及冻结 JSON；false不排除同设备bind mount。内容后的 ST_RDONLY、dev/ino/size/mtime_ns、完整长度与boot_id复核继续保留，任何失败无重试、提权或remount。
- 强制 -T、ControlMaster=no、ClearAllForwardings=yes、StrictHostKeyChecking=yes、固定 known_hosts；另明确 ControlPath=none、ForwardAgent=no、ForwardX11=no。固定候选解释器 `/usr/bin/python3 -I -S -B`。host-pin、known_hosts 存在性及解释器目标身份尚未核验，仍为阻塞条件，无 SSH 执行器或目标连接命令。
- ST_RDONLY 是打开目标所在挂载点在检查时的 VFS 只读断言；SSH 登录仍可能写系统日志。整张 SD 卡绝对零写入要求意味着继续离线。

没有将 FPGA_RST_CTRL[21] 当作 HP1/AFI1 复位状态：该位 reserved，默认1、写要求0，其读值不能作为准入依据。

## 测试与历史保护

本轮最终新测试：50 个方法通过 + 20 个子测试通过；JUnit 70 条记录包含子测试，不与历史459/76/43项相加。ext4 4项包含 ee_len=0x8000 边界，FSBL/FDT12项，Golden7项，预案27方法+20子测试。红绿与中间失败日志均保留。

前轮曾有一次汇总测试误收集提取的镜像测试模块，其顶层导入在主机缺少 PYNQ 时产生26个收集错误；不能声称整个会话完全未执行镜像来源 Python 导入语句。没有执行 PYNQ 驱动/C扩展、ARM载荷或板卡操作。已改为显式四文件测试，保留 `manager/collection-scope-error.*`。

320 个历史索引文件、389 个非敏感历史文件及锁定 wo15.bit/hwh 前后复核一致；restricted-raw 不遍历不读取。原开发目录 Git 状态与既有 diff、两处 HEAD 及 worktree 既有 tracked/staged diff 不变；新增未提交、未合并。BIT/HWH 锁定值见完整报告与 summary。

## 请求复审的问题与最小下一步

请核查：PS7 Python 配宽分支与 kernel 副作用是否清楚分离；FSBL 无 PL 推导的前提和 JTAG 例外是否充分；表解释器间接寻址是否还缺关键证据；缓存属性语义是否准确；B1/B2 fail-fast 和读取前只读门是否仍有可修补的离线漏洞。

下一步仍是用户/Gemini 审阅本包。未来若单独授权，最小候选仅 B1；结果先独立审阅，不自动转 B2，更不转 MMIO/复位/Overlay/DMA。任何未知项均不意味着恢复准入。本材料没有申请立即执行板端操作。

入口：[verification-report.md](verification-report.md)、[A JSON](pynq-afi-and-base-hwh-audit.json)、[B JSON](fsbl-disassembly-and-dtb-audit.json)、[C JSON](golden-and-cache-audit.json)、[白名单 v2](command-audit-and-whitelist-v2.md)、[审批草案 v2](next-step-approval-request-v2.md)。

## POSIX 封板增量结论

本次共享核心、冻结文本与白名单已同步完成三项POSIX加固：O_NONBLOCK及普通文件/正32MiB上限门，dir/file同st_dev及boot_dev_differs_from_root元数据，B1最前置非root门与统一64KiB受限helper。对应新边界mock全部通过；当前四文件汇总50+20子测试，不与前轮44+11相加。实镜像21表测试改核对封存JSON；本次没有读取/哈希8.33GB镜像或重解21张表，没有目录式测试收集。

状态保持STOPPED_PRECONDITION_UNVERIFIED和NOT_APPROVED_DO_NOT_EXECUTE；无板端接触、无SSH、无B1/B2执行。请后续由用户决定继续离线，或单独审批B1；本轮不自动推进。
