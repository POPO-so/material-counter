# WO-0015 Run-only 恢复 Retry 1 核验报告

日期：2026-10-15（Asia/Shanghai）

最终裁决：**FAIL_STOPPED，停留于 Step 2 的分配后、DMA 启动前 CMA 计量门控**。Run-only 和历史 Guard 归档均 PASS，Step 1 为 **PASS (Resumed via Run-only)**；单帧 DMA 未启动，三帧未执行。

## 环境与锁定身份

| 项目 | 实测/执行结果 |
|---|---|
| boot_id（前后相同） | `822a9c29-37c0-438c-81c4-da998d57073b` |
| CmaTotal | 131072 KiB |
| 首次 CmaFree | 57000 KiB |
| 最终 CmaFree | 54448 KiB |
| FPGA manager | operating / flags=0 |
| Overlay 新加载 / 软复位 | 0 / 0；无本次加载耗时 |
| SSH | 一次连接；严格指纹匹配；隐藏输入密码 |
| 退出占用扫描 | findings=[]，denied=[] |
| 退出 Ping | exit code=0 |

板端已部署文件由适配器重复对原始文件字节计算 SHA-256；最终取证再次复核一致：

- `wo15.bit`：`a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420`。
- `wo15.hwh`：`bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6`。
- manifest：`84c27012ec038d48267128af08deb9685eb1bb9a5fdb1a79e5da064083788312`。
- 原 `board_once.py`：`921ee6aae276b53f68c359decdfadf188e67667514edfecac3e6f84c09bb0422`。
- 原 `board_preflight.py`：`4c62fd37e5b6fa6c349a58ad7c2dc49b49e0f890b77b07400d304616c7a005d0`。

Tier 1 来源为 `MEMINFO_IOMEM_VERIFIED`：实测 System RAM 为 `[0x00000000,0x20000000)`，CmaTotal=131072 KiB，执行前 CmaFree 达阈值；安全包络 `[0x00100000,0x20000000)`。这不是实测 CMA 子区间。完整解析、PYNQ 已加载 BIT 路径/hash、manager、进程占用和内核增量证据在 `remote-evidence/run-only-result.json`、`step1-acceptance.json`。

## 阶段 1：Run-only PASS

三份 Guard 的摘要直接在板端对 `read_bytes()` 计算，并回传 Base64 原始字节由主机再次核验，未执行 strip 或文本换行转换。摘要全部与授权历史值一致。

新受限脚本在独立目录 `/home/xilinx/wo0015_runonly_resume_retry1_20261015` 部署，启动器固定摘要核对全部源码、锁配置，再执行；适配器重复检查源码与原部署工件摘要。请求命令已逐项核对等于生成模板。离线边界/模型/采集/启动篡改拦截/证据原样导出共 **63 passed**。

仅执行两次控制写入：S2MM `0x40400030 = 0x00010003`，随后 MM2S `0x40400000 = 0x00010003`。两份写入意图在写入前落盘，新增固定 `run-only-init-consumed.json` 保留。

| 寄存器 | Run-only 前 | Run-only 后 | 最终取证 |
|---|---|---|---|
| MM2S_DMACR / 0x00 | 0x00010002 | 0x00010003 | 0x00010003 |
| MM2S_DMASR / 0x04 | 0x00000001 | 0x00000000 | 0x00000000 |
| MM2S_SA / 0x18 | 0x00000000 | 0x00000000 | 0x00000000 |
| MM2S_LENGTH / 0x28 | 0x00000000 | 0x00000000 | 0x00000000 |
| S2MM_DMACR / 0x30 | 0x00010002 | 0x00010003 | 0x00010003 |
| S2MM_DMASR / 0x34 | 0x00000001 | 0x00000000 | 0x00000000 |
| S2MM_DA / 0x48 | 0x00000000 | 0x00000000 | 0x00000000 |
| S2MM_LENGTH / 0x58 | 0x00000000 | 0x00000000 | 0x00000000 |

