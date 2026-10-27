# Gray8 DMA 新链路验证（2026-10-27）

> 后续状态：本页以下内容保留首次交付时的历史记录。用户随后批准清除旧 RGB 可执行链路；新位流已加载，3/3 合成帧 DMA 往返和 Binary8 全像素比对通过，真实相机因 USB 未枚举待验。当前代码与完整后续证据见 [Gray8 清理验收](../gray8-cleanup-20261027/README.md)。以下“旧链路保留”“尚未加载”等描述不再代表当前状态。

状态：**软件和 RTL 已实现；Vivado 2026.1 位流已生成；板端尚未验证／生效。**

用户已明确授权将整条输入改为 `MJPEG→Gray8→Gray8 DMA→PL threshold→Binary8`，彻底删除新链路中的灰度复制 RGB 及 PL RGB 转灰度。旧 RGB 链路仍独立保留。正式接口签字和物料准确率不由本记录宣告通过。

## 已验证

- 新发送包 76,832 字节（16 字节头＋76,800 Gray8 像素＋16 字节尾）；旧 RGB 包 230,432 字节。输入像素载荷减少 2/3；回传 Binary8 长度不变。
- Worker 先建立失败测试，再实现正式相机 MJPEG 直接灰度解码、严格 JPEG／尺寸校验，以及可选 `tx_bytes` 的双缓冲。旧默认 RGB 行为保留，Gray8 入口显式选择新长度。
- XSim：5×3 pipeline 测试覆盖部分尾 KEEP、连续帧、输入气泡、输出反压稳定、阈值边界、错误 magic／长度／KEEP／TLAST／checksum 和复位恢复；[日志](final/xsim/sim.log) 含 `GRAY8_ADAPTER_PASS pixels=15`。
- XSim：真实默认 320×240 wrapper 完整喂入 Gray8 包，验证 19,200 个 Binary8 S2MM 输出字及结束条件；[日志](final/xsim/wrapper-sim.log) 含 `GRAY8_WRAPPER_320X240_PASS beats=19200`。小尺寸验证针对 adapter/pipeline，wrapper 功能验证针对默认尺寸。
- 完整 Vivado 50 MHz 实现与导出成功：setup slack **+9.812 ns**、hold slack **+0.017 ns**；DRC／方法学无 Error 或 Critical Warning，普通 Warning 仍保存在报告中。
- 实现后整体资源：2,777 LUT、3,470 FF，包含 PS/DMA/互连及图像通路；不是仅 Gray8 算法模块资源。
- 新 Gray8 软件的协议、制品、DMA状态、逐像素检查与入口测试 **16 passed**。集成定向运行早期为 **196 passed**。
- 最近完整 `scripts/wo0016 scripts/gray8` 运行：**514 passed、6 skipped、2 failed**。两个失败分别为测试临时目录在仓库内导致 ablation 异常类型差异，以及 OpenCV 抗锯齿文本没有精确纯白像素；对应 ablation 与预览实现未被本次改动修改。之后新增的 DMA quiescent 测试单独通过。
- 最后一次扩大后的定向运行：198 passed，旧相机 timeout 测试一次墙钟 0.215 秒，超过其 0.2 秒断言；该测试单独复跑通过，未放宽超时断言。不能把存在该时间敏感失败的整次运行记录成全通过。

## 制品

- [manifest.json](export/manifest.json)，SHA-256：`b81e6ea09cba48d643b3f09ae88b9ea784176c81ebc53799f1af38aedb0cd484`。
- [gray8.bit](export/gray8.bit)，SHA-256：`feadeae19058e23c6bfb443e4f25aade48a90d19c8b64bbbc7fe3219ebcea926`。
- [gray8.hwh](export/gray8.hwh)，SHA-256：`4ab3f7cf4aa11c614b0a8a2740454dc2148e8246e5019f8d99bb3feb532ab5d4`。
- [完整时序报告](reports/timing-implemented.rpt)、[资源](reports/utilization-implemented.rpt)、[DRC](reports/drc.rpt)、[方法学](reports/methodology.rpt)。
- 构建源快照 SHA-256 在 manifest 中；仅包括 Gray8 adapter、Gray8 threshold pipeline、Gray8 wrapper 和复用的 threshold/checker/packer，没有 RGB 模块。

Vivado MCP proxy 启动失败，但配置中的原生 `C:/AMDDesignTools/2026.1/Vivado/bin/vivado.bat` 经 batch 启动验证可用；仿真和实现均继续使用同一个 Vivado/XSim 2026.1 后端，没有切换模拟器。

## 板端进度

连接目标 `xilinx@192.168.2.99`，部署前只读得到 boot ID `1b08e489-d2dc-41e2-9639-aa46f7f5fbca`。软件复制到独立目录 `/home/xilinx/gray8-dma-20261027/` 的过程中 SFTP 连接中断，随后 SSH 和 Ping 均不可达。

**尚未执行新位流加载、Gray8 DMA 发送或摄像头新链路运行。** 不能报告新链路板端 FPS、像素通过或物料准确率。板上独立目录可能包含不完整的软件副本；恢复连接后必须重新同步并核对哈希，不能直接运行残留文件。

## 恢复后的验证顺序

1. 确认板卡当前 IP 与 boot ID，核对源码、manifest、BIT/HWH 哈希和空闲状态。
2. 先运行 `run_gray8_counter.py --mode offline`；再以明确 `--execute --boot-id` 和首次 `--load-overlay` 运行 3 帧 `self-test`，逐像素核对已知灰度输入。
3. 自检通过后运行 30 帧 `camera`，核对当前相机、Gray8 传输和 Binary8 输出；报告含启动／预热／逐像素验证开销，不能与旧稳态性能直接混算。
4. 单独做一致条件的性能与实物准确率验收。旧 RGB 位流和软件保留作回退；Route 和正式冻结状态不自动改变。
