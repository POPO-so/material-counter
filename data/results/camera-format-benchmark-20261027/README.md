# OV9281：MJPEG / YUYV 板端采集基准（2026-10-27）

结论：当前相机／PYNQ-Z2／驱动组合下，不建议为提速切换到 YUYV。640×400 YUYV 协商成功但未收到帧；1280×720 YUYV 可以输出，但实际处理仅约 6 FPS。保留 MJPEG 并直接解码为灰度，是本次得到有效改善的路径。

## 环境与口径

- 用户明确授权在其 PYNQ-Z2 USB 摄像头上测试；SSH 目标为 `xilinx@192.168.2.99`。
- PynqLinux 3.0 / Ubuntu 22.04，ARMv7，板端 OpenCV 4.5.4；Python 完整版本见 JSON。
- 本轮为相机独立诊断，不加载位流，不运行 FPGA、DMA、跟踪、计数或预览服务。
- MJPEG 两条路径均为 640×400、请求并回读 60 FPS，采用相同最近邻缩放及补边输出 320×240，OpenCV 线程数为 1。
- 三轮交替次序，每条路径每轮预热 15 帧、采样 5 秒。曝光保持手动 5 ms、gamma 100；全部相机控制值见 JSON，测试前后相同。
- 处理 FPS 是主机完成读取及图像准备的吞吐，不证明传感器唯一帧率、图像精度、长时间可靠性或整机计数性能。没有测量逐帧序号与丢帧率。

## 实测

| 路径 | 相机模式 | 处理 FPS | 进程 CPU 时间 / 帧 |
| --- | --- | ---: | ---: |
| 类似当前采集路径：OpenCV 解为 BGR，再取首通道 | MJPEG 640×400 @ 60 | 35.68（三轮中位） | 25.76 ms（三轮中位） |
| 读取压缩帧，直接解为 Gray8 | MJPEG 640×400 @ 60 | 59.39（三轮中位） | 8.87 ms（三轮中位） |
| 原始 YUYV 直接取 Y | YUYV 640×400 @ 30 | 读取超时 | 无有效值 |
| 原始 YUYV 直接取 Y | YUYV 1280×720 @ 10 | 6.03（一次 5 秒采样） | 33.47 ms |

同轮配对计算得到：MJPEG 直接灰度解码相对 BGR 路径，FPS 增幅中位数 **66.60%**，每帧进程 CPU 时间降低中位数 **65.49%**。这些百分比不是 YUV 格式收益，也不是整机计数系统收益。总 CPU 时间包含读取和缩放；BGR 路径的 JPEG 解码在 `read()` 内，不能只比较 `decode_or_luma_wall_ms`。

相机真实枚举只提供两个 YUYV 模式：640×400 @ 30 与 1280×720 @ 10。640×400 的 MJPEG 则提供 60/120/210 FPS，未枚举与 YUV 同为 30 FPS 的模式，所以本轮不存在同尺寸、同源帧率的 JPEG/YUV 有效配对，不能给出格式本身带来的提速百分比。

## 排除 OpenCV 单一路径问题

[V4L2 直接采集记录](v4l2-direct-yuv.json) 使用 `v4l2-ctl --stream-mmap=3 --stream-count=30 --stream-to=/dev/null --stream-poll`：

- 640×400 @ 30：`select timeout`，没有采到帧。该工具退出码仍为 0，因此退出码本身不能证明成功。
- 1280×720 @ 10：采到 30 帧，工具报告约 6.07–6.14 FPS，报告过 1 个 dropped buffer。

因此 640×400 YUYV 问题不限于 OpenCV 的原始帧提取；具体相机固件、USB 或驱动根因尚未确定，不能据此宣称该型号所有设备都不支持此模式。

## 复现与恢复

实际板端命令：

```bash
sudo /usr/local/share/pynq-venv/bin/python3 -u \
  /home/xilinx/camera-format-benchmark-20261027/camera_format_benchmark.py \
  --include-current-mjpeg --sweep-yuv --seconds 5 --warmup 15 --repeats 3 \
  --output /home/xilinx/camera-format-benchmark-20261027/benchmark.json
```

复跑必须使用新的输出文件名。板端基准与仓库源码 SHA-256 一致：`0167bc68c58d2151a6337ef87619f5db88441281a05d95bfbffca0b0a45f69be`。

结束后已额外回读确认恢复开始时的 **MJPG 640×400 @ 210**，相机控制值不变；`fuser /dev/video0` 无占用。没有修改正式采集程序。为测试新增安装板端 `v4l-utils` 和 `libv4l2rds0`；板端 DNS 不可用，通过电脑下载 Ubuntu 官方 armhf 包后传入安装。

原始统计与完整枚举：[benchmark.json](benchmark.json)。本地脚本的 10 项合成数据单元测试通过；它们不代替上述板端证据。
