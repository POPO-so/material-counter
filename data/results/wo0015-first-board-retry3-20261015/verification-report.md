# WO-0015 首次上板 Retry 3 核验报告

生成时间：2026-10-15T19:00:53.574160+08:00（Asia/Shanghai）。最终裁决：**FAIL_STOPPED**。

停止位置：**Step 1：Overlay 加载与签名通过之后，补充 DMA 初始就绪核对未通过**。新的两级 CMA 架构已落地，板端 Tier 1 PASS；锁定 50 MHz 工件实际加载一次，签名符合预期，但两个通道实测 Halted=1，不满足本次正式授权的 Halted=0 准入条件。已立即阻断 Step 2 / Step 3，无自动重试、RUN 修复写入、DMA 复位或 Overlay 重载。

## 本次架构与离线验证

本次队长正式授权缺少精确静态 CMA 子区间时采用内核记账加 DDR 安全包络。`MEMINFO_IOMEM_VERIFIED` 要求 System RAM 精确为 `[0,0x20000000)`、CmaTotal=131072 KiB、CmaFree>=32768 KiB，允许包络 `[0x00100000,0x20000000)`，并保持 `physical_cma_range_known=false`。有精确元数据时仍优先验证；显式冲突的 cma= 信息仍拒绝。本次包络不是 CMA 子区间实测。

Tier 2 检查在任何寄存器写入前核对 allocator 给出的 physical_address 与 device_address 一致、地址不低于 1 MiB、整段落在 DDR/HP 包络、长度精确、MM2S 8 字节/S2MM 4 字节对齐、两段不重叠；MM2S 8 字节约束保留 HWH 中无 DRE 的 64-bit 内存端口要求。证据新增分配前、TX 后、RX 后、释放后 CmaFree，转移前要求总下降至少覆盖两个页取整缓冲的 304 KiB，正常 PASS 要求最终余量恢复原值。全局 CmaFree 仅是分配记账佐证，不能单独证明某一进程的地址所有权。

新增两级规则测试 12 项，首次 RED 为 6 failed / 6 passed，具体缺口随后修复。最终全仓 `python/`、`scripts/` 回归 **431 passed + 5 subtests passed**，其中 WO-0015 **127 项通过**。首次全仓在未修改的 WO-0014 40 ms deadline 测试失败，已保留原始日志；单独复核通过，随后完整复跑全绿。未改动该旧测试或其代码。新包 PC offline / Mock 通过，均不是板端 DMA 证据。

独立 Worker 审查了门控与 HOLD 路径。Step 1 的初态检查由一次补充只读快照强制执行；失败时记录 terminal-failure，且本地调度器必须存在 Step 1 acceptance 才能发单帧命令。本次未生成 Step 1 acceptance。未知在途 DMA 仍须保留缓冲/进程、不得释放或复位；本次没有进入 DMA，在途风险为否。

## 工件、部署与 boot 身份

新部署目录 `/home/xilinx/wo0015_first_board_retry3_20261015`，由 xilinx 上传并以 xilinx 解压，配置及执行文件 UID=1000，后续硬件命令使用 sudo。不存在前次 root 解压导致的配置覆盖失败。整个新包 SHA256SUMS 在板端逐项核对，退出时再次实测核心文件摘要一致。

boot 全程为 `822a9c29-37c0-438c-81c4-da998d57073b`。新 manifest `84c27012ec038d48267128af08deb9685eb1bb9a5fdb1a79e5da064083788312`。BIT/HWH 保持原锁定字节：

| 工件 | 板端退出时实测 SHA-256 |
|---|---|
| wo15.bit | `a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420` |
| wo15.hwh | `bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6` |
| board_once.py | `921ee6aae276b53f68c359decdfadf188e67667514edfecac3e6f84c09bb0422` |
| board_preflight.py | `4c62fd37e5b6fa6c349a58ad7c2dc49b49e0f890b77b07400d304616c7a005d0` |

新包 ZIP：`C:\Users\POP\.codex\worktrees\wo0015-design-20261015\material-counter\data\results\wo0015-retry3-two-tier-bundle-20261015.zip`；SHA-256 `d990f2fcbd3d735cb00d01793b2b488acb60555e19ec5a6ffc7a1ef644a36002`。本次源码修改仅为 CMA/Tier 2 与相关测试/说明，功能 RTL、BIT/HWH、Golden、打包入口和完成/数据判定不变；包内 boot 配置按此前与本次批准绑定，源码默认历史 boot 未改。原包、原离线包和全部历史结果逐项复核无覆盖。

## Step 1 实测

1. Tier 1 静态结果 `STATIC_TIER1_PASS`，source=MEMINFO_IOMEM_VERIFIED。所有文件摘要、boot、进程 FD/maps、manager 均通过。
2. `overlay-only` 调用一次，实际下载一次，返回 `OVERLAY_LOAD_PASS`。仅执行器读取两个签名。随后一次补充快照读取 Checker/Packer 和 DMA CR/SR/地址/长度，未写 DMA 寄存器。
3. 实测 Checker signature **0x574F3134**、Packer signature **0x574F3135**。RTL 没有独立版本寄存器；没有伪造版本读数。

