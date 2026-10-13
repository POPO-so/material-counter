# WO-0012 · 特征/二值帧传输接口技术草案

状态：**Proposed，未批准、未接入生产**。不是 `docs/02_interfaces.md` 的修订版。
保留原字段位宽，提出序列化、量化、错误位及epoch补充；后两项属于明确的接口变更待审批。
首版320×240，FF前景/00背景、Morph旁路；640×480不属于当前草案，8位行坐标不足。

## 1. 输入与帧标签

输入Binary8 AXI4-Stream：TDATA[7:0]，TUSER[0]=SOF，TLAST=EOL，valid/ready成功握手才推进坐标。
无握手时run/坐标/标志不推进；满行后可停收处理，禁止承诺每拍持续接收。
末像素仍计入run，EOL关闭未结束run；第240行EOL派生EOF，关闭全部剩余对象。
输出不能在完整帧验证前提交计数用特征，否则晚到的错误会污染累计计数。

初次板级文件回放采用单帧job：PS在idle时设置原输入frame_id与epoch，SOF时锁存；
timestamp取该模式入口SOF握手时PL计数器值，不称为曝光时刻或墙上时间。
逐job保留原帧号间隙，不能以“PS收到的第几个帧”重编号。
真实连续视频另需采集端SOF原帧计数及匹配的标签FIFO/丢帧协议，现有RTL不具备此标签链。
CSR位置和跨时钟握手待后续明确，不擅自给正式接口C分配寄存器地址。
100MHz时32位timestamp约42.95s回绕，PS按模2^32差值和epoch辨别重置；不假设它永不回绕。

## 2. Region逻辑契约

- bbox为全帧、0起始、包含端点 `(x_min,x_max,row_min,row_max)`，面积为实际FF像素数，不是bbox面积。
- 内部面积17位、sum_x/sum_y各25位，防止完整76800像素帧回绕；只输出16～65535面积记录。
- 未满16面积的组件可在关闭时过滤；原始row-run和活动槽容量检查发生在此之前。
- 大于65535拒收整帧，不截断/饱和；记录宽度>24沿用bit0疑似合并，触边bit1，二维bit2=1，peak=0。
- 宽度24是当前软件参考门限，未实物标定，也不能识别所有小宽度粘连。
- 质心建议整数最近值、半值向上：`q=(sum + area//2)//area`；每轴误差≤0.5像素。
  原接口仅给位宽，未给取整细节，本规则必须由人确认，不默默修改计数线或迟滞。
- 记录按组件关闭顺序；同一行多个关闭组件及EOF剩余槽按slot升序。无PL排序网络。
  PS不得假定x顺序或物体身份；黄金集合对照独立按几何排序，字节对照采用明确关闭顺序。

## 3. 特征包的字节与拍布局

草案标识 `WO12-FEATURE-v0`，小端，每记录16字节，不含GT ID或跟踪ID。
首版 RECORD_CAP=32，每帧N=0～32；AXIS32，每记录4拍，TKEEP=1111。

| 记录字 | bit布局（低位→高位） |
|---|---|
| word0 | x_min[9:0]，x_max[19:10]，centroid_x[29:20]，高2位零 |
| word1 | area[15:0]，peak[23:16]，row_min[31:24] |
| word2 | row_max[7:0]，centroid_y[15:8]，flags[23:16]，高8位零 |
| word3 | 全零保留 |

帧尾同为16字节，在实际接收长度的最后16字节（位置由长度确定）：

| 帧尾字 | 布局 |
|---|---|
| word0 | frame_id32 |
| word1 | timestamp32 |
| word2 | total_fg32 |
| word3 | seg_count[7:0]，frame_flags[15:8]，**候选新增epoch[31:16]** |

epoch16是新增技术建议，原受保护文档没有此字段。它利用对齐空间，不增加包长；
须走接口审批。若不批准，不能将此草案直接当作现行ABI，应另定重置/旧包隔离合同。
epoch由PS会话控制，模式/配置/硬重置切换后变更；16位将回绕，回绕前须完整停流、撤销DMA、排空并重建接收会话。

包长 `L=16*(N+1)`，N=0时仍16B；20记录336B；32记录528B。
特征TLAST只在帧尾word3握手时置1，不能把输入每行EOL直接转为特征包EOF。
输出stall时data/keep/last稳定，状态只在握手后退休。帧尾先固定再开始发送。
首版只需简单DMA最大包容量；64条可作为另一配置（1040B），必须双方显式协商，不由8位seg_count自动授权。

## 4. 错误与原子接收（位定义均为待批准补充）

