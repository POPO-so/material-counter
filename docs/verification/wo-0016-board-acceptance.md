# WO-0016 板端验收记录

## 当前结论

**Stage A Attempt 2 进入 BOARD_STREAM_FAIL_HOLD，完成帧数为 0；板端计数验收未通过。** 首帧数据已到达 DMA 通道启动阶段，等待完成时发生异常，DMA 是否完成/仍在运行无法确认。运行器按保护逻辑保留全部 CMA 缓冲区并进入永久 fail-hold。板端 Python 进程仍在运行；不得中断、杀进程、复位 DMA、释放 CMA、回收缓存或重试。

## Attempt 1 依赖对齐与 Guard 隔离

WO-0015 规范依赖已从板端已验证隔离目录恢复并与 Manifest 对齐：

| 文件 | SHA-256 |
|---|---|
| board_preflight.py | f6556d811f2b4488b1ad471ea5d8ad565b74ae194b6db0b4356ae907e05de2d6 |
| camera_dma_oneshot.py | 5b9684f4b02988e8a822c36ef0ac7faec78c0a7980f2fca1574afd3027c9df28 |

board_once.py、camera_adapter.py、prepare_vectors.py 同样与板端规范隔离包一致。源码对齐提交为 b1cf74a3934ea10a883e1707a87bf830706015b1。

Attempt 1 因关键字参数不匹配，在 DMA 启动前失败。其 Guard 没有删除，而是按授权原样移动到：

    /var/lib/wo0016/attempt-1/stream-counter-consumed.json

归档目录权限为 0700，Guard 权限为 0600；归档文件 SHA-256 仍为 44e312a27b793ab276f8a24c30ba6d1ef6a4f8fddb530c6f272244d7c0ffc671。

## Attempt 2 前置检查和部署

已部署 Commit 312bb369a31f36d908b948e9eda659ce2666b9af 的两个脚本，板端 SHA-256 完全匹配：

| 文件 | SHA-256 |
|---|---|
| stream_counter.py | 3a66dacc8501f2377a080098047ab6ac379fef4b8c00d1581986462da6f622d8 |
| run_stream_counter.py | 4f50c6e9384d5fc196af7a07b4728781c06d70b38faf9b98d02ac46ae6263ef6 |

执行一次标准 sync/drop_caches 后，CmaTotal 为 131072 KiB、CmaFree 为 113868 KiB。Boot ID 仍为 f8b24aee-ed7e-430a-b3f3-d26056746bfd；/dev/video0 无占用；auto_exposure 为手动模式 1，exposure_time_absolute 为 50。Manifest SHA-256 为 2c24709bbe60711c095b5ccbdf4b3582e93e65d98a9538ad2b413136ad942e8b。

## Attempt 2 运行结果（2026-10-18）

本次运行使用 50 MHz Stage 1 Manifest、--max-frames 30、XILINX_XRT=/usr、Overlay download=False 和单次授权开关。运行器通过了 Boot/Manifest/Overlay、Checker/Packer 签名、AFI1 和 CMA 地址检查：

- AFI1 精确回读：RD=0x00000001、WR=0x00000f01。
- Checker/Packer 签名：0x574f3134 / 0x574f3135。
- CMA admission 时 CmaFree=95460 KiB；四个 CMA 缓冲区分配后为 59684 KiB，仍高于 32768 KiB 门槛。
- status=BOARD_STREAM_FAIL_HOLD，board_accessed=true，pynq_imported=true，frames_processed=0。
- 异常为 AttributeError: 'NoneType' object has no attribute 'wait'。在流处理逻辑中，S2MM 与 MM2S transfer 启动后才等待完成；因此本次传输完成状态不确定，不能记为 DMA PASS。
- buffers_released=false；hold_reason 为 DMA ownership or channel-idle state is uncertain; retain all CMA buffers。
- 只读进程快照显示 sudo PID 23668 和 Python PID 23673 仍运行于板端。没有对 DMA 寄存器作额外读写，也没有终止该进程。

Attempt 2 原始 JSON 保存在 /home/xilinx/wo0016/stage-a-30frames-attempt2-result.json，SHA-256 为 161fa38f8561915d9e45f26fd741f72c3f9d511c11b0d1168c2a53dbc50b3fe9。新的活动 Guard 位于 /var/lib/wo0016/stream-counter-consumed.json，SHA-256 为 44e312a27b793ab276f8a24c30ba6d1ef6a4f8fddb530c6f272244d7c0ffc671。其哈希与归档 Attempt 1 Guard 相同，因为两者记录同一 Boot、Manifest、BIT、HWH、stage 和帧数；两份文件通过路径分别保留。

## 尚未完成的物料验收

| 记录项 | 结果 |
|---|---|
| 完成处理帧数 / 30 帧目标 | 0 / 30 |
| DMA / Checker / Packer 已验证完成帧数 | 0；DMA 完成状态不确定，处于 fail-hold |
| 总计数、人工真值、误差、准确率 | NOT RUN |
| M6 螺母、垫片及混合物料样本 | NOT RUN |
| 输入 FPS、端到端处理 FPS、浏览器预览 FPS | NOT RUN |
| 传送带速度、照明、视野及物料过线工况 | NOT RUN |

当前不得清除任何 Guard、重启/终止 fail-hold 进程、复位或重启 DMA/Overlay、释放缓冲区或再次运行。必须由架构师审阅 Attempt 2 原始 JSON 和板卡恢复状态，再另行确定安全恢复方案及新授权。当前记录不构成 Stage A 通过，也不能用于推断计数准确率或实板 FPS。
