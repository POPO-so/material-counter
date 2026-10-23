# WO-0015 首次上板 Retry 2 核验报告

生成时间：2026-10-15T17:24:34.980841+08:00（Asia/Shanghai）。最终裁决：**FAIL_STOPPED**。

停止于 **Step 1：运行时 CMA 物理范围准入，实际 Overlay 加载之前**。权限修复、配置更新、板端静态核对和 offline 均通过。`overlay-only` 命令仅调用一次，返回 `OVERLAY_ONLY_REJECTED`、exit code 2：

```text
PreflightError: CMA reserved-memory tree is unavailable: /proc/device-tree/reserved-memory
board_accessed=false
pynq_imported=false
```

因此实际 Overlay.download() 为 0 次，未打开 MMIO、未分配 CMA、未启动 DMA。Step 2 / Step 3 未执行。没有自动重试、改写 CMA 检查、替换范围来源、复位 DMA 或重载 Overlay。本次是软件运行时准入拒绝，不是 Overlay 或 DMA 硬件功能失败。

## 权限修复与配置更新

仅在明确批准的 `/home/xilinx/wo0015_first_board_retry1_20261015` 上执行一次 `chown -R --no-dereference xilinx:xilinx`。执行前检查精确路径、所有子路径均位于目标目录内、无符号链接、boot 正确、Guard 为空、BIT/HWH 摘要正确。修复后核对 UID/GID=1000、文件属主读写位；随后使用 xilinx SFTP 成功安装已审查的三个配置文件。静态检查确认配置文件 UID 均为 1000，manifest、执行器授权常量和实时 boot 均为 `822a9c29-37c0-438c-81c4-da998d57073b`。

本次沿用 Retry 1 已离线审查的配置，manifest SHA-256 为 `ceac16e28b968bc28d3e4f30828402447c9dbe8c566f9800a789478a435a3d8b`。只涉及已批准的 boot 常量、manifest 和派生摘要；RTL、功能逻辑、BIT/HWH、packet/Golden、打包逻辑、固定 Guard 与测试判定标准均未修改。原 ZIP 与两个历史证据目录的全部已索引文件摘要复核无变化。

## 板端环境与 CMA

| 项目 | 首次只读准入 | 配置后、Overlay 命令前 | 退出只读检查 |
|---|---|---|---|
| boot_id | `822a9c29-37c0-438c-81c4-da998d57073b` | 相同 | `822a9c29-37c0-438c-81c4-da998d57073b` |
| CmaTotal | 131072 KiB | 131072 KiB | 131072 KiB |
| CmaFree | 73764 KiB | 73564 KiB | 73916 KiB |
| FPGA manager | operating / flags=0 | operating / flags=0 | operating / flags=0 |
| FPGA 设备占用 | 阶段前静态扫描通过 | findings=[] / denied=[] | findings=[] / denied=[] |

内核：`6.6.10-xilinx-v2024.1-g3c0eca68c652`。`CmaTotal=131072 KiB` 表明系统报告 128 MiB CMA，不能据此确定其物理起止地址或证明 HP 可达性。本轮退出 CmaFree 相对首次快照变化 152 KiB；本次没有分配任何缓冲，不据此推断泄漏或分配行为。

只读现场证实 `/proc/device-tree` 存在并指向 `/sys/firmware/devicetree/base`；两个路径下的 `reserved-memory` 均不存在。`/proc/iomem` 报告 System RAM 为 `00000000-1fffffff`，本轮未从中确定 CMA 子区间。执行器 `board_preflight.py` 的 `read_cma_ranges()` 在目录缺失时直接拒绝；未进入 PYNQ 导入、Overlay 或 consumed Guard 创建。因此三个硬件 Guard 均为空，未删除或更名任何 Guard。

退出阶段只读取 Linux 文件、进程表与 dmesg；没有 MMIO。FPGA FD/maps 归属扫描通过，未发现占用。板端短时 Python 检查已正常退出，无本次待释放 CMA 对象。已关闭本地凭据窗口并结束 SSH。退出 Ping 返回码 0。

## 工件实测摘要

以下值在 chown 前后、配置后与退出时核对一致：

| 工件 | 板端实测 SHA-256 | 判定 |
|---|---|---|
| wo15.bit | `a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420` | 与授权值一致 |
| wo15.hwh | `bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6` | 与授权值一致 |

新 manifest：`ceac16e28b968bc28d3e4f30828402447c9dbe8c566f9800a789478a435a3d8b`。

`board_once.py`：`e21ee59ffb39400d6b2855bb12efc051aacc7fec2606440ed710db5145642094`（功能执行器原样）。`board_preflight.py`：`8337f5728b73a5acc6c10554e8ba127d10a9cdb21cce0778f03603820c539e91`（仅已批准的 boot 常量更新）。全包 `SHA256SUMS.json` 逐文件校验通过。

## 三阶段结果与证据边界

| 阶段 | 命令调用次数 / 结果 | 寄存器、统计和 Golden |
|---|---|---|
| Step 1 | 1 次，OVERLAY_ONLY_REJECTED；实际加载 0 次 | CMA 范围门控失败，未读签名、版本、DMA 状态或 Checker/Packer |
| Step 2 | 0 次，NOT_RUN | 未启动 MM2S/S2MM；回收 0 字节；Golden 未比较 |
| Step 3 | 0 次，NOT_RUN | 未启动 MM2S/S2MM；回收 0 字节；Golden 未比较 |

Overlay 加载耗时不适用。`request-005-transport.json` 的 4.844000 秒是被拒绝命令的总耗时，不能写作加载耗时。本轮没有 DMA 状态字、Checker/Packer 计数、唯一 EOF 或零差异结果。原 RTL 无独立版本寄存器；计划核对的身份为 WO14 Checker / WO15 Packer 签名，本轮未实际读取。

## 完整文件与分支

本地完整目录：`C:\Users\POP\.codex\worktrees\wo0015-design-20261015\material-counter\data\results\wo0015-first-board-retry2-20261015`。

核验报告：`verification-report.md`；机器可读裁决：`summary.json`；板端原始结果：`board-offline-result.json`、`overlay-only-result.json`、`exit-os-result.json`；全过程命令与日志：`request-001` 至 `request-005`、`failure-readonly-*`、`events.jsonl`；现场：`initial-dmesg.txt`、`postflight-dmesg.txt`、`proc-iomem.txt`、`proc-cmdline.txt`；完整摘要索引：`evidence-sha256.json`。

板端保留原部署目录，已修复的授权配置保留，没有任何硬件 Guard 或待释放 DMA 缓冲。原开发目录 HEAD：`7a868c070024bdc29cdeb057fbec9ccaf94ef160`；worktree HEAD：`b7d8be05caff7da3ad2235617b43b591daa5e5fc`。两处 tracked diff 均为空，git diff --check 通过，未提交、未合并、未暂存。本次仅新增 Retry 2 结果目录，历史证据保留。

后续需要先核实当前内核的实际 CMA 物理区间及可验证的数据来源，并审查执行器与该内核的兼容性；CmaFree 充足不能替代范围证据。本轮不修改执行器或绕过范围门控来继续。
