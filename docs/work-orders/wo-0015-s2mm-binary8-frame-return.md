# WO-0015 第一阶段：AXI DMA S2MM Binary8 整帧回传设计

日期：2026-10-15，Asia/Shanghai。**状态：队长已批准离线RTL、XSim及50MHz综合实现；本轮禁止所有板端连接或操作。完成状态以本轮验证报告为准。**

2026-10-15追加裁决：WO0014以既有收官档案正式封存，不恢复Guard、不补测；S2MM Memory Map改为32-bit，经1×1 AXI Interconnect接32-bit HP1。以下与此有关的旧64-bit提案已同步为批准配置。

## 1. 队长决策与阶段边界

本次直接授权作为架构决策来源：11月4日唯一主交付采用路线A；PS采集/读帧→CMA DDR→MM2S→PL图像流水线→S2MM→CMA DDR→PS连通域、跨帧跟踪计数。PL主时钟50 MHz。路线B（PL硬件连通域特征提取）冻结开发。Binary8固定320×240、背景00、前景FF、76800 bytes；32-bit小端打包为19200 beats，每拍4像素。

本文件记录新决定，不将历史README/ADR里的候选状态冒充新结论，也不将路线冻结写成系统已实现。受保护范围/接口/路线文件的正式同步作为后续明确修改清单。当前最小闭环先复用Gray2Binary输出；形态学串接为同接口后续配置，ROI真裁剪不进入此固定全帧契约。100 MHz只保留历史静态证据。

## 2. 数据链路与统一接受点

```text
DDR --MM2S memory64/stream32--> WO0014 Packet Adapter
    --RGB888/24,SOF,EOL--> RGB2Gray --Gray8/8,SOF,EOL-->
Gray2Binary --Binary8/8,SOF,EOL--> [后续可选同尺寸Morphology]
    --> 同一成功握手点 ──> Checker旁路监听（保留AXI-Lite）
                     └─> Frame Packer 8→32, keep=F, 唯一EOF
                         --> S_AXIS_S2MM/32
                         --> M_AXI_S2MM/32 --> 新HP1 AXI Interconnect
                         --> PS S_AXI_HP1/32 --> DDR
```

MM2S的230432 bytes含现有16-byte包头、230400-byte RGB payload和16-byte包尾，共57608拍；这些元数据不回传到Binary8缓冲。Adapter输入TLAST是包尾，内部图像TLAST继续是EOL。S2MM输出不再携带图像SOF/EOL侧带，PS依据固定几何恢复行列。

推荐保留现有只读Checker，不添加axis_broadcaster、不改变Checker为透传模块。新WO0015 wrapper将原 `pipe_m_tready=drain_enable` 改为：

```verilog
pipe_m_tready = drain_enable && packer_s_tready;
packer_s_tvalid = pipe_m_tvalid && drain_enable;
// Checker继续观察 pipe_m_tdata/tuser/tlast/tvalid，以及pipe_m_tready。
```

两个分支在同一时钟沿接受同一像素；Checker本身没有ready输出。不能将Checker视为always-ready独立消费者，或只把TVALID复制给两个有独立TREADY的消费者，否则会丢失/重复数据。TREADY反压经Packer→Gray2Binary→RGB2Gray→Adapter→MM2S传播。所有寄存输出在VALID=1且READY=0时保持DATA/KEEP/LAST和VALID不变；drain停顿不丢弃已呈现数据。

Checker现有0x00～0x48寄存器地址与语义保持，包括0x24为**输入MM2S包TLAST累计数**，0x3c为**当前输出帧EOL数**。Checker统计到Packer接受完76800像素不代表S2MM写回完成，必须另验EOF握手和DMA状态。

## 3. axis_s2mm_frame_packer接口与状态

拟新增 `fpga/experimental/wo0015/axis_s2mm_frame_packer.v`，固定参数WIDTH=320、HEIGHT=240，非法几何在elaboration时拒绝；不提供运行中可变长度。

| 接口 | 方向/位宽 | 定义 |
|---|---|---|
| aclk / aresetn | 输入1/1 | 50 MHz，同步复位；正常帧间不reset DMA或流水线 |
| arm | 输入1 | 空闲且无遗留输出时单周期接受；每帧由PS显式武装 |
| s_tdata/tuser/tlast | 输入8/1/1 | Binary8、首像素SOF、每320像素EOL |
| s_tvalid / s_tready | 输入1/输出1 | 输入仅在握手后计数 |
| packet_done / packet_error | 输入1/1 | 来自本帧Adapter包校验；分别锁存，不能当图像EOF直接转发 |
| m_tdata / m_tkeep / m_tlast | 输出32/4/1 | 4像素小端；所有拍keep=F；仅最后拍EOF |
| m_tvalid / m_tready | 输出1/输入1 | 连接DMA S_AXIS_S2MM |
| busy/done/fault/error_code | 输出1/1/1/8 | done锁存到下次合法arm；fault只由明确复位清除 |
| accepted_pixels/sent_beats | 输出17/15 | 可通过新AXI-Lite只读寄存器获取 |

