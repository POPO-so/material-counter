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

## 五帧越线计数补测

使用 [count-sequence](count-sequence/) 中的五张连续合成 JPEG：同一 40×40 目标中心依次位于 x=239.5、269.5、299.5、329.5、359.5，计数线 x=320，预期越线计数 1。五张图片均为 640×400、三分量 baseline 4:2:2、restart interval 40；总输入 31,242 bytes。

板端报告：[count-sequence-board-report.json](count-sequence-board-report.json)，SHA-256：494fdfde9c074077061cf54f7a76a0b301e26a18fd9bdd1920c66bf6df830ce7。真实 PL 链路五帧各检出 1 区域；逐帧累计计数为 0、0、0、1、1，计数器在目标中心越过 x=320 后增加一次。HDMI 像素监视器读到 frame ID 1–5，逐帧与提交帧一致，invalid=0、partial=0；运行结束 hdmi_stopped=true、buffers_held=false。板端 DMA MM2S/S2MM 状态为 0x1001/0x1001，HDMI VDMA control/status 为 0x11000/0x11001，CMA Free 为 53,184 KiB，无 runner 残留。

这是已知合成轨迹上的硬件链路功能验证，不代表真实物料计数准确率。HDMI monitor 仍只核验帧标记与扫描；不抽样 mask、框或计数字形像素，也没有外接显示器观察。

## 累计计数字形 RTL XSim

独立 count-glyph 仿真在数字像素域验证了 BCD 数字 7、黑色计数面板和同一帧 latch；另一个 atomic-update 用例验证相邻帧的面板/掩膜银行切换。结果收据为 [overlay_xsim_receipt.json](count-glyph-xsim/overlay_xsim_receipt.json)，状态 PASS；count 与 atomic 日志分别为 [count.simulate.log](count-glyph-xsim/count.simulate.log) 和 [atomic.simulate.log](count-glyph-xsim/atomic.simulate.log)。源码 SHA 与收据一致（overlay `e44969b0…a5e8871`、count testbench `0fcde17c…12b838b`）。

这补充的是 RTL 像素逻辑证据。板载 HDMI monitor 没有采样计数字形像素，XSim 也没有通过物理 TMDS/外接显示器，因此实体显示仍待人工观察。

## 无效 fixture 的安全恢复记录

首次探针误用了单分量灰度 JPEG；联合 decoder 只接受上述三分量格式。输入副本：[unsupported-grayscale-640x400.jpg](unsupported-grayscale-640x400.jpg)。报告 [positive-output-probe.json](positive-output-probe.json)，SHA-256：533f8d7dede7ca7fb9a901a16f04223a743f941734c3b31075a2ed330834c60e，记录 DMA 完成超时和 FAIL_HOLD。寄存器读回的 JPEG status=0x10 对应结构错误；当时 S2MM 仍活动，runner 保留缓冲。清 Run/Stop 后 S2MM 未及时 halted，因此对 AXI DMA 发 soft reset，确认 MM2S/S2MM 与 HDMI VDMA 的 Halted 位后，才终止本次 runner；随后 CMA Free 从 18,860 KiB 回到 55,292 KiB。此后 runner 增加输入 header 预检，灰度或不兼容 JPEG 在任何硬件访问前拒绝。

## 证据边界

该测试贯通了真实 USB MJPEG 输入、PL JPEG 解码、RLE/FRG 处理路径和进入 HDMI 前端的 PL 像素流。它证明候选 BIT/HWH 在本次 boot ID 99f9fcff-0685-42ed-a8df-9822cfca6cd0 下完成了监视器核对。

板载监视器读取的是 HDMI 输出像素流及写入画面的身份标记；独立 RTL XSim 覆盖了数字 count glyph 像素，但没有人员观察外接显示器，因此仍不作为物理显示器肉眼验收。120 帧样本没有检测到前景物料，total_count=0 不能证明物料检测或计数准确率。实测吞吐量是本次采集、解码、处理和监视器核对的端到端结果，不是 HDMI 刷新率或持续满负载性能保证。

