# WO-0015 首次上板 retry1 核验报告

时间：2026-10-15T17:16:26.676499+08:00（Asia/Shanghai）。最终裁决：**FAIL_STOPPED**。

停留位置：**Step 1 部署授权配置，Overlay 加载之前**。本次新 boot 身份准入已通过。包上传与解压成功、BIT/HWH 板端摘要匹配，但解压命令以 root 运行，导致配置文件归 root 所有；随后以 xilinx 身份通过 SFTP 覆盖 `board_preflight.py` 授权常量时收到 `PermissionError: [Errno 13] Permission denied`。这是代理部署工具的权限安排错误。没有重试上传、现场改权限或修补后继续，没有执行三条上板阶段命令。

本次没有加载 Overlay、导入板端 PYNQ、访问 MMIO、分配 CMA 或启动 DMA。Step 2 / Step 3 未执行。该结果属于部署失败，不能认定 FPGA 或 DMA 功能失败。

## 授权绑定与工件边界

队长在本聊天中明确重授权 boot 为 `822a9c29-37c0-438c-81c4-da998d57073b`。原 ZIP 保持不变；仅在本结果目录的 `authorized-deployment/` 中更新 `board_preflight.py` 的一个授权 boot 常量、manifest 的 boot 字段与执行器摘要，以及 SHA256SUMS 派生摘要。`board_once.py`、RTL、BIT/HWH、packet、Golden、打包逻辑、固定 Guard、功能判定标准均未修改。申请单已记录本次授权及具体命令。PC 离线与 Mock 通过，仅为离线证据。

本地新 manifest SHA-256：`ceac16e28b968bc28d3e4f30828402447c9dbe8c566f9800a789478a435a3d8b`。

板端 manifest 仍为原包摘要 `817d09ac508e07ab9c156541a0e39cf8c71d361a7dfaaba67951729dd907c674`，授权配置未生效。因此没有执行板端 offline、overlay-only、dma-single 或 dma-three。退出只读实测确认三个配置文件全部保持原包字节，首个失败文件未被截断或修改。

原 ZIP SHA-256：`873d245ad92926b00e9f57b2793220ab14dca0c287f49c213d7af5d36dd14879`。

| 工件 | 板端上传后与退出时实测 SHA-256 | 判定 |
|---|---|---|
| wo15.bit | `a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420` | 与授权摘要一致 |
| wo15.hwh | `bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6` | 与授权摘要一致 |

板端目录保留为 `/home/xilinx/wo0015_first_board_retry1_20261015`，用于追溯部署失败；未删除、改名或覆盖旧实验。

## 环境与资源

| 项目 | 准入 | 只读退出检查 |
|---|---|---|
| boot_id | `822a9c29-37c0-438c-81c4-da998d57073b` | `822a9c29-37c0-438c-81c4-da998d57073b` |
| CmaTotal | 131072 KiB | 131072 KiB |
| CmaFree | 74736 KiB | 73976 KiB |
| FPGA manager state / flags | operating / 0 | operating / 0 |
| WO-0015 Guard | 空 | 空 |

Overlay 加载次数 0，加载耗时不适用。原包解压与哈希命令耗时 2.188000 秒，这不是 Overlay 加载耗时。

退出 root 级 `/proc/*/fd` 与 maps 扫描 findings=[]、denied=[]，未发现 FPGA 设备占用；扫描复用原交付 `audit_process_ownership` 函数。短时板端只读 Python 进程正常退出。本次没有申请任何 TX/RX CMA 缓冲，因此没有本次待释放对象。CmaFree 两次快照差 -760 KiB，记录为系统余量变化，不能据此推断本次泄漏。原始进程表、dmesg 和 UID/权限证据均已保留。退出 Ping 返回码 0；SSH 严格指纹 `SHA256:TB0FfGjfMzgZUv/reJgTpeq/AM68sMCpGC5xm6Pit7o`，known_hosts 未改变。完成后的本地凭据窗口已关闭，见 `local-console-cleanup.json`。

## 三阶段结果

| 阶段 | 本轮结果 | DMA / Checker / Packer / Golden |
|---|---|---|
| Step 1 | boot 通过、BIT/HWH hash 通过；配置部署失败，FAIL_STOPPED | 未加载，未读寄存器；无签名、初始 DMA 状态或计数实测 |
| Step 2 | NOT_RUN，阶段命令 0 次 | 未发送/回收帧；Golden 未比较 |
| Step 3 | NOT_RUN，阶段命令 0 次 | 未发送/回收帧；Golden 未比较 |

原 RTL 无单独版本寄存器，后续如执行只读身份核验，应记录 WO14 Checker / WO15 Packer 签名。本轮没有相应 MMIO 证据，没有“零差异”或 DMA PASS 结论。

## 结果文件与分支保持

完整结果目录：`C:\Users\POP\.codex\worktrees\wo0015-design-20261015\material-counter\data\results\wo0015-first-board-retry1-20261015`。

主要文件：`summary.json`、`authorization-rebind.json`、`boot-constant-only.diff`、`local-boundary-qa.json`、`initial-snapshot.json`、`postflight-snapshot.json`、`initial-dmesg.txt`、`postflight-dmesg.txt`、`controller-error.txt`、`events.jsonl`、`request-001-stdout.txt`、`command-templates.json`、`evidence-sha256.json`。

原目录 HEAD：`7a868c070024bdc29cdeb057fbec9ccaf94ef160`；worktree HEAD：`b7d8be05caff7da3ad2235617b43b591daa5e5fc`。两处 tracked diff 为空，diff --check 通过；未提交、未合并、未暂存。本次新增结果目录并更新原本未跟踪的申请单，保留原有工作。旧 `wo0015-first-board-20261015` 中 32 个已索引证据文件逐一复核，摘要未变。

本轮停止于部署错误。后续若重新授权继续，应先使用权限一致的部署方式安装预先审查的三个配置文件，再核对完整 manifest/工件身份；不得直接运行当前板端旧 boot 绑定的执行器。此次不会自行重新执行任何步骤。
