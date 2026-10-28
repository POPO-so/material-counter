# Gray8 运行开销优化：软件验证与板测等待

日期：2026-10-28（北京时间）。状态：**软件修改与回归已完成；优化后的 FPGA 板端吞吐未验证**。用户指定等待另一 RLE 会话释放板卡后再做后续板测。本会话没有覆盖 RLE 位流、没有停止该会话程序，也没有修改在途 RLE 实现或桌面界面。

## 本次修改

1. `run_gray8_counter.py --validation full|runtime`：默认 full；self-test 强制 full。runtime 不重解码发送包、不生成软件期望掩膜、不逐像素比较，但仍验证硬件签名、状态、帧身份、长度/帧计数、输入 PL 错误状态与返回掩膜的输出 checksum。结果区分 `GRAY8_PIXEL_VERIFIED` 与 `GRAY8_RUNTIME_CHECKED`。
2. `PeriodicMjpegPublisher` 单独跟踪二值预览订阅，只在存在 `/binary.mjpg` 订阅时编码第二张裸掩膜 JPEG；无人订阅仍不编码。主图绘制/编码、二值编码、发布分别记录 wall/thread CPU 时间；不将完整预览耗时说成单次 JPEG 压缩。
3. `--dma-wait cooperative`：针对已核对的 PYNQ 3.1.1 simple DMA，每次状态读取后以 asyncio sleep 让出 CPU，间隔目标 0.5 ms；没有逐传输新建等待线程。成功路径维持 PYNQ RX invalidate、transferred 与 active buffer 收尾；取消、错误、长度错不释放 active buffer，由双槽流处理进入 FAIL_HOLD。PYNQ 原 `driver` 等待仍是默认基线，未因本机测试就切为生产默认。
4. 去掉 Gray8 入口在 PYNQ wait 已 invalidate 后的重复 RX `sync_from_device()`。原生 wait 和 cooperative 成功返回都保证同步后才复制掩膜。
5. 将 `roi_count` 分离为实际计数 worker 的 wall/CPU 与派发/返回等待，另记录帧源及空闲槽等待。原调度、槽位、跟踪语义保持不变；暂未凭推测改线程数量或丢帧策略。

## 软件证据

- Gray8 模式测试 RED：缺少 validation 参数时 2 个新行为失败；GREEN：最初 24 passed。
- DMA 等待测试 RED：新模块不存在，10 项按预期失败；GREEN：状态、收尾、错误、取消、重复传输、非法参数通过。CLI 的 cooperative 选项也先按预期失败，再实现通过。
- 双槽集成及 stream 计时：66 passed（包含 normal completion 与 stall/FAIL_HOLD）。
- `software-regression.txt`：同一相关回归 **249 passed、1 failed，28.96 s**。覆盖 Gray8、stream、运行入口、预览 HTTP、camera-only 与网页启动。失败为既有 `test_preview_draws_red_virtual_line_and_count_text_on_binary_mask` 对抗锯齿文字要求精确纯白像素；本任务未修改绘制函数或放宽该断言。
- 在预览测试中增加 join 后再读取后台计时，相关 5 项再次通过，避免发布通知与计时样本追加之间的测试竞态。
- Manager 审查并核对只读边界检查结果：取消、错误、长度异常均在清除 active buffer 前抛错，stream 与入口会保持 FAIL_HOLD。板端实际读取的 PYNQ 3.1.1 源码确实从 `offset+0x28` 更新 transferred；MM2S 命令长度读回本身不独立证明像素全部传送，仍需 PL 字节/拍/像素计数共同检查。同步 MMIO 访问若因硬件异常不返回，asyncio 超时不能强制中断该同步调用；本次不修改这一底层平台限制。

测试使用 CPython 3.12、`-B -m pytest -p no:cacheprovider --basetemp <系统临时目录>`；本机运行不证明 PYNQ 吞吐提升。

## 板端事实与中断

- SSH 可用，boot ID `71c03d07-ef08-47f2-b871-725f30f5d5e1`。只读查询确认 PYNQ **3.1.1**：其 simple-DMA `wait()` 为寄存器忙轮询，RX 完成时 invalidate，再更新 transferred/清 active buffer；transfer() 已执行 TX flush。
- 将未修改运行源码快照上传到独立 `/home/xilinx/gray8-perf-20261028-ordered/baseline`，46 个 Python 文件上传后逐个 SHA-256 核对；`baseline-source-sha256.json` 记录实际文件哈希。export 指向既有已验 Gray8 制品，manifest SHA-256 为 `b81e6ea09cba48d643b3f09ae88b9ea784176c81ebc53799f1af38aedb0cd484`。
- `self-test.json`：3/3 合成帧全像素正确，状态 `GRAY8_PIXEL_VERIFIED`，buffers_held=false。该文件是**修改前源码基线自检**，不是新 cooperative 路径的板端验证。
- 开始 180 帧 camera 基线前，另一会话将加载路径改成 `/home/xilinx/rle-20261028/data/results/gray8-rle-20261028-build/export/rle.bit`。Gray8 admission 在 DMA 与相机采集前报 `PYNQ global state loaded BIT path differs from the reviewed artifact`。本会话没有追加 load-overlay、重试或复位。
- `camera-180.log` 与 `camera-180.cpu` 记录这次**启动检查失败**；其 40.289 秒及 CPU 值仅属于启动失败过程，不能计算每帧 CPU、有效 FPS 或优化收益。最初尝试 `/usr/bin/time` 发现板上无该程序（exit 127，未执行 runner），随后使用 Bash 自带 time 记录这次启动检查。

