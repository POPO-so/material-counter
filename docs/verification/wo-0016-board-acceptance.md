# WO-0016 板端验收记录

## 当前结论

**Stage A Attempt 5 完成 30/30 帧，端到端吞吐为 11.409 FPS，仍低于 15 FPS 目标；物料准确率尚未验收。** Packer 验证 30 帧，最终两个 DMA 通道 Idle 且无错，缓冲区已释放。未提供人工真值，故观测总计数 0 不能转换为准确率。M6 螺母、垫片及传送带工况仍未验收。

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

## Attempt 2 恢复与 Attempt 3 结果（2026-10-18）

SIGKILL 前再次只读确认 Boot ID 匹配、PID 23673 命令行仍对应 Attempt 2、MM2S/S2MM `DMASR` 均为 `0x00001002`。随后按授权对 PID 23673 执行 `sudo -n kill -9`；一秒后进程及其 sudo 父进程均已退出。执行一次 `sync && echo 3 > /proc/sys/vm/drop_caches` 后，CmaFree 为 112444 KiB。

Attempt 2 活动 Guard 已原样移至 `/var/lib/wo0016/attempt-2/stream-counter-consumed.json`，SHA-256 仍为 `44e312a27b793ab276f8a24c30ba6d1ef6a4f8fddb530c6f272244d7c0ffc671`，目录权限 0700、文件权限 0600。Attempt 3 使用修复后 `stream_counter.py`（SHA-256 `8c281fd0ab73ff42e16068a7b0457d7ab7ba72b90586ad031e152961fe7c6b40`）及 `run_stream_counter.py`（SHA-256 `4f50c6e9384d5fc196af7a07b4728781c06d70b38faf9b98d02ac46ae6263ef6`）。运行前 Boot、Manifest、WO-0015 辅助模块哈希、CMA、摄像头占用、手动曝光 `1/50` 均通过；8080 端口空闲。运行器复用已加载 Overlay 并复核 AFI1，不重新下载 Bitstream。

Attempt 3 原始 JSON 保存在 `/home/xilinx/wo0016/stage-a-30frames-attempt3-result.json`，SHA-256 为 `6e3407ad056e5890cf46530f62293bc96eb879f8ef6f5002a43f50d6decbe2e1`。关键结果如下：

| 指标 | 实测 |
|---|---:|
| 运行状态 | `BOARD_STREAM_COMPLETE` |
| 处理帧数 / 目标 | 30 / 30 |
| Packer 输出帧验证 | 30 / 30 |
| 相机模式 | MJPG 640×400，缩放为 Gray8 320×240 |
| 观测总计数 | 3 |
| 人工真值 / 准确率 | 未提供 / 不可计算 |
| 总耗时 | 74.621 s |
| 端到端吞吐 | 0.402 FPS |
| 最终 MM2S / S2MM DMASR | `0x00001002` / `0x00001002` |
| 缓冲区释放 | 是 |
| 运行前 / 分配后 / 释放后 CmaFree | 95280 / 58688 / 54628 KiB |

运行后只读检查确认 Boot ID 不变、CmaFree 为 73932 KiB、没有残留流计数进程或 `/dev/video0` 占用，8080 监听已关闭，两个 DMA 通道仍 Idle 且无错。Attempt 2 Guard 保留在归档目录；Attempt 3 一次性 Guard 留在活动路径，未清除。此次只证明 30 帧链路完成，不代表达到实时帧率目标，也不证明传送带上 M6 螺母或垫片的计数准确率。

## 尚未完成的物料验收

| 记录项 | 结果 |
|---|---|
| 完成处理帧数 / 30 帧目标 | Attempt 5：30 / 30 |
| DMA / Checker / Packer 已验证完成帧数 | Attempt 5：30；运行后两个 DMA 通道 Idle 且无错 |
| 总计数、人工真值、误差、准确率 | Attempt 5 观测计数 0；无人工真值，误差与准确率不可计算 |
| M6 螺母、垫片及混合物料样本 | NOT RUN |
| 输入 FPS、端到端处理 FPS、浏览器预览 FPS | 相机请求/回读 60 FPS；Attempt 5 端到端 11.409 FPS；浏览器实际显示 FPS 未测 |
| 传送带速度、照明、视野及物料过线工况 | NOT RUN |