构建状态字段 ROUTED_NOT_BOARD_VERIFIED 与原 route receipt 的 board_ready=0 是构建/route 阶段生成的不可变快照；本次板测由单独的 board-run.json 记录，不能回写成 route 当时已验证。

## 最终 route CDC 复核

T7 最终 routed CDC 报告、timing 报告、route 收据及两份 CDC 约束已归档于 [route-cdc-review](route-cdc-review/README.md)。复核确认 CDC-13 critical 为 0；998 条 CDC-15 和 1 条 CDC-6 均按最终 routed 报告完成路径归因，warning 仍保留并有对应约束/协议说明。`datapath_only` 例外路径不做 hold 检查；该限制及握手稳定数据的处置依据已写入复核记录。没有为隐藏 warning 添加 false path。

## v8 默认入口运行包

已生成 [jpeg-pl-runtime-v8.zip](jpeg-pl-runtime-v8.zip)，SHA-256：`6939451aa26c943c7a66c1f60ba1b7d5d650e2dd2a758515eb6f16b2dedf817f`，大小 1,323,908 bytes。包内 18 个运行源码均按 manifest 逐项校验；新增默认 JPEG PL 分发代码和兼容别名已包含。JPEG runner 会优先使用 `--native-lib`/`RLE_NATIVE_LIB` 指定的库，否则在硬件初始化前从包内 C++ 源码构建一个以源码 SHA 命名的 `.so`；这需要 PYNQ 系统已有 `g++`。包内 BIT/HWH 与已板测的候选相同：BIT `9535be948365ff2278232e13a9dbe2f99cb521ca193aa82b28fc06a401e264ed`，HWH `79b8abcb954667e6791c8d7f0eba3e59ad2215ca3f175ebb8b5e98892dd9968f`。release 收据：[jpeg-pl-runtime-v8-release.json](jpeg-pl-runtime-v8-release.json)。下面命令运行的是独立 JPEG 候选包；不是合并后的 LJ-01 硬件快照。

将 ZIP 复制到 PYNQ-Z2（下例使用现有地址），再在板端解包并运行：

```powershell
scp data/results/jpeg-pl-board-20261102/jpeg-pl-runtime-v8.zip xilinx@192.168.2.99:/home/xilinx/
```

```bash
mkdir -p /home/xilinx/jpeg-joint-20261102-v8
unzip -oq /home/xilinx/jpeg-pl-runtime-v8.zip -d /home/xilinx/jpeg-joint-20261102-v8
cd /home/xilinx/jpeg-joint-20261102-v8
sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -B -m scripts.rle.run_counter \
  --mode camera --frames 120 --execute \
  --bit artifacts/jpeg-joint.bit --bit-sha256 9535be948365ff2278232e13a9dbe2f99cb521ca193aa82b28fc06a401e264ed \
  --hwh artifacts/jpeg-joint.hwh --hwh-sha256 79b8abcb954667e6791c8d7f0eba3e59ad2215ca3f175ebb8b5e98892dd9968f \
  --build-status ROUTED_NOT_BOARD_VERIFIED \
  --boot-id "$(cat /proc/sys/kernel/random/boot_id)" \
  --output /home/xilinx/jpeg-joint-20261102-v8/run-camera.json
```

JPEG runner 会执行已有相机占用、DMA/VDMA idle、BIT/HWH 哈希和 boot ID 检查；遇到不匹配会停止，不会继续加载不匹配位流。实际接 HDMI OUT 显示器观察画面，并用运行报告核对 120 帧、packet invalid/partial、累计计数和安全停机状态。此前 120 帧空场景、五帧合成越线板测是链路证据；物料准确率仍需要你用已知数量的实物完成真值核对。

## 合入 LJ-01 后的证据边界

此目录的 T7 route 报告和 v8 ZIP 绑定独立 JPEG 候选源码。JPEG 结果合入含 SW1 更新的 LJ-01 后，入口代码、显示时序与 CDC 约束已作合并；合并后的源码组合尚未重跑联合综合/route，也没有对应的新 BIT/HWH。因此本目录证据不能替代合并快照的硬件实现验证，部署时须先生成并核对该快照匹配的 BIT/HWH。