## 释放板卡后的下一步

先重新读取 boot、运行进程、相机占用、位流身份和 DMA 状态，核对独占权；不能沿用本次 boot 或因 idle 就视作另一会话已结束。按相同光照/物料/阈值/profile，在独立目录依次测 `full+driver`、`runtime+driver`、`runtime+cooperative`，先 3 帧 full self-test，再短测，成功后重复长测。仅在独占确认及必要时恢复匹配 Gray8 位流，不自动覆盖另一会话。

记录总墙钟、process CPU、各阶段 wall/thread CPU、帧源/槽位等待和缺帧情况；同帧统计和计数结果应一致。预览另做无客户端、仅主图、主图+二值图三种对照。RLE 全分辨率/特征协议沿用独立实现与验收，不把其结果归入现有 Gray8 优化收益。无人工真值不宣称物料准确率通过。

## 第二轮：补齐调度、内存与粘连开销

用户继续要求直接优化全部项，本轮没有再次访问板卡或改变等待 RLE 会话释放板卡的安排。

| 优化类 | 本轮落地与验证 |
| --- | --- |
| 采集/处理解耦 | 原专用相机线程、预取和双 DMA 槽继续使用。新增 `--check-execution worker`（默认），将 Gray8 完成校验移出事件循环；保留 `inline` 对照。集成测试在第 1 帧校验仍阻塞时确认第 0 帧消费者可完成，但第 1 帧不能提前计数。取消必须等校验 worker 不再访问缓冲区后才传播，不因 offload 提前 free。 |
| 重复扫描/分配 | 每个 MaskCounter 复用 Binary8 ROI validator 的 uint8 工作区，替代 `(roi!=0)&(roi!=255)` 临时布尔数组；0/255 严格语义、只读/非连续输入不变。同步网页、桌面部署模块清单，防止部署后缺依赖。full/runtime 验证与按订阅编码预览沿用第一轮。 |
| DMA 等待 | 沿用已实现的 cooperative 非忙等候选，与 runtime+worker 可在同一入口组合；driver 默认保留用于板测对照，未凭本机测试改成默认。 |
| RLE 与复杂候选 | 另一会话提交 `3334a84` 提供 RLE 候选链，本会话未重复修改其 RTL 或入口。已有原生游程/局部候选/离线计数测试再次运行：`rle-software-integration.txt` 为 20 passed。共享粘连 helper 去掉只为了得到连通域数量而计算的 stats/centroids 和预先空掩膜扫描，保持 8 连通、非零视前景及拆分判据。 |

软件回归 `software-second-wave-full.txt`：**317 passed、1 failed，30.97 s**，覆盖 Gray8、双槽、相机/预览、网页部署和桌面控制。失败仍为此前精确纯白文字像素断言。`software-second-wave.txt` 是首次命令误写了不存在的 `test_controller.py` 的选择错误（no tests ran），不计作测试通过；随后改用真实存在的 `test_client.py` 运行完整范围。

最终审查另补上新 `binary_stats.py` 在 `formal_runtime_sha256()` 中的依赖身份绑定，先确认新断言失败再修复；运行入口全文件 **74 passed**，见 `runtime-identity-regression.txt`。另一会话在 RLE 增加原尺寸参数后，针对当前源码复跑原生区域、counter、entrypoint 三文件为 **22 passed**（`rle-software-integration-current.txt`）；前述 20 项日志保留作为较早版本的检查记录。

本机微基准：`benchmark_helpers.py` 可重放；`helper-benchmark.json` 保存交替旧/新顺序、7 批原始数据和环境。Binary8 校验各尺寸/场景的局部加速比约 **0.95–1.24×**（小 ROI 噪声场景约慢 6%，绝对差约 0.3 µs；减少临时分配不代表所有小图都更快）；录制局部候选连通性检查约 **1.66×**。初次逐调用计时 `binary-validation-benchmark.csv` 也原样保留，其中一个样本有更大波动；没有选择性删除慢样本。以上均为 Windows 微基准，不代表 ARM CPU、整帧、粘连工况或板端 FPS。

第二轮 source hashes 单独记录在 `second-wave-source-sha256.json`，不覆盖第一轮哈希。板卡释放后的对照增加 `full+driver+inline`、`runtime+driver+inline`、`runtime+driver+worker`、`runtime+cooperative+worker`，区分验证开销、调度交叠和 DMA 策略；RLE 使用其独立链路的原分辨率配置，不能直接混算不同分辨率/算法的加速比。
