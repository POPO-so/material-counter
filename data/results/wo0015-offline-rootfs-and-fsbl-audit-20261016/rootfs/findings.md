# WO-0015 离线 rootfs / PYNQ / FSBL 静态审计

## 结论与边界

本次审计从本地镜像取得静态字节证据：未挂载文件系统、未回放 ext4 journal、未连接板卡或访问网络、未执行镜像中的任何程序；未读取 `restricted-raw` 或 `REVISION` 内容。所有结论仅描述镜像磁盘当前字节和其中可见的静态源文件，不证明 BOOT.PY 曾实际运行、Overlay 已在板上下载、PS 寄存器实值或 DMA/cache 行为。

镜像全文件 SHA-256 复核为 `4a6ac1cdbb413d768fcb059b7182e1f53aa7f728b898b7f5061cbb0b7cc93aab`，大小 `8,337,309,696` 字节，均与附件给定值一致；读取前后主机文件大小/修改时间稳定。身份记录在 `image-identity.json`。ext4 分区 UUID 为 `63b8b04d-af08-419a-a9dc-16f374e283dc`，MBR 分区 2，起始 LBA `268288`、字节偏移 `137363456`、分区长度 `8199946240`；块大小 4096、块数 2,001,940、inode 数 1,001,920、inode 大小 256、组描述符 64、62 个 block groups。`feature_incompat=0x2c2`；superblock state 原值 `1`（clean 位为 1、error 位为 0），needs_recovery 位为 false。**没有 journal replay**；因此这里仍是 on-disk 静态视图。

结构化记录：结果目录顶层的 `pynq-afi-and-base-hwh-audit.json`；逐文件原始镜像路径、提取路径、长度与 SHA-256：`source-manifest.json`。

## 版本、提取目录与完整性

镜像内 `/usr/local/share/pynq-venv/lib/python3.10/site-packages/pynq-3.1.1.dist-info/METADATA` 给出 `Name: pynq`、`Version: 3.1.1`。PYNQ Python 源提取目录（Manager/C 可直接引用）：

`data/results/wo0015-offline-rootfs-and-fsbl-audit-20261016/rootfs/extracted/pynq/`

包含 `overlay.py`、`bitstream.py`、`registers.py`、`mmio.py`、`buffer.py`、`pl_server/device.py`、`pl_server/embedded_device.py`、`pl_server/xrt_device.py`、`overlays/base/base.py` 及 base overlay 文件。实际模块为 `pynq.pl_server.xrt_device`；PYNQ 树中不存在 `pynq/pl_server/xrt.py`。`pyxrt` 绑定不是 Python 文件，镜像路径 `/usr/lib/python3.10/pyxrt.so` 已作为不可执行二进制提取。

将提取出的所有 `pynq/*.py` 与镜像本地 dist-info `RECORD` 中带 SHA-256 的行比对：173 行均匹配，0 不匹配、0 缺失。这只证明提取字节与镜像自身 `RECORD` 一致，不能认证上游官方 wheel/tag，也不能排除 vendor patch 在生成/安装此 RECORD 前已进入发行包。

核心源文件 SHA-256（完整清单以 manifest 为准）：

| 源镜像路径 | 提取相对路径 | SHA-256 |
| --- | --- | --- |
| `/home/xilinx/pynq/buffer.py` | `extracted/pynq/buffer.py` | `532f1cee01509b4f90e3976d4270a0a5f7d525d4ba94f7233b0b5fda0f281463` |
| `/home/xilinx/pynq/overlay.py` | `extracted/pynq/overlay.py` | `ee8145ff89a3ec88e28bc44368a8708ac2d2a3d6cd2e55843a33dbc546f69626` |
| `/home/xilinx/pynq/bitstream.py` | `extracted/pynq/bitstream.py` | `b7e37302fefca9f2d09ebf487aa14abac8e603e12c544e8888d9ebaca9bf2914` |
| `/home/xilinx/pynq/pl_server/embedded_device.py` | `extracted/pynq/pl_server/embedded_device.py` | `124511991cb3c0d15842344efb8621a5dd15fc0e5d8eb70d29cbfa81f398a12f` |
| `/home/xilinx/pynq/pl_server/xrt_device.py` | `extracted/pynq/pl_server/xrt_device.py` | `fb4bdbe4cac759c66feeb0248e13c83592ddfad32b36bc83764484411812556a` |
| `/usr/lib/python3.10/pyxrt.so` | `extracted/system/usr/lib/python3.10/pyxrt.so` | `78cc6cf1617db6e35fd6c23989ab2a95c982b47917cd3a88f5881eaa43384748` |
| `/usr/include/libxlnk_cma.h` | `extracted/system/usr/include/libxlnk_cma.h` | `c3c6a79fe75d509be6a18e3c15c5940bfa7e90a274fce4271c704fe39b3c7df9` |
| `/usr/lib/libcma.so` | `extracted/system/usr/lib/libcma.so` | `126301f9575d119f3b12e41bc2ce26df79a0fdae210169da3bf4b6548dd1fdd3` |
| `/usr/include/xrt/version.h` | `extracted/system/usr/include/xrt/version.h` | `83a73b17321a5de270ea9cfc7629c73ad986ffce71a3fd0401a4067fd567ceb0` |

