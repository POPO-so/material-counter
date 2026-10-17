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

## Attempt 6 缓存读取与预览解耦（2026-10-18）

优化提交为 `60b2610eee84264c8d8a030c8654dd7eaa32ecde`。每个 S2MM 缓冲区完成传输后先执行 `sync_from_device`，再单次复制到预分配的 Cached `uint8` 掩码；Packer 检查、ROI 计数和预览快照均读取该缓存。MJPEG 发布回调只复制最新快照；JPEG 编码移至低优先级后台线程，且只在存在流客户端时执行。

Attempt 5 活动 Guard 在归档前 SHA-256 为 `44e312a27b793ab276f8a24c30ba6d1ef6a4f8fddb530c6f272244d7c0ffc671`，原样移至 `/var/lib/wo0016/attempt-5/stream-counter-consumed.json`，归档后 SHA 未变；目录/文件权限为 `0700/0600`。本次运行前 Boot ID、CMA 水位、摄像头空闲状态、手动曝光 `1/50`、8080 端口和两个 `DMASR=0x00001002` 均通过只读核验。Manifest SHA-256 为 `2c24709bbe60711c095b5ccbdf4b3582e93e65d98a9538ad2b413136ad942e8b`。部署脚本 SHA-256：`run_stream_counter.py` `c0c8b1137cd6826c805bd54677a134cb9bdec2dcf976abd4fa64132c1f8bf6d6`、`stream_counter.py` `c88368d65b0abbdac3a508a386367b4dd2162c8361218eef5c87e515f658c74c`、`mjpeg_preview.py` `f9555feec0a301704accc6550a2de5e49d75c91fce1e8141205b078c34a31c30`，均与提交文件匹配。

板端原始结果位于 `/home/xilinx/wo0016/stage-a-30frames-attempt6-result.json`，本地副本为 [`stage-a-30frames-attempt6-result.json`](../../data/results/wo0016-stage-a-attempt6-20261018/stage-a-30frames-attempt6-result.json)，SHA-256 为 `bebfeca5f2c586a61f3aaacc1c5cf0949656a854641a4c5ec12eabb61c6f83b7`。运行使用已加载 Overlay（`download=False`），未重载 Bitstream，也未自动重试。

| 指标 | Attempt 6 实测 |
|---|---:|
| 状态、帧数 | `BOARD_STREAM_COMPLETE`，30 / 30 |
| 总耗时 / 端到端吞吐 | 2.609 s / 11.498 FPS |
| `avg_capture_ms`（30 样本） | 38.888 ms |
| `avg_encode_ms`（30 样本） | 11.922 ms |
| `avg_dma_ms`（30 样本） | 8.328 ms |
| `avg_rx_sync_ms` / `avg_rx_copy_ms`（各 30 样本） | 0.366 / 0.521 ms |
| `avg_packer_check_ms`（30 样本） | 0.252 ms |
| `avg_roi_count_ms`（30 样本） | 25.909 ms |
| `avg_mjpeg_encode_ms`（30 次快照移交） | 0.307 ms |
| `avg_mjpeg_background_encode_ms` | 无样本；无客户端请求，0 帧编码 |
| `avg_postprocess_ms`（30 样本） | 31.945 ms |
| 观测计数 / 人工真值 / 准确率 | 0 / 未提供 / 不可计算 |
| Packer 验证帧数、缓冲区释放 | 30 / 30，是 |
| 最终 MM2S / S2MM DMASR | `0x00001002` / `0x00001002` |
| 运行前 / 分配后 / 释放后 CmaFree | 56132 / 40444 / 41424 KiB |

Attempt 6 的 30 帧链路通过，但吞吐仍低于 15 FPS 目标；ROI 计数 25.909 ms 也未达到 2 ms 目标。RX 同步与复制分别低于 1 ms；预览移交降至 0.307 ms，且本轮没有同步 JPEG 编码。由于没有客户端，实际后台 JPEG 编码耗时未测得。计数耗时仍是主要待优化项。运行后 Boot ID 未变，CmaFree 为 67184 KiB，`/dev/video0` 无占用、8080 已关闭、DMA 保持 Idle / 无错。Attempt 6 的一次性 Guard 留在活动路径且已消费，SHA-256 为 `44e312a27b793ab276f8a24c30ba6d1ef6a4f8fddb530c6f272244d7c0ffc671`；由于 Guard 内容只编码 Boot、Manifest、Bitstream、HWH、stage 和帧数，本次与 Attempt 5 归档 Guard 内容相同。未提供人工真值或传送带物料工况，不据此宣称计数准确率。

