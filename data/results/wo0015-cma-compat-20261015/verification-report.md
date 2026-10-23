# WO-0015 CMA 只读取证与兼容性修复报告

时间：2026-10-15T17:46:01.519815+08:00（Asia/Shanghai）。状态：**只读取证与离线修复完成；Retry 3 未执行，当前快照仍不满足运行时准入**。

## 阶段 A：实测事实与未知项

严格指纹 SSH 已认证，boot 前后均为 `822a9c29-37c0-438c-81c4-da998d57073b`。本轮板端只运行元数据读取：cmdline、chosen/bootargs、过滤后的 dmesg、iomem、debugfs CMA 可用只读字段、CmaTotal/Free，以及用于 PFN 单位核对的页大小查询。没有挂载 debugfs、修改启动参数、重启、导入 PYNQ、打开设备、加载 Overlay、分配 CMA 或启动 DMA。SSH 与本地凭据窗口均已关闭。

| 证据源 | 实测结果 | 能证明的边界 |
|---|---|---|
| /proc/cmdline | 无 cma= | 不能提供 CMA 大小/位置启动参数证据 |
| chosen/bootargs | 同样无 cma= | 不能提供替代启动参数证据 |
| dmesg 过滤输出 | 有 DRM 提及 CMA，无 cma: Reserved 基址行 | 不能确定 CMA 基址；也不能据此断言日志为何缺失 |
| /sys/kernel/debug/cma | 不存在 | 没有 base_pfn/count 可读；未挂载或修改系统来尝试暴露它 |
| /proc/iomem | System RAM = 00000000-1fffffff | 精确 DDR 区间为 [0x00000000, 0x20000000)，共 512 MiB |
| /proc/meminfo | CmaTotal=131072 KiB，CmaFree=73944 KiB | 存在 128 MiB CMA 记账，不能确定其物理位置 |
| getconf PAGESIZE | 4096 | PFN 转换单位是 4096 字节 |

**CMA 的真实配置来源与精确物理区间尚未探明。** 不能将“128 MiB CMA”推写为 `[0x18000000, 0x20000000)`。本轮也没有读取内核配置，不能声称已验证 CONFIG_CMA 默认划分。旧 Retry 2 在同一 boot 上记录的 reserved-memory 节点缺失证据保留。

原始启动参数输出（两处一致，chosen 属性末尾 NUL 已按文本读取去除）：

```text
root=/dev/mmcblk0p2 rw earlyprintk rootfstype=ext4 rootwait devtmpfs.mount=1 uio_pdrv_genirq.of_id="generic-uio" clk_ignore_unused
```

原始 CMA 余量输出：

```text
CmaTotal:         131072 kB
CmaFree:           73944 kB
```

原始 dmesg 过滤输出：

```text
[    0.043261] hw-breakpoint: found 5 (+1 reserved) breakpoint and 1 watchpoint registers.
[   40.238561] IPVS: Connection hash table configured (size=4096, memory=16Kbytes)
[   40.456512] Freeing unused kernel image (initmem) memory: 1024K
[  146.377578] [drm] Memory 0 is not reserved in device tree. Will allocate memory from CMA
[  146.377655] [drm] Memory 1 is not reserved in device tree. Will allocate memory from CMA
[  146.377724] [drm] Memory 2 is not reserved in device tree. Will allocate memory from CMA
[  146.377792] [drm] Memory 3 is not reserved in device tree. Will allocate memory from CMA
[  146.377856] [drm] Memory 4 is not reserved in device tree. Will allocate memory from CMA
```

完整 System RAM/内核/外设原始地址表在 `iomem.txt`，完整命令、stderr、debugfs 缺失状态在 `board-cma-probe.json`。`[drm] ... Will allocate memory from CMA` 是已有内核日志，不是本轮分配动作，也不包含 CMA 基址。

## 阶段 B：检查模块修改

`board_preflight.py` 新增独立 CMA 来源解析和分配后物理缓冲检查。原有功能执行器的修改只发生在 `board_once.py` 的三处：运行时 CMA 读取集成、分配后门控、物理地址取得。逐函数 AST 与原批准包对比见 `scope-audit.json`；原有 preflight 函数未改动。RTL、工件、packet/Golden、打包逻辑、Guard、传输顺序和完成/数据判定标准保持不变。

1. 优先设备树；其次解析内核 `cma: Reserved ... at 0x...` 或 debugfs `base_pfn * 4096`、`count * 4096`。精确来源须相互一致、与 CmaTotal 大小一致、页面对齐、在 512 MiB DDR 内，并与 iomem 中非 System RAM 区域不冲突。格式错误、信息矛盾直接拒绝，不能用低优先级信息掩盖错误。
2. 缺少精确来源时，只有 size-only `cma=` 唯一明确、与 CmaTotal 一致、CmaFree 足够且 iomem 精确证明 512 MiB RAM 时，才建立高端候选窗口。来源写为 `ram_high_candidate`、`physical_cma_range_known=false`；该窗口只是保守允许范围，**不是 CMA 实测范围**，可能拒绝本来合法的分配。显式放置的 cma= 复杂语法没有足够精确证据时仍拒绝。
3. 实际 PYNQ 缓冲的 `physical_address` 与 `device_address` 必须均存在且相同。所有缓冲须正地址、实际字节长度分别精确为 230432/76800、MM2S 8 字节/S2MM 4 字节对齐、整段位于允许范围及对应 HP 窗口、末字节不超过 0x1fffffff、互不重叠。缺失区间不能跳过检查。检查在任何 DMA/Checker/Packer 寄存器写入前完成，结果记录实际物理区间和大小。
4. 新增回归 61 项，包括 DT 缺失、内核/debugfs 精确区间、RAM 候选标注、多源冲突、大小与保留区矛盾、零地址、对齐/长度/越界/重叠、PYNQ 地址不一致，以及失败时零寄存器写入并释放 Mock 对象。

