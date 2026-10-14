# WO-0013 Phase A Level 2 单帧板级验证

日期：2026-10-14（Asia/Shanghai）。**结果：Level 2 PASS，真实 MM2S 发送 1 次，PL Monitor 完整校验通过。** 本轮未加载 Overlay、复位 DMA、重启、重试或进入 Phase B。

## 缓冲区与 pagemap 审计

依据 [PYNQ allocate 官方文档](https://pynq.readthedocs.io/en/latest/pynq_libraries/allocate.html)，pynq.allocate 返回供 HP/ACP 等 PL 访问的物理连续 PynqBuffer，device_address/physical_address 是交给 PL 的地址；sync_to_device/flush 负责发送方向同步。板端 PYNQ 3.1.1 的实际调用链为 allocate → EmbeddedDevice/XrtMemory → _xrt_allocate → pyxrt.bo(normal)；device_address 来自 bo.address()，physical_address 是它的别名。板端源码哈希与先前已阅读的版本一致，记录在 current-audit 与 buffer-evidence.json 中。确认实际类型为 PynqBuffer、pyxrt.bo、EmbeddedDevice，驱动为 /sys/bus/platform/drivers/zocl-drm；无 IOMMU group 或设备树 iommus 条目。DRM 记录 Memory 0 使用 CMA，Linux CmaTotal=128 MiB。

旧 256 MiB 值是 PYNQ 合成 XCLBIN topology 的 PSDDR 容量，不是实测物理地址上界。当前设备树 memory@0、root 读取的 /proc/iomem 与 HWH HP0 均确认完整 RAM/访问窗口 [0x00000000,0x20000000)。DMA 地址宽度 32 位、长度字段 17 位，MM2S Simple mode、无 SG、无 S2MM；地址为 DMA 0x40400000、Monitor 0x43C10000。

先分配仅用于诊断的缓冲区，记录地址，采集填充前 pagemap，再 CPU 填充、sync_to_device、CPU 回读并采集填充后结果，最终释放；没有启动 DMA。填充前后 BO 的 19 个 pagemap 条目均为零。该 root 进程有 CAP_SYS_ADMIN，同时已触页的匿名对照页返回 present 和非零 PFN。因此本次现象不是全局 PFN 权限屏蔽，也不能仅解释为尚未 CPU 触页。该 DRM BO mmap 没有通过此次 pagemap 查询提供 PFN；具体内核处理路径尚未追溯。不能把零条目当成专用 DMA Buffer 不连续或物理地址不可访问的证明。

[Linux pagemap 文档](https://www.kernel.org/doc/html/latest/admin-guide/mm/pagemap.html) 描述 present、swapped、PFN 位及 CAP_SYS_ADMIN 限制。根据用户本轮授权，缺失 PFN 作为辅助诊断：仅在已核实支持的分配器、BO 身份、CMA 后端、无未审计地址转换、容量、地址窗口、位宽、对齐、CPU 数据和缓存同步后允许发送。若 pagemap 提供非零 PFN 且与 BO 地址冲突，仍立即拒绝；不忽略真实矛盾。

## 修复与离线测试

所有改动仅位于本 evidence 目录，保留历史脚本和原交付文件。

- 先填充 CPU 数据并同步到设备，再调用地址检查；分配地址、长度、对齐在释放前记录。
- 验证完整区间同时落在 HWH、Linux System RAM、设备树 RAM 内；检查 32 位溢出、8 字节对齐、BO/address/size、数组布局和 offset。
- pagemap 无 PFN 仅在 runtime 验证支持的 PYNQ/XRT/CMA 分配器时可接受；无分配器证据仍拒绝，已提供的错误 PFN 永不被覆盖。
- 发送前 7 个 unittest 方法通过，涵盖 16 个原非法边界子案例及新增 PFN 缺失有/无分配器证明、错误 PFN 不被覆盖场景。新策略先复现 1 个失败测试，再实现并全绿。独立审阅后又新增 3 个诊断组合测试：present=true/PFN=0 且确认为可见 PFN 时拒绝、权限屏蔽时辅助、不暴露 PTE 的 DRM 映射辅助；先复现边界缺陷，再修复，最终 10 项全部通过。日志见 boundary-tests-red.txt、review-boundary-red.txt、boundary-tests-green.txt。
- 执行脚本静态检查只有一次 DMA LENGTH 写入，无 run_replay、DMA reset 或 Overlay load。异常时仅在 DMA 未取得所有权或确认 Idle 后释放缓冲区；所有权不明确时保留缓冲区与进程，不自动恢复。

独立审阅发现，初始辅助逻辑可能忽略 present=true/PFN=0 的可见零 PFN。已在本地增加 validate_pagemap_rows：CAP_SYS_ADMIN 且匿名对照证明 PFN 可见时，present 的零 PFN 不能与非零 BO 页面相容，必须拒绝。本次实际 19 行均 present=false，因此该边界未发生、原始 PASS 不受影响。runtime-buffer-check.executed.py / ddr_validate.executed.py 保留执行版本；最新加强版只在本地修改、测试、语法验证，未再上传或重发 DMA。

## 实际单次发送

本次启动 ID：a2fed90e-2394-472f-afcd-1422680fed9d。Overlay 为 /home/xilinx/wo0013_level2_once_20261014_113914/wo0013_phase_a.bit，bit hash 与已验证修复版一致；BIT/HWH SHA-256 分别为 47e6cd65c552244236f2564c11cba3e81a9a0c6aced0c9974c04de78256da3f7、a31d71f8e7004de710fa4da07078b80e2a0a8b40ff52ac523d322f21362df1fb。操作前扫描 /proc，没有其他硬件使用者、Notebook 内核或检查权限拒绝；单一认证 SSH 控制会话与排他锁。

新发送缓冲区真实物理地址 **0x15860000**，长度 **76816**，exclusive end **0x15872C10**；BO address、device_address、physical_address 相同，BO size=76816，8 字节对齐，uint8 shape=(76816,)，offset=0。区间完全位于上述三个 [0,0x20000000) 窗口，未超出 32 位 DMA 地址范围。先生成/填充精确 TB payload，CPU 回读逐字节相等，再成功调用 sync_to_device；coherent=false。物理连续性依据受支持的 PYNQ 分配器及核实的实际 XRT/ZOCL CMA 后端，pagemap 不提供额外 PFN。

初始 DMA：DMACR=0x00010002、DMASR=0x00000001（Halted），SA=0，LENGTH=0，无错误。Monitor EXPECTED_BYTES 实读 76816；按 RTL 向 CONTROL bit0 写 1 后 STATUS=1，观察寄存器和 error_code 全零。

所有预检查通过后，写 DMACR.RS=1，确认 Halted 清除，再写 SA=0x15860000，**仅一次写 LENGTH=76816**。有界轮询限时 5 秒；第一次完成轮询即得到 DMA DMASR=0x00001002、Monitor STATUS=2。无自动重试。完成并校验后 freebuffer 释放发送缓冲区。

| 寄存器/指标 | 期望 | 实际 |
|---|---|---|
| DMA DMACR | RS=1，Reset=0 | 0x00010003 |
| DMA DMASR | Idle、IOC，无错误 | 0x00001002 |
| DMA SA | 分配物理地址 | 0x15860000 |
| DMA LENGTH | 76816 | 76816 |
| Monitor STATUS | Done=1，Armed/Error=0 | 0x00000002 |
| BYTE_COUNT | 76816 | 76816 |
| BEAT_COUNT | 19204 | 19204 |
| TLAST_COUNT | 1 | 1 |
| FIRST_WORD | 0xFFFF00FF | 0xFFFF00FF |
| LAST_WORD | 0x12340000 | 0x12340000 |
| CHECKSUM（32 位字累加 mod 2^32） | 0x70B5017B | 0x70B5017B |
| ERROR_CODE | 0 | 0 |

软件记录从 LENGTH 写入开始到完整快照约 9.27 ms，包含 Python/轮询及 fsync 日志开销；这不是硬件纯传输延迟或吞吐测量，不用于性能结论。也不据此声称 S2MM 逐像素回读、连续帧运行或 Phase B 已完成。

## 最终状态与证据

没有 DMA/Monitor 错误或超时。发送后 Ping 收到回复，<1 ms、无丢包；SSH banner 正常，同一认证控制连接完成 uname/id/uptime/free/dmesg 检查。启动 ID 不变，FPGA state=operating，Overlay 身份一致；最终进程扫描无硬件占用、Notebook 内核或权限拒绝。DMA 保持完成后的 RS=1/Idle，无额外 halt/reset 写入。

内核日志存在本次启动早期的 regulatory.db、autofs4、journal、ZOCL IRQ、FAT 未正常卸载等既有警告；本轮测试时段无新增条目，不能称整机日志完全无警告。未调整板卡时钟（仍为旧墙钟），日期以客户端为准，事件顺序使用 monotonic_s。

完整日志：complete-terminal-transcript.txt；原始寄存器：raw-registers.json；缓冲区/源哈希/CMA/辅助 pagemap：buffer-evidence.json；最终 Linux：post-health.json；机器可读验收：acceptance-summary.json。密码由用户隐藏输入，没有写入代码、聊天或日志。

git diff 与 staged diff 均为空；生产 RTL、受保护文档、G1/Route 未修改，未提交、push 或启动 Phase B。**WO-0013 Phase A Level 2 的一次真实单帧 DMA + Monitor 验证已完成。** 下一步先保留该结果作为基线，后续多帧/异常恢复或 Phase A 其他等级单独确定范围；不自动扩展实验。
