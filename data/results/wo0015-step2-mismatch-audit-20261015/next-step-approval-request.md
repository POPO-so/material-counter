# WO-0015 下一阶段审批申请（本文件不是执行授权）

本轮所有工作均离线完成，实际数据差异根因未最终确认。历史板端保持FAIL_STOPPED；不使用旧状态代替实时准入。

## 建议先批准 A：仅 AFI1 运行时位宽只读核验

一次严格SSH会话，核对boot、BIT/HWH、现有Guard和无进程占用，不归档/移除Guard，不加载Overlay、不分配CMA、不启动DMA、不写任何寄存器。只读取AFI1 AFI_RDCHAN_CTRL（0xF8009000）与AFI_WRCHAN_CTRL（0xF8009014），记录原始值及bit0；与锁定HWH的HP1=32比较。bit0=1为32，bit0=0为64。boot或工件不匹配即停止。

## 根据 A 的证据另批 B：修复方案

若AFI1实际不是32位，说明当前PS运行配置与这版HWH声明不同。先评审最小、保留其余位的AFI1位配置方案及安全切换条件，或选择与镜像一致的64-bit DMA memory/HP1方案。不要运行包含时钟/复位等其他写入的完整ps7_init/post_config序列。

若选择新BD：仅经批准调整DMA c_m_axi_s2mm_data_width=64和PS HP1=64，S2MM stream维持32，仍是76800字节/19200个32-bit word；核验DMA内部宽度适配、AXI协议转换、WVALID/WREADY/WSTRB/地址/拍步进。使用新构建目录，重新仿真、实现时序/DRC、导出BIT/HWH并形成新锁定摘要，再另行申请一次上板。当前未改BD或生成新BIT。

若AFI1本就32位，不基于该候选修改硬件。保留原工件，先评审一次加强取证单帧：完整RX/Golden落盘、奇偶统计、失败前后DMA/Checker/Packer快照，同时记录真实Cache方法选择、coherent元数据及同步前后内容。Cache调用不要盲目增删。

## 另批 C：一次诊断捕获

打包新取证执行器，固定新源码摘要和续执行配置，保留历史与Guard审计。证据放root管理的新目录；更新回传器，使诊断子目录的.bin和.json均按原始字节导出并核验hash。仅单帧一次，任一差异/错误停止，不自动进入三帧。落盘失败按证据HOLD保留缓冲；不确定DMA则保持原DMA HOLD。

A、B、C不是本轮已经批准或执行的操作。建议先只批准A取得关键缺失证据，再决定B/C，避免在缺乏RX样本与运行位宽读数时猜改。