将本次实际采集数据在 PC 上回放，得到：

```text
decision=REJECTED_MISSING_CMA_SOURCE
error=no precise CMA range and no unambiguous cma= boot evidence
physical_cma_range_known=false
```

这是正确的拒绝：本次不仅缺精确区间，也缺少用户要求第三级回退所需的 cma=。没有为了使当前会话通过而放宽到“CmaFree 足够即可”。回放只处理保存的文本，没有连接板卡。

## 离线验证

- 新功能首次 RED：56 项按缺失功能或既有未拒绝行为失败，原始日志 `red-tests.txt`。审查建议新增的两项 iomem 冲突测试也先观察失败，见 `review-red-tests.txt`。
- WO-0015 全部离线测试：**117 passed**，其中新增 CMA 回归 **61 项**，JUnit 0 失败、0 错误、0 跳过。
- 全仓 canonical Python 测试根 `python/` 与 `scripts/`：**421 passed，另 5 subtests passed**；JUnit 0 失败、0 错误、0 跳过。
- 首次全仓测试遇到 worktree 缺少 H3 PNG 的环境问题：415 通过、1 失败。只从主目录复制缺失的 487 张既有 PNG，逐张 SHA-256 核对，不重新生成、不覆盖、不修改原件；记录在 `offline-fixture-restore.json`。复跑后全通过。另保留 importlib 模式不适配旧测试导入方式的收集错误日志，未修改测试来回避失败。
- 独立 Worker 完成只读代码审查；建议已落实，Manager 检查实际函数差异及打包边界，见 `review-record.md`。
- 新包 PC offline 与 Mock 通过，二者 board_accessed=false、pynq_imported=false；Mock 物理地址/寄存器/CMA 数值不能作为板端证据。

## 更新后的交付摘要

最终新包：`C:\Users\POP\.codex\worktrees\wo0015-design-20261015\material-counter\data\results\wo0015-retry3-cma-bundle-20261015.zip`。

| 项目 | SHA-256 |
|---|---|
| 最终 ZIP | `4b684418c159f0d0e8d7eb96ba92ecd0355e4fad618c3c535080400ae7a6949d` |
| run-manifest.json | `f91323b0d8bfc3f0ed831cebc806d01d2caa901dfbcf0a7f6521db183029e974` |
| 包内 board_once.py | `604a267256857f5372f2d659080cc8c1a5e1767e4824631d42c702380e63e7f5` |
| 包内 board_preflight.py | `6325f6d475890264fa970906cb6f0cba4c1a7e0d925e8c87c04ce1c325d62932` |
| wo15.bit | `a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420` |
| wo15.hwh | `bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6` |

原打包入口未改动并检查既有 RTL/仿真/build 摘要。其历史 boot 中间输出留在本结果目录。最终部署副本仅沿用此前已批准的新 boot 常量 `822a9c29-37c0-438c-81c4-da998d57073b` 并更新派生摘要；源码默认常量不改。除该配置常量外，包内 preflight 与修复源码完全一致；board_once 完全一致。源码 preflight 摘要为 `67e4b6c9f0c8e3016d1201b87b86972f295ed04624a5255dc88c811a338f8fc2`，不同于包内摘要的原因仅是该 boot 配置。最终 ZIP 所有条目按 SHA256SUMS 逐一复核。

原包和三次历史结果未覆盖，历史已索引文件逐一复核无变化。新脚本/包**未上传板端**，板端仍保留 Retry 2 的旧检查器；本轮没有在板端运行任何硬件验证执行器。

## Retry 3 申请与准入结论

条件式申请见 `retry3-approval-request.md`，锁定新包/脚本/boot，仍要求三个阶段各一次、前一步 PASS 才推进、任意异常熔断。

**当前申请状态为待补证，不能立即批准执行硬件步骤。** 已具备离线测试与工件身份条件，但缺少精确 CMA 区间，且第三级条件不成立。请先审查并补齐可验证的 CMA 物理区间来源（例如实际可用的只读 PFN/count 或保存的初始化基址日志）；如需挂载 debugfs、检查额外系统接口或改变启动环境，应另行限定操作范围。不能通过删除 cma= 条件、猜测高端位置或分配一次缓冲探地址来让本轮通过。

## 文件与分支

结果目录：`C:\Users\POP\.codex\worktrees\wo0015-design-20261015\material-counter\data\results\wo0015-cma-compat-20261015`。原开发目录 HEAD `7a868c070024bdc29cdeb057fbec9ccaf94ef160`；worktree HEAD `b7d8be05caff7da3ad2235617b43b591daa5e5fc`。没有提交、合并或暂存，git diff --check 通过。WO-0015 源码原本为未跟踪文件，实际变化以两个 `.py.diff` 与 `scope-audit.json` 为准，不能用空 tracked diff 声称没有修改。

修改的正式文件为 `scripts/wo0015/board_preflight.py`、`board_once.py`、`test_board_once.py`、`README-board.md`，新增 `test_cma_compat.py`；另有独立取证/报告/打包结果及按原件补齐的测试 PNG。没有修改 RTL、BIT/HWH、主目录原数据或全局配置。