Attempt 2、3、4 Guard 已归档，Attempt 5 Guard 保留在活动路径；板端无残留流计数进程，DMA 已静止且缓冲区已释放。当前最高端到端吞吐为 11.409 FPS，未达到 15 FPS 实时门槛；没有物料真值，不能推断 M6 螺母或垫片计数准确率。

## Attempt 4 前的离线性能审计与优化

Attempt 3 的 JSON 只保存总耗时 `74.621 s` 和 `0.402 FPS`，没有 capture、encode、DMA、postprocess 的分阶段耗时；因此无法从该报告把 2.487 s/帧归因到某一具体环节。

源码审计确认，相机由一次 `VideoCapture` 在帧循环外打开并协商 MJPG 640×400，循环退出后释放；Overlay 与 MMIO 同样在单次流启动阶段初始化。每次相机 `read()` 会通过 daemon worker 执行，但没有每帧重新打开相机的行为。旧 Packet 编码器先向量化扩展 Gray8，再调用 `make_packet()` 按 4 字节构造约 57,600 个 Python Beat 对象，随后再次逐 Beat 序列化；这是已定位到的主要软件热路径。Checker/Packer 完成状态使用有限次寄存器回读，ROI 投影采用 NumPy，预览使用 OpenCV JPEG 编码，没有 Python 逐字节 Golden 比对。

优化版保留原来的 230,432 字节 Packet 布局和字段，通过 WO-0016 自有的 `packet_encoder.gray320x240_to_rgb_packet_into()` 使用 `struct.pack_into` 写入包头/包尾、NumPy 广播填入 RGB 三通道及计算 checksum，并直接写入预分配 CMA TX 缓冲区，避免每帧创建 Beat 列表、序列化列表和临时完整 Packet。WO-0015 的 `camera_adapter.py` 原样保留，以免改变已验证 Manifest 依赖哈希。离线测试直接以该原版编码器作为参照，对多个随机 Gray8 帧和 frame_id 检查完整 Packet 字节相等。

主机微基准（Python 3.12.10、NumPy 2.5.2，320×240，5 次）：旧路径平均 `35.908 ms`，优化后的预分配缓冲区原地写入平均 `0.377 ms`，约 `95.33×`；所有迭代的 230,432 字节输出逐字节相同。此结果是 Windows 主机编码微基准，不代表 Cortex-A9 或整机吞吐。

优化版还为有限板端运行加入每帧打点：`avg_capture_ms`（read、Gray8 转换与缩放）、`avg_encode_ms`（Packet 原地写入 CMA TX）、`avg_dma_ms`（缓存同步/Checker arm、双通道传输及 wait）、`avg_postprocess_ms`（Packer 回读校验、ROI 计数和预览 JPEG 更新）。各字段同时记录样本数；不同阶段可与相邻帧并行，均值不应简单求和作为端到端帧周期。

Attempt 4 之前的离线回归：`pytest scripts/wo0016 scripts/wo0015`，`225 passed, 1 skipped`。

## Attempt 4 结果（2026-10-18）

Attempt 3 活动 Guard 在复核 DMA Idle 后原样移动至 `/var/lib/wo0016/attempt-3/stream-counter-consumed.json`；SHA-256 仍为 `44e312a27b793ab276f8a24c30ba6d1ef6a4f8fddb530c6f272244d7c0ffc671`，权限为目录 0700、文件 0600。执行前 Boot ID 为 `f8b24aee-ed7e-430a-b3f3-d26056746bfd`，CmaFree 为 73948 KiB，`/dev/video0` 无占用，曝光为手动模式 1 / 50，8080 空闲，MM2S/S2MM DMASR 均为 `0x00001002`。

优化版 `run_stream_counter.py`、`stream_counter.py`、`packet_encoder.py` 已部署并逐项匹配本地 SHA-256；WO-0015 `camera_adapter.py` 和 `board_preflight.py` 仍匹配 `80438da151d7450caaa9ccf2fd78758eb0943414a8bdb7dafe36d43b5879f8ba` 与 `f6556d811f2b4488b1ad471ea5d8ad565b74ae194b6db0b4356ae907e05de2d6`。运行使用现有 Stage 1 Overlay（`download=False`）、Manifest `2c24709bbe60711c095b5ccbdf4b3582e93e65d98a9538ad2b413136ad942e8b`、`XILINX_XRT=/usr` 及单次 30 帧授权。

