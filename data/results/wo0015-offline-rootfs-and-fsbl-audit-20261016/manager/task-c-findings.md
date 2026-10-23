# C：Golden 与缓存同步静态闭环

状态：`STOPPED_PRECONDITION_UNVERIFIED`。仅对本地 Golden、历史保存的执行器源码和候选镜像内源码进行静态读取；没有导入 PYNQ、执行其代码或连接板卡。

## VERIFIED：Golden 本地字节事实

Golden 为 `wo0015-retry3-two-tier-bundle-20261015/frame-0.binary.bin`，76800B，SHA-256 `174164ae33086f8606b83113f43ebd5a52470f8640bde1c489e51e76f411a60e`，与历史 manifest 记录相符。

偏移 0..3 为 `00 00 00 00`，第 0 个小端 word 为 `0x00000000`；前 64B 均为零。全帧含 74560 个零和 2240 个 FF。奇、偶 word 各 9600 个，每侧 1120 个 FF 字节。两侧各自的 word 直方图均为：9250 个 `00000000`、70 个 `0000FFFF`、70 个 `FFFF0000`、210 个 `FFFFFFFF`（以小端数值展示）。完整位置与原始字节见 `golden-all-19200-words.csv`。

偶 word 强制为零时差异来自其中 1120 个前景字节；奇 word 为 A5 时 38400 个字节全部与 Binary8 不同，合计 39520。因为第 0 word 本来为零且 Golden[4]=0，假设下首差偏移正是 4。若 Golden 前四字节有 FF，偶零假设的首差就会提前；若 RX 全部未被修改且仍为 A5，首差应为 0、差异数应为 76800。

另一独立构造为：复制 Golden，仅在偏移 4..39523 对每字节 XOR 1，产生不同内容，但差异数同为 39520、首差同为 4。该反例并非实际 RX。这两种构造证明摘要不唯一；不能从首 word 匹配推断它的最后写入者、硬件 lane 或缓存状态。

## VERIFIED for local image：分支与参数

历史实际保存的 `remote-evidence/board_once_watermark.py` 第 927–930 行仅调用 `pynq.allocate(shape=(size,), dtype="u1")`。第 445–447 行分别同步 TX 数据和预填 A5 的 RX，接收完成后第 513 行调用 device→CPU 同步。

在候选镜像包的未修改源码与 EmbeddedDevice 条件下，路径为：

`buffer.allocate(target=None)` → `Device.active_device.allocate` → `Device.allocate` → `EmbeddedDevice.default_memory` → `EmbeddedXrtMemory` 所继承的 `XrtMemory.allocate` → `_xrt_allocate(cacheable=0)` → `allocate_bo` → `pyxrt.bo`。

这里需要区分两个同名语义：`_xrt_allocate` 的 allocation 参数默认 `cacheable=0`，因此选择 `pyxrt.bo.flags.normal`；它构造 `PynqBuffer` 时却明确传入 `coherent=False`，而 `PynqBuffer.cacheable` 属性只是 `not self.coherent`，所以属性为 True。不能把这个 True 写成已测得的内核页表缓存属性，也不能把 allocation 参数 0 写成不需要同步。

| 方向 | 选中分支 | 后续调用 |
|---|---|---|
| CPU→device | 历史适配器优先找到可调用的 `PynqBuffer.sync_to_device` | `self.flush()` → coherent=False 条件成立 → `XrtDevice.flush` → `bo.sync(XCL_BO_SYNC_BO_TO_DEVICE)` |
| device→CPU | 优先找到可调用的 `PynqBuffer.sync_from_device` | `self.invalidate()` → coherent=False 条件成立 → `XrtDevice.invalidate` → `bo.sync(XCL_BO_SYNC_BO_FROM_DEVICE)` |

因此，在该候选类上，执行器的缺方法 fallback 不会被选择，但 primary 方法内部确实调用 flush/invalidate。`sync_for_cpu` / `sync_for_device` 不是这里选择的 PynqBuffer 方法。`libcma.so` 的存在也不能证明这条 `allocate` 路径调用了它；所核对的链路采用 pyxrt BO。

逐行摘录与源码 SHA-256 见 `historical-cache-call-sites.json`、`cache-source-evidence.json` 及顶层 `golden-and-cache-audit.json`。镜像中的源码包含自身的 BO 分配实现，不能将其细节泛化为所有官方 PYNQ v3.1.1 安装行为。

## INFERRED / UNKNOWN

上述分支是“历史执行器 + 候选镜像软件包 + EmbeddedDevice + 未被替换的类/方法”条件下的静态闭环。历史实际安装包哈希、对象类型、coherent 元数据及所命中分支没有保存，候选包与历史运行包是否相同仍未绑定。pyxrt 的实际运行派发、内核 BO 映射及缓存维护效果亦未实测。

因此，源码中存在同步调用不能排除缓存问题，也不能证明缓存导致了本次差异；历史 RX 字节仍缺失，偶零/奇 A5、AFI 运行时位宽及根因均不能升格为 VERIFIED。
