# WO-0016 Stage B-1 空转底噪验证结果

**状态：未采样（启动器前置条件拒绝）**  
**boot_id：** `58e6af6c-6660-4d44-b0c5-e18f4a564202`

## 结果

Stage B-1 启动器在读取 HWH 阈值时拒绝继续。启动器把条件写成 `THRESHOLD=128`，而实际 HWH 使用 XML 属性 `<PARAMETER NAME="THRESHOLD" VALUE="128"/>`（`wo15.hwh` 第 3115 行）。HWH 中的阈值确为 128，这是启动器匹配格式的问题，不代表硬件阈值不符。

拒绝发生在相机采集和 DMA 启动之前：`board_accessed=false`、Stage B-1 guard 未消费。未取得采样帧，因此不能声称计数为零或任何底噪/FPS/DMA 指标通过。

## 本轮只读门禁与恢复状态

- 预览进程 PID 15100 收到 SIGTERM 后退出；`/dev/video0` 无占用、8080 已释放。
- 停止预览后、采样前 `CmaFree=64,504 KiB`，高于 32,768 KiB 门槛。
- 相机读回 MJPG 640×400、60 FPS、手动曝光模式、曝光值 50（5.0 ms）。
- 预览已恢复：PID 2370 正在占用 `/dev/video0` 并监听 `0.0.0.0:8080`；运行中的 `CmaFree=42,112 KiB`。当前预览只显示“CAMERA ONLY - COUNTING PAUSED”，没有 ROI/遥测叠加。
- 冷启动 `boot_id` 未变化。Stage B-1 guard 仍不存在；原 WO-0016 通用 guard 未被使用。

## 请求的指标

| 指标 | 结果 |
|---|---|
| 采样帧数 / 平均 FPS | 0 / 未测量 |
| MM2S / S2MM `DMASR` 通过率 | 未测量；DMA 未启动 |
| ROI `bg_mean` / RMS / PTP | 未测量 |
| ROI 接缝峰值灰度 | 未测量 |
| S2MM ROI 前景像素峰值/均值 | 未测量 |
| `min_area=12` 后连通域数 | 未测量 |
| 空转 `total_count == 0` | 未验证；不能判 PASS |

当前配置仍是硬件阈值 `128`、`min_area=12`。因为未采到数据，本报告不建议改阈值或面积过滤值。

## 后续

本轮在首个前置条件异常处停止，没有重试，也没有进入 Stage B-2。修复启动器对 HWH XML 的阈值解析后，需要一枚新的单次 Stage B-1 执行授权。当前系统已回到同一 boot 下的 8080 camera-only 预览，等待确认。

原始拒绝证据保存在 JSON 的 `raw_launcher_rejection`，远端副本：`/home/xilinx/wo0016-phase110-58e6af6c-6660-4d44-b0c5-e18f4a564202/evidence/stageb1-idle-belt-baseline.json`。
