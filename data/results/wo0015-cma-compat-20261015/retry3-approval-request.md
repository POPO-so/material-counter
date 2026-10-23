# WO-0015 Retry 3 条件式批准申请

状态：**待补证；当前不应启动硬件验证**。本文件是具体申请，不是执行许可。本轮硬件禁令保持有效，Retry 3 未执行。

## 锁定交付

- boot：`822a9c29-37c0-438c-81c4-da998d57073b`。
- ZIP：`C:\Users\POP\.codex\worktrees\wo0015-design-20261015\material-counter\data\results\wo0015-retry3-cma-bundle-20261015.zip`。
- ZIP SHA-256：`4b684418c159f0d0e8d7eb96ba92ecd0355e4fad618c3c535080400ae7a6949d`。
- Manifest SHA-256：`f91323b0d8bfc3f0ed831cebc806d01d2caa901dfbcf0a7f6521db183029e974`。
- board_once.py SHA-256：`604a267256857f5372f2d659080cc8c1a5e1767e4824631d42c702380e63e7f5`。
- board_preflight.py SHA-256：`6325f6d475890264fa970906cb6f0cba4c1a7e0d925e8c87c04ce1c325d62932`。
- BIT SHA-256：`a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420`。
- HWH SHA-256：`bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6`。

离线已通过：421 tests + 5 subtests；其中 WO-0015 为 117 tests，新 CMA 回归为 61。新包 offline / Mock 均通过，但不构成硬件证据。

## 必须先满足的条件

当前 probe 没有精确 CMA 元数据，也没有 cma=。当前元数据回放仍为 REJECTED_MISSING_CMA_SOURCE。必须先补齐可验证的精确区间，或满足已审查的完整候选回退条件，再离线复核范围、保留区冲突、CmaTotal、HP 边界。补证方式如果超出本轮五类只读命令，应明确其权限和操作边界；本轮没有挂载、配置修改或分配探测。

只有在该条件通过、队长另行批准 Retry 3 后，才将新包上传到独立目录，以同一普通用户解压安装所有配置，再用 sudo 运行硬件命令，避免 root 解压/普通用户覆盖的不一致。boot、文件摘要、Guard、环境、CMA 任一不匹配即停止；不得用清理 Guard 或新 manifest 重试。

## 拟批准顺序（当前禁止执行）

1. Step 1：Overlay 仅加载一次，核对 Checker/Packer 身份；补充一次只读初始寄存器快照，按原批准的 Halted=0、Err=0 门控。原 RTL 无独立版本寄存器，记录实际签名。
2. Step 2：仅前步 PASS 后，单帧执行一次，RX=76800 字节/19200 words，完整 Checker/Packer/DMA 和 Golden 零差异核验。
3. Step 3：仅单帧及后检 PASS 后，三帧执行一次，总 RX=230400 字节，累计/分帧计数与全量 Golden 核验。

不修改功能、工件或判定标准。任何异常停止，无自动重试；DMA 可能在途时保持对象/进程、不得释放或复位。正常完成后释放缓冲并核对环境/CMA，独立归档，未汇报前保持未提交、未合并。

请在 CMA 证据条件补齐并复核后批准此具体三步申请。**当前快照下不能用批准代替缺失的物理范围证据，故本申请暂缓执行。**
