# WO-0015 PS7 初始化与 AFI1 准入离线审计

日期：2026-10-15。范围：只读检查已保存的 Vivado 导出、上板脚本和报告；未启动 Vivado、未连接板卡、未读取/写入寄存器、未修改生产或历史文件。新建本报告只位于本目录。

证据标签：**VERIFIED** 表示保存源文件/历史记录直接包含该事实；**INFERRED** 表示仅在相应函数确实被调用时预计发生；**UNKNOWN** 表示现有存档不能证明；**CONFLICT** 表示生成资料的定义互相不一致。

## 发现

### 生成设计及版本

**VERIFIED —** 保存的实现/综合 `runme.log` 显示 Vivado v2026.1（例如 `data/results/wo0015-hw-50mhz-20261015/project/wo15_50m.runs/impl_1/runme.log:31,39,44,60`）。生成 PS7 文件位于 `.../wo15_50m.gen/sources_1/bd/wo15/ip/wo15_ps7_0/`；SHA-256：`ps7_init.c` `7dc2df18d874d2fa56fa4c4ca28a85730ffbbd54b567e56fd2a741ad7fb9eab8`，`ps7_init.tcl` `ac0bed12175d1767213dcbb99e2f37c774cebc409659763b40f9e7d748aceb8a5`，`ps7_init.html` `bf47ba7e5734e8ba114bbdc2a6ced9df094ddffc3f000370d56f944b5b44695e`。这是本地生成文件身份，不是启动镜像身份。

**VERIFIED —** 静态 S2MM 和 PS 配置彼此匹配：DMA `C_M_AXI_S2MM_DATA_WIDTH=32`、`C_S_AXIS_S2MM_TDATA_WIDTH=32`（`export/wo15.hwh:998-1012`）；PS `S_AXI_HP1` 为 32-bit、50 MHz，HP0 为 64-bit（`export/wo15.hwh:4110-4115`）。BD Tcl 将 S2MM 接到 `hp1_fabric/S00_AXI`，再接 `ps7/S_AXI_HP1`（`scripts/wo0015/create_s2mm_bd.tcl:191-194`）；静态 readback 指明 `auto_pc` 的 AXI4→AXI3 协议转换仍为 32-bit（同 Tcl:245-253；配置约束见 284-289）。这证明保存的静态设计，不证明启动后 PS 寄存器的实时值。

### `ps7_init` 与 `ps7_post_config` 是不同调用

**VERIFIED —** 生成 C 的 `ps7_post_config()` 在 `ps7_init.c:6944-6960`，按 PS 硅版本选择 `_1_0/_2_0/_3_0` 数据表；生成 Tcl 有独立 `proc ps7_post_config`，在 `ps7_init.tcl:529-545` 做同类版本分派。它们不是 `ps7_init()` 的别名。

**VERIFIED —** `ps7_init()` 的 C 主体在 `ps7_init.c:6983-7038`，执行 MIO、PLL、Clock、DDR、Peripherals 初始化后返回；其中没有调用 `ps7_post_config()`。Tcl `proc ps7_init` 在 `ps7_init.tcl:562-589` 同样仅调用上述五组初始化过程，不调用 `ps7_post_config`。因此，仅找到/调用 `ps7_init()` 不能作为 post-config 已执行的证据。

**VERIFIED —** 头文件 `ps7_init.h:68-69,103-106` 定义 `PS7_POST_CONFIG`，注释说这是供 FSBL 检查是否存在 `ps7_post_config()` 的标志，并分别声明两个函数。此处只说明生成器期望 FSBL 能识别该入口；它不是调用点。

**INFERRED —** 若启动链的 FSBL 或其他代码显式调用 `ps7_post_config()`，被选中的生成表会按所记录的 silicon revision 写入相应 post-config 内容。生成表本身不执行。

### bit21、AXDS1 与 AFI1 的生成行为

**VERIFIED —** `FPGA_RST_CTRL` 地址为 `0xF8000240`；bit 21 对应掩码 **`0x00200000`**。C 的 `_3_0` 表在 `ps7_init.c:2080-2082` 将它注释为 `reserved_FPGA_AXDS1_RST`，同表在 `:2132` 生成 `EMIT_MASKWRITE(0xF8000240, 0xFFFFFFFF, 0)`。同一 `_3_0` post-config 表在 `:2142` 将 AFI1 RD `0xF8009000` bit0 置 1，`:2147` 将 AFI1 WR `0xF8009014` bit0 置 1。Tcl 对应 `_3_0` 命令为 `ps7_init.tcl:150-156`。

