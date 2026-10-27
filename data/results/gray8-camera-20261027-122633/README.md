# OV9281 Gray8 相机链路板测（2026-10-27）

用户通知摄像头已接回后执行既定测试。**3 帧合成输入及 960 帧真实相机输入均完成 DMA 往返和 Binary8 全像素比对。** 三轮各 300 帧的加权处理吞吐为 **19.54 FPS**，包含启动/预热、逐帧像素校验及 PS 计数。本轮相机输出的 Binary8 前景全部为零；不能据此宣称物料识别/计数准确率或复杂画面下的处理性能。

## 测试对象与版本

- PYNQ-Z2，SSH 地址 `192.168.2.99`，本次 boot ID 为 `442a249b-1a20-41a1-bbc6-46debb0f4af8`。板卡已重启，因此重新检查并加载位流，没有沿用上次 boot ID。
- USB 相机 `0edc:2076`，UVC 名称 `USB Camera: USB GS CAM`，设备 `/dev/video0`；[模式与控制值](camera-capabilities.txt) 确认支持 MJPG 640×400@60，且无其他相机运行程序占用。
- 新独立部署目录 `/home/xilinx/gray8-camera-20261027-122633/`。23 个源码/制品文件哈希匹配，见 [部署核对](deployment-verification.json) 和 [版本清单](source-sha256.json)。本地相对旧部署的三个已有修改在 [差异记录](source-differences.diff) 中；旧部署没有被覆盖。
- 使用 [原 Gray8 构建](../gray8-dma-20261027/README.md) 的 BIT/HWH；manifest SHA-256 为 `b81e6ea09cba48d643b3f09ae88b9ea784176c81ebc53799f1af38aedb0cd484`。本次没有修改生产源码或重建 FPGA。
- 相机实际协商为 MJPG 640×400@60，`CAP_PROP_CONVERT_RGB=0`，直接 `IMREAD_GRAYSCALE` 解码。映射到 320×240、profile 为 `lighting-calibrated`，source ROI `(0,20)-(640,380)`，输出有效 ROI `(0,30)-(320,210)`。LUT 补偿与 PL `>=128` 等价于源灰度 `>100`。
- 固定控制值：manual exposure、exposure_time_absolute=50、brightness=0、gamma=100，运行后回读通过；驱动未暴露 gain，不能声称已验证 gain。板上系统日历时间不准确，记录目录采用主机时间，耗时使用单调时钟。

## 功能验证与帧率

| 阶段 | 帧数 | 全像素通过 | 处理 FPS |
|---|---:|---:|---:|
| [合成自检](self-test.json) | 3 | 3/3 | 8.99，仅作自检 |
| [相机短测](camera-30.json) | 30 | 30/30 | 9.76，短样本含启动 |
| [独立预热运行](camera-warmup.json) | 30 | 30/30 | 9.91 |
| [300 帧第 1 轮](camera-300-r1.json) | 300 | 300/300 | 19.11 |
| [300 帧第 2 轮](camera-300-r2.json) | 300 | 300/300 | 19.37 |
| [300 帧第 3 轮](camera-300-r3.json) | 300 | 300/300 | 20.19 |

合计 963 帧、73,958,400 个 Binary8 像素全部通过比较。900 帧三轮加权吞吐按 `900 / 三轮耗时之和` 计算为 **19.5448 FPS**；三轮 FPS 中位数为 19.3684。汇总与计算口径见 [summary.json](summary.json)。相机协商的 60 FPS 是输入模式，不能视作本程序处理 FPS。

所有运行退出码为 0，状态 `GRAY8_PIXEL_VERIFIED`，`buffers_held=false`。每帧校验包括 packet/frame ID、字节/beat/帧计数、故障状态、输出 checksum 和完整像素参考。合成图像包含不同灰度值并跨越阈值边界；真实相机 Binary8 全黑，因此本次不具备物料准确率或目标密集/粘连场景的验证条件。

## 900 帧阶段平均耗时

| 阶段 | 平均 ms/帧 | 口径 |
|---|---:|---|
| 采集、解码、映射合计 | 35.23 | `capture_timings_ms` 包含映射，不是纯 USB/解码耗时 |
| 其中图像映射 | 22.62 | 裁剪/缩放及当前 profile 的图像处理 |
| Gray8 编码 | 4.75 | 包头尾、Gray8 载荷与 checksum |
| DMA 阶段 | 12.67 | 含发起前检查和软件等待，不是纯 PL 运算延迟 |
| RX 同步 / 拷贝 | 0.44 / 0.86 | 缓存同步及 CPU 掩膜拷贝 |
| 回传检查 | 18.27 | 寄存器、packet 及全像素验证 |
| ROI / 计数阶段 | 24.18 | 本轮前景为零，未执行 CCL |

各阶段存在流水并行和时间包含关系，不能把表中数值相加后取倒数，也不能直接用删除某阶段的耗时预测帧率。JSON 的 `stage_timings_ms.avg_capture_ms`、`avg_mapping_ms` 为 null，是该入口未把独立采集数组合并到通用摘要；原始逐帧采集/映射数组完整，各轮均为 300 个样本，本表据该数组计算，没有将 null 当作零。

Gray8 相对旧 RGB 包减少约 2/3 输入像素载荷，并不等于整机 FPS 提升相同比例。本次没有相同场景、相同软件校验开销的旧 RGB 对照，不能与历史 15/22 FPS 或相机单独解码基准直接计算新旧整机增幅。

## 软件与收尾检查

- 当前相关软件回归：**108 passed in 4.14s**，见 [完整日志](software-validation-unrestricted.log)。首次在受限环境下停在 Windows asyncio 创建 self-pipe 的 `socket._fallback_socketpair → accept`，尚未进入用例的流处理逻辑；限定 12 秒的栈检查定位后，在获准的沙箱外复跑同一范围通过。首次中断日志 [保留](software-validation.log)，不记作代码测试通过或全部回归失败。
- [最终板端状态](board-final.json)：MM2S/S2MM 均为 `0x1002`，空闲且无 DMA 错误；PL 完成帧数 963、输入字节数 73,989,216=`963×76,832`，packet/adapter/packer 故障均为零。无残留测试 runner，位流及全部部署文件哈希保持一致。
- [新增内核日志](kernel-added.txt) 未见本次新增的 Oops、Kernel panic、Internal error、DMA error 等故障信息；该记录不是长期稳定性或内存泄漏验收。

真实物料准确率、复杂前景吞吐、去除全像素调试校验后的生产性能及 YOLO 收益仍未测量；正式 Route 与接口签核状态不因本次测试自动改变。
