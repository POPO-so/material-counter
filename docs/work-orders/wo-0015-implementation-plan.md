# WO-0015 离线实现执行计划

Goal: 50MHz、HP0读/HP1写、固定Binary8整帧返回；本轮不进行任何板端连接或操作。

本次队长批准覆盖原设计中的待批准项，并将S2MM memory接口改为32-bit，经AXI Interconnect接HP1。原WO0014证据封存；不修改其失败事实，不恢复Guard，不补跑。

- [x] 新Packer与新BD wrapper：保留旧Checker/寄存器，统一像素接受点，包尾验证后提交EOF，显式逐帧arm，无帧间硬件复位。
- [x] XSim：先证明自检能失败，再A单帧/B三帧/C随机反压/D异常；独立Python逐字节复核。记录精确seed/哈希/日志。
- [x] PS驱动：TDD/Mock、两个CMA分配一次、RX优先、无自动恢复、固定新Guard和新构建manifest准入；默认不接触硬件。
- [x] 仿真全通过后建立新50MHz BD，核对HP0/HP1、32-bit S2MM、18-bit长度；综合/实现/bit生成，报告时序/资源/DRC和工件SHA。
- [x] Manager审阅实际改动与测试、工件身份、上板申请精确命令。没有板级PASS声明。

文件边界：新增`fpga/experimental/wo0015/`、`fpga/sim/wo0015/`、`scripts/wo0015/`及对应WO0015报告/数据；旧RTL、旧构建工件和收官原始记录不覆盖。
