# G1.5 独立显示与合成性能证据

日期：2026-10-30。目标板：实体 PYNQ-Z2。证据仅覆盖 G1.5 独立 HDMI 显示、三槽普通 RAM 共享帧 IPC 与绘图子进程；没有 RLE 计数接入。G1.5 门禁通过，G2 具备启动条件但尚未开始；这不表示 G2、G2.5、G3、G4 或整机验收通过。

## 门禁与通过轮次

通过轮次为 [g15-camera-90s-retry5.json](g15-camera-90s-retry5.json)：状态 `STOPPED`，运行 92.9690167 s；boot ID `e48ff153-bd85-4dd6-98f6-438bd51da08d`，session ID `9e4b9d8a-440e-49e0-bfc3-f333cd142b2a`。所用 manifest SHA256 为 `685bb871fed4d0c1ed58ff9bb5c079b2a51748c1102c25b86dee5ca8a6e7c8d3`。

稳定帧身份样本共 40 个（报告索引 6–45），覆盖 78.8617309 s。样本期身份、灰阶、标记及上下画面配对正确，invalid/partial 增量为 0。平均有效显示速率为 13.5807316 FPS；24 个端点 30 秒窗口最低 12.6615273 FPS、median 14.0399 FPS。合成进程 23 个 30 秒窗口最差 13.7979772 FPS、median 14.0412 FPS。

共提交 1113 帧。生产者 attempted 1278、published 1114、dropped 164；同步抽样 70 次、missed 0，每 16 个提交帧抽查一次。显示证据仅适用于本轮独立显示配置。

## 完整阶段耗时

单位为毫秒；各阶段样本数如表。阶段指标覆盖本轮完成的性能路径。

| 阶段 | n | P50 | P95 | P99 |
|---|---:|---:|---:|---:|
| scale | 1113 | 17.430 | 24.742 | 31.133 |
| draw | 1113 | 14.684 | 27.374 | 66.377 |
| compose | 1113 | 53.509 | 86.616 | 107.943 |
| IPC handoff | 1113 | 3.341 | 9.976 | 15.567 |
| copy | 1113 | 4.354 | 10.925 | 14.754 |
| submit | 1113 | 8.318 | 22.088 | 26.844 |
| sync | 70 | 26.915 | 36.491 | 41.277 |

## CPU、内存与安全停机

| 项目 | 结果 |
|---|---|
| CPU0 P50/P95/P99 | 96.013 / 97.667 / 98.139% |
| CPU1 P50/P95/P99 | 95.638 / 98.676 / 99.813% |
| CMA 起始 / 运行最低 / 报告退出时 | 48,888 / 7,924 / 31,152 KiB |
| 独立停止后读回 | `CmaFree` 51,352 kB；`MemAvailable` 428,964 kB |
| systemd | unit stopped；result success |
| 相机与视频 DMA | `/dev/video0` 无占用；VDMA status `0x11001`，halted/no error |
| 监测与复位 | monitor signature `0x4d435631`；SLCR reset 为 0 |
| 所有权与进程 | `camera_released=true`；`cpu_frame_owned=false`；producer exit 0；ownership findings/denied 均为 0 |

## 缩放诊断

本次 G1.5 实验仅把上方灰度显示缩放切换为 `INTER_LINEAR`。GrayComposer 默认策略及 G1 仍使用 `INTER_AREA`；二值图一直使用 `INTER_NEAREST`。20 张真实相机源帧的 AREA/LINEAR 比较得到平均绝对差 0.0807 灰阶、最大差 2；差值大于 1 的像素占比为 0（四位小数舍入后）。2 线程空闲 resize 比较中，LINEAR+NEAREST 约 8.168 ms，AREA+NEAREST 约 24.811–25.368 ms。G1.5 实跑 scale P50 从 retry4 的 44.419 ms 降至 retry5 的 17.430 ms。

这些数值是本次候选显示缩放的诊断证据，不是物料检测或计数精度证据，不冻结正式接口、Route 或 ADR。

## Retry 历程

| 轮次 | 结果摘要 |
|---|---|
| retry1 | `STOPPED`；平均显示 9.218 FPS；缺少 sync metrics。 |
| retry2 | 1 线程；平均 9.2075 FPS，最低 30 秒窗口 8.6917 FPS；sync 187 次、4 missed。 |
| retry3 | 2 线程、sync 每 4 帧；平均 9.4444 FPS，最低窗口 8.6625 FPS，process 最低 9.816 FPS；sync 195 次、4 missed。 |
| retry4 | 2 线程、sync 每 16 帧；平均 9.4480 FPS，最低窗口 8.7112 FPS，process 最低 9.6725 FPS；sync 49 次、0 missed。 |
| retry5 | `INTER_LINEAR` 复测，通过本页“门禁与通过轮次”所列指标。 |

初始 [g15-camera-90s.json](g15-camera-90s.json) 是 `FAIL_HOLD` 的早期 watchdog 误判，仅归档用于追溯，不属于通过证据。G1 单独由其既有报告通过；本页不替代 G1 证据。

## 制品身份与原始报告校验值

板端运行代码 SHA256：

| 文件 | SHA256 |
|---|---|
| `g1_run.py` | `312146dcea2ce1f19cba77c7e42e29addf15eff3a1724cf4ec844739941ce383` |
| `compositor.py` | `81738f62ac7094678e9cb49ca62e6abb839aa08dd36522192d3fc78c609faac3` |

报告 SHA256 与字节数：

| 报告 | SHA256 | bytes |
|---|---|---:|
| [g15-camera-90s.json](g15-camera-90s.json) | `4f66fd6074f98a6832a01c560c63f4799485306407733aad2005976e2c547ff6` | 4381 |
| [g15-camera-90s-retry1.json](g15-camera-90s-retry1.json) | `7bdb1c355c48fc1e43419007f7ada8cf40606892889638223bc20b549eaca1ac` | 44540 |
| [g15-camera-90s-retry2.json](g15-camera-90s-retry2.json) | `806612e197d3fee43e43c7e3f0aea16cc13c59ad883e7763f05c7ff4ca6fb1e3` | 44616 |
| [g15-camera-90s-retry3.json](g15-camera-90s-retry3.json) | `4ff2839abef7806fb83a75d44b9ed3cd7721c059500ad3dea65916dcef08e537` | 54269 |
| [g15-camera-90s-retry4.json](g15-camera-90s-retry4.json) | `1e417781f1d4b1978443c3cbf1bf771e75fc642fa1b6173299d10a854b612ce4` | 54309 |
| [g15-camera-90s-retry5.json](g15-camera-90s-retry5.json) | `12f91bf346abad37a2c33175031f7dc7b40c297aec18da48d286e98474ebf389` | 54575 |

原始 JSON 报告在本次文档更新中保持不变。

## 局限与后续门禁

本证据不包含 RLE 计数接入、并发 RLE/HDMI 压力、整机功能回归、真正脱机验收或有真值物料精度测试。平均显示速率、身份样本、资源读数和安全停机结论均限于上述 boot/session 与报告。G2 仍待启动；后续 G2/G2.5/G3/G4 及整机门禁尚未通过。