状态：IDLE→WAIT_SOF→COLLECT→WAIT_LAST_COMMIT→DRAIN_EOF→DONE；任意格式错误→FAULT。arm清零本帧计数及packet校验锁存；busy/待发输出/FAULT时拒绝arm。WAIT_SOF收到首个成功握手必须SOF=1；之后SOF必须为0。严格校验每个像素只为00/FF，每行第320个像素有且只有一个EOL，总240行。非法拍可以被接受后锁存错误，但不得参与合法字节打包；停止接受新像素，不开始下一帧。

17-bit像素数表示0～76800；9-bit列计数0～319；8-bit行计数0～239；2-bit byte_lane，15-bit输出握手数表示0～19200。计数饱和/比较后停止，禁止回绕。像素计数只在 `s_tvalid && s_tready` 增加；输出数只在 `m_tvalid && m_tready` 增加，不能按时钟数或VALID周期数计数。

对第k拍（k=0～19199）：`TDATA={pixel[4k+3],pixel[4k+2],pixel[4k+1],pixel[4k]}`。一组4字节收齐后才提交完整32-bit输出寄存器；没有空余输出槽则反压输入。可以采用一个24-bit组装寄存器+一个32-bit输出槽；第一版允许组间气泡，优先保证简单可审计。禁止使用只透传EOL的通用宽度转换器作为EOF转换。

最后4像素满足geometry且字节数=76800后，暂存最后word，等待本帧packet_done已锁存且无packet_error。Adapter当前输入在payload结束后仍能接收包尾，不能将包尾接收ready错误依赖于这个暂存word的下游接受，否则死锁。packet_done也可能早于最后像素经过流水线，必须锁存而非只看同周期脉冲。MM2S输入末尾校验失败即锁存FAULT，不签发成功EOF。

在合法完整帧中：非末拍LAST=0；末拍只有在先前已发19199拍时呈现LAST=1；该拍在反压期间LAST保持1，这是同一拍，不算多个EOF。第19200次输出握手后sent_beats=19200、done=1，绝不输出第19201拍，未重新arm不得接纳下一帧。

必须准确界定“严格定长”保证：**每个正常完成的包恰好76800 bytes、19200次握手、1个EOF。** 如果上游永久缺像素或下游永不ready，硬件无法凭空保证完成；不得用提前TLAST或伪造像素填充凑成成功。异常时可能已有部分无EOF数据进入DMA，锁存错误/软件超时并保留现场，不报告完整包、不自动reset/retry。若要求所有畸形输入均零字节进入DMA，则需整帧存储后提交，属于另一个资源/架构选择，不纳入最小流式Packer。

## 4. BD修改清单（拟议，尚未执行）

复制WO0014工程生成逻辑到新的 `scripts/wo0015/create_s2mm_bd.tcl`，新建WO0015工程/输出目录，保留旧RTL、Tcl、BIT/HWH及结果。WO0014既有18-bit长度不回退。

```tcl
# 拟议的DMA配置差量，实际应用后必须逐项readback和HWH核对
CONFIG.c_include_s2mm 1
CONFIG.c_m_axi_s2mm_data_width 32
CONFIG.c_s_axis_s2mm_tdata_width 32
CONFIG.c_include_s2mm_dre 0
CONFIG.c_s2mm_burst_size 16
CONFIG.c_sg_length_width 18
CONFIG.c_addr_width 32
CONFIG.c_include_sg 0
CONFIG.c_micro_dma 0
```

保留MM2S memory64/stream32、MM2S→hp0_protocol→HP0原连接。批准独立启用 `PCW_USE_S_AXI_HP1=1`、`PCW_S_AXI_HP1_DATA_WIDTH=32`；新建1×1 `hp1_fabric` AXI Interconnect，处理AXI4到AXI3转换。DMA M_AXI_S2MM→hp1_fabric/S00_AXI→hp1_fabric/M00_AXI→ps7/S_AXI_HP1。不能将两个AXI主口裸并接在原单输入协议转换器上。若改共用HP0，必须增加2输入仲裁fabric，属于备选，本方案不采用。

