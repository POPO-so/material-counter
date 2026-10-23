# WO-0015 纯离线补证与 B1/B2 预案复核

日期：2026-10-16，Asia/Shanghai。当前状态保持 **`STOPPED_PRECONDITION_UNVERIFIED`**。本次增量工作为 B1/B2 POSIX 边界收口；A–C 的镜像与 FSBL 结论沿用前轮封存成果，没有重新全量读取/哈希 8.33GB 镜像，也没有重新解码真实 FSBL 的 21 张表。

本轮没有连接板卡、执行 SSH、读取或写入板端寄存器、加载 Overlay、运行 DMA、挂载镜像或执行 ARM 目标二进制。没有读取、复制或遍历 `restricted-raw`，没有读取 REVISION 内容，没有提交或合并。新增交付集中在本结果目录。以下所有软件栈事实限定为本地候选镜像，不代表已确认历史板端运行栈。

## 认可的直接事实（VERIFIED）

### A．镜像、根分区、PYNQ 与 base Overlay

前轮完整读取本地镜像所得 SHA-256 记录为：8337309696B，`4a6ac1cdbb413d768fcb059b7182e1f53aa7f728b898b7f5061cbb0b7cc93aab`，与锁定值一致。本次仅复用该记录。MBR 第 2 分区偏移 137363456，ext4 UUID 为 `63b8b04d-af08-419a-a9dc-16f374e283dc`，块大小 4096。superblock state=1，needs_recovery=0；前轮解析的是未挂载、未回放 journal 的磁盘静态视图。

镜像内 METADATA 声明 PYNQ 3.1.1。173 条带哈希的 PYNQ Python 源文件 RECORD 记录全部匹配本次提取字节；这只核对镜像内安装记录，不认证 upstream 软件包或排除打包前的修改。提取清单逐项记录镜像内路径、字节数、SHA-256；未执行所提取的 PYNQ 驱动或 C 扩展。

下载链路可由本地源码追踪到 `Overlay.download` → `Bitstream.download` → `EmbeddedDevice.download`：完整重配置分支调用 shutdown/gen_cache，向 `/sys/class/fpga_manager/fpga0/flags` 与 `firmware` 写入参数，然后调用 `set_axi_port_width`、XRT 下载及软件元数据 post_download。**这些是源码中的写操作，不是本轮执行的操作。**

关键区别是：此镜像的 `EmbeddedDevice.set_axi_port_width` 仅在 `parser.family_ps == "zynq_ultra_ps_e"` 分支进行宽度寄存器写入。Zynq-7000 的 `processing_system7` 不进入该分支。HWH 参数的存在，不能说明此 Python 路径把 `PCW_S_AXI_HP1_DATA_WIDTH` 写入 PS7 AFI1。源码字面量检索与该受限分支中未发现对 `0xF8009000`／`0xF8009014` 的直接写入。

BOOT.PY 中的 `BaseOverlay("base.bit")` 可静态定位到 `/home/xilinx/pynq/overlays/base/`；调用存在不证明历史执行过该脚本。

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| base.bit | 4045672 | `3239e9cc37ee4993c25f02ae6c97da0b3372f0e722e267d10073e20adc5d6717` |
| base.hwh | 4529854 | `222898ef2c0ccc4a0c47608e99d8dc7c9c6cdfed5130964884ff35005827ef3a` |

base.hwh 中 HP0–HP3 的宽度参数均为 64；配套 base.tcl 明确启用 HP0、HP2，禁用 HP1、HP3。因此 HP1 的 64-bit 参数是候选静态元数据，不能写成 base 实际使用过 HP1，更不能写成 WO-0015 历史 AFI1 实测为 64-bit。

本地 kernel 配置及 modules.builtin 显示 Zynq FPGA manager、AFI、bridge/region 等支持已构建；对应驱动 C 实现不在可读源码树中。`Device.reset` 重置软件字典，也不能说明 Linux FPGA manager 不产生 PS 复位副作用。底层效果的未知边界见下。

详见 [结构化 A 审计](pynq-afi-and-base-hwh-audit.json)、[rootfs 证据](rootfs/findings.md) 和 [提取清单](rootfs/source-manifest.json)。

### B．FSBL 机器码、字节码表与设备树

