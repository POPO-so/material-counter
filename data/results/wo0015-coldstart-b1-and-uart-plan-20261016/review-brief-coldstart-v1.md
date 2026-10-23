# WO-0015 coldstart v1 审阅摘要

**NOT_APPROVED_DO_NOT_EXECUTE** · **STOPPED_PRECONDITION_UNVERIFIED**

本轮仅增量离线预案、注入核心与定向测试。联合 **77 passed**；364 封板、320 历史索引、389 非敏感历史与 BIT/HWH 哈希均未变；两 checkout 的 HEAD/tracked/staged 差异基线未变。无提交、合并或板端接触。

请审阅 `coldstart-command-audit-and-whitelist.md`、`coldstart-approval-request.md`、`uart-design.md` 和冻结 B1 文本。新 B1 合法新 UUID 仅标候选，退役 ID 不允许；首尾一致且只读 /proc 成功仍停在 `STOPPED_CANDIDATE_BOOT_UNAPPROVED`。没有自动 B2 或硬件授权。

旧 B2 安全门可作为未来提案的合约参考，但固定旧 boot_id，不能直接执行；新基线须用户在用户/Gemini 审阅后显式批准，再单独申请绑定新 ID 的 B2。mountinfo 与现场 ST_RDONLY 双门，任何失败即停。

Zero-TX 分两层：软件接口拦截已 mock 验证；USB-UART 驱动/硬件开口瞬态未验证。固定流控 False 不能证明物理零脉冲，需物理 RX-only/控制线/供电隔离方案及独立批准。不得承诺整卡零写入。

真实板端 boot_id、挂载状态、启动日志与身份对应关系仍未知。下一步仍是审阅本离线提案；本文件没有发起任何执行。