连上DMA m_axi_s2mm_aclk、HP1_ACLK、hp1_fabric各ACLK及新wrapper/packer到同一个FCLK_CLK0=50 MHz，统一由现有reset_pl提供复位；HP1 issuecap控制按HP0模式接常量。为Data_S2MM分配512 MiB DDR地址窗，并保留Data_MM2S原窗；软件仍须验证实际Linux RAM/CMA可达范围，不能靠旧PYNQ psddr.size=256 MiB假设做裁决。

新wrapper新增32-bit AXIS master到DMA S_AXIS_S2MM，原AXI-Lite地址0x43c20000及0x00～0x48不变；拟在0x4c起添加packer_status（busy/done/fault）、0x50 accepted_pixels、0x54 sent_beats、0x58 error_code、0x5c arm。新增寄存器不能改变老地址读写副作用；这是待实现WO0015 wrapper接口，不修改WO0014文件。

队长本次已批准在XSim全部通过后进行50MHz综合、实现与BIT/HWH导出。上板仍须另行批准，本轮禁止SSH/MMIO/DMA/Overlay。

## 5. PS双通道状态机与CMA管理

遵循[PG021 Simple DMA顺序](https://docs.amd.com/r/en-US/pg021_axi_dma/Direct-Register-Mode-Simple-DMA)。寄存器偏移均相对DMA基址：

| 状态 | 操作与通过条件 |
|---|---|
| PRECHECK | 明确新boot/独占/Guard/健康/50MHz BIT-HWH来源；simple DMA、32-bit地址、18-bit长度、两个数据口存在；验证CMA区间实际映射、连续、4-byte对齐、收发不重叠且在可访问DDR内 |
| ALLOCATE_ONCE | 帧循环前静态分配发送230432与接收76800 byte的uint8 buffer，保存物理地址与实际分配/页对齐长度；分配失败停止 |
| PREPARE | 写入本帧合法RGB包（frame_id递增），mm2s_buf.sync_to_device；s2mm_buf填非二值哨兵如A5并sync_to_device，之后直到接收完成禁止CPU写该缓冲；记录Checker累计基线和本帧期望 |
| ARM | Packer空闲且无故障，写新arm；确保drain_enable=1，开始前无遗留数据 |
| START_RX | 写S2MM_DMACR(0x30).RS=1，期限内等Halted=0且无错误；写DA(0x48)；最后只写一次LENGTH(0x58)=76800 |
| START_TX | 写MM2S_DMACR(0x00).RS=1，期限内等Halted=0且无错误；写SA(0x18)；最后只写一次LENGTH(0x28)=230432 |
| WAIT_BOTH | 有限期限轮询0x04和0x34；最终两端Halted=0、Idle=1、无DMA错误、各自本帧新IOC完成；Packer done=1、fault=0、19200拍；Checker当前帧统计匹配，包错误增量=0 |
| VERIFY_RX | 回读S2MM_LENGTH=76800；s2mm_buf.sync_from_device，reshape(240,320)，逐像素和独立RGB→Gray→Binary Golden比对，差异数必须0且值只00/FF；Checker checksum=255×前景数、SOF=1/EOL=240/像素76800，输入包完成/累计值用本帧增量验收 |
| NEXT | 仅VERIFY通过后才复用buffer、清本帧已处理IOC（W1C，不误写错误位）、下一帧arm/接收/发送；每帧S2MM_LENGTH与MM2S_LENGTH各一次，禁止出现多帧同时复用同槽 |
| RELEASE | 全部帧结束、双端确认idle且无在途事务后，正常路径freebuffer一次；对象/底层BO引用释放证据和CmaFree前后快照都要落盘，返回值不足以证明释放干净 |
| FAIL_HOLD | 任一错误/超时/不一致立即停派发，不retry/reset/reload、不释放可能仍被DMA访问的buffer；保存原始状态、地址、计数和最后阶段，由后续明确授权处理 |

PG021规定RS后Halted应解除；**首次LENGTH之前Idle允许为0**，不能重引入WO0014首帧错误判据。后续同通道复用必须先确认上一帧Idle=1。只对32-bit地址写SA/DA；不无条件访问未配置的MSB寄存器。错误位与IOC按PG021/配置逐位解析，不能只比较某个历史DMASR常量。

[S2MM_LENGTH](https://docs.amd.com/r/en-US/pg021_axi_dma/S2MM_LENGTH-S2MM-DMA-Buffer-Length-Register-Offset-58h) 是接收容量和完成后的实际字节数；写76800不会自动修正过早的TLAST，因此要同时核对实际length、packer和内存。双端Idle也不能替代像素验证。

最小阶段用**一对固定收发buffer**，串行三帧复用，禁止帧循环allocate/free；这不是帧间重叠的真正Ping-Pong。若后续要PS处理与DMA重叠，再预分配两套tx[2]/rx[2]，以CPU_FREE→DMA_OWNED→CPU_READY→CPU_FREE管理槽所有权，均在循环外分配。当前不引入第二套以增加CMA占用。仅两方向数据总量307232 bytes；按4KiB页向上取整为228+76=304 KiB，不等于PYNQ/XRT总开销。严格CMA前后门保留，差值不为零就报告失败，不擅自用容差豁免。

PS CCL/跟踪接收接口为二维uint8 Binary8数组，必须复用既有8-connected、同阈值/面积/ROI/关联协议；最小闭环验收先做逐像素，不把离线TP19当板级计数结果。GT只在离线评分阶段使用。

## 6. XSim和软件离线验收计划

拟新增 `fpga/sim/wo0015/tb_axis_s2mm_frame_packer.sv`、`tb_axis_dma_s2mm_pipeline.sv` 及 `scripts/wo0015/run_sim.tcl`，由自检scoreboard判定，不靠波形目测。50 MHz仿真；每次用新结果目录保存源码SHA、seed、命令、日志、mismatch明细和统计。

1. 单帧完整320×240：校验76800个字节逐像素、小端lane、19200握手、keep全F，前19199拍无LAST、最后仅1次LAST；全0、全FF、稀疏、边缘和棋盘图案。
2. 三帧不同frame_id/不同内容：连续输入与帧间间隙两组；每帧独立arm；帧边界清组装状态；无跨帧粘连、重复或丢字节。复用buffer的DMA模型接收顺序和实际长度逐帧记录。
3. 输入TVALID随机气泡、S2MM_TREADY随机反压，固定seed 1/7/42/20261015；特别在第4像素、EOL和最后beat停顿，断言所有待发信号稳定、计数只随握手推进。长停顿超过一行也必须正确。
4. EOL不能泄入EOF：内部每帧240 EOL，DMA只有1 EOF；构造直接透传EOL的负例证明scoreboard会捕获320-byte提前结束。
5. 未arm、缺SOF、重复SOF、提前/缺失EOL、额外像素、非00/FF、输入截断、包尾checksum错误、packet_done早到/晚到：不得签发错误成功EOF或第19201拍；超时/FAULT保存计数，不自动恢复。
6. reset在空闲、半word和最后word反压时的断言；只在仿真验证复位清理，不据此授权板端错误恢复；drain_enable切换不丢握手。
7. Wrapper级Checker旁路比对和0x00～0x48兼容；packer最后beat需包尾commit但不阻塞Adapter包尾，检查无死锁。后续Morphology MODE=0/1/2全部需独立Golden、行相位/底边flush和多帧回压用例，不能用bypass通过代替erosion/dilation。
8. BD validate/readback/HWH检查两个DMA通道、18-bit长度、32-bit streams、MM2S memory64/S2MM memory32、HP0/HP1、地址窗、50MHz及所有复位；带DMA simulation model验证接收先启动、过早LAST/长度不符会被驱动拒绝。普通AXIS sink仿真不冒称真实DMA IP已验证。
9. PS寄存器Mock验证START_RX在START_TX前、两LENGTH各一次、首帧Idle=0准入、超时/错位/短包/像素差异fail-hold、循环中allocate调用数为0、failure不free未知在途buffer；所有旧相关回归保持。

进入后续硬件构建的最低门：上述自检零错误、逐像素零差异、三帧字节与EOF计数准确、Checker兼容、BD配置读回一致。XSim通过不等于综合/实现/时序/实板通过。

## 7. 已批准修改顺序与范围

拟议文件：仅新增WO0015 packer、wrapper、TB、BD/simulation Tcl与PS双通道驱动/Mock；复用既有adapter/RGB/Gray源码，原WO0014文件保持。先Packer自检，再wrapper/Checker联调，再BD生成与验证，再PS驱动Mock，最后交叉审阅证据。路线A与50MHz等已批准决定无需重复投票。

队长已批准进入编码/XSim采用：**独立HP1回写、Checker继续监听、第一阶段不插入Morphology/真ROI、一对固定收发buffer串行三帧**。同时确认后续把本次已批准决策同步到README/PROJECT_INDEX/路线/接口/ADR记录的文件范围。WO0014已正式封存，不再恢复或补测；新WO0015使用独立受控准入。

本轮执行RTL、XSim、PS离线Mock和50MHz综合实现；结果与工件见独立验证报告，绝不上板。
