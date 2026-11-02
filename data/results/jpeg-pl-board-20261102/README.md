# JPEG PL 实机链路验收（2026-11-02）

报告：[board-run.json](board-run.json)
报告 SHA-256：2cac80251f9dbdcf2a0570ac94ea8e45dfff3974adcafba2b3117c11367e281

## 结果

- 状态：JPEG_PL_RUN_COMPLETED_OUTPUT_MONITORED；输入模式 raw_mjpeg_to_jpeg_pl，分辨率 640×400。
- PYNQ-Z2 实际采集并处理 120/120 个 MJPEG 帧，0 个无效 packet；端到端实测 11.31 fps。
- 每帧 PL 显示帧号与板载 HDMI 像素监视器读到的 frame/session 标记相符；全部逐帧记录 invalid=0、partial=0，像素及标记诊断均无错误。
- 测试结束后报告确认 hdmi_stopped=true、buffers_held=false。另一次只读状态确认 AXI DMA、HDMI VDMA 均 halted，没有运行中的本测试 runner。

## 证据边界

该测试贯通了真实 USB MJPEG 输入、PL JPEG 解码、RLE/FRG 处理路径和进入 HDMI 前端的 PL 像素流。它证明候选 BIT/HWH 在本次 boot ID 99f9fcff-0685-42ed-a8df-9822cfca6cd0 下完成了监视器核对。

板载监视器读取的是 HDMI 输出像素流及写入画面的身份标记；没有人员观察外接显示器，因此不作为物理显示器肉眼验收。120 帧样本没有检测到前景物料，total_count=0 不能证明物料检测或计数准确率。实测吞吐量是本次采集、解码、处理和监视器核对的端到端结果，不是 HDMI 刷新率或持续满负载性能保证。

构建状态字段 ROUTED_NOT_BOARD_VERIFIED 与原 route receipt 的 board_ready=0 是构建/route 阶段生成的不可变快照；本次板测由单独的 board-run.json 记录，不能回写成 route 当时已验证。