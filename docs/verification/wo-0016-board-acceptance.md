# WO-0016 板端验收记录

## 当前结论

**Stage A Attempt 2 进入 BOARD_STREAM_FAIL_HOLD，完成帧数为 0；板端计数验收未通过。** 原始 JSON 只记录异常摘要，没有 Python traceback。之后按授权只读读取了 AXI DMA 状态：MM2S/S2MM 均为 Idle、无错误。按授权向 Python fail-hold 进程发送一次 SIGTERM，但它仍在运行；源码安装了忽略 SIGTERM/SIGINT 的处理函数。进程仍持有 CMA 缓冲区，活动 Guard 仍在原路径。尚未执行 SIGKILL、缓存回收、Guard 归档、DMA/Overlay 复位或重试。

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
- 异常摘要为 `AttributeError: 'NoneType' object has no attribute 'wait'`。流处理先调用 S2MM 与 MM2S 的 `transfer()`，再等待完成；因此本次 0 帧，不能记为 DMA PASS。
- buffers_released=false；hold_reason 为 DMA ownership or channel-idle state is uncertain; retain all CMA buffers。
- 只读进程快照显示 sudo PID 23668 和 Python PID 23673 仍运行于板端。没有对 DMA 寄存器作额外读写，也没有终止该进程。

Attempt 2 原始 JSON 保存在 /home/xilinx/wo0016/stage-a-30frames-attempt2-result.json，SHA-256 为 161fa38f8561915d9e45f26fd741f72c3f9d511c11b0d1168c2a53dbc50b3fe9。新的活动 Guard 位于 /var/lib/wo0016/stream-counter-consumed.json，SHA-256 为 44e312a27b793ab276f8a24c30ba6d1ef6a4f8fddb530c6f272244d7c0ffc671。其哈希与归档 Attempt 1 Guard 相同，因为两者记录同一 Boot、Manifest、BIT、HWH、stage 和帧数；两份文件通过路径分别保留。

## Attempt 2 静止状态只读核验与恢复门

原始 JSON 已拉取到本地临时审阅目录并复核 SHA-256 为 `161fa38f8561915d9e45f26fd741f72c3f9d511c11b0d1168c2a53dbc50b3fe9`；JSON 未包含 traceback，仅有上述异常摘要。读取前先从 Manifest 对应 HWH 复核其 SHA-256 为 `bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6`，确认 `axi_dma_0` 基址为 `0x40400000`。

在不加载 Overlay 的情况下，通过 `/dev/mem` 的只读映射读取一次 DMA 寄存器：

| 寄存器 | 读值 |
|---|---:|
| MM2S_DMACR | `0x00010003` |
| MM2S_DMASR | `0x00001002` |
| S2MM_DMACR | `0x00010003` |
| S2MM_DMASR | `0x00001002` |

两通道 `Idle=1`、`Halted=0`，错误位 4–6 均为 0。Boot ID 仍为 `f8b24aee-ed7e-430a-b3f3-d26056746bfd`。进程快照确认 PID 23668（sudo）及 PID 23673（Python）存在。根据本次授权，只向 PID 23673 发送过一次 `sudo -n kill -TERM`；随后进程仍为 `SLl`，因为 `board_once.py` 的 fail-hold 明确忽略 SIGTERM。当前仍不能释放缓冲区或清理 CMA；若要继续恢复，需在重新只读确认 DMA 仍 Idle/无错后另行授权强制终止。

本地复现测试使用 PYNQ 通道形状：`transfer()` 返回 `None`、存在 `wait_async()`、且中断对象为 `None`。板端安装的 PYNQ `dma.py` 中 `wait_async()` 会执行 `await self._interrupt.wait()`；此路径与 Attempt 2 的异常吻合。修复后的 `transfer_async()` 优先在线程中调用 `channel.wait()`，不依赖传输返回值或未配置的中断句柄。离线回归结果：`210 passed, 1 skipped`。

## 尚未完成的物料验收

| 记录项 | 结果 |
|---|---|
| 完成处理帧数 / 30 帧目标 | 0 / 30 |
| DMA / Checker / Packer 已验证完成帧数 | 0；运行时等待失败后仍进入 fail-hold；后续只读快照显示两个 DMA 通道 Idle 且无错 |
| 总计数、人工真值、误差、准确率 | NOT RUN |
| M6 螺母、垫片及混合物料样本 | NOT RUN |
| 输入 FPS、端到端处理 FPS、浏览器预览 FPS | NOT RUN |
| 传送带速度、照明、视野及物料过线工况 | NOT RUN |

当前不得清除任何 Guard、重启/终止 fail-hold 进程、复位或重启 DMA/Overlay、释放缓冲区或再次运行。必须由架构师审阅 Attempt 2 原始 JSON 和板卡恢复状态，再另行确定安全恢复方案及新授权。当前记录不构成 Stage A 通过，也不能用于推断计数准确率或实板 FPS。
