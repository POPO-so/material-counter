# WO-0016 — 异步双缓冲连续流与传送带物料过线计数

| 项目 | 内容 |
|---|---|
| 状态 | Offline/Mock 实现与验证完成；板端验收 `NOT RUN` |
| 基线 | `feat/pl-stream-skeleton`，WO-0015 50 MHz、320×240 Binary8 S2MM 合同 |
| 工作分支 | `codex/wo0016-counting-20261017` |
| 范围 | Python PS 侧双缓冲调度、过线计数、MJPEG 预览和离线 Mock |

## 目标与接口

- 沿用 320×240 Binary8 帧，RX 每帧 76,800 字节；TX 沿用 WO-0015 的 230,432 字节 RGB 包。
- 一次分配两组 TX/RX CMA 缓冲区。槽位按 `FREE → DMA_OWNED → CPU_READY → CPU_OWNED → FREE` 轮换；DMA 可能仍拥有缓冲区时进入 `HELD`，不重试、不复位、不释放。
- 每帧先启动 S2MM，再启动 MM2S。CPU 在另一个缓冲槽仍由 DMA 使用时处理已完成帧。
- 计数器只分析 `line_y ± roi_half_height` 的行区间，默认过线 `Y=120`；返回 ROI 的行/列前景像素投影，并用 8 邻域连通域、最小面积、逐帧质心关联、方向、迟滞及连续帧确认抑制小噪声和重复计数。
- `/` 提供网页，`/stream.mjpg` 提供最新 Binary8 预览；默认监听 `0.0.0.0:8080`，图像叠加红色过线及 `Total Count`。
- 软件通过 `sync_to_device`/`sync_from_device` 显式维护 CMA 缓存可见性。

## PYNQ DMA API 约束

PYNQ Simple DMA 通道提供非阻塞 `transfer()`，并通过 `wait()` 或支持中断时的 `wait_async()` 完成操作。本地 `transfer_async()` 是项目异步适配器：它立即调用官方 `transfer()`，再异步等待完成。不要调用不存在的 `recvchannel.transfer_async()` 方法。

参考：[PYNQ DMA API](https://pynq.readthedocs.io/en/latest/pynq_package/pynq.lib/pynq.lib.dma.html)、[PYNQ DMA 源码文档](https://pynq.readthedocs.io/en/latest/_modules/pynq/lib/dma.html)。

## 硬件边界

板端模式默认关闭。启动要求显式 `--execute --authorize-stage wo0016-stream --approve-once`、经审阅的 WO-0015 50 MHz manifest 与 SHA-256、同 boot 的 Stage 1 Overlay PASS 回执、有限 `--max-frames`、全新绝对证据路径，以及板端重新执行的 boot/Overlay/CMA/地址/进程/寄存器检查和一次性 guard。通过 `Overlay(..., download=False)` 复用已加载 BIT；在创建 DMA 驱动前读取并要求 AFI1 精确值 RD=`0x00000001`、WR=`0x00000F01`，并把 CMA 水位保持在至少 32,768 KiB。运行不自动下载 Overlay、不重试 DMA。DMA 可能仍访问 CMA 时保留缓冲区并进入 fail-hold。

板端部署包将所需 WO-0015 Python 辅助模块放在 `/home/xilinx/wo0016/wo0015/`，运行器优先从该独立副本加载，不覆盖既有 `/home/xilinx/wo0015/`。

V4L2 采集明确请求并回读核验 MJPG 640×400；不改变相机曝光控制。板端启动命令以 `sudo -n env XILINX_XRT=/usr` 运行 PYNQ venv，运行证据记录 `XILINX_XRT` 值。

本工单的 Offline/Mock 结果只证明软件路径、合成掩码和网页编码行为。它们不证明摄像头输入、真实传送带、M6 螺母/垫片准确率或实板 FPS。请以[板端验收表](../verification/wo-0016-board-acceptance.md)记录实际物料真值、工况和现场测量。