后检两通道 Halted=0、Err=0；SR=0 表示本次尚无 IOC/Idle 完成状态。初次 Idle=0 被允许；这不是帧传输完成。地址、LENGTH、Checker/Packer 计数为零。Run-only 前后与归档前的 dmesg 增量健康检查 PASS。

## 阶段 2：历史 Guard 归档 PASS

旧 terminal 原始字节以独占创建方式复制到 `/var/lib/wo0015-s2mm/history/20261015-retry3-initial-state-terminal-failure.json`，fsync、重读核对原始 SHA 后，才移除原 canonical terminal。属主 uid/gid=0/0，mode=0600 保留。

旧标记 SHA-256：`662e03d871f7275ed47bec0c302440a94dbc4e2fa35d499bce49a44d8decdeda`；最终板端历史文件再次验证相同。原 Overlay consumed Guard 与 Overlay PASS 收据保持字节不变。恢复收据绑定旧 Overlay PASS、新 Run-only PASS、原 terminal hash 与归档位置。

## 阶段 3：Step 2 单帧在 CMA 计量门控 FAIL_STOPPED

原 `board_once.py` 只调用一次，退出码 3。两次 allocate() 已返回，随后执行既有分配计量检查：要求 CmaFree 至少下降两缓冲页对齐总量 304 KiB。实际为：

| 采样 | CmaFree / KiB |
|---|---|
| 初次连接 | 57000 |
| Run-only before | 40472 |
| Run-only after | 40288 |
| 归档前复核 | 39456 |
| Step 2 before | 37884 |
| Step 2 after_mm2s | 38612 |
| Step 2 after_s2mm | 38612 |
| Step 2 after_release | 38612 |
| 最终 OS 采样 | 54448 |

`37884 - 38612 = -728 KiB`，未达 `>=304 KiB`，因此抛出 `CmaFree did not show the required allocated-page decrease before DMA`。全局 CmaFree 在采样期间上升；它不能证明每个缓冲区的底层分配归属，也不能据此推断泄漏。没有修改或放宽这一检查。

失败发生在读取 `physical_address` 与执行地址 Tier 2 之前，因此 **MM2S/S2MM 物理地址未记录，Tier 2 地址校验未执行**。目标缓冲长度为 TX230432 / RX76800 字节。没有执行 Packer ARM、SA/DA/LENGTH 或此帧的 DMACR 写入。

执行器字段：`dma_start_attempted=false`、`transfer_started=false`、`dma_may_be_active=false`、`frames=[]`。S2MM 回收字节为 0，Checker/Packer 本次接受帧数和 EOF 次数为 0；没有 Golden 比对，不能报告“零差异 PASS”。本次结果属于软件准入失败，不构成 DMA 传输结果。

## 阶段 4：Step 3 未执行

Step 2 未 PASS，未创建三帧请求，未生成 dma-three consumed Guard，未分配三帧缓冲区，回收字节与 Golden 结果不适用。

## Checker/Packer 全寄存器只读快照

此 IP 无单独版本寄存器；Checker WO14 / Packer WO15 签名为设计身份。0x00 为既有控制值 1，0x04/0x60 为签名，其余寄存器在三个快照中均为零，包含输入字节/拍/TLAST、packet_done、像素/SOF/EOL/校验和、Packer 状态/像素/拍/错误。

| offset | Run-only 前 | Run-only 后 | 最终取证 |
|---|---|---|---|
| 0x00 | 0x00000001 | 0x00000001 | 0x00000001 |
| 0x04 | 0x574f3134 | 0x574f3134 | 0x574f3134 |
| 0x08 | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x0c | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x10 | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x14 | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x18 | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x1c | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x20 | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x24 | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x28 | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x2c | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x30 | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x34 | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x38 | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x3c | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x40 | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x44 | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x48 | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x4c | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x50 | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x54 | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x58 | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x5c | 0x00000000 | 0x00000000 | 0x00000000 |
| 0x60 | 0x574f3135 | 0x574f3135 | 0x574f3135 |