候选 BOOT.BIN 的三个载荷与历史哈希相符：FSBL 98320B，SHA-256 `16081243af5ed1d5b908b0c038c13c3e205c131b31d538690786a530dba82161`；U-Boot 1041824B；DTB 19740B。FSBL 的装载与入口地址均为 0。`zynq_fsbl.elf` 是 BOOT 头中的名称，实际提取载荷无 ELF magic，不能当作原始带符号 ELF 或源构建身份。

以 base=0 完成 ARM 原始反汇编，并用机器码解释器的 opcode/argc 逻辑解码 21 个候选表、675 条操作，包括三个 post-config 表。完整地址、mask、value、command offset 均在 [decoded-tables.json](fsbl/decoded-tables.json)；这些是备选表集合，不声称全部执行。654 条候选 WRITE／WRITE_ZERO／MASKWRITE 中，AFI1 两地址的目标参数命中均为 0，`0xF8000240` 命中 3 次，分别属于三个互为替代的 post-config 候选。

三个 post-config 表各为以下四条操作加 EXIT，语义相同：

| 操作 | 地址 | mask | value |
|---|---|---|---|
| WRITE | `0xF8000008` | — | `0x0000DF0D` |
| MASKWRITE | `0xF8000900` | `0x0000000F` | `0x0000000F` |
| MASKWRITE | `0xF8000240` | `0xFFFFFFFF` | `0` |
| WRITE | `0xF8000004` | — | `0x0000767B` |

表解释器 `0x2b04` 的寄存器间接访问并未忽略：其 WRITE/MASKWRITE 有效地址绑定到表内 address 参数；掩码写机器语义为 `(old & ~mask) | (value & mask)`。delay handler 另外使用固定 timer 寄存器，亦记录在细分报告。对未识别的其他控制流、任意别名指针及 U-Boot，不能把表内零命中扩大为不存在任何间接写。

直接机器事实包括：启动 BSS 清零范围覆盖 `0x193ED`；分区循环入口 `0xAC0` 再将此 byte 写零；`0xB80` 测试 partition bit `0x20`，只有该条件下的路径在 `0xBAC` 写一；`0x12DC` 读取 byte，`0x12E4` 仅非零才跳到 `0x1350` 调用 `0x2C48` post-config 选择器。候选镜像三个分区属性为 `0x10/0x10/0x12`，均无 bit `0x20`。据此得出的正常无 PL 路径结果属于下节的静态推导，而非运行测量。

DTB 完整解析为 98 节点、478 属性；FDT total_size=19738，分区末尾另有 2B padding。[system.dts](fsbl/system.dts) 是可读结构渲染；本机无 dtc，未做独立编译往返。`/fpga-region` 的 fpga-mgr phandle 指向 `/axi/devcfg@f8007000`，另有 SLCR syscon 节点。未发现 afi／xlnx,afi-fpga 命名命中；这不证明无软件寄存器写入。

U-Boot 仅提取 16 条启动/FPGA/AFI 相关环境白名单项，包括 `bootcmd=run distro_bootcmd`；没有把默认字符串当作历史所选环境。原始 FSBL ELF、BIF、MAP、串口日志的限定搜索与归档检索范围见 [B 详细报告](fsbl/findings.md)。未发现已能与此载荷身份绑定的原始 FSBL 构建材料；这不是全机不存在的声明。

### C．Golden 首 word 与缓存同步

Golden 76800B，偏移 0..3 为 `00 00 00 00`，前 64B 均零。整帧含 74560 个零、2240 个 FF，奇偶 word 各有 1120 个 FF 字节。每侧 9600 个 word，字值分布均为 9250×`00000000`、70×`0000FFFF`、70×`FFFF0000`、210×`FFFFFFFF`。全部 word 的偏移与字节值见 [CSV](manager/golden-all-19200-words.csv)。

“偶零／奇 A5”合成缓冲产生 1120+38400=39520 个差异、首差 4。复制 Golden 后仅在连续偏移 4..39523 XOR 1，亦产生相同摘要且内容不同。两者都不是历史 RX。全 A5 未修改缓冲则会首差 0、差异 76800，不能用预填操作本身解释首差 4。

在历史保存的执行器与候选 EmbeddedDevice/PynqBuffer 组合下，`allocate` 未传 cacheable 参数，走 `_xrt_allocate(cacheable=0)`、`pyxrt.bo.flags.normal`；对象却明确 `coherent=False`，其 `cacheable` 属性为 `not coherent`，即 True。两个语义必须分开。

