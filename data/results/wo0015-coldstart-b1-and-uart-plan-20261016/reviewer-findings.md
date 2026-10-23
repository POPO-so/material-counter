# Manager / Worker 离线审阅记录

**NOT_APPROVED_DO_NOT_EXECUTE**

独立 Worker 只读复核 B1、mountinfo、相关测试及旧 B2 核心；Manager 核对实际源文件、修复回归结果与最终联合测试。未执行远端草案、旧 B2 或真实串口传输。

## 已处理发现

1. 退役集合若为一次性 iterable，会在转换 tuple 后被再次遍历而漏掉旧 ID。新增回归确认旧实现失败；现只 materialize 一次，在保存的 tuple 上校验；旧 ID 仍在首项后拒绝。独立复核已确认修复。
2. geteuid 异常原先直接传播，与 invalid record 合约不一致。现仍为首个 OS 调用，但位于异常捕获中；异常 invalid/零打开，root 同样零打开。冻结进程包装器失败非零退出。
3. Manager 逐文件发现 UART 的 rtscts/xonxoff 与原测试同时错误设为 True。保留历史日志，先改断言观测 RED，再改实现为 False；dsrdtr 也为 False，最终另有 Manager 独立合约检查。
4. UART 原先 `read(-1)` 不限大小、只解析 BOOT.SCR basename。已改为默认 4096、入参范围 1..65536，增加完整 BOOT.SCR 来源行/路径观察与合成测试。
5. 独立复核又指出普通 mangled 属性可直接暴露 backend、返回字节长度未受请求限额约束。针对性回归与修复另记在 uart-boundary-red/green.txt；最终以联合测试和 Manager 实际代码检查验收。Python 同进程反射/原始注入句柄不是安全隔离边界，文档必须明确，不能据 API guard 宣称物理 Zero-TX。

## 不解锁的事项

- 旧 B2 全部 POSIX 门控保留，但 EXPECTED_BOOT_ID 仍为退役值；未来新基线必须另建增量冻结提案，绝不就地改旧模板或直接运行。
- 流控 False 仅为合约，当前封装不打开、不配置驱动；未来独立批准的适配器必须验证实际设置、超时、硬件 RX/control 隔离。
- UART 版本/UUID/FPGA 文本只是日志观察；不能证明加载完成、系统身份或板级正确。
- 没有真实新 boot_id、mountinfo、UART 捕获或 fstatvfs 证据；所有板端动作继续禁止。