| frame_flags位 | 提议含义 |
|---|---|
| 0 | 输出记录容量溢出（沿用原语义） |
| 1 | 原始行程数量超运行限值 |
| 2 | 活动统计槽容量溢出 |
| 3 | 不同根ID汇合，候选不支持 |
| 4 | 组件面积超过65535 |
| 5 | SOF/EOL/行长/行数异常或被中断的帧 |
| 6～7 | 保留0 |

特征模式任何fatal都丢弃暂存的全部记录，只发送N=0的错误帧尾；PS发现非零frame_flags即拒收。
正常帧可以包含记录flags5的宽区域；它表示疑似合并，PS按现有参考暂停该Region计数，不能拆成多物体。
复位截断已发送包时可能没有TLAST，必须由DMA超时/取消和epoch恢复处理，不能期待一个完整错误帧尾必然送达。
多个错误的优先级/置位时序需RTL错误时序黄金模型；当前整帧PC模型不验证这些时序。

PS在DMA完成与缓存同步之后，校验：实际字节数、16B对齐、N≤配置容量、L=16*(N+1)、
期望epoch/frame_id、flags、保留位、坐标范围、bbox、正面积、质心范围和sum(area)≤total_fg。
不一致整帧丢弃并记原因，不能把已解析的前几个Region提交Tracker。
正常空帧调用Tracker的空观察；坏/掉帧保持原ID间隙，遵循缺失观察策略，不凭预测计数。
本轮codec实现此草案并拒绝partial/count mismatch/stale ID/epoch/error包；这不是已验证的DMA驱动。
codec允许不传expected_frame_id供离线检查；实际接收器必须显式传job的期望ID/epoch，
不能把离线解析成功当作原子接收成功。正式接口采用新增位前仍需PR+三人确认+ADR。

## 5. 临时Binary8帧模式（独立合同）

草案标识 `WO12-BINARY-v0`，不可当作接口B稀疏特征包。模式在idle时选择，同帧不能变更。
76800B逐行Binary8有效像素后跟16B帧尾，N=0，total_fg为实际FF数；总长76816B。
本轮codec验证编码、长度、00/FF、foreground统计和独立解码；PS只reshape前76800字节。
四像素一32位拍，最早像素放最低字节；输入SOF/EOL只作内部校验，TLAST等到最后帧尾word3。
坏帧若已流出部分像素不能撤回：终止/错误尾后以实际长度或flags拒收；复位无TLAST走超时恢复。
错误原始像素包的硬件排空恢复尚未实现；不得把完整数组codec测试当作AXI坏帧测试。

推荐第一板级POC用普通AXI DMA+明确帧包适配，单帧MM2S回放、S2MM接收，便于确定EOF/长度与输入保持。
未来连续视频可再比较VDMA原生SOF/EOL路径；本选择不冻结真实视频输入。
Simple DMA的接收长度须至少容纳最大包，完成后核对实际传输字节数，不能只读预分配数组长度。
参见 [PG021 S2MM_LENGTH](https://docs.amd.com/r/en-US/pg021_axi_dma/S2MM_LENGTH-S2MM-DMA-Buffer-Length-Register-Offset-58h)。

两种模式共用一S2MM的首版可人工切换；文件输入由PS保留原始帧，因此拒收特征后可离线PS兜底。
实时自动兜底需要**事先**影子保存同一Binary帧，或独立并行帧通路/DDR环形缓存；发现帧尾错误后无法取回已丢像素。
本轮不批准双DMA/tee/自动切换，也不声称临时路径已替代正式PL特征目标。

## 6. 缓冲与缓存合同

候选双缓冲，每槽 `FREE→DEVICE→COMPLETE_SYNC→CPU_READ→FREE`；generation/epoch/mode/配置版本绑定同一job。
设备拥有槽时CPU不得读写，CPU拥有槽时设备不得覆盖。old completion不交付已复用新槽。
TX读前clean/flush，RX提交前避免脏行回写污染，完成后invalidate/device-to-CPU同步后再读。
PYNQ分配器是否已经同步、是否cacheable/coherent必须按目标板软件版本核实，不能双重猜测。
基址、分配长度与stride按总线/cache line对齐；若暂按64B对齐，特征最大528B分配576B，二值76816B分配76864B。
DMA提交最大长度不小于包长，解码只使用actual length，padding不是有效负载。
同步要求参考 [Linux DMA API](https://docs.kernel.org/core-api/dma-api-howto.html)。

EOF/超时、reset、epoch和模式切换时须先停新提交、取消/排空DMA、确认DEVICE所有权解除，再重建槽并加载新配置。
过载丢完整帧并留原ID间隙，不覆盖处理中槽；双缓冲不是无限缓存，也不能补救不可暂停摄像头突发。
缓存一致性、坏帧排空、持续FPS、DMA错误恢复与任何自动兜底仍待硬件/目标PS测试。
