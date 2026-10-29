# G1 停机失败复现（2026-10-30）

本记录保存现有板端 G1 摄像头显示会话的原始运行报告。它覆盖独立灰度显示，不含 RLE 计数。

## 运行与图像证据

- 板卡 boot ID：`e48ff153-bd85-4dd6-98f6-438bd51da08d`。板端墙上时钟不准确，持续时间以单调时钟为准。
- 瞬态单元：`material-display-g1-20261030.service`；输入为真实摄像头，目标提交率 20 FPS。
- 报告共 1131 个监测样本。前两点包含旧监测身份及启动身份切换；从稳定新身份开始共 1129 点，持续 `2297.941 s`，新身份增加 29201，平均有效显示 `12.707 FPS`。
- 稳定身份区间 `invalid` 和 `partial` 增量均为 0。启动身份切换时出现的 41 次 invalid 和 1 次 partial 不归入稳定区间。
- 稳定区间采样端点枚举的至少 30 秒窗口中，最低为 `6.767 FPS`：样本 799 至 818，持续 `39.459 s`，新身份增加 267。此窗口不满足 10 FPS 门槛；平均值不能替代窗口门槛。

## 停机结果

- 应用报告为 `FAIL_HOLD`，错误：`camera reader did not stop; retain process until V4L2 release`。
- 报告中的 `driver_state=CLOSED` 且 driver job 已结束；代码在相机关闭前已完成 HDMI close 和 VDMA halted 读回。失败发生在相机读取线程关闭：当时 `LatestCamera.close()` 仅等待 2 秒。
- 该瞬态 systemd 单元配置为 `TimeoutStopSec=90s`、`SendSIGKILL=yes`。应用进入 FAIL_HOLD 后，systemd 最终强制结束了进程；因此这不算正常应用停机或资源释放验收。退出后的 CMA 读数为 68988 KiB，不能将系统强制结束当作安全停机证据。

## 判定与原始数据

G1 已有超过 10 分钟的稳定显示，但停机门禁未通过，G1 仍未完成。G1.5 未通过：至少一个采样的 30 秒以上窗口低于 10 FPS，且此运行未记录计划要求的 IPC、阶段耗时分位数及分核 CPU/CMA 证据。G2 及后续阶段未开始。

原始板端报告：[`screen-run-failhold.json`](screen-run-failhold.json)，916166 字节，SHA256：`e112b7aee87dbd921333786cbf82d2d3591105f5bc863f7a7117cfd0f97d3daf`。
