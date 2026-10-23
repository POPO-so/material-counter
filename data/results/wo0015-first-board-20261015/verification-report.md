# WO-0015 首次上板核验报告

生成时间：2026-10-15T17:00:17.096334+08:00（Asia/Shanghai）。

最终裁决：**FAIL_STOPPED**。停留在 **Step 1：Overlay 加载前的 boot 身份准入**。

批准文档 `docs/verification/wo-0015-first-board-request-20261015.md` 明确要求固定 boot；本次正式授权沿用该门控。严格指纹 SSH 认证成功，但实测 boot 不符，因此立即停止上板流程。未上传或解包上板包，未加载 Overlay，未导入板端 PYNQ，未进行 MMIO、CMA 分配或 DMA。Step 2 和 Step 3 未执行。本次没有重试、修改执行器、改写 manifest、删除 Guard、复位 DMA 或重启板卡。

## 板端环境与退出检查

| 项目 | 加载前准入 | 只读退出检查 |
|---|---|---|
| boot_id | `822a9c29-37c0-438c-81c4-da998d57073b` | `822a9c29-37c0-438c-81c4-da998d57073b` |
| 申请锁定 boot_id | `cb087bbb-932d-4a6e-9600-98dd1c7e5f8d` | 不匹配，拒绝推进 |
| CmaTotal | 131072 KiB | 131072 KiB |
| CmaFree | 74780 KiB | 74740 KiB |
| FPGA manager state / flags | operating / 0 | operating / 0 |
| WO-0015 Guard 文件 | 空 | 空 |

内核：`Linux pynq 6.6.10-xilinx-v2024.1-g3c0eca68c652 #1 SMP PREEMPT Sat Apr 27 05:22:24 UTC 2024 armv7l`。

SSH 实测指纹：`SHA256:TB0FfGjfMzgZUv/reJgTpeq/AM68sMCpGC5xm6Pit7o`；固定 known_hosts 摘要保持不变。批准解释器路径存在。Overlay 加载次数为 0，加载耗时不适用；`initial-readonly-transport.json` 的 2.062000 秒仅为只读准入命令耗时。

本次没有申请 CMA 缓冲，因此没有待释放的本次 TX/RX 缓冲。两次快照间 CmaFree 变化 -40 KiB；这是观测到的系统余量变化，未确定原因，不能据此认定本次发生分配或泄漏。退出时复用交付执行器中原样提取的只读 `audit_process_ownership` 函数扫描 `/proc/*/fd` 与 maps：findings=[]、denied=[]，没有发现 FPGA 设备占用进程。板端短时只读 Python 检查正常退出；常驻系统 Python/Jupyter 服务未被终止。`remote_bundle_directory_exists=false`。退出 Ping 返回码 0。初始及退出原始 dmesg 已保存；它们仅提供系统现场，不构成 DMA 结果。本地两个凭据窗口对应的控制进程也已关闭，见 `local-console-cleanup.json`。

## 工件身份

| 工件 | 本地实测 SHA-256（离线准入通过） | 板端实测 SHA-256 |
|---|---|---|
| wo15.bit | `a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420` | 未测：boot 门控失败，未上传 |
| wo15.hwh | `bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6` | 未测：boot 门控失败，未上传 |

ZIP 本地实测：`873d245ad92926b00e9f57b2793220ab14dca0c287f49c213d7af5d36dd14879`。

Manifest 本地实测：`817d09ac508e07ab9c156541a0e39cf8c71d361a7dfaaba67951729dd907c674`。包内与 worktree 两份执行器源码均与锁定 manifest 摘要相符。`local-offline-result.json` 为 OFFLINE_PREFLIGHT_PASS、board_accessed=false、pynq_imported=false，仅是 PC 离线证据。

## 三阶段结果

| 阶段 | 执行/裁决 | DMA 状态及寄存器 | Checker / Packer / Golden |
|---|---|---|---|
| Step 1 | 准入失败，Overlay 命令执行 0 次 | 未读；MMIO 次数 0 | 签名与版本未读；未建立 Overlay PASS 收据 |
| Step 2 | NOT_RUN，命令执行 0 次 | 未读、未启动 | 无统计；0 帧回收；Golden 未比较 |
| Step 3 | NOT_RUN，命令执行 0 次 | 未读、未启动 | 无统计；0 帧回收；Golden 未比较 |

没有“零差异”或 DMA 通过结论。本次拒绝属于硬件操作前的 boot 身份门控，不是 FPGA/DMA 功能失败。

## 文件与分支状态

完整结果目录：`C:\Users\POP\.codex\worktrees\wo0015-design-20261015\material-counter\data\results\wo0015-first-board-20261015`。

主要证据：`initial-snapshot.json`、`postflight-snapshot.json`、`initial-dmesg.txt`、`postflight-dmesg.txt`、`events.jsonl`、`controller-error.txt`、`*-dispatch.json`、`*-stdout.txt`、`*-stderr.txt`、`summary.json`、`evidence-sha256.json`。

原目录 HEAD：`7a868c070024bdc29cdeb057fbec9ccaf94ef160`；worktree HEAD：`b7d8be05caff7da3ad2235617b43b591daa5e5fc`。两处 tracked diff 均为空；原有未跟踪交付及工作状态保留。本次只新增本结果目录，没有提交、合并或暂存。实际 before/after 状态见 `main-git-*.txt` 与 `worktree-git-*.txt`。

若要在当前 boot 上开展硬件验证，需要先复核新 boot 的板端基线并另行明确授权 boot 绑定；本轮不会自行修改锁定文档、manifest 或工件来继续。
