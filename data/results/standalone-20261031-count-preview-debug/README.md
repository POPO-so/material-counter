# 2026-10-31 板端计数与预览卡顿排查

用户先报告无法计数、低帧率和预览卡顿。首次连接时网络不可达；连接恢复后实测 boot ID 为 `f1462f32-61d2-44c7-9b42-d46d7d4d64c6`，系统刚重启，计数 service inactive。完成包身份与唯一 owner 核查后启动原 v4。用户随后确认“已经能计数，但画面仍卡顿”。此前不能计数的根因未确认；本轮没有改检测、跟踪、阈值或计数方向，也没有物料准确率真值结论。

板卡时钟为 2025-05-08，与会话日期不一致；使用 boot/session、单调时钟及文件哈希关联证据，不将板卡日历时间当作真实采集日期。

## 原版本现场证据

[原版本最终报告](baseline-session-b20ccad3-90fc-4abd-996a-451fb09dbf80.json)记录：processed=1900、submitted=866、duration=131.7316 s、total_count=19、invalid_frames=0、recoveries=[]。会话包含启动及用户现场操作，各时段输入不同，不能把全程平均帧率当作固定场景性能基准。

| 阶段 | p50 (ms) | p95 (ms) |
|---|---:|---:|
| DMA transfer | 50.55 | 77.08 |
| PS count | 0.96 | 5.43 |
| preview publish | 7.12 | 26.78 |
| scale | 17.24 | 30.10 |
| draw | 9.76 | 471.27 |
| copy/submit | 18.27 | 38.71 |

原版本的检测数量、累计或遥测变化会使 dashboard chrome key 改变，整套中英文标签重新栅格化，产生绘图尖峰。绘图子进程与计数进程竞争 PS CPU；显示数据采用最新帧，慢绘图会丢过时帧而形成卡顿。

正常停止后 DMA 两通道状态 `0x1002`（idle、无错误），VDMA 状态 `0x11001`（halted、无错误），相机与全部缓冲释放。切换前再次实测状态并核对 owner 无占用；见 [v5 切换记录](v5-switch.json)。

## 修改与验证

- `b509821`：为原逐字中英文标签增加文字遮罩缓存；更新旧计数方向/旧显示布局测试并加入当前 x=320 左→右 RLE 过线验证。
- `7fe464a`：PYNQ `_xrt_download` 会将 `loaded.xclbin` 写入 cwd。原 service 的 cwd 是 release，首次运行生成该文件后会使完整性校验拒绝再次启动。已将生成文件归档到本 boot 的运行目录，并复核原包 56 文件完整性 PASS，见 [归档记录](v4-generated-file-archive.json)。新 `BoardBackend.configure` 只在运行目录内执行下载，正常/异常后均恢复 cwd，并在切换目录前解析 BIT 绝对路径；该分支本轮由 fake Overlay 测试覆盖，没有重新冷启动验证。
- `1b525c8`：将整串英文标题、状态和数值的遮罩纳入同一有界 128 项 LRU，保留原字体、字号、锚点、颜色、布局、Gray8 缩放和标记。

本地命令 `.venv/Scripts/python.exe -m pytest scripts/standalone/test_compositor.py scripts/standalone/test_boot_policy.py scripts/standalone/test_count_controls.py scripts/standalone/test_integrated_run.py -q`：**94 passed in 1.76s**。相关 diff 格式核对通过。

板端 PYNQ Python 3.10.4 / Pillow 9.0.1 / OpenCV 4.5.4，以原包 Droid Sans 和 DejaVu 字体运行纯 CPU 绘图对比，不打开相机或 DMA。每轮 15 帧、前 3 帧预热；累计、显示 FPS 和目标数持续变化，每帧逐像素比较：

| 版本 | 绘图总耗时 p50 (ms) | p95 (ms) | 像素差异 |
|---|---:|---:|---|
| 原 v4 | 325.86 | 331.70 | 对照 |
| 最终缓存版 | 64.12 | 64.76 | 15 帧均为 0 |

原始结果见 [最终板端绘图比较](board-render-comparison-final.json)；[第一轮比较](board-render-comparison.json)仅缓存逐字标签，为中间版本。纯 CPU 对比不能替代整机 FPS 或物料准确率。

## 发布身份

新包 `/opt/material-counter/releases/standalone-20261031-v5-preview-1b525c8`，应用源码提交 `1b525c89ad69d8f41ddf51605ef1fa3055892718`。37 个模块以该提交 Git blob 上传后逐字节 SHA-256 核对；55 文件建索引，56 文件完整性核对 PASS。索引 SHA-256 `8fc121034d31e4d2679271d6277c1476e1a0399bbeec49b27401a569793f0382`。旧包保留；BIT/HWH/native/字体及计数算法未改变。见 [包完整性与源码映射](v5-package-integrity.json)。

本轮按同一 boot 的 READY marker 复用原已加载同哈希 BIT，不重载 overlay。环境和 current 已切换，手动启动 service；自动启用状态保持 disabled。G3/G4、冷启动和真实物料准确率均不据此放行。

## v5 真实相机运行

主进程 PID 2747、绘图子进程 PID 2865，计数 session `33d57ccb-5146-4442-86dd-ae5f1c820386`。启动报告记录 `already_ready`、包完整性 PASS、运行环境核对 PASS；没有再次配置硬件。启动后再次比对 37 个运行模块 SHA-256，差异 0；[运行期间包完整性](v5-running-integrity.json)再次核对 56 文件 PASS。

- [快照 0](v5-session-a42dbb3e-5f7d-4764-8471-1adbe33cf643.snapshot-0.json)：运行 35.99 s，processed=435、submitted=226、count=13、invalid_frames=0、recoveries=[]，最近五个显示采样 9.38–12.89 FPS。
- [快照 1](v5-session-a42dbb3e-5f7d-4764-8471-1adbe33cf643.snapshot-1.json)：运行 111.36 s，processed=1279、submitted=1016、count=115、invalid_frames=0、recoveries=[]，最后五个显示采样 12.66、11.91、12.02、7.74、9.67 FPS；同期处理 10.16–14.12 FPS。VDMA 状态 `0x11000`、video_state=RUNNING、无 DMA 错误位。

用户随后明确反馈：**“明显流畅了，计数也正常”**。当前显示已更接近处理速率，纯绘图尖峰有所降低；主处理链路仍约 52–67 ms（快照最后五个处理窗口），计数/捕获/显示父进程约 104% 单核 CPU、绘图进程约 65% 单核 CPU，因此不将此次现场认可表述为高帧率或物料准确率验收。实际输入未固定，不能由不同场景快照推断严格的整机加速比，准确率仍需独立人工真值。v5 保持运行，不为取得最终分位数报告而再次停止用户现场任务。
