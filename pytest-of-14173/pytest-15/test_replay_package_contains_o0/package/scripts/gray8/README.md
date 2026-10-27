# 当前唯一 Gray8 运行链路

用户已批准将活动运行路径统一为 `GRAY8_PACKET_V1`。代码不保留 RGB encoder、RGB PL 流水线或运行期 RGB 回退；历史结果仍保存在原目录，且不改标为新协议结果。

流程为 **raw MJPEG→Gray8→76,832 字节新包→FPGA threshold→76,800 字节 Binary8→现有 MaskCounter**。相机使用 `imdecode(IMREAD_GRAYSCALE)`；Gray8 强度可先经 `lighting-calibrated` LUT 补偿，二值阈值仍在 PL。软件发送前检查硬件签名，并逐帧验证回传 Binary8 像素。

包为 320×240 Gray8：四个小端头字（magic `0x47525938`、`(H<<16)|W`、payload 长度、frame ID）、逐像素 Gray8 payload，及四个小端尾字（magic `0x454E4438`、frame ID、payload sum、`0xC0DEC018`）。RX 是每像素一字节的 Binary8 掩膜。HWH admission 检查 Gray8 pipeline 模块名、bit/HWH 和 manifest SHA-256；硬件 checker 必须报告 Gray8 签名。

## 构建与验证状态

唯一 Vivado Tcl 流程在本目录：`run_sim.tcl` 完成仿真 gate 后，依次运行 `create_bd.tcl`、`build.tcl`、`export.tcl`。工程复用 50 MHz PS、DMA、HP0/HP1 拓扑。RTL 位于 `fpga/experimental/gray8/`，测试台位于 `fpga/sim/gray8/`；实施范围见 [PLAN.md](../../PLAN.md)。构建输出需使用新的 `data/results/gray8-*` 路径。

XSim 的 adapter、320×240 wrapper、S2MM guards 三项均已通过，Vivado BIT/HWH 已构建，离线 delivery verify 已通过。新 BIT 已加载到 PYNQ-Z2；首次 DMA 启动时，状态检查将状态值 0 误判为失败，因此尚无通过的 DMA 帧传输、逐像素验证或 Gray8 板端性能结果。Manager 正在修复启动检查。完整记录见 [2026-10-27 验证记录](../../data/results/gray8-dma-20261027/README.md)。

当前软件全套结果为 700 passed、8 skipped、2 个已知失败：ablation 临时路径断言、预览抗锯齿白点精确断言。此结果不表示板测通过。

## 运行入口

`run_gray8_counter.py` 支持 `offline`（默认，只验证 manifest/制品）、`self-test`（板端合成输入）和 `camera` 模式。命令需要 manifest、对应 SHA-256 和一个从未存在过的结果路径：

```powershell
python scripts/gray8/run_gray8_counter.py `
  --manifest data/results/gray8-<build>/export/manifest.json `
  --manifest-sha256 <manifest-sha256> `
  --output data/results/gray8-run-<new-id>.json
```

板端 `self-test` 或 `camera` 运行还必须显式提供 `--execute --boot-id <当前启动ID>`；首次为该启动加载 overlay 时再提供 `--load-overlay`。输出必须是新路径，程序不覆盖已有结果。板端每帧逐像素检查 Binary8，所以报告吞吐包含校验开销，不能当作纯 DMA/FPGA FPS。当前没有新的 Gray8 帧率、准确率、端到端性能收益或 YOLO 收益结论。

DMA 所有权不确定时程序可能进入 `FAIL_HOLD` 并保留 CMA 缓冲区。它不会自动 retry 或 reset；须由操作员按板端恢复流程处理。
