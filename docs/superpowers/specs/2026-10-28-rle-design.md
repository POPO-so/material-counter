# 640×400 Gray8 / RLE 软硬件分工设计

用户在 2026-10-28 批准开始优化；本记录确定本次实现边界，不替代项目 Route 正式签核。

## 数据链

PS 采集 MJPEG 并直接解码为 640×400 Gray8，不缩放、不先做整帧连通域过滤。MM2S 继续使用 Gray8 的四字头、像素和四字尾结构，几何改为 640×400，总长 256032 B。PL 实施 ROI、可配置灰度偏移（饱和 LUT 的等价实现）和阈值，支持可关闭的 3×3 二值形态学，输出逐行最大前景游程。PS 用原生编译实现进行 8 连通游程合并，保留面积、包围框和一阶矩，复用跟踪；仅对需要复杂拆分的候选重建局部掩膜。

现行 320×240 已验链路保留作独立证据基线；新的生产候选入口为 `scripts/rle/`，不得将未经板测的实现标为已经部署。正常输出 RLE，掩膜仅在调试或局部候选处理时重建。

## RLE_PACKET_V1

全部为小端 uint32，完整帧为 16 字头、N 个游程、P 个投影字、16 字尾。P=ceil((height+width)/2)，投影为先 height 个行计数、再 width 个列计数的 uint16，奇数末项补零。640×400、MAX_RUNS=16384 时最大 67744 B，小于当前 18 位 DMA 长度上限。

头字依次为 magic=0x31454c52、version=1、(height<<16)|width、frame_id、params_version、(roi_y0<<16)|roi_x0、(roi_y1<<16)|roi_x1（右下排他）、hw_timestamp_lo、hw_timestamp_hi、flags=1（包含投影）、P、MAX_RUNS、threshold、signed_intensity_offset、morph_mode、reserved=0。

游程字为 `(row<<20)|(x_start<<10)|x_end`，坐标绝对、两端包含，高三位为零；按行及起点递增，游程必须最大化且互不相邻。支持 width<=1024、height<=512。

尾字依次为 magic=0x31444e45、frame_id、params_version、total_fg、total_run_count、emitted_run_count、status（bit0 overflow、bit1 input_error）、actual_bytes、前面所有头/游程/投影 uint32 字之和模 2^32、input_error_code、accepted_pixels、0、0、0、0、end_magic=0xc0dec0de。AXIS TLAST 仅在末字，所有 TKEEP=0xf。

游程达到上限后停止发游程，继续消费输入并完成统计和帧尾，不能静默截断为有效帧。零前景仍输出头、投影、尾。PS 必须以 DMA 实际接收长度切片，验证完整性、几何、身份、参数版本与统计；status!=0 不更新计数。帧号继续前进使跟踪感知缺帧。硬件时间戳为帧启动时 PL 时钟计数，不能代替相机采集时间。

## 配置与硬件

配置在帧启动时锁存；忙时拒绝重新启动和修改生效。ROI、threshold、intensity_offset、morph_mode、params_version 均可写可读。阈值比较为映射灰度 >= threshold，恒等偏移默认 0。形态学默认关闭，边界用零填充；启用后必须有黄金向量逐像素验证，不宣称普遍改善准确率。

S2MM 在 MM2S 之前启动。流背压必须稳定保持 data/keep/last，并仅在握手时推进。输入校验错误必须进入错误帧或安全故障状态，不能假完成。DMA 所有权不明确时保留 CMA 缓冲，沿用 FAIL_HOLD，不自动复位或重试。

## 验收

1. 协议对空帧、满帧、对角接触、孔洞、投影重叠、最大游程及溢出进行往返和损坏拒绝测试。
2. 原生 PS 8 连通区域与 OpenCV 统计对照，包括面积、坐标和质心；常态路径不得重建整帧掩膜。
3. RTL 自检覆盖输入气泡、输出反压、空帧、行尾游程、配置锁存、错误输入与有界溢出，及实际 640×400 帧；要求终端 PASS 和 watchdog。
4. Vivado 实现核对 50 MHz 时序、资源、DRC，并生成有源码哈希的匹配 BIT/HWH/manifest。
5. 板端先只读核对 boot、程序所有权和 DMA，再用合成帧逐像素还原对照；相机测试报告分阶段时间、游程分布、字节量和帧率。无人工真值不宣称物料准确率通过。

## 综合后差异的额外门禁

首次板测发现奇数行／列投影为零，而 RTL 行为仿真通过。小尺寸综合后功能网表仿真复现了该错误：函数内读取全局 RAM、同周期调用两次的描述在 Vivado 2026.1 生成了不一致结果。改成两个显式独立读口后，综合后仿真通过。后续构建同时要求 RTL 八组实际尺寸向量和综合后功能自检，不以时序／DRC 通过代替内容正确。