板端 JSON `/home/xilinx/wo0016/stage-a-30frames-attempt4-result.json` 的 SHA-256 为 `48a9573bcc5937bfc6da395441a2a8d5cc538007dba85c059fb6dadccd972ca2`；运行日志保存在 `/home/xilinx/wo0016/stage-a-30frames-attempt4.stdout.log`。Attempt 4 活动 Guard 保留于 `/var/lib/wo0016/stream-counter-consumed.json`，SHA-256 为 `44e312a27b793ab276f8a24c30ba6d1ef6a4f8fddb530c6f272244d7c0ffc671`，权限为 0600；它与 Attempt 3 Guard 内容相同，因为复用同一 Boot、Manifest、BIT、HWH 和帧数标识。

| 指标 | Attempt 4 实测 |
|---|---:|
| 状态、帧数 | `BOARD_STREAM_COMPLETE`，30 / 30 |
| 总耗时 / 端到端吞吐 | 7.122 s / 4.213 FPS |
| `avg_capture_ms`（30 样本） | 167.085 ms |
| `avg_encode_ms`（30 样本） | 14.872 ms |
| `avg_dma_ms`（30 样本） | 10.493 ms |
| `avg_postprocess_ms`（30 样本） | 193.861 ms |
| 观测计数 / 人工真值 / 准确率 | 0 / 未提供 / 不可计算 |
| Packer 验证帧数、缓冲区释放 | 30 / 30，是 |
| 最终 MM2S / S2MM DMASR | `0x00001002` / `0x00001002` |
| 运行前 / 分配后 / 释放后 CmaFree | 62300 / 45712 / 47464 KiB |

与 Attempt 3 的 0.402 FPS 相比，本次吞吐约提升 10.48 倍，但仍比 15 FPS 门槛低约 3.56 倍。计时指出 capture 与 postprocess 均值高于 66 ms 目标帧周期；encode 和 DMA 均值较低。因为处理阶段会重叠，这些平均数不相加作为端到端耗时。运行后 Boot ID 未变，CmaFree 为 74372 KiB，摄像头无占用，流进程与 8080 监听均已退出，DMA 仍为 Idle / 无错。该一次实测不提供物料真值，也未运行 M6 螺母、垫片或传送带速度验收。

## Attempt 5 的离线优化（2026-10-18）

采集器在打开相机后一次性请求 MJPG 640×400 和 60 FPS，并回读验证实际帧率至少 50 FPS；开流后的两帧预热及相机协商都排除在稳态 FPS 计时之外。稳态彩色帧直接使用首通道视图，并通过 `cv2.resize(..., INTER_NEAREST)` 缩放；没有逐帧启动 `v4l2-ctl`，也未修改 Manifest 固定的 WO-0015 文件。软件在传输完成后仍对每张 Binary8 掩码执行 Packer 检查和 ROI 计数，预览 JPEG 则以质量 70 每 4 帧更新一次。

新的 JSON 打点分别给出 `avg_capture_ms`、`avg_encode_ms`、`avg_dma_ms`、`avg_packer_check_ms`、`avg_roi_count_ms`、`avg_mjpeg_encode_ms` 和 `avg_postprocess_ms`，并记录每项样本数、相机 FPS 回读、预热帧数及实际预览编码帧数。

离线回归为 `pytest scripts/wo0016 scripts/wo0015`，`228 passed, 1 skipped`。Windows 主机转换微基准（Python 3.12、OpenCV 5.0.0、NumPy 2.5.2，100 次）中，旧的 BGR 转灰度加双线性缩放均值为 2.254 ms，新首通道视图加最近邻缩放为 0.082 ms（约 27.39 倍）。主机 JPEG 基准为质量 85 的 0.128 ms、质量 70 的 0.126 ms；每四帧编码的主机摊销值为 0.032 ms/输入帧。此微基准不包含 V4L2 读帧、Cortex-A9 负载、DMA、Packer 或 ROI 计数，不能作为板端吞吐证据。

## Attempt 5 受控实测结果（2026-10-18）