## 失败 Guard 与资源收尾

最终 dmesg 增量另由已部署同源检查函数离线核对，结果见 `exit-kernel-health.json`；该检查仅评价内核日志，不改变 Step 2 失败裁决。

单帧执行器已重新建立 canonical `terminal-failure.json`，stage=`dma-single`，reason 为上述 CMA 计量失败；最终取证保留该原标记，没有覆盖历史归档。Run-only 与单帧 consumed Guard 均保留，三帧 absent。

| Guard 路径 | 最终原始 SHA-256 |
|---|---|
| `/var/lib/wo0015-s2mm/overlay-only-pass.json` | `ee3d9ba6462962657bbd43807302387af16d4fbec34ee65f909a385886802d96` |
| `/var/lib/wo0015-s2mm/terminal-failure.json` | `1bac1e556839b37a9deb42fa229801f21637904b2ad4a288026a8618dc9dc9ea` |
| `/var/lib/wo0015-s2mm/overlay-only-consumed.json` | `c333975de186464bc3db9809c7762d7d7115cfc73f8b17f8e8672e3349efa37f` |
| `/var/lib/wo0015-s2mm/dma-single-consumed.json` | `1585844bceb4bca82cee2f4a2be14916b2fee847a26fb7f28c207e9234fcf862` |
| `/var/lib/wo0015-s2mm/run-only-init-consumed.json` | `ac53e6acba00404c86acd7c2e5dae1729ef0a62228538f857b8104d6b866df18` |
| `/var/lib/wo0015-s2mm/history/20261015-retry3-initial-state-terminal-failure.json` | `662e03d871f7275ed47bec0c302440a94dbc4e2fa35d499bce49a44d8decdeda` |

失败清理路径依次调用 `free(s2mm_buffer)` 和 `free(mm2s_buffer)`；`after_release` 是两次 free() 均返回后记录。最终 root /proc FD/maps 扫描 findings=[]、denied=[]，单帧进程已退出，未进入 FAIL_HOLD。SSH 已正常关闭，本地凭据控制器控制台已结束，无硬件进程被强制终止。

**CmaFree 未精确恢复至单帧分配前值**（37884→38612 KiB；最终全局采样54448 KiB），因此不声称满足“CMA 原值恢复”验收，也不以全局回升替代单缓冲物理回收证明。当前证据支持两次正常释放调用与无残留设备句柄。

## 归档与复核

各阶段均只调用一次，无自动重试、重载、复位或现场修改原执行器、RTL、BIT/HWH。新脚本与证据只写入独立恢复目录。原开发目录和 worktree 未提交、未合并、未暂存；前后 Git 状态已保存，diff --check 通过。

三个历史索引复核文件数：`{"wo0015-first-board-retry3-20261015": 114, "wo0015-runonly-proposal-20261015": 16, "wo0015-runonly-resume-20261015": 34}`，全部未变化。

远端命令总耗时（含 Python/PYNQ 初始化与采集，不能解释为纯 Run 写入耗时）：`{"deploy": 1.8589999999967404, "run-only": 16.703000000008615, "archive": 16.03200000000652, "dma-single": 17.797000000005937}` 秒。

完整本地结果目录：`C:\Users\POP\.codex\worktrees\wo0015-design-20261015\material-counter\data\results\wo0015-runonly-resume-retry1-20261015`。

完整远端结果目录：`/home/xilinx/wo0015_runonly_resume_retry1_20261015`；root 文件均通过 sudo 只读 Base64 导出至本地 `remote-evidence/` 并逐文件核验 SHA-256，未通过文本改写或权限放宽获取。

主要证据：`initial-snapshot.json`、`request-002-stdout.txt`、`request-003-stdout.txt`、`request-004-stdout.txt`、`exit-os-result.json`、`remote-evidence/`、`offline-final.xml`、`summary.json`、`evidence-sha256.json`。当前保持 FAIL_STOPPED，后续操作需另行裁决，不能直接重试 Step 2/3。
