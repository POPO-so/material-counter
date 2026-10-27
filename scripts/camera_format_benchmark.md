# 相机 MJPEG / YUV 模式基准

`camera_format_benchmark.py` 用于在 PYNQ-Z2 Linux 上单独测量 USB 相机格式，不运行 FPGA、DMA 或物料计数程序。依赖板端 Python、OpenCV、NumPy 和 `v4l-utils`（提供 `v4l2-ctl`）。此板访问摄像头需要 `sudo`；请使用 PYNQ 环境中的 Python：`/usr/local/share/pynq-venv/bin/python3`。

运行前关闭占用相机的预览或计数程序，并保持光源、曝光和场景不变。脚本会拒绝相机正被占用的情况，读取并检查相机控制值；测试时不主动修改曝光等控制。

先枚举实际支持的像素格式、分辨率和离散帧率：

```bash
sudo /usr/local/share/pynq-venv/bin/python3 scripts/camera_format_benchmark.py --list-only \
  --output /tmp/camera-format-modes.json
```

默认测试 `640×400 @ 60 FPS` 下被相机实际枚举的 MJPEG/YUV 同条件模式，各三轮，每轮采样 5 秒、预热 15 帧。每轮次序交替；读取 V4L2 实际协商结果，拒绝回退到其他格式、分辨率或帧率的运行。

```bash
sudo /usr/local/share/pynq-venv/bin/python3 scripts/camera_format_benchmark.py \
  --output /tmp/camera-format-640x400.json
```

如果当前分辨率下没有与 MJPEG 帧率相同的 YUV 模式，可用 `--compare-available` 测试最高的不超过目标帧率的 YUV 模式。若两个模式帧率不同，报告会把结果标成“可用模式切换”，说明分辨率相同但源帧率也改变了，不会把它算作格式本身带来的同条件 FPS 提升。

```bash
sudo /usr/local/share/pynq-venv/bin/python3 scripts/camera_format_benchmark.py \
  --compare-available --output /tmp/camera-format-available.json
```

`--include-current-mjpeg` 会额外运行当前软件路径的对照：OpenCV 将 MJPEG 解码成 BGR，再取首通道；这与直接解成灰度的路径使用相同 MJPEG 相机模式，可单独比较解码和通道提取的差别。

```bash
sudo /usr/local/share/pynq-venv/bin/python3 scripts/camera_format_benchmark.py \
  --include-current-mjpeg --output /tmp/camera-format-mjpeg-paths.json
```

如需查看相机枚举的其他 YUV 离散模式，可用 `--sweep-yuv` 将它们各测试一轮。它们用于了解其他模式，不与不同分辨率或帧率的 MJPEG 基线混算。

```bash
sudo /usr/local/share/pynq-venv/bin/python3 scripts/camera_format_benchmark.py --sweep-yuv \
  --output /tmp/camera-format-yuv-sweep.json
```

测试把 MJPEG 压缩帧解码为灰度；YUYV/YUY2、UYVY 从原始 YUV422 帧直接取亮度 Y，不经过 BGR 图像。两种路径都执行相同的最近邻缩放并补边到 `320×240`。GREY 模式只有在枚举结果中出现时才测试；脚本不会假定相机支持该格式。

同尺寸、同请求帧率且实际协商成功的配对结果才报告百分比：

- FPS 增幅 = `(YUV 处理 FPS / MJPEG 处理 FPS - 1) × 100%`
- 每帧 CPU 时间降低 = `(1 - YUV CPU ms/帧 / MJPEG CPU ms/帧) × 100%`

不同分辨率、帧率、相机控制值或未按请求协商的运行均标为不可比较，不计算收益。`read` 墙钟时间包含相机等待、USB、驱动和后端开销；CPU 用量只统计这个单线程采集进程，不代表整机 CPU 占用。处理 FPS 也不是传感器实际帧率或计数系统 FPS；报告不证明计数准确率。

脚本会将 JSON 写入指定的新路径；若输出文件已存在会停止，不覆盖旧结果。每种模式在单独子进程中运行，并设置有限超时。测试结束时会尝试恢复开始时的相机格式和帧率并回读验证；若强行杀死整个脚本或断电，不能保证恢复。恢复状态和每种模式的实际协商值都记录在报告中。

离线单元测试（使用合成帧，不访问相机）：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s scripts -p "test_camera_format_benchmark.py" -v
```
