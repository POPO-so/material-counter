# G2.5 监测增量决定（Manager，2026-10-30）

本构建沿用已过 G2 的 RLE/HDMI/HP0/HP1/HP2、地址和时钟拓扑，原制品保留。为实现用户剩余计划要求的 RLE 活动/传输/反压测量，在现有候选 pipeline AXI-Lite 控制窗口中增加监测寄存器。数据包、ROI/阈值/形态学、DMA 所有权和既有地址不变；Route 与正式接口不冻结。

监测在 RLE `aclk` 50 MHz 域累计：总时钟、`busy || input_window` 活动周期、输入/输出握手 beat、输入/输出 `valid && !ready` 反压周期。均为 64 位 modulo 环绕；只在 `aresetn` 清零，单帧 start 不清。

`0x3c` 写值 1 将六个计数一次性锁存；同地址读序号为 32 位 modulo，每次 snapshot 加 1。锁存允许在 busy 时执行，不修改帧配置，不影响 AXIS 握手。读 `0x40/44`、`0x48/4c`、`0x50/54`、`0x58/5c`、`0x60/64`、`0x68/6c` 依次为六组低/高半字。软件先后检查序号一致，计算 delta 时做 64 位 modulo；本寄存器只存在于新制品，必须先核对 BIT/manifest 身份，禁止在旧 BIT 上尝试发现 ABI。

RTL 已在原实现上看到新增测试以 `AXI write rejected 0000003c` 失败；实现后八个原 640×400 场景和新增 snapshot/beat/反压/忙时锁存/锁存稳定性断言通过。仍需新制品综合、实现、时序/CDC/DRC、完整 pipeline 网表回归及板端重新准入。旧 G2 的通过只归属于旧 BIT，不自动转移到此构建。

Ruling：使用同域锁存而非跨域 mailbox，因为此 wrapper 的 AXI-Lite 和数据通路均在同一 50 MHz `aclk`；GP0 到该接口的时钟转换由原 AXI interconnect 负责。新 counters 不新增 CDC；不能用该推断替代构建 CDC 报告。
