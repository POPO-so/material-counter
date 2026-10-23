# WO-0015 冷启动未来审批草案

**NOT_APPROVED_DO_NOT_EXECUTE**

当前全局状态仍为 **STOPPED_PRECONDITION_UNVERIFIED**。用户说明板卡断电后仍未连接；本文件不构成批准，不在本轮请求立即连接。历史 ID `822a9c29-37c0-438c-81c4-da998d57073b` 已退役。

未来需按实际证据分别审议，任何一步均不自动批准下一步：

1. UART 与冷启动准备：先审阅 uart-design.md 和白名单，核验 RX-only/控制线隔离、USB 供电路径、驱动、固定串口身份与采集时长/容量/停止条件。板载 Micro-USB 是否满足物理 Zero-TX 当前未知。只有用户明确批准具体连接/上电/一次监听范围后才能打开设备；禁止扫描、TX、按键打断 U-Boot、登录、break、DTR/RTS 脉冲。若无法满足隔离，停止并修改离线方案。
2. B1-coldstart：另行核验 SSH host pin、普通用户身份、固定解释器与冻结源哈希，再申请仅六个 /proc 路径、七次只读事务的批准；不请求 sudo、/boot、MMIO、AFI、Overlay、DMA。成功只登记 `CANDIDATE_BOOT_ID_UNAPPROVED`，状态为 `STOPPED_CANDIDATE_BOOT_UNAPPROVED`。
3. 候选审阅：将原始 B1 结果、首尾 boot_id、mountinfo、主机解析结果交用户与 Gemini 审阅。本轮没有向任何外部审阅者发送消息。由用户显式批准新 ID 作为当前会话基线；空值、退役 ID、测试 UUID、日志推断或脚本输出均不能代替这项授权。
4. B2：仅在第 3 项完成、mountinfo 表明 `/boot` 只读后，另写绑定新批准 ID 的独立冻结提案并申请执行。现场读取前/后必须 `fstatvfs & ST_RDONLY`，同时保持白名单中全部 POSIX 安全门；可写则终止，不 remount。旧 v2 B2 绑定退役 ID，不可直接复用执行。

当前未登记真实新 ID（`approved_boot_id=null`），未解锁任何硬件动作。UART 日志即便出现候选 U-Boot/UUID/base.bit 字符串也仅是日志观察，不证明镜像身份、AFI 正确性、DMA 或板级通过。启动自身可能写 SD；目标是监听器无主动发送带来的额外操作，不能承诺整卡零写入。
