# WO-0016 Stage B-1 调光预览升级执行记录

1. 本地静态核对 HUD 与下一轮单次采样启动器；不导入 PYNQ、不访问板卡采样路径、不消费 guard。
2. 将 camera-only 8080 服务替换为同一启动周期的 CPU Gray8 HUD 预览。
3. 核验网页与 MJPEG 帧可访问、`/dev/video0` 只有预览进程占用、`CmaFree >= 32768 KiB`。
4. 保存预览状态与下一轮采样器准备情况后停止；本轮不运行 DMA 采样。