本次运行对应提交 `3a4349f8fde1946ae57bff9956842f2ee0640c58`。运行前只读检查确认 Boot ID 为 `f8b24aee-ed7e-430a-b3f3-d26056746bfd`、CmaFree 为 74372 KiB、`/dev/video0` 空闲、`auto_exposure=1` / `exposure_time_absolute=50`，8080 端口空闲，两个 DMASR 都为 `0x00001002`。V4L2 能力列表中 MJPG 640×400 支持 60、120 和 210 FPS。运行器请求 60 FPS，回读 60 FPS；完成两帧预热后开始稳态计时。运行复用已加载的 50 MHz Overlay（`download=False`），AFI1 精确回读 RD=`0x00000001`、WR=`0x00000f01`，没有下载 BIT。

板端原始 JSON 位于 `/home/xilinx/wo0016/stage-a-30frames-attempt5-result.json`，本地副本为 [`stage-a-30frames-attempt5-result.json`](../../data/results/wo0016-stage-a-attempt5-20261018/stage-a-30frames-attempt5-result.json)，SHA-256 为 `46006ef24ae9dc0e31b97ee5f017bec18e65f128920aa6de9c452815f1f49244`。板端 stdout 日志 SHA-256 与 JSON 相同。部署脚本 SHA-256：`run_stream_counter.py` `ac8680972c672959dcae9cdeb3f0adc95ec19ea9ea449a72f4bb7aa108da0b50`、`stream_counter.py` `a176154dacda6b2059b71c374f0377c979b8293eecec469ef1b0c2a5d0e8c547`、`mjpeg_preview.py` `d8136bc34d360837e954efbd9a2813f9f905cbff76b78bccd927cc4ab76d112`。WO-0015 `camera_adapter.py` 与 `board_preflight.py` 仍分别匹配 `80438da151d7450caaa9ccf2fd78758eb0943414a8bdb7dafe36d43b5879f8ba`、`f6556d811f2b4488b1ad471ea5d8ad565b74ae194b6db0b4356ae907e05de2d6`。

| 指标 | Attempt 5 实测 |
|---|---:|
| 状态、帧数 | `BOARD_STREAM_COMPLETE`，30 / 30 |
| 总耗时 / 端到端吞吐 | 2.630 s / 11.409 FPS |
| `avg_capture_ms`（30 样本） | 38.231 ms |
| `avg_encode_ms`（30 样本） | 13.869 ms |
| `avg_dma_ms`（30 样本） | 10.155 ms |
| `avg_packer_check_ms`（30 样本） | 0.535 ms |
| `avg_roi_count_ms`（30 样本） | 51.054 ms |
| `avg_mjpeg_encode_ms`（8 个编码样本） | 49.696 ms |
| `avg_postprocess_ms`（30 样本） | 68.735 ms |
| 预览 JPEG | 每 4 帧一次，质量 70；8 帧已编码 |
| 观测计数 / 人工真值 / 准确率 | 0 / 未提供 / 不可计算 |
| Packer 验证帧数、缓冲区释放 | 30 / 30，是 |
| 最终 MM2S / S2MM DMASR | `0x00001002` / `0x00001002` |
| 运行前 / 分配后 / 释放后 CmaFree | 59136 / 43060 / 43540 KiB |

Attempt 5 比 Attempt 4 的 4.213 FPS 提升约 2.71 倍；capture 从 167.085 ms 降到 38.231 ms，postprocess 从 193.861 ms 降到 68.735 ms，但都仍高于设定子阶段目标，整机吞吐也未达到 15 FPS。子阶段显示 ROI 计数为主要剩余开销；JPEG 每次编码均值 49.696 ms，虽然每四帧才编码一次。运行后 Boot ID 未变，CmaFree 为 74076 KiB，摄像头无占用、8080 已关闭、DMA 仍 Idle 且无错、缓冲区已释放。Attempt 4 Guard 原样归档至 `/var/lib/wo0016/attempt-4/stream-counter-consumed.json`，SHA-256 保持 `44e312a27b793ab276f8a24c30ba6d1ef6a4f8fddb530c6f272244d7c0ffc671`；Attempt 5 的一次性 Guard 已消费并保留在活动路径，未执行第二次实测。此次没有人工真值或传送带物料工况，不能据此宣称计数准确率。