**VERIFIED — 生成定义按 silicon revision 区分。** `ps7_post_config_3_0` 的 HTML 表 `ps7_init.html:28269-28284` 把 bit21/mask `200000`（十六进制显示，即 `0x00200000`）列为 Reserved/Do not modify；`_2_0` (`:56357` 下的 `:56919-56934`) 和 `_1_0` (`:84368` 下的 `:84930-84945`) 则描述为 AXDS1 AXI soft reset。C 表与之逐版本对应：`_3_0` 在 `ps7_init.c:2080-2082` 写作 `reserved_FPGA_AXDS1_RST`，`_2_0` 在 `:4414-4416`、`_1_0` 在 `:6679-6681` 写作 `FPGA_AXDS1_RST`。这是生成文件中按版本区分的字段定义，不应单独当成同一 revision 的内部冲突；当前板端实际 silicon revision/dispatch 分支仍是 UNKNOWN。\n\n**CONFLICT —** 保存的 UG585 AFI RD/WR 控制字段摘录要求访问前把关联 `FPGA_RST_CTRL.FPGA_AXDSN_RST` 写成 0（`data/results/wo0015-afi1-readonly-20261015/amd-rd-control-search.json:5`、`amd-wr-control-search.json:5`；前一审查报告 `verification-report.md:29-37`），而生成 `_3_0` 表把 AXDS1 位标为 reserved。现有证据没有协调通用 AFI 前置字段名称与当前适用 silicon revision 的位定义；因此不能把 `ps7_post_config` 的全寄存器写0或读零当成已验证的实时安全前置条件。

生成 post-config 表的静态预期是：对 `_3_0`，全寄存器 mask-write `FPGA_RST_CTRL=0`，然后分别置 AFI1 RD/WR `32BitEn` bit0 为 1；对 `_1_0/_2_0`，相应数据表也将 FPGA_RST_CTRL 置零并将 AFI1 两个 bit0 置一（C 表 `_2_0` 从约 `:4382` 起、AFI 行约 `:4476-4481`；`_1_0` 从约 `:6647` 起、AFI 行约 `:6741-6746`）。这是**条件式预计行为**，并不证明任一表在板端启动时运行过。

### 保存的启动和 Overlay 路径

**VERIFIED —** 保存的 Overlay 代码路径是 `_overlay_stage()` → `api.load_overlay()`（bundle `board_once.py:1270-1285`）；`PynqBoardAPI.load_overlay()` 调用 `pynq.Overlay(bit_path, download=False)`，随后 `.download()`（同文件 `:900-905`）。脚本源码没有显式引用 `ps7_init()` 或 `ps7_post_config()`；当前 WO-0015 `scripts/` 与保存 bundle/AFI 审查材料中也未找到外部调用点。`runonly-resume-retry2` 报告记载那一轮 Overlay/Reset/Run-only 新操作均为 0，并记录已加载 BIT 路径及 PYNQ global-state 文件 hash（`verification-report.md:17,27`）。PYNQ 缓存记录是软件状态，不是 PS init 调用证明或 PL 读回证明。

**UNKNOWN —** 当前实际 `BOOT.BIN`、FSBL ELF/source、U-Boot/启动脚本或 rootfs 启动链来源没有随 WO-0015 结果保存；对 `data/results/wo0015-*` 的只读文件名审查没有找到 `BOOT.BIN`、FSBL、`.elf`、`u-boot`、`image.ub` 或 `boot.scr`。没有启动介质读取或当前板端采集，本地保存证据不能确认哪个 FSBL 被烧录、它是否链接此 PS7 文件、是否调用 `ps7_post_config()`，也不能确认设备当前 silicon-version dispatch 分支。历史运行回执仅能说明当时 OS/PYNQ 软件快照；前一份 AFI 审查明确说明无当前寄存器读数（`data/results/wo0015-afi1-readonly-20261015/verification-report.md:9-17,37`）。

## 新增只读准入采集器审阅

