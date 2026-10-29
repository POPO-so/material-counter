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

## 首次复现时的判定与原始数据

此段记录首次复现后的历史状态：持续显示时长达到 10 分钟，但停机门禁未通过，因此当时 G1 尚未完成。首次运行也不构成 G1.5 性能验收。

原始板端报告：[`screen-run-failhold.json`](screen-run-failhold.json)，916166 字节，SHA256：`e112b7aee87dbd921333786cbf82d2d3591105f5bc863f7a7117cfd0f97d3daf`。

## 停机修复候选的启动失败与资源诊断（2026-10-30）

- 在候选部署 `/home/xilinx/standalone-g1-stopfix` 上启动 `material-display-g1-stopfix-20261030.service`，使用同一 boot ID `e48ff153-bd85-4dd6-98f6-438bd51da08d`。运行报告为 [`screen-stopfix-bo-failhold.json`](screen-stopfix-bo-failhold.json)，1157 字节；本地文件 SHA256 与板端一致：`476ec91dad83a7f3fd92536951567569adb9fa686f2245c9b9072f272ca3fd4b`。
- 报告在 `setup_hardware` 运行 12.550 秒后进入 `FAIL_HOLD`，错误为 `BO allocation failed: std::bad_alloc`，`retained_count=0`。该函数的首项是一个 24 MiB CPU-only BO 探针，因此失败发生在 Overlay、时钟及 HDMI/VDMA 配置之前；没有据此运行有效显示或停机验收。
- 释放前确认 unit 主进程 PID 30600 持有 `/dev/video0`，`SendSIGKILL=no`，报告 `retained_count=0`，MM2S 控制/状态为 `0x11000/0x11001`（halted、无错误位），监视器签名 `0x4D435631`，SLCR reset `0`。随后只对该 unit 发送 `SIGKILL` 以清除失败进程；unit 进入 `failed/result=signal`、`MainPID=0`，相机设备无占用，MM2S 仍 halted，CMA 空闲读数为 80,960 KiB。此强制释放是故障恢复记录，不计作应用正常停机证据。
- 单独打开真实相机并读取一帧时，帧为 `(400, 640) uint8`；相机打开前/打开时 `CmaFree` 分别为 55,608/41,432 KiB。相机关闭返回 `OK`，随后 `/dev/video0` 无占用。这验证了候选版相机关闭在单独诊断中的返回，不替代整套显示服务的正常停机验收。
- XRT 分配对照显示：无相机时 `CmaFree` 约 97 MiB，1、4、8、16 MiB 单块 BO 成功，24 MiB 单块 BO 失败；同一时段四个独立 `1280×720 uint8` BO 均成功并释放，合计 3,686,400 字节。故本次失败由 24 MiB 单块准入探针无法满足触发；板端结果不支持“CMA 总空闲量不足”这一解释。内核日志仅有通用 CMA 初始化信息，未提供更具体的 XRT 拒绝原因。
- 准入探针已改为四个实际 Gray8 VDMA 帧槽的独立分配，并加入四槽参数和部分分配失败回收的单测；修复提交为 `b6619ab`。随后完成的全程复测见下节。

## 修复后 G1 正常运行与停机复测（2026-10-30）

- 运行单元 `material-display-g1-stopfix-20261030-02.service`，boot ID 与前次一致，manifest SHA256 为 `685bb871fed4d0c1ed58ff9bb5c079b2a51748c1102c25b86dee5ca8a6e7c8d3`。候选 `g1_run.py` SHA256 为 `94b13d8e591e0b6d1747ee3b1283ee836e62c5129a2393b0b795540bc13069ea`。
- 应用运行 `638.856 s`；监视器的新身份稳定区间 `632.542 s`（10 分 32.5 秒），311 个稳定样本，新增有效帧身份 8,278，平均 `13.087 FPS`。按监视器采样端点枚举的约 30 秒窗口共 296 个，最低 `12.085 FPS`；扫描率 `60.029 Hz`。
- 稳定身份区间的 `invalid` 与 `partial` 增量均为 0，`gray_error`、`marker_pixel_error`、`pair_mismatch`、`top_invalid`、`bottom_invalid` 标志均未置位。身份切换发生在启动阶段，切换后的稳定计数不再增长。画面中包含 0/128/255 灰度参考块；本地合成器测试验证这些像素值，板端帧监视器全程未报灰度通道不一致或几何/帧身份错误。既有用户目视确认、四按钮及 HDMI 热插拔证据仍见 [G1 续接记录](../standalone-20261029-g1/README.md)。
- 停机通过 `SIGTERM` 正常完成：应用报告 `state=STOPPED`、`camera_released=true`、`cpu_frame_owned=false`，日志出现 `G1_STOPPED_BUFFERS_RELEASED`；`systemctl stop` 返回 0，unit `Result=success`、`MainPID=0`，`/dev/video0` 无持有者。停机后 MM2S 状态 `0x11001`（halted、无错误位），监视器签名 `0x4D435631`，SLCR reset 为 `0`，`CmaFree=30,508 KiB`。运行期 CMA 最低 `18,996 KiB`，停止后回升。
- 本次报告的 `compose_ms` P50/P95/P99 为 `57.53/96.70/122.64 ms`，`copy_ms` 为 `4.28/5.02/6.95 ms`，`submit_ms` 为 `4.40/11.01/13.39 ms`。原始报告 [`screen-stopfix-g1-pass.json`](screen-stopfix-g1-pass.json)，255,592 字节，SHA256：`595596561021afb4b8fb903c5a6b3678430d79fd3a88e35cf9d9db94dfa32ba1`。

## 当前阶段判定

G1 的持续显示和正常停机门禁已通过。G1 仍为独立显示验证，不包含 RLE 计数接入。G1.5 仍未验收：本次运行的帧率超过 10 FPS，但渲染路径尚未实际使用三槽共享帧 IPC，也没有分核 CPU 采样和计划要求的缩放、绘制、IPC、同步分阶段 P50/P95/P99。G2 及后续阶段未开始。
