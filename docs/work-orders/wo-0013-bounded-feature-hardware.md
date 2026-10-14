# WO-0013 · 隔离DMA回放与有界特征硬件验证候选工作单

状态：**Proposed，未授权执行**。本工作单由WO-0012准备，不是已完成代码或上板证据。
平台PYNQ-Z2/XC7Z020CLG400-1，首版320×240，目标100MHz、30fps需实际验证。

## Phase A · 路线无关的单帧回放/传输POC

开工条件：负责人确认临时DMA方案、Binary帧协议/epoch/缓冲合同；新IP和接口通过项目review。
该阶段不选择Route，不包含生产区域/CCL后端，也不改变正式PS/PL职责。
Phase A仅采用已确认的**独立实验Binary协议**，不宣称修改或实现正式接口B/C。
若将epoch/错误位/CSR提案并入正式接口或生产接入，必须先有接口PR、三人确认和ADR；
独立实验批准不能替代该流程。新增IP仍需按MASTER_PLAN附-6说明理由并通过PR review。

候选文件清单如下；开工前核对是否已有同功能，避免重复开发。这些文件本轮均未创建。

| 阶段 | 计划文件 | 用途 |
|---|---|---|
| A | `fpga/rtl/experimental/axis_binary_frame_packetizer.v` | B8像素打包/帧尾/EOF与坏帧恢复 |
| A | `fpga/sim/wo0013/tb_binary_frame_packetizer.sv` | 随机反压/标志/复位self-checking TB |
| A | `scripts/wo0013/create_dma_replay_bd.tcl` | 隔离PS/DMA回放顶层重建 |
| A | `python/boardtests/wo0013/dma_replay_verify.py` | 实际buffer同步/长度/CRC/超时及epoch测试 |
| B | `fpga/rtl/experimental/axis_bounded_run_features.v` | 有界run/活动槽/精确矩与异常 |
| B | `fpga/rtl/experimental/axis_feature_packetizer.v` | 缓存记录/元数据/AXIS32帧包 |
| B | `fpga/sim/wo0013/tb_bounded_run_features.sv` | 像素→逐记录/字节/EOF检查 |
| B | `python/refmodel/experiments/wo0013_cycle_golden.py` | 握手级排空/错误优先级黄金模型 |
| A/B | `scripts/wo0013/run_rtl_verify.py`、`scripts/wo0013/run_ooc.tcl` | 新旧回归、Lint/综合证据 |

1. 先写握手级黄金TB：Binary8→32位包适配，元数据尾、pixel byte order、EOF、empty/error包。
2. 仅在隔离顶层配置PS+AXI DMA，MM2S单帧文件回放、S2MM接收Binary包；
   B8后端回放不冒充RGB→Gray→Binary全前端板测。源RGB888接口和原生产RTL保持原状。
3. TB覆盖随机反压、行尾/末行、SOF/EOL畸形、缺/重复标志、复位半包、旧epoch与模式切换。
   无握手所有状态不推进，完整帧76816B，部分帧只记录错误/丢弃，不调用Tracker。
4. 目标PS编写槽位/generation管理，验证TX/RX同步、actual length、超时取消、错误恢复。
   先做驱动模型，再以真实板软件版本核对cacheable/coherent及同步API。
   实际接收器必须从提交job绑定期望frame_id与epoch，禁止使用离线codec的可省略ID校验方式。
5. Vivado官方Skill/MCP运行编译仿真、Lint、OOC综合，再完整实现/XDC时序；
   查MCP结果是否fresh而非重复SKIPPED，保留日志/结构诊断和资源，不能以OOC代替实现收敛。
6. 上板已知帧CRC/逐像素一致，注入旧cache/未同步/旧完成回调，证明正确合同消除污染；
   记录板端DMA和PS处理延迟p50/p95/p99、队列水位、丢帧、连续运行FPS。暂不接真实相机。

## Phase B · Route相关的最小特征后端

额外开工条件：G1 R1+R2完成、正式Route结论、若Route B则PL实现ADR已由人批准，
接口草案完成PR与三人确认。Phase A通过不能替代这些条件。

1. 先写周期/错误优先级黄金模型和self-checking TB，再实现行run/固定槽/矩累加，不加union-find。
2. 建议缓存64 run/行，运行限制40，活动32、帧记录32；配置取决于本轮审批，不能用默认64物体代替。
   先关旧槽再生新槽；同ID分叉汇合，跨ID整帧拒收；EOF统一关闭/量化/发送。
3. 对H3全部487 PNG和孔/对角/U形/噪点/容量/面积极值做bbox、area、sum、flags、字节/拍比对。
   用关闭顺序生成包，无硬件x排序。错误帧不允许出现部分有效Region。
4. RGB→Gray→Binary→Morph旁路→候选后端在随机反压下集成；恢复原全部RTL回归。
   EOF flush、接收/处理/发送时间、最大stall和持续吞吐需XSim真实握手计数。
5. 共享乘法/串行除法和RAM读改写需真实综合证据；避免同slot多run更新旧值和大并行比较树。
   测试30fps稠密极值，检查默认row40与候选64下的时序/周期成本，不用理论频率替代。
6. PS解码后与软件特征黄金对照，再用原严格GT事件评分验证量化后的计数；
   不用取消困难序列或放宽时机窗口伪造提升，H3原12漏计仍须完整报告。
7. 经批准才与Phase A隔离顶层接通，在PYNQ-Z2上验证特征CRC/逐记录与计数。
   自动PS帧兜底需另确认影子帧保存/双通路预算；首版文件回放保留原输入供手工PS诊断。

## 验收/止损

输入采集源仍未确定，连续不可暂停输入的FIFO/CDC不由本单自动解决。
新核的BRAM/LUT/FF/DSP、实现WNS/TNS、时钟/IO约束、真实延迟和FPS独立记录；
未上板的条目标Not run。无法承接正常工作包线时停并回报，不强塞完整版CCL。
原RTL/环境/H3不做无关修改；受保护文档变更走已确认审批流程。
提交/合并/推送授权另行处理。本单现在不执行、不配置overlay、不写生产RTL。