审阅 `data/results/wo0015-afi1-preconditions-20261015/readonly_admission_collector.py` 与同目录测试；未运行测试。现存 `collector-green.txt` 记录该模块 fake 测试 `44 passed`。本地改动边界仅此离线审计报告。

**VERIFIED — 无隐式硬件写入/初始化。** 模块没有 PYNQ、Overlay、MMIO、`/dev/mem`、`mmap`、DMA、重置、shutdown、socket、subprocess 导入/调用。默认 CLI 只输出 `OFFLINE_REVIEW_ONLY` 并返回 2（模块 `:233-239`）；没有实机采集 CLI。显式调用 `collect_os_evidence()` 时只会经传入的 `io` backend 读取 OS/工件/Guard 元数据；标准 backend 使用 `O_RDONLY|O_CLOEXEC|O_NOFOLLOW|O_NONBLOCK`、检查 regular-file 类型、限长读取并在 `finally` 关闭 fd（`:190-218`），另只枚举 `/proc` 和读取 fd symlink target（`:220-230`）。模块结果的顶层 `status` 固定初值 `STOPPED_PRECONDITION_UNVERIFIED`、`afi_read_authorized=False`、寄存器读写计数 0，reset 状态 UNKNOWN、reset 定义 CONFLICT（`:57-66`）；成功最多把 `os_precheck` 标为 VERIFIED（`:140`），不会授权 AFI 读取。

**VERIFIED — 证据边界大体标注清楚。** `os_session_identity` 表示 OS-reported identity；PYNQ global state 只证明软件缓存匹配；加载硬件身份标为 INFERRED；进程扫描 VERIFIED 也明确限于单次快照，不能提供排他锁（`:96,118-130`）。测试确实断言 OS precheck 成功时仍为 STOPPED、`afi_read_authorized=False`、reset UNKNOWN（测试 `:79-88`）。因此不能把模拟成功或 `os_precheck=VERIFIED` 当作真实 AFI/复位准入。

**风险/限制：**

1. **路径安全仍是部署前约束。** 源码文档已修正：`O_NOFOLLOW` 只保护最终路径组件，父目录/挂载必须可信，且不防恶意 root 或路径竞态（模块 `:193-196`）。`fstat` 在 `open` 后检查最终对象类型，不能消除打开前路径替换风险。当前只读 allowlist 没有硬件写入路径；任何未来 OS 采集也必须先确认受控运行环境及固定父路径/挂载，且只限 OS 文件采集，不能因此放行 AFI 读取。
2. **挑战值不验证新鲜度。** `:80` 只检查调用者提供的 challenge 是 32 个小写 hex 字符；没有 nonce 生成、重放防护、签名或传输会话绑定。docstring `:52-55` 明确把生成 fresh nonce 留给未来固定传输。`test_fresh_challenge_required...` 实际只覆盖格式拒绝，测试默认使用固定 challenge（测试 `:74,101-105`）。所以该字段不是新鲜性证据。
3. **来源不可认证。** `evidence_kind` 来自 `getattr(io, "kind", ...)`（`:61`）；任何注入 backend 都可自报来源。函数文档已声明注入 backend 与 JSON 不可信（`:52-55`），而当前没有 transport/live verifier。采集结果不能独立充当认证远端板卡的凭证。
4. **保留原始样本需谨慎处理。** 大多数 OS/工件/Guard 样本以 base64 一并放入结果（`:69-76`），包括 BIT/HWH 字节；这是取证完整性优点，但调用方需保护 stdout/result，避免不必要地复制或公开完整硬件工件和 Guard 文件。

## 结论

当前静态生成源码说明“若 post-config 被调用，配置表预计怎样写”，却没有证据说明本次/当前启动实际调用了它。bit21 的 mask 可确定为 `0x00200000`；生成表的 active/reserved 定义随 silicon revision 分支变化，当前分支 UNKNOWN，且 UG585 AFI 前置字段与 `_3_0` reserved 定义的适用关系仍 CONFLICT。真实复位状态、实际 FSBL 与启动镜像来源仍是 UNKNOWN。新 collector 保持 fail-closed，不存在可由 CLI 开启的板操作；其可作为离线 OS 证据采集候选，不能作为 AFI 读取准入器。现阶段继续 **STOPPED_PRECONDITION_UNVERIFIED**，不建议把模拟门控升级为实板证据。