候选类存在可调用的 `sync_to_device`／`sync_from_device`，因此适配器选 primary 方法，内部再调用 flush／invalidate，最终调用 XRT `bo.sync` 的 TO_DEVICE／FROM_DEVICE 方向；不走“缺方法”fallback，也不选 sync_for_cpu／sync_for_device。libcma.so 的存在不能说明该分配链使用它。逐行源码与哈希见 [C 审计 JSON](golden-and-cache-audit.json) 和 [详细说明](manager/task-c-findings.md)。

### D．仅离线的 B1/B2 加固

两份 v2 文档与冻结文本均为 **`NOT_APPROVED_DO_NOT_EXECUTE`**。共享模板无默认 OS adapter，mock 直接测试同一 capture 核心；冻结文本 wrapper 保留未批准拒绝门、非零退出和 root 用户拒绝门，仅做 AST/哈希审查，未执行。

POSIX 收口后的 B1 共享核心先执行 `os.geteuid() != 0`，root 在首个 boot_id 之前即零文件打开拒绝。全部六个 `/proc` 路径与首尾 boot_id 复核统一经 `O_RDONLY | O_NOFOLLOW` 受限 helper，`MAX_PROC_READ_BYTES=65536`；超限输入失败，不允许自定义 reader 绕过。首个文件仍是 boot_id，不符历史授权值立即失败，冻结 wrapper 非零退出，不读其他五个文件。

B2 钉住 `/boot` 的 dir_fd 后，以 `O_RDONLY | O_NOFOLLOW | O_NONBLOCK` openat `BOOT.BIN`，避免 FIFO 在 open 阶段挂起。任何内容读取前，必须完成 ST_RDONLY、`S_ISREG`、`0 < size <= MAX_BOOT_BIN_BYTES`（32 MiB）、目录与文件 `st_dev` 一致四道门控；非普通文件、空文件、超限文件和跨设备替换均零内容读取，关闭双 fd 后失败退出。元数据查询 `os.stat("/", follow_symlinks=False)` 记录 `boot_dev_differs_from_root` 到捕获结果及冻结 B2 JSON；它标识设备是否不同，不把 false 当作不存在同设备 bind mount 的证明。

内容读取后的 ST_RDONLY、fstat 四字段一致、完整长度、尾项 boot_id 检查继续保留。普通用户只读，无 sudo，EACCES 停止；所有失败无重试、提权或挂载回退。

SSH 与 Python 固定选项、尚未核验的 host-pin/known_hosts/解释器前置条件、目标挂载点只读与整卡零写入的区别，均载于 [白名单 v2](command-audit-and-whitelist-v2.md) 和 [审批草案 v2](next-step-approval-request-v2.md)。没有创建或修改全局 SSH 文件。

## 推断与候选（INFERRED & HYPOTHESIS）

1. **正常候选 BOOT 分区路径跳过 handoff-time post-config：INFERRED。** 给定已识别启动清零、分区遍历及静态属性，无 PL bit 时 flag 保持零，handoff 条件分支跳过 post-config。符号名与 bit 含义由已安装 SDK 模板辅助关联，非原始 ELF 符号。另有 JTAG/PCFG_DONE 独立调用路径，不能笼统写成所有启动方式均不调用。
2. **候选镜像与历史板端软件相同：尚未绑定。** UUID 一致、历史加载过 base.bit/wo15.bit 的日志及软件版本线索可支持关联，但不唯一标识根分区内容、启动介质或本次实际执行的软件包。
3. **偶零／奇 A5、运行时 AFI1 仍为 64-bit、由其造成差异：HYPOTHESIS。** 静态设计宽度、候选 FSBL 路径及 PYNQ 的 PS7 宽度配置缺口，使检查运行时前置条件有价值，但没有替代寄存器读值或历史 RX 证据。
4. **本地同步链与历史实际命中链相同：有条件推断。** 静态组合已闭环，历史 package/class/hash/coherent 元数据及维护效果未记录，不能升级为板端 VERIFIED。

## 仍未知项（UNKNOWN）

- 本地镜像、候选 BOOT.BIN 与历史运行环境的唯一绑定；实际启动方式与所选 U-Boot 环境。
- FPGA manager 内核路径在完整加载前后对 SLCR/AFI/复位的实际副作用。Python 无直接字面量不等于内核无间接操作。U-Boot 其他代码路径的完整 MMIO 数据流亦未被穷尽证明。
- 历史 RX 原始字节、最后写入者、实际 AFI1_RD/WR 位宽与相关前置状态，以及真正根因。
- 未来 `/boot` 是否普通用户可读且已只读挂载、固定 known_hosts 是否存在且绑定可信 host key、固定解释器是否可用。任何一项失败均应停止。
- `FPGA_RST_CTRL[21]` 是 reserved（默认 1、写要求 0），其读值 **不能作为 HP1/AFI1 复位状态依据**。本轮不据此导出任何恢复或寄存器读取许可。

