# 实时调光辅助台

`live_lighting_tuner.py` 是独立单文件工具，依赖 OpenCV 和 NumPy，不接入计数运行时。默认监听 `127.0.0.1:8080`，包含 MJPEG 视频、四项参考指标、原始灰度/辅助/二值化/三区视图、图像与相机参数滑块，以及原图和 JSON 快照。

## 离线查看网页

在仓库根目录运行：

```powershell
python scripts/live_lighting_tuner.py --demo
```

打开 <http://127.0.0.1:8080/>。模拟图像带 DEMO 标记，整体达标状态始终为 false；不打开相机，不写相机参数。

## 实际采集（由操作员明确授权后启动）

启动前停止占用同一相机的其他预览/计数程序。工具不管理其他进程的相机锁。实际预览须与正式采样分开；正式采样前先退出本工具并重新检查相机配置。

PYNQ/Linux 示例：

```bash
python3 scripts/live_lighting_tuner.py --device /dev/video0 --port 8080 --allow-camera
```

默认仅采集，不修改 V4L2 参数。若明确需要相机滑块，增加 `--allow-camera-controls`；该模式启动时尝试设置手动曝光 `auto_exposure=1`、曝光 `exposure_time_absolute=50`、gamma 100、gain 0。设备必须支持这些控制与对应范围；命令错误或回读不一致会停止采集并显示错误。脚本不会静默切换控制名或把请求值当作实际值。曝光单位以设备定义为准。

```bash
python3 scripts/live_lighting_tuner.py --device /dev/video0 --allow-camera --allow-camera-controls
```

Windows 相机示例（不提供 V4L2 控制）：

```powershell
python scripts/live_lighting_tuner.py --device 0 --allow-camera
```

默认请求 640×400、60 FPS，可用 `--width`、`--height`、`--fps` 修改；实际帧尺寸不匹配会报错，状态显示的是实际处理帧率，不保证 60 FPS。默认 ROI 是 `[20,380)`，可在页面调整。

从电脑访问板子时，优先转发板端 loopback 的 8080 端口。若明确需要局域网访问，可增加 `--host 0.0.0.0`，然后访问 `http://板子IP:8080/`。该 HTTP 服务没有登录认证，只应在可信网络运行；同源访问可以调整已启用的控制并保存文件。不同 Origin 的控制请求被拒绝。

按 Ctrl+C 退出；启用 V4L2 写入时会尝试恢复启动前读到的控制值，并核对回读。强制结束进程、设备断开或驱动读阻塞时无法保证恢复；控制台会报告可检测到的恢复失败。工具不控制 DMA、FPGA 或输送带。

## 指标口径与限制

- 背景灰度参考 20–45，金属前景灰度参考 160–235；这是可见图像的阈值统计，不是物理标定或材料准确率验收标准。
- 过曝统计为 ROI 内灰度 `>= overexp_thresh` 的像素，默认阈值 255。比例固定以全部 ROI 像素为分母，参考 `<=0.5%`。原始示例的分母在前景和 ROI 间切换，会造成指标跳变，本工具不采用该做法。辅助视图的红色提示覆盖整张图，统计数值仍只针对 ROI。
- 前景为灰度 `> binary_thresh`，默认 100；背景使用排除前景边缘后的掩码。左/中/右三区背景极差参考 `<=10`。缺失任一区背景时为 unavailable，不能达标；无有效零件时前景状态为 NO_PART。
- `RETR_CCOMP` 的外轮廓面积减去子轮廓面积为净轮廓面积，单位为像素面积；它不是严格的前景像素计数。孔面积累计达到 12 时显示绿框，否则橙框。无孔不能证明阴影遮挡，有孔也不能证明成像质量合格。接触 ROI 边缘的零件在报告中带 `touches_roi_edge` 提示。
- 只有真实、未过期帧的四项指标均达标时，整体指示才通过。整体提示只针对当前帧，不包含连续稳定性、内孔识别准确率或物理尺寸测量；`physical_calibration_status` 始终为 `NOT_MEASURED`。
- 采集打开失败或读帧失败时停止，不自动生成替代画面。状态超过 3 秒没有新帧时标记 stale，旧图变暗，整体指标失效，拒绝保存旧快照。

## 接口和证据

- `GET /`：内嵌网页；`GET /stream.mjpg`：只发送新帧。
- `GET /api/status`：数据源、错误、是否 stale、当前参数、帧参数、帧序号、指标与硬件回读值。未启用硬件控制时 `hardware_applied=null`，滑块默认值不是实际硬件读数。
- `POST /api/control`：`application/json` 参数对象；校验类型、数值范围与 ROI 顺序，未知参数拒绝。
- `POST /api/snapshot`：`application/json` 空对象；保存原图 PNG 和该帧对应的参数/指标 JSON，避免保存请求时新参数与旧图错配。目录默认 `calibration_snapshots`，可用 `--snapshot-dir` 指定。文件保存在服务端，返回绝对路径；每次使用唯一名称。

离线测试：

```powershell
python -m unittest discover -s scripts -p test_live_lighting_tuner.py -v
```

测试使用合成图像、本机 HTTP 和模拟 V4L2 命令；不构成相机兼容性、板端性能或实物标定证据。

2026-10-25 离线验证结果：20 项 unittest 通过；本机 Edge 无头浏览器验证了 640×400 MJPEG、参数更新、视图切换、PNG/JSON 快照以及硬件滑块禁用状态，无页面脚本错误。1366px 桌面截图经检查，320px/390px 布局无横向溢出。模拟服务已停止；未部署或进行真实相机测试。