`xrt/version.h` declares XRT `2.17.0`, build hash `a75e9843c875bac0f52d34a1763e39e16fb3c9a7`; image contains versioned `libxrt_core`, `libxrt_coreutil`, and `libxrt++` 2.17.0 libraries. The audit extracted XRT headers and these binaries, not vendor implementation source. The installed `libcma.so` is also binary-only; its header provides declarations such as `cma_alloc`, `cma_flush_cache`, `cma_invalidate_cache`, but no `libcma` implementation source was present in the bounded image source inventory. Binary files were hashed, never loaded or executed.

## BOOT.PY、base.bit/base.hwh 与 HP1

镜像 FAT16 启动分区先前只读提取的 `/BOOT.PY` 长 991 字节，SHA-256 `b2653ef809c3271231c78772342c2be728d39686a877d3c6fe8cdabfd4ac1d42`。其第 27 行导入 `BaseOverlay`，第 29 行构造 `BaseOverlay("base.bit")`。`base.py` 第 56–57 行将构造参数传给 `Overlay`；`overlay.py` 第 289–318 行默认 `download=True`，第 353–354 行据此调用 `download()`。这是静态配置路径，不能证明该脚本在任何实际开机时执行。

镜像 PYNQ 包中的 `/home/xilinx/pynq/overlays/base/base.bit` 长 4,045,672 字节，SHA-256 `3239e9cc37ee4993c25f02ae6c97da0b3372f0e722e267d10073e20adc5d6717`；同目录 `base.hwh` 长 4,529,854 字节，SHA-256 `222898ef2c0ccc4a0c47608e99d8dc7c9c6cdfed5130964884ff35005827ef3a`。

解析 HWH 中 PS7 参数：HP0–HP3 的 `PCW_S_AXI_HP*_DATA_WIDTH` 都是 64（HP1 在 HWH 第 54118 行）；关联的 `base.tcl` 第 4118–4121 行则设置 HP0=1、HP1=0、HP2=1、HP3=0。故 HP1 的 64-bit HWH 参数是宽度元数据，不能据此说 HP1 已使能或本镜像配置与 WO-0015 HP1 32-bit 设计相同。TCL 没有为禁用的 HP1 导出 `PCW_S_AXI_HP1_DATA_WIDTH` 属性；不能用 width 参数覆盖使能位。

## PYNQ 代码路径与 AFI/reset 边界

- `BOOT.PY:27,29 → overlays/base/base.py:56–57 → overlay.py:289–318,353–354,402–432 → bitstream.py:99–118,151–168`：静态入口从名称解析 bit 文件，`Overlay` 默认触发完整下载，并将操作委托给 Device。
- `embedded_device.py:538–539,665–690`：`EmbeddedDevice` 使用 `/sys/class/fpga_manager/fpga0/flags` 和 `/firmware`；完整 bitstream 下载分支先执行 `shutdown()`/元数据缓存流程，写 flags 与 firmware 名称，随后调用 `set_axi_port_width()`、XRT 下载和 post-download。`device.py:383–412` 中 `shutdown()` 通过已登记的 AXI shutdown-manager IP 做 MMIO；`device.py:147–188,414–423` 的 `reset()` 重建/清理软件状态字典并更新时间戳。它们都不能证明内核 fpga_manager 实现没有硬件复位副作用。
- `embedded_device.py:604–637` 的 `set_axi_port_width()` 仅在 `parser.family_ps == "zynq_ultra_ps_e"` 时遍历 ZU FPD SLCR/AXI-FM寄存器映射。PYNQ Python 源中没有直接字面量 `0xF8009000`、`0xF8009014`、`0xF8000240`、`FPGA_RST_CTRL`、`AFI1_RDCHAN_CTRL` 或 `AFI1_WRCHAN_CTRL`（扫描了 173 个已哈希 `.py` 源文件）。因此，Python 静态路径没有对 PYNQ-Z2/Zynq-7000 PS7 AFI/FPGA_RST_CTRL 的直接写操作证据；但写 fpga_manager sysfs 已进入内核链路，不能据 Python 搜索反向断言整个加载链不复位。
- 镜像 `/usr/lib/modules/6.6.10-xilinx-v2024.1-g3c0eca68c652/modules.builtin` 和 `.config` 表明 FPGA framework、`FPGA_MGR_ZYNQ_FPGA`、`FPGA_MGR_ZYNQ_AFI_FPGA`、bridge/region 以及 CMA 支持构建为内核内建；CMA 配置为 128 MiB。这是构建配置/模块索引，不证明启动后这些驱动的具体绑定状态。`/usr/src/kernel/drivers/fpga/` 只有 Kconfig、Makefile、tests，没有 `zynq-fpga.c`、`zynq-afi.c` 或 fpga manager C 实现；内核 DTS 源目录没有 `.dts/.dtsi/.dtb` 文件，ext4 根的 `/boot` 也没有候选 DTB。因缺精确驱动实现和绑定 DTB，fpga_manager 写入引发的 PS7 reset/AFI 硬件副作用标记为 **UNKNOWN**。