## 修订后的最小下一步

当前仍保持纯离线，先由用户/Gemini 审阅本轮报告与 [review-brief-v2.md](review-brief-v2.md)。本交付不申请或暗含板端执行许可。

若将来另有明确授权，最小候选仍仅为 B1 六个 `/proc` 文件，先解决固定 host pin、普通用户与解释器前置条件，再使用冻结、重新审核的 payload；boot_id 不符立即进程内失败退出。B1 结果必须先单独审阅，不能自动转 B2。B2 还需单独授权并满足已存在的 ST_RDONLY 只读挂载条件；不能为了采集去改挂载状态。若要求整张 SD 卡绝对零写入，则继续离线，不使用 SSH。

两阶段均不包含 MMIO、AFI 采样、寄存器试写、复位、Overlay 或 DMA。未知项与审批草案不构成重试/恢复准入。

## 测试、完整性与执行偏差

本轮显式列出四个新增测试文件的最终结果为 **50 passed + 20 subtests passed**，无失败、错误或跳过；JUnit 共 70 条记录包含子测试，不能称为 70 个独立测试方法。按域为 Golden 7、ext4 4、FSBL/FDT 12、预案 27 方法+20 子测试。历史 459／76／43 项未重跑、未相加。

红绿与中间失败日志保留。前轮汇总测试曾误用整个结果目录，收集了提取的镜像测试模块；它们在主机缺少 PYNQ 时报告 26 个收集错误，未发生板卡连接。该次会执行所收集测试模块的顶层导入语句，不能声称前轮整个会话“绝无镜像来源 Python 语句执行”。没有执行 ARM 载荷、PYNQ 驱动或 C 扩展。失败日志保留在 `manager/collection-scope-error.*`；最终命令仅显式运行四个本轮测试文件。另有 Worker 初次路径解释偏差，自己的新增文件已迁入本结果目录；最终检查仓库根无残留。

完整性终检由独立 `manager/integrity.py` 执行，避开旧终结器中读取 restricted-raw 的逻辑：320 个历史索引文件及其 manifest 全部重新匹配，另外比较 389 个非敏感历史文件的前后 SHA-256；restricted-raw 明确不遍历、不读取、不复制。锁定 `wo15.bit` SHA-256 `a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420`，`wo15.hwh` SHA-256 `bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6` 均不变。原开发目录 Git 状态、HEAD、tracked/staged diff 与初始记录一致；worktree HEAD 与既有 tracked/staged diff 不变，新增交付未提交、未合并。终检记录见 `manager/integrity-after.json`。

新交付的 SHA-256 索引、独立审查和最终汇总分别见 `evidence-sha256.json`、`reviewer-findings.md`、`summary.json`。索引排除自身及 Python 缓存；所有实际新证据均可据清单复核。

## POSIX 增量封板记录

本次三项边界已在共享核心、冻结文本、mock 回归及两份 v2 文档中同步闭环。核心与冻结脚本均继续 NOT_APPROVED_DO_NOT_EXECUTE；所有测试只注入 fake OS，没有执行冻结入口或真实捕获后端。预案定向结果为27方法+20子测试；显式四文件汇总为50方法+20子测试，不与前轮44+11或历史459/76/43相加。

新增回归覆盖非普通文件FIFO/字符设备/块设备/目录/socket、size为零/超过32MiB、dir/file跨设备、B1 root零文件打开、proc超过64KiB以及root/boot设备相同和不同的元数据。全部B2读取前失败均校验零内容读取与双fd关闭。以已有decoded-tables.json检查替代两项实镜像表重解，仍保留合成decoder和小DTB测试。本次没有重哈希大镜像或重解21张真实FSBL表，也没有发生目录式pytest收集。

前轮汇总、索引及审查记录保留于manager/posix-closeout/previous-*；POSIX红绿日志位于proposals/logs/posix-*。当前实现/模板SHA与完整交付清单由最终索引重新封存。继续等待用户决定保持离线，或另行审阅并单独审批B1；本次不请求也不推定板端授权。