### Attempt 6 后的离线 ROI 快速路径

板端单次运行后，`_extract_roi_detections` 又做了离线优化：直接把 Cached `uint8` ROI 视图交给 CCL，不再为二值化创建布尔整帧副本；若 OpenCV 提供接口，则使用 `CCL_GRANA`，并先用 NumPy 向量化筛掉面积不足的标签，避免 Python 遍历无效连通域。新增用例检查 ROI 与输入缓存共享内存，现有合成样本和 WO-0015 实拍样本仍与旧面积、质心和投影结果一致。

这项后续改动在 Attempt 6 之后当时只完成了离线回归，Attempt 6 结果仍只对应提交 `60b2610eee84264c8d8a030c8654dd7eaa32ecde`。以下 Attempt 7 记录了该 ROI 快速路径和单槽采集预取流水线的首次板端测量。

## Attempt 6 的延迟核算与 Attempt 7 离线改动

Attempt 6 原始 JSON 的 `elapsed_seconds=2.609150131`、`frames_processed=30`，因此 `wall_frame_ms=2.609150131 × 1000 / 30 = 86.971671 ms`。分阶段均值为：capture 38.888 ms、encode 11.922 ms、DMA 8.328 ms、postprocess 31.945 ms，ROI 计数 25.909 ms。四个顶层阶段均值相加为 91.083 ms，比单帧墙钟时间多 4.111 ms；这些阶段跨帧重叠，且 ROI 计数是 postprocess 的子项，所以不能把阶段均值相加解释成单帧墙钟构成。RX 同步/复制分别为 0.366/0.521 ms，预览快照移交为 0.307 ms；后台 JPEG 没有样本，因为没有客户端请求。

Attempt 7 最终源码提交为 `e305b02959e9226b4747cbcbeb12e3e9df146029`（分支 `codex/wo0016-counting-20261017`）。ROI 默认限制在过线中心上下各 24 行，即 `cached_mask[96:145, :]` 的 49×320 视图；前景数不足 `min_area=12` 时短路，不进入连通域标记；否则只对该 ROI 调用 `connectedComponentsWithStats` / `CCL_GRANA`，标签类型为 `CV_16U`。ROI 投影、CCL 与计数输入均来自 S2MM 后复制得到的 Cached 掩码。采集线程使用单槽预取并将 V4L2 缓冲数设为 1；消费者失败时会取消等待中的下一帧请求，并在关闭源时保留原始异常。MJPEG 编码仍由低优先级预览线程在有客户端请求时处理。

离线回归命令为 `pytest scripts/wo0016 scripts/wo0015`，结果 `239 passed, 1 skipped`。在 Windows/Python 3.14 上运行；WO-0015 测试的 `ORIGINAL_ROOT` 仅在 pytest 进程内指向只读临时源样本副本，仓库测试和受保护源样本未改动。Attempt 7 运行脚本 SHA-256：`run_stream_counter.py` `d8facbcbd7e91a09a3ff704e88f0505d75b6315fd32098de2d0d62568452ddb3`，`stream_counter.py` `dabcc9dba7732e141c880774d2af9e487f337bfbfbc49a2270f66e4a90f79d4d`。其他运行文件 `mjpeg_preview.py` 与 `packet_encoder.py` 分别为 `f9555feec0a301704accc6550a2de5e49d75c91fce1e8141205b078c34a31c30`、`19a6c642088153088fd98876b8e28804d39a59f5fe7e5a7f6b54c873a4595bc8`。板端实际选用的 WO-0015 bundled helper 哈希与本地一致：`board_once.py` `95204794257a3522afc3b7a7cae944f1c1e8bbde7b8d131a19faf32f29235525`、`board_preflight.py` `f6556d811f2b4488b1ad471ea5d8ad565b74ae194b6db0b4356ae907e05de2d6`、`camera_adapter.py` `80438da151d7450caaa9ccf2fd78758eb0943414a8bdb7dafe36d43b5879f8ba`。

## Attempt 7 Stage A 实测（2026-10-18）