## Buffer / XRT / libcma 静态路径（供任务 C）

提取路径：`rootfs/extracted/pynq/buffer.py`、`rootfs/extracted/pynq/pl_server/xrt_device.py`、`rootfs/extracted/pynq/pl_server/embedded_device.py`；`pyxrt.so` 位于 `rootfs/extracted/system/usr/lib/python3.10/pyxrt.so`。

`buffer.py:85–128` 定义 `cacheable = not coherent`，`sync_to_device()` 调 `flush()`、`sync_from_device()` 调 `invalidate()`；`buffer.py:138–158` 的 `allocate()` 委托给当前 Device。`xrt_device.py:59–88` 的 `_xrt_allocate(..., cacheable=0, ...)` 默认分配参数为 0，但构造的 `PynqBuffer` 显式 `coherent=False`，所以属性 `buffer.cacheable` 为 True。两处的 “cacheable” 语义不同，不能从 Python 属性推断 XRT BO flags。

`xrt_device.py:334–346` 在分配参数为假时选择 `pyxrt.bo.flags.normal`，为真时才选择 `flags.cacheable`。`xrt_device.py:322–332` 的 flush/invalidate 调 `bo.sync(XCL_BO_SYNC_BO_TO_DEVICE/FROM_DEVICE)`；这对应 PYNQ buffer 的同名 sync 包装。该 Python allocate/sync 路径没有调用 `libcma.so` API；`buffer.py:10–14` 在 target 上加载的是 `libc.so.6` 用于 `munmap`。这不排除其它模块或不透明二进制里的其他路径。

`registers.py:94–134` 以 `MMIO(address, ...)` 建立绝对物理寄存器映射并支持切片字段；`mmio.py:42–85` 对 MEMORY_MAPPED capability 使用 `device.mmap`。PYNQ `Register`/MMIO 为通用内存映射设施，源文件本身不提供 WO-0015 PS7 AFI 或 FPGA reset 的实际硬件值。

## 解析器和测试

新增只读解析器 `ext4_ro.py` 以 `rb` 打开常规镜像，校验 MBR 分区边界并按 UUID 选分区。它仅支持本镜像需要的 incompat feature 子集，对未知 incompat 位 fail closed；extent tree 深度限制 5、单文件读取限制 512 MiB，不做 mount 或 journal replay。

定向回归覆盖 UUID/分区选择、按 inode/extent/目录路径读文件、损坏路径边界，以及 ext4 `ee_len=0x8000` 的边界语义：该值表示 initialized 的 32768 blocks，只有 `>0x8000` 才编码 unwritten extent。初次红测及该边界的专门红测日志保留；最终命令

`C:\fpga\material-counter\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q test_ext4_ro.py`

结果 `4 passed in 0.07s`，见 `tests-final.log`。

## 风险与未知

1. 结论只针对该镜像的静态磁盘字节；ext4 state clean / no needs_recovery 不等于文件已通过运行时验证，且没有 journal replay。
2. 不知道 BOOT.PY 是否执行，不知道板上实际 bitstream/HP1 enable、PS7 AFI 值、FPGA_RST_CTRL 值或 fpga_manager 的运行态内核行为。
3. 本镜像版本记录为 PYNQ 3.1.1，但仅验证了其自身 RECORD；若需断言为官方未修改发行版，需独立可信上游 wheel/tag 进行离线比较。
4. pyxrt、libcma 与 XRT C++ 库均为不透明二进制；未执行、未反汇编。CMA/XRT 驱动的底层行为超出本轮确定证据。
5. ext4 parser 对未知 incompat、inline data、non-extent inode 等拒绝支持；当前通过只证明定向单元测试和此次镜像路径读取，不构成通用 ext4 实现声明。
6. 一次初始路径误放的测试文件已迁入指定隔离 worktree，原 checkout 对应文件/空目录已清除；顶层 JSON 最初误写在 worktree repo 根，现已迁到本轮结果目录顶层。完成时会再核实两个越界候选路径均不存在；不提交。