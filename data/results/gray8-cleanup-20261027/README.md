# Gray8 唯一路径清理与板端验证（2026-10-27）

用户批准彻底清除旧 Gray→RGB→Gray 可执行链路。当前输入为 **MJPEG 直接解码 Gray8→Gray8 DMA→PL 阈值→Binary8→现有 PS 计数**。不保留 RGB 编码或运行期回退。历史结果原样保留；显示用彩色标记和独立离线参考不属于相机灰度复制再转回灰度的通路。

## 清理范围

- 删除旧 RGB encoder API、RGB2gray RTL、旧 RGB DMA wrapper、旧构建 Tcl 和依赖旧转换链的实验/回放入口；WO-0015、WO-0016 采集、编码、预检、mock、向量、包部署和测试统一改为 Gray8。
- 唯一编码实现为 `scripts/gray8/packet_codec.py`；唯一构建/仿真在 `scripts/gray8/`。交付检查和可迁移回放包也已切换到该源集合。
- 320×240 时 TX 为 76,832 字节（旧值 230,432），RX 仍为 76,800 字节。像素发送载荷由三字节降为一字节；这不是帧率提升百分比。
- 保留 manifest、BIT/HWH、硬件签名、长度、DMA 所有权及逐像素校验，拒绝把新包发给旧或未知位流。
- 不引入 YOLO，不改变物料识别/计数算法及正式 Route 冻结状态。

## 验证

- [XSim 日志](simulation.log)：`GRAY8_ADAPTER_PASS`、`GRAY8_WRAPPER_320X240_PASS`、`GRAY8_GUARDS_PASS`，包含异常 SOF/EOL/像素值和忙时 ARM 拒绝。
- `scripts/delivery/verify_delivery.py` 通过：当前 manifest、BIT/HWH、拓扑和六个 RTL 源一致。构建制品与时序/资源报告见 [首次构建记录](../gray8-dma-20261027/README.md)。
- Gray8 与交付局部测试：21 passed，包含首次 DMA 启动允许条件和已有地址/长度/输入记录时拒绝条件。
- 完整软件套件：706 passed、8 skipped、2 failed，见 [python-tests.log](python-tests.log)；已知 ablation 仓库内临时目录断言与预览抗锯齿纯白像素断言失败，不能宣称全套通过。

## PYNQ-Z2 实测

目标 `192.168.2.99`，boot ID `1b08e489-d2dc-41e2-9639-aa46f7f5fbca`。新 BIT 已实际加载。[部署哈希核对](deployed-source-verification.json) 证明板上 23 个源码/制品文件与交付集合一致。板卡系统时钟不准确，本记录日期采用主机日期；不据其日志日历时间推断时序。

1. [首次启动失败](self-test-clean-2.json)：PYNQ 创建 DMA 驱动会先启动通道，首次地址/长度未设置时状态为 0。软件曾错误地将该状态一律视为忙碌，发送前退出并保留缓冲区。
2. [安全恢复审计](unprogrammed-dma-recovery.json)：核对两路地址、长度均为零，PL 输入/帧/故障计数均为零，证实未提交传输。正常停止两路通道、确认 Halted 后才结束保留缓冲区的本次进程；没有 DMA reset 或重载 overlay。随后仅放行“首帧且所有地址/长度与输入计数均为零”的初始状态，其他忙碌状态继续拒绝。
3. [后续合成自检](self-test-clean-3.json)：退出 0；3/3 不同合成 Gray8 帧 DMA 往返完成，230,400 个 Binary8 输出像素全部与阈值参考一致，缓冲区正常释放。该自检包含启动、逐像素验证与复杂合成图案的 PS 处理，不作为稳态 FPS 或物料准确率结论。
4. [真实相机尝试](camera-clean-1.json)：在任何 DMA 发起前失败，原因是 `/dev/video0` 无法打开。[只读枚举](board-final-readonly.txt) 显示只有 USB root hub，没有摄像头或 `/dev/video*`。用户随后确认是主动拔除摄像头，约定待其通知插好再测，并非已诊断为 USB 硬件故障。`dma_may_be_active=false`、`buffers_held=false`；[最终 DMA 状态](board-final-dma.json) 两路均空闲、故障为零、累计仅 3 帧。

尚未得出新旧整机帧率对比、物料准确率或 YOLO 收益结论。[后续测试步骤与命令](../../../scripts/gray8/README.md#摄像头插回后的测试顺序) 已整理：用户通知插好后，依次进行只读预检、合成自检、30 帧相机全像素验证、300 帧三轮持续运行和结果归档。正式接口签核仍按项目流程处理。
