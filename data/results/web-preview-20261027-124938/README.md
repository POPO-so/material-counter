# 网页相机图传启动记录（2026-10-27）

**已启动：<http://192.168.2.99:8080/>**，MJPEG 流路径 `/stream.mjpg`，遥测 `/status.json`。

Worker 核对了现有 HTTP/MJPEG 实现、相机预览入口和相关测试；Manager 完成板卡预检、部署、启动和真实访问验证。当前服务使用 `camera_only_preview.py`：相机直接灰度解码、软件二值小窗与 `SIM COUNT` 展示，不启用 FPGA DMA 或板端计数，也不加载位流。它与此前约 19.54 FPS 的 Gray8 DMA 校验测试是不同运行模式，不能直接比较 FPS。

## 当前运行状态

- Boot ID：`442a249b-1a20-41a1-bbc6-46debb0f4af8`；服务 PID **9345**。
- 独立部署目录：`/home/xilinx/web-preview-20261027-124938/`；入口 `launch_viewer.py`，日志 `viewer.log`，PID 文件 `viewer.pid`。
- 状态：`viewer/camera-preview-442a249b-1a20-41a1-bbc6-46debb0f4af8-06/preview-state.json`，已达到 `READY_WAITING_FOR_OPERATOR_OBSERVATION`。
- 进程以 `xilinx`（UID/GID 1000）服务，保留视频设备补充组 44；没有修改用户永久组成员、sudoers 或防火墙。
- 后台进程独立于 SSH 会话继续运行；未配置开机自启。该服务占用 `/dev/video0` 与 8080 端口，后续相机/DMA 测试前须先停止。

## 已验证

- 页面 HTTP 200，视频流连续解码 **5 帧 JPEG**，HUD 输出为 320×325；相机原始输入为 MJPG 640×400@60，灰度主画面为 320×240。
- 视频 sequence 从 182 增至 186，完成帧数从 3652 增至 3661；不是仅返回一张静态占位图，见 [客户端验证](verification.json)。
- [服务端核对](service-verification.json) 时已处理 7015 帧，UID 1000、`Groups: 44`，部署文件哈希全部一致，`dma_enabled=false`、`board_counter_enabled=false`。
- 内部 180 帧 loopback 客户端测得约 25.82 FPS；它是带 HUD 的相机软件预览交付速率，不代表 FPGA 处理速度或用户浏览器实际显示 FPS。
- [当前截图](preview-snapshot.jpg) 显示画面很暗，HUD 背景均值约 2.4、阈值前景为 0；不据此推断物理上没有物料或相机故障。
- 相关 `test_camera_only_preview.py`：Worker 按 TDD 验证先 9 passed/3 failed，再修改后 **12 passed**。Manager 检查实际 diff，并在板上验证降权、视频组和实际 HTTP 生命周期。

## 启动时修复与保留记录

1. 原预览脚本绑定旧 boot ID 和旧目录。独立启动配置只将期望 boot 和输出根目录绑定到本次已核对值；实际 boot、相机 profile、DMA 空闲及内存检查仍执行，生产源码中的旧常量未冒充当前状态。
2. 首次普通预览相机打开后 CMA 为 32,532 KiB，低于其 32,768 KiB 连续保留门槛，程序退出并释放相机，见 [首次失败状态](first-attempt-failure.json)。本次使用已有 `--calibration-mode`：启动仍要求 CMA >32 MiB，采集后执行其 16 MiB 应急门槛、可用内存和 RSS 检查；没有删除或临时关闭检查。校准 API 仅接受 loopback 来源，局域网网页用来查看。
3. 首次部署漏了 `dashboard.html`，已补齐。另发现降权后 `sudo -n v4l2-ctl` 不可用：修复 `drop_to_xilinx()` 在降权前保留实际视频设备组，并让查询/设置直接调用 `v4l2-ctl`。服务仍降为 `xilinx`，控件白名单、回读和 loopback 限制保持。仅修改这两处相关生产/测试文件，没有开启 DMA 或修改计数算法。

## 停止与复用

停止前核对当前 boot、`/proc/9345/cmdline` 指向本目录的 `launch_viewer.py`，再向该进程发送 SIGTERM；脚本关闭 HTTP/publisher、恢复标准相机控件并释放相机。随后核对进程退出、8080 不再监听、`/dev/video0` 无占用，再执行其他采集或 DMA 任务。PID 和 boot 仅适用于本次服务，不在重启后盲目复用。

部署源码与配置哈希见 [source-sha256.json](source-sha256.json)，启动参数见 [viewer-launch.json](viewer-launch.json)。本次网页已请求在 Codex 侧栏打开；工具返回 queued，实际是否显示取决于客户端当前页面。
