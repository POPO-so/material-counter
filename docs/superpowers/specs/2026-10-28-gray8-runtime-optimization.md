# Gray8 运行开销优化执行记录

用户已在当前对话批准按顺序优化。本记录仅管理现有 320×240 Gray8 基线的运行开销；另一条 640×400 RLE 实现沿用其独立设计和在途工作，不混入本次提交。

## 顺序与验收

- [ ] 在独立板端目录建立未修改源码基线，记录 boot、位流/源码哈希、进程所有权、帧数、墙钟/CPU 时间和场景。不停止或覆盖其他会话进程；若设备被占用则先做无相机微基准并如实记录限制。
- [x] 将 `scripts/gray8/run_gray8_counter.py` 的 full 逐像素验证与 runtime 模式分离；runtime 保留签名、DMA、帧身份、长度、错误和输出 checksum，self-test 保持 full。先运行行为失败测试，再实现、回归与单独提交。
- [x] 核对 `mjpeg_preview.py` 的绘制/主图编码/二值图编码/发布，避免无人订阅的额外 JPEG；只在实际调用与订阅语义明确后修改。桌面图传已有不使用 JPEG 的路径，不修改其在途 UI。
- [x] 已核对板端 PYNQ 3.1.1 源码，新增隔离的 `scripts/gray8/dma_polling.py` 和 `--dma-wait cooperative`，以 asyncio sleep 有界让出 CPU，不再启动 TX/RX wait 线程。取消/故障/长度错保留 active buffer，成功路径完成缓存同步和驱动收尾；默认 driver 等待暂保留，cooperative 板测待完成。
- [ ] 核对采集、DMA、后处理槽位依赖，基于计时只优化实际阻塞点，不盲目增加线程、队列或内存拷贝。
- [ ] 相关回归、同版本独立板端对照、最终 diff 审查；只提交本次文件。无物料真值不声称准确率通过；无板测不声称 FPS 收益。

## 验证命令

使用 `C:/Users/14173/AppData/Local/Programs/Python/Python312/python.exe -B -m pytest -p no:cacheprovider --basetemp <系统临时目录中的独立目录> <修改相关测试> -q`。先核对本次修改测试，必要时运行 Gray8、stream、camera 和预览相关回归，保留既有失败与环境问题的区分。

## 工作区边界

开始时已有 `PLAN.md`、桌面 Qt 文件、GUI 说明、Gray8 构建脚本的修改，以及 pytest 历史目录删除/新增；这些变化与在途 RLE 文件均不纳入本任务提交。保持当前分支，不推送。

## 当前进展

软件相关回归 249 passed、1 个既有预览文字精确纯白像素断言失败；DMA 双槽/FAIL_HOLD 集成通过。计数 wall/CPU、派发等待和槽位等待已拆开测量，调度本身尚未凭推测修改。板端基线合成自检 3 帧通过，随后 RLE 会话切换位流导致 Gray8 相机基线在启动检查阶段被拒绝；用户指定等另一会话释放板卡再测。此时未验证新策略板端性能，不声称达到 30/45 FPS。完整证据及复测顺序见 [优化记录](../../../data/results/gray8-runtime-perf-20261028/README.md)。
