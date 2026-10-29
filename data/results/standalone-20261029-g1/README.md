# G1 实体灰度分屏与 HP2 位宽续接记录

日期：2026-10-29。续接原分支 `feat/板端脱机显示`（`e9a9046`），本轮分支 `fix/续接独立显示验收`；整项计划原始分支仍为 `LJ-01`。本记录只覆盖独立显示，未集成 RLE 计数。

## 已确认的实物结果

- 显示器已呈现实时相机分屏；用户随后明确确认“画面正常，四按钮均已测试正常”。
- 输入保持 640×400 MJPEG 直接解码 Gray8；上下显示副本为 576×360，DDR 扫描缓冲是 720×1280 二维 uint8。
- HDMI 输出端实际扫描约 60 Hz。不能将扫描率、提交率或重复扫描作为有效显示 FPS。
- 用户按要求拔下 HDMI 约 5 秒再插回，并明确确认“重新插回后画面已正常恢复”。持续运行和停止验收以本记录后续的实际结果为准；这些实物确认不等于所有 G1 门禁通过。

## 从失败定位到 HP2 修正

初始 [短测](short.json) 的有效新帧为 0，约 30 秒内 1802 次扫描全被拒绝。随后相机显示虽然肉眼可见，监测器仍拒绝所有身份；[诊断样本](diag-marker-initial.txt) 中上下标识一致、像素是黑白且 RGB 等值，但 `4d43` 变成了 `4444`，每组 8 个标识像素中的前 4 个被重复。

[CPU 编码探针](marker-cpu-probe.txt) 和 [合成／复制探针](marker-composer-probe.txt) 证明同一板端 NumPy 1.21.5 的标识编码、画布和普通内存复制保持正确。RTL 与综合网表监测器均通过自检，像素展开 RTL 在输入间隙及反压下也通过。

实际 [HP2 寄存器](hp2-before.json) 显示 `AFI2_RDCHAN_CTRL=0`，即仍为 64 位；当前 HWH 的 PS 参数及 HP2 总线均是 32 位。HP 接口的物理读宽度由 [UG585 的 32BitEn](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-AFI_RDCHAN_CTRL-Details) 控制，不能只依据 Vivado 参数认定板端状态已同步；[AFI2 地址](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-axi_hp-AFI_RDCHAN_CTRL) 为 `0xf800a000`。

确认 VDMA 停止、无故障、FPGA reset 为 0 后，只把 HP2 **读**控制寄存器从 0 改为 1，HP2 写控制 `0xf00` 保持原值，见 [修改与读回](hp2-correction.json)。使用相同诊断位流重跑后，标识恢复 `4d43…`，有效新帧开始累计，后续稳定扫描未再持续增加无效计数；[初步读回](hp2-short-sample.txt) 保留现场。初始化代码现在执行同样的 HWH、reset、VDMA 和寄存器读回门禁，拒绝活动 DMA 下修改宽度。

## 软件、RTL 与构建验证

- 本轮 standalone 软件集合：72 项通过（包含原先的共享内存候选模块测试；共享内存尚未接入本次单进程 G1 运行器）。
- [RTL 监测器](resume-rtl-sim.txt)：`VIDEO_MONITOR_PASS`。
- [综合网表监测器](resume-netlist-sim.txt)：`VIDEO_MONITOR_NETLIST_PASS`，覆盖 1280×720 合法帧、重复身份与新身份。
- [Gray8 像素展开](resume-pixel-unpack-sim.txt)：`PIXEL_UNPACK_GRAY8_PASS`，验证低字节先行、0/128/255、侧带和反压。`ap_ctrl_none` 在 reset 后先进入默认 mode=0，测试先排空初始帧，再检查 mode=2；不把该预热帧计为有效 Gray8 输出。
- 诊断构建路由结果：setup 0.066 ns、hold 0.038 ns；最终 DRC 和 methodology 无 Error/Critical Warning。早期 vendor SerialClk 约束报错由后置 XDC 按真实 MMCM 路径修正；[时钟读回](resume-clocks.rpt) 显示其 master 为 `I`（axi_dynclk CLKOUT0），未屏蔽 TMDS 时序检查。
- 最终 CDC 报告仍有 vendor VTC 的 CDC-6 以及稳定邮箱的 CDC-15 警告；邮箱通过请求／应答锁存，跨域数据路径限定 10 ns，不把“有警告”写成“无 CDC 问题”。

本轮诊断制品由 `export/manifest-diag-resume.json` 锁定，BIT/HWH 路径、哈希及源码快照均与旧的 `manifest.json` 区分。工程、位流和完整原生日志为本地生成产物，不以本记录替代重建或离线交付验收。

## 2026-10-30 持续与停机审计补记

原始 `screen-hp2.json` 的逐字节副本、SHA256、schema/时间校验、身份分段、计数器增量、有效 FPS 及采样点窗口明细见 [G1 审计记录](../standalone-20261030-g1-audit/README.md) 和 [summary.json](../standalone-20261030-g1-audit/summary.json)。总采样跨度 661.371 s；稳定同身份区间 659.347 s：新帧增量 5469（带诊断实测 8.295 FPS），扫描增量 39580（60.029 Hz），重复增量 34111，无效与 partial 增量均为 0，图像完整性计数有效。末约 150 s 附加了 `strace`（原仓库 `camera-close-trace.txt`，约 12:13:00–12:15:30，包含停机后）；8.295 FPS 不是优化后的无追踪正常性能基准。无效累计值 23375 在稳定身份首样本已存在，不能全归入稳定期。

按全部稳定身份采样点两两枚举的至少 30 s 区间，最低/最高有效 FPS 为 2.482 / 11.143；采样点窗口不证明任意连续 30 s 均达标。`button_counts` 为 pause 4、clear 2、background 10、page 8。已有用户人工确认画面、四按钮及 HDMI 拔出约 5 s 后重插恢复。

终态为 `FAIL_HOLD`，错误 `driver call exceeded 2s; resources retained`，`driver_job_finished=false`：停机门禁失败，不能声称缓冲已释放。G1 持续图像证据部分通过；G1.5 未通过/证据不足：本次带诊断有效 FPS 低于 10，但不能据此断定正常无追踪工况必然不达标；G2 以后等待前序门禁。没有新增字体或性能改善实测。

## 当前运行与未完成项

板端使用临时 systemd 单元运行，避免 SSH 连接退出使前台会话丢失；它不是 G4 的 SD 自启部署，也没有修改永久开机服务。板端墙上时间未校准，性能与超时使用单调时钟。

初始相机运行在退出时曾报 `camera reader did not stop`。当时 driver 已关闭且 VDMA halted 已独立读回，才清理遗留进程；见 [首次安全恢复](resume-safe-recovery.txt)。该问题仍需停止阶段验证，不能把手动清理写成正常退出。

持续图像与热插拔证据已按上方审计补记汇总，仍需通过正常停止／相机回收检查及 G1.5 合成性能。G2 至 G4 尚未开展；RLE 计数、每个 30 秒窗口双 FPS 均≥10、真实脱网冷启动与 60 分钟有目标运行均不能由本次画面证明。
