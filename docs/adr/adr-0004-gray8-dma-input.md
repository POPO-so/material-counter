# ADR-0004：黑白相机使用独立 Gray8 DMA 输入

状态：**Proposed；用户已授权作为当前唯一运行实现，代码迁移、三项 XSim、BIT/HWH 构建、离线交付验证及板端 Gray8 合成/真实相机像素验证均已完成。板端完成 963 帧（3 合成、960 相机）逐像素 Binary8 校验，DMA 空闲、fault=0、buffer已释放**。不代替 `docs/02_interfaces.md` 的 PR／三人确认，也不冻结视频源、算法 Route 或正式接口。

## 原因

现用 OV9281 是黑白相机。原链路把 MJPEG 解为 BGR，取一个通道后又复制成 RGB888 包，PL 再将相等的 R/G/B 转回灰度。2026-10-27 独立相机基准只测了采集/解码处理路径；它不是完整 Gray8 DMA 的帧率比较，也不证明 YOLO 收益。用户随后明确要求连 FPGA 输入接口一起改，彻底移除 Gray→RGB→Gray。

## 实现选择

当前生产代码只保留 Gray8 协议；旧 RGB encoder、RGB RTL、RGB 构建及回放代码已删除。旧文档和 `data/results/` 只作为原配置下的历史设计/验收记录，不是运行回退。Gray8 代码位于 `scripts/gray8/`、`fpga/experimental/gray8/` 和 `fpga/sim/gray8/`。每像素一字节，320×240 的 TX 包为 76,832 字节；RX Binary8 为 76,800 字节。相机直接读取原始 MJPEG，使用 `imdecode(IMREAD_GRAYSCALE)`，拒绝不符合 raw JPEG 格式、解码失败或尺寸不符的返回值。

新包头和硬件 Checker 签名为 `0x47525938`，尾 magic 为 `0x454e4438`，末字为 `0xc0dec018`。新软件校验 HWH 模块、制品哈希和硬件签名；旧位流不能接收新包。维持既有 50 MHz PS/DMA、MM2S HP0 64-bit、S2MM HP1 32-bit 拓扑，复用原 S2MM Packer 与 PS 跟踪计数。照明校正仅处理 Gray8，不移走 PL 的阈值运算。

## 证据和限制

- 相机基准：[记录](../../data/results/camera-format-benchmark-20261027/README.md)。该速度收益不是新完整 DMA 链路的实测收益。
- 新链路：[当前相机板测与性能记录](../../data/results/gray8-camera-20261027-122633/README.md)、[清理与早期合成板测记录](../../data/results/gray8-cleanup-20261027/README.md)、[首次构建时序/资源记录](../../data/results/gray8-dma-20261027/README.md)、[运行说明](../../scripts/gray8/README.md)、[实施状态](../../PLAN.md)。
- adapter、320×240 wrapper、S2MM guards 三项 XSim 已通过；BIT/HWH 构建与离线 delivery verify 已通过。最新板端 boot ID 为 `442a249b-1a20-41a1-bbc6-46debb0f4af8`；3 帧合成、30 帧相机短测、30 帧预热和三轮 300 帧相机流均完成 Binary8 全像素比对，共 963 帧、73,958,400 个像素；DMA 最终 idle、fault=0、buffers 全部释放。
- 三轮相机吞吐分别为 19.1063、19.3684 和 20.1920 FPS，900 帧加权 19.5448 FPS。该结果包含启动/预热、图像映射、逐像素校验及 PS 计数；各阶段存在重叠。本次相机 Binary8 掩膜全黑、未检出前景，CCL执行0帧，因此这次性能结果只代表该相机样本下的 Gray8 数据通路，不证明原始 Gray8 图像全黑或画面中确实没有物料，也不验证有目标时的检测准确率和处理速度。不能由此推导 YOLO收益，也不直接与历史 RGB 整机 FPS 比算提升。
- 当前 Gray8/packet/stream/calibration 相关软件回归在 sandbox 外运行 108 passed、4.14 秒。较早更宽的软件/离线套件记录为 706 passed、8 skipped、2 known failures（ablation 仓库临时路径断言、预览抗锯齿纯白像素断言），该记录不是当前相关集，也不能称全仓全绿。
- Route 和正式接口审批仍未冻结；本 ADR 记录当前实现授权，不代替 PR／三人签核。
