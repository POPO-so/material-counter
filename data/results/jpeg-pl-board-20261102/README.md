# JPEG PL 实机链路验收（2026-11-02）

报告：[board-run.json](board-run.json)
报告 SHA-256：2cac80251f9dbdcf2a0570ac94ea8e45dfff3974adcafba2b3117c11367e281

## 结果

- 状态：JPEG_PL_RUN_COMPLETED_OUTPUT_MONITORED；输入模式 raw_mjpeg_to_jpeg_pl，分辨率 640×400。
- PYNQ-Z2 实际采集并处理 120/120 个 MJPEG 帧，0 个无效 packet；端到端实测 11.31 fps。
- 每帧 PL 显示帧号与板载 HDMI 像素监视器读到的 frame/session 标记相符；全部逐帧记录 invalid=0、partial=0，像素及标记诊断均无错误。
- 测试结束后报告确认 hdmi_stopped=true、buffers_held=false。另一次只读状态确认 AXI DMA、HDMI VDMA 均 halted，没有运行中的本测试 runner。

## 合成正样本补测

使用 [positive-box-ycbcr422-dri40.jpg](positive-box-ycbcr422-dri40.jpg) 做单帧板测；该测试图含一个 40×40 高对比区域，JPEG 头符合 PL 解码器要求的 640×400、三分量 baseline 4:2:2、restart interval 40。fixture SHA-256：23e831640eab294b7fbe7e1e06e929646527acb3b40d2e24f36b524eddaf10da。

板端报告：[positive-output-probe-color.json](positive-output-probe-color.json)，SHA-256：e9e14df8139fe64c2ec6aac95f98053851830b8ed77d0fd6851b070adce7949f。PL 返回 1 个区域，边界框 (100,100)–(139,139)，面积 1600；overlay 的 displayed frame ID=1，HDMI 像素监视器也读到 frame ID=1，invalid=0、partial=0，诊断标志均为 false。退出时 DMA/VDMA halted、缓冲已释放。单帧样本的 total_count=0 是预期的跨帧计数行为，不作为计数准确率证据。

HDMI monitor 核验帧身份和扫描，不统计 mask/框的非零像素，也没有外接显示器肉眼观察。因此正样本证明的是 PL 检测结果已提交到匹配帧的 HDMI overlay 路径；overlay 的白色 mask 像素另有 [5 帧视频连续性 XSim](../../../fpga/experimental/jpeg_integration/tb_joint_video_continuity_results.json) 证据，不是板端 TMDS 肉眼证据。

## 无效 fixture 的安全恢复记录

首次探针误用了单分量灰度 JPEG；联合 decoder 只接受上述三分量格式。输入副本：[unsupported-grayscale-640x400.jpg](unsupported-grayscale-640x400.jpg)。报告 [positive-output-probe.json](positive-output-probe.json)，SHA-256：533f8d7dede7ca7fb9a901a16f04223a743f941734c3b31075a2ed330834c60e，记录 DMA 完成超时和 FAIL_HOLD。寄存器读回的 JPEG status=0x10 对应结构错误；当时 S2MM 仍活动，runner 保留缓冲。清 Run/Stop 后 S2MM 未及时 halted，因此对 AXI DMA 发 soft reset，确认 MM2S/S2MM 与 HDMI VDMA 的 Halted 位后，才终止本次 runner；随后 CMA Free 从 18,860 KiB 回到 55,292 KiB。此后 runner 增加输入 header 预检，灰度或不兼容 JPEG 在任何硬件访问前拒绝。

## 证据边界

该测试贯通了真实 USB MJPEG 输入、PL JPEG 解码、RLE/FRG 处理路径和进入 HDMI 前端的 PL 像素流。它证明候选 BIT/HWH 在本次 boot ID 99f9fcff-0685-42ed-a8df-9822cfca6cd0 下完成了监视器核对。

板载监视器读取的是 HDMI 输出像素流及写入画面的身份标记；没有人员观察外接显示器，因此不作为物理显示器肉眼验收。120 帧样本没有检测到前景物料，total_count=0 不能证明物料检测或计数准确率。实测吞吐量是本次采集、解码、处理和监视器核对的端到端结果，不是 HDMI 刷新率或持续满负载性能保证。

构建状态字段 ROUTED_NOT_BOARD_VERIFIED 与原 route receipt 的 board_ready=0 是构建/route 阶段生成的不可变快照；本次板测由单独的 board-run.json 记录，不能回写成 route 当时已验证。