| 项目 | MM2S | S2MM |
|---|---|---|
| DMACR | 0x00010002 | 0x00010002 |
| DMASR | 0x00000001 | 0x00000001 |
| Run/Stop（CR bit 0） | 0 | 0 |
| Halted（SR bit 0） | **1，门控失败** | **1，门控失败** |
| Idle（SR bit 1） | 0，首次传输前本次规则允许 | 0，首次传输前本次规则允许 |
| Err | 0 | 0 |
| IOC | 0 | 0 |
| SA / DA | 0 | 0 |
| LENGTH | 0 | 0 |

初态快照的 errors 为 `MM2S Halted != 0`、`S2MM Halted != 0`。没有通过补写 Run=1 或 reset 把现场改成期望状态。上述停止只证明未满足本轮初始就绪要求，**不能认定 DMA 传输发生故障，因为没有启动传输**。

Checker status/error、packet_done/error、输入字节/握手/TLAST、像素/SOF/EOL/checksum/累计输出计数均为 0。Packer status=0、accepted_pixels=0、sent_beats=0、error=0。完整快照在 `raw-registers.json` 与 `initial-state-result.json`。

Overlay 命令从传输发出到退出总耗时 **20.485000 秒**，包括 PYNQ 导入与前后审计；实际 Overlay.download() 调用未单独计时，不能把这个数值称为纯加载耗时。内核原始日志包含 fpga_manager 写入 wo15.bin 和 xclbin 装载事件，保留于后检与 dmesg 文件。执行器 dmesg 增量审计为 PASS；厂商驱动的原始 skip-kind 等消息完整保留，未改写为“零日志”。

## Step 2 / Step 3 与 Tier 2 板端证据

| 阶段 | 调用次数 | 结果 |
|---|---|---|
| Step 1 Overlay | 1 | Overlay/签名 PASS；组合 Step 1 因 Halted FAIL_STOPPED |
| Step 1 初态只读快照 | 1 | INITIAL_STATE_FAIL_STOPPED |
| Step 2 单帧 | 0 | NOT_RUN |
| Step 3 三帧 | 0 | NOT_RUN |

未调用 `pynq.allocate()` 申请 TX/RX，故没有实际缓冲 phys_addr，也没有本次“分配前/中/释放后”的 Tier 2 板端样本。未下发帧、未回收数据，Golden **未比较**；不能填入零差异结论。Mock 中的模拟地址、CMA 数值和三帧计数没有冒充本节实测。

## 环境与退出资源检查

| 快照点 | CmaTotal（KiB） | CmaFree（KiB） |
|---|---|---|
| 首次只读连接 | 131072 | 72612 |
| 新包静态审计 | 131072 | 66200 |
| Overlay 执行器加载前准入 | 131072 | 65476 |
| 加载后初态 MMIO 快照 | 131072 | 55832 |
| 退出只读审计 | 131072 | 72772 |

这些是系统/驱动全局余量快照；本次没有用户 TX/RX 分配周期，不能据此认定本次 DMA 泄漏或已验证分配回收。首次至退出净变化 160 KiB；没有臆测变化原因。

退出 boot 未变，FPGA manager=`operating`、flags=`0`。root 级设备 FD/maps 归属扫描 findings=[]、denied=[]；板端短时 Python 检查正常退出，SSH 与本地凭据窗口均已关闭。没有本次 TX/RX 对象需要释放。退出 Ping 返回码 0。没有触发不确定 DMA HOLD，也没有强杀可能持有 DMA 缓冲的进程。

Guard 现场保留：`overlay-only-consumed.json`、`overlay-only-pass.json`、`terminal-failure.json`。terminal stage=`initial-state`，reason=`MM2S Halted != 0; S2MM Halted != 0`，身份绑定相同 boot/manifest/BIT/HWH。没有单帧或三帧 consumed Guard，未删除、改名或改换 Guard 路径。

## 结果路径、分支与后续边界

完整目录：`C:\Users\POP\.codex\worktrees\wo0015-design-20261015\material-counter\data\results\wo0015-first-board-retry3-20261015`。

主要文件：`summary.json`、`verification-report.md`、`static-check` 对应的 `request-002-stdout.txt`、`offline-result.json`、`overlay-only-result.json`、`initial-state-result.json`、`raw-registers.json`、`guard-snapshot.json`、`exit-os-result.json`、`initial-dmesg.txt`、`postflight-dmesg.txt`、`request-001` 至 `request-005`、`failure-readonly-*`、`full-offline-final.*`、`evidence-sha256.json`。

原目录 HEAD `7a868c070024bdc29cdeb057fbec9ccaf94ef160`；worktree HEAD `b7d8be05caff7da3ad2235617b43b591daa5e5fc`。保持未提交、未合并、未暂存，git diff --check 通过。正式 WO-0015 文件原为未跟踪，实际变化由 before 文件、原包对比 `.diff` 与 `scope-audit.json` 留证，未用空 tracked diff 隐藏脚本修改。

本轮已结束硬件推进。若后续要从当前 Run/Stop=0、Halted=1 状态建立运行态，需要队长单独裁决允许的 DMA 控制寄存器初始化与现有 terminal/consumed Guard 处理；本轮没有执行这些额外操作，也没有把首次 Halted=1 自动豁免为 PASS。