运行前最后一次只读检查确认 Boot ID `f8b24aee-ed7e-430a-b3f3-d26056746bfd`、CmaFree 70,928 KiB（高于 32,768 KiB 门槛）、`/dev/video0` 空闲、手动曝光 `1/50`、两个 `DMASR=0x00001002`、8080 空闲。Attempt 6 活动 Guard 原样归档到 `/var/lib/wo0016/attempt-6/stream-counter-consumed.json`，前后 SHA-256 均为 `44e312a27b793ab276f8a24c30ba6d1ef6a4f8fddb530c6f272244d7c0ffc671`；目录/文件权限为 `0700/0600`。部署后 `run_stream_counter.py`、`stream_counter.py` 与上表 SHA 逐项一致。使用原固定 Manifest SHA-256 `2c24709bbe60711c095b5ccbdf4b3582e93e65d98a9538ad2b413136ad942e8b` 和单次 board-mode 授权执行 30 帧命令，没有自动重试或重新加载 Overlay。

原始结果保存在板端 `/home/xilinx/wo0016/stage-a-30frames-attempt7-result.json`，本地副本为 [`stage-a-30frames-attempt7-result.json`](../../data/results/wo0016-stage-a-attempt7-20261018/stage-a-30frames-attempt7-result.json)，SHA-256 为 `fc0382e9146ecd79583b7eb1fd898ce901aa05c808118e2acab3e838e5f63c1f`。一次实测数据如下：

| 指标 | Attempt 7 实测 |
|---|---:|
| 状态、处理帧数 | `BOARD_STREAM_COMPLETE`，30 / 30 |
| Packer 验证帧数 / 缓冲区释放 | 30 / 30，是 |
| 总耗时 / wall frame / 端到端吞吐 | 1.986029222 s / 66.200974 ms / 15.105518 FPS |
| `avg_capture_ms` / `avg_encode_ms` / `avg_dma_ms` | 40.011 / 14.627 / 12.125 ms |
| `avg_postprocess_ms` / `avg_roi_count_ms` | 35.791 / 27.070 ms |
| `avg_packer_check_ms` / `avg_rx_sync_ms` / `avg_rx_copy_ms` | 0.337 / 0.443 / 0.919 ms |
| MJPEG 快照移交 / 后台编码 | 0.350 ms / 无样本、0 帧编码 |
| 观测计数 / 人工真值 / 准确率 | 0 / 未提供 / 不可计算 |
| 最终 MM2S / S2MM DMASR | `0x00001002` / `0x00001002` |
| runner admission / 四缓冲分配后 / 释放后 CmaFree | 54,192 / 38,544 / 40,084 KiB |

Stage A 的 30 帧吞吐门槛达到：15 FPS 对应 66.667 ms/帧，本次 wall frame 为 66.201 ms，余量约 0.466 ms。相较 Attempt 6 的 86.972 ms/帧，下降约 20.771 ms；吞吐从 11.498 提升到 15.106 FPS。ROI 计数均值为 27.070 ms，仍未达到离线目标 `<=2.5 ms`，且比 Attempt 6 的 25.909 ms 高 1.161 ms；因此吞吐门槛通过不代表 ROI 子阶段目标也通过。当前 `roi_count_ms` 计时包围完整的 `MaskCounter.update()`，包括线程调度、Binary8 检查、ROI 行列投影、可能发生的 CCL 和跟踪；结果 JSON 没有单独记录 ROI 前景像素数、CCL 是否触发或 CCL 本体耗时，不能仅凭这一字段把 27.070 ms 归因给连通域实现。预览移交小于 1 ms，但没有客户端，所以实际 JPEG 后台编码耗时仍未测得。

运行结束后只读复核 Boot ID 未变、CmaFree 70,428 KiB、摄像头空闲、曝光仍为手动 `1/50`、8080 已关闭、两个 DMA 通道仍为 Idle / 无错，且无残留 `run_stream_counter.py` 进程。JSON 记录 `buffers_released=true` 和最终状态 `4098`（`0x1002`）/`4098`。Attempt 7 一次性 Guard 保留在活动路径且已消费，SHA-256 为 `44e312a27b793ab276f8a24c30ba6d1ef6a4f8fddb530c6f272244d7c0ffc671`。本次没有人工真值或传送带物料工况，不据此宣称 M6 螺母/垫片计数准确率。
