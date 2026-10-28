# 当前唯一 Gray8 运行链路

用户已批准将活动运行路径统一为 `GRAY8_PACKET_V1`。代码不保留 RGB encoder、RGB PL 流水线或运行期 RGB 回退；历史结果仍保存在原目录，且不改标为新协议结果。

流程为 **raw MJPEG→Gray8→76,832 字节新包→FPGA threshold→76,800 字节 Binary8→现有 MaskCounter**。相机使用 `imdecode(IMREAD_GRAYSCALE)`；Gray8 强度可先经 `lighting-calibrated` LUT 补偿，二值阈值仍在 PL。软件发送前检查硬件签名，并逐帧验证回传 Binary8 像素。

包为 320×240 Gray8：四个小端头字（magic `0x47525938`、`(H<<16)|W`、payload 长度、frame ID）、逐像素 Gray8 payload，及四个小端尾字（magic `0x454E4438`、frame ID、payload sum、`0xC0DEC018`）。RX 是每像素一字节的 Binary8 掩膜。HWH admission 检查 Gray8 pipeline 模块名、bit/HWH 和 manifest SHA-256；硬件 checker 必须报告 Gray8 签名。

## 构建与验证状态

唯一 Vivado Tcl 流程在本目录：`run_sim.tcl` 完成仿真 gate 后，依次运行 `create_bd.tcl`、`build.tcl`、`export.tcl`。工程复用 50 MHz PS、DMA、HP0/HP1 拓扑。RTL 位于 `fpga/experimental/gray8/`，测试台位于 `fpga/sim/gray8/`；实施范围见 [PLAN.md](../../PLAN.md)。构建输出需使用新的 `data/results/gray8-*` 路径。

XSim 的 adapter、320×240 wrapper、S2MM guards 三项均已通过，Vivado BIT/HWH 已构建，离线 delivery verify 已通过。新 BIT 在 boot ID `442a249b-1a20-41a1-bbc6-46debb0f4af8` 下运行并完成 3 帧合成自检、30 帧相机短测、30 帧预热及 3×300 帧相机运行：合计 963 帧，Binary8 全像素比对全部通过，DMA 空闲、fault=0、buffers 均释放。三轮长测分别为 19.1063、19.3684、20.1920 FPS；加权 19.5448 FPS。该吞吐包含启动/预热、逐像素验证和 PS 计数。相机实际协商 MJPG 640×400@60，解码为 Gray8 后处理成 320×240；输出掩膜全黑、前景为零、CCL执行0帧，因此未验证物料准确率或有目标时吞吐。详见[相机板测报告](../../data/results/gray8-camera-20261027-122633/README.md)。清理细节见 [清理记录](../../data/results/gray8-cleanup-20261027/README.md)；BIT/HWH 构建时序与资源见 [首次构建记录](../../data/results/gray8-dma-20261027/README.md)。

较宽的旧软件/离线套件记录为 706 passed、8 skipped、2 个已知失败（ablation 临时路径断言、预览抗锯齿白点断言），不能称作全套全绿。当前针对 Gray8/packet/stream/calibration 的 108 项测试在 sandbox 外通过：108 passed、4.14 s，完整日志见[相机板测报告](../../data/results/gray8-camera-20261027-122633/README.md)。

## 运行入口

2026-10-28 起可指定 `--validation full|runtime`，默认 `full` 保持逐像素验收行为，`self-test` 必须使用 `full`。`runtime` 跳过输入包的软件重解码、软件期望掩膜生成和全像素比较，仍检查硬件签名、状态、帧号、长度计数和返回数据的输出 checksum；它不等于逐像素验证通过。结果分别为 `GRAY8_PIXEL_VERIFIED`、`GRAY8_RUNTIME_CHECKED`，并报告验证帧数及检查阶段的墙钟/线程 CPU 耗时。输入包 checksum 仍由编码器生成并由 PL 验证，不能用输出 checksum 替代。

新增 `--dma-wait driver|cooperative`（默认 `driver` 用于保留原测量基线）。`cooperative` 使用已经核对的 PYNQ 3.1.1 simple-DMA 状态/缓存收尾约定，每 0.5 ms 让出事件循环后重新查询完成状态，不为每次 TX/RX 创建等待线程。实际唤醒间隔受系统调度影响；不承诺精确 0.5 ms。取消、错误或长度不符时不释放驱动的 active buffer，由流处理进入既有 FAIL_HOLD；没有自动复位或重试。RX 成功时只做一次驱动缓存失效，不再额外重复 `sync_from_device()`。该模式需要板端对照验证后再决定是否作为默认。

结果新增 `process_cpu_s`、`cpu_core_ms_per_frame`（仅流处理区间，包含进程内线程）、DMA 查询/缓存收尾计时，以及 `roi_worker_wall`、`roi_worker_cpu`、`roi_dispatch_wait`、`frame_source_wait` 和 `free_slot_wait`。`roi_count` 仍为提交计数至返回的墙钟时间；派发等待不是 CPU 时间，阶段之间可以重叠，不能相加推算 FPS。

`run_gray8_counter.py` 支持 `offline`（默认，只验证 manifest/制品）、`self-test`（板端合成输入）和 `camera` 模式。命令需要 manifest、对应 SHA-256 和一个从未存在过的结果路径：

```powershell
python scripts/gray8/run_gray8_counter.py `
  --manifest data/results/gray8-<build>/export/manifest.json `
  --manifest-sha256 <manifest-sha256> `
  --output data/results/gray8-run-<new-id>.json
```

板端 `self-test` 或 `camera` 运行还必须显式提供 `--execute --boot-id <当前启动ID>`；首次为该启动加载 overlay 时再提供 `--load-overlay`。输出必须是新路径，程序不覆盖已有结果。板端每帧逐像素检查 Binary8，所以报告吞吐包含校验开销，不能当作纯 DMA/FPGA FPS。本轮相机的 900 帧加权为 19.5448 FPS；二值掩膜未检出前景，不能据此判断计数准确率或有目标场景性能，也不与旧 RGB 整机 FPS 计算新旧收益。当前无 YOLO 收益结论。

DMA 所有权不确定时程序可能进入 `FAIL_HOLD` 并保留 CMA 缓冲区。它不会自动 retry 或 reset；须由操作员按板端恢复流程处理。

## 摄像头板测与复测流程

本轮摄像头已接回并完成以下测试。板卡本轮重启后使用 boot ID `442a249b-1a20-41a1-bbc6-46debb0f4af8`；之后若板卡重启、更换位流或相机设置变化，可按此流程复测。每次都使用最新 boot ID 和新的输出路径。

1. **只读确认环境。** SSH 连接 `xilinx@192.168.2.99`，读取当前 boot ID、`lsusb`、`v4l2-ctl --list-devices` 和实际设备的 `--list-formats-ext`，确认 OV9281、MJPG 640×400@60、设备路径及相机没有被其他进程占用。核对部署源码、manifest、BIT/HWH 哈希，检查 DMA/PL 无错误及无未结束任务。若板已重启，重新做加载前检查；不沿用旧 boot ID。
2. **离线检查与合成自检。** 先以 `--mode offline` 检查制品；通过后以 `--mode self-test --frames 3` 验证 DMA、长度、帧计数及每个 Binary8 像素。合格条件：`GRAY8_PIXEL_VERIFIED`、`frames=3`、`pixel_verified_frames=3`、`buffers_held=false`，并确认 DMA 空闲。已加载且哈希匹配时直接 attach；只有确需加载并通过检查时才加 `--load-overlay`。
3. **真实相机短测。** `--mode camera --frames 30 --profile lighting-calibrated --lighting-threshold 100`，按实际路径设置 `--device`。记录协商模式、直接 Gray8 解码、裁剪/缩放区域与相机控制值。合格条件：30/30 帧 DMA 往返、全部像素正确、无 checker/packer/DMA 故障、缓冲区正常释放。没有实际物料真值时不宣称计数准确率。
4. **持续运行与耗时分析。** 短测通过后，固定光照、曝光、阈值、场景与 profile，先做一次预热运行，再各运行 300 帧、重复三轮。保留原始 JSON 和每轮 FPS、阶段耗时；区分采集与解码（当前合计）、图像映射、编码、DMA、缓存同步、验证和 ROI/计数。当前入口的 FPS 含启动/预热与逐像素验证，不作为去除校验后的生产性能。若要比较处理路径，另按 [相机基准](../camera_format_benchmark.md) 在同模式下测量；旧历史整机 FPS 不与新结果直接计算提升百分比。
5. **归档并报告。** 下载结果到新的 `data/results/gray8-camera-<主机时间>/`，记录 boot ID、源码/制品哈希、相机设置、运行帧数与失败项。分别报告功能正确性、持续处理吞吐和物料准确率，后者需要独立的实物及人工真值。本轮 3 帧合成＋30 帧短测＋30 帧预热＋900 帧持续实测记录在[相机板测目录](../../data/results/gray8-camera-20261027-122633/README.md)。

板端使用 `/usr/local/share/pynq-venv/bin/python3`，硬件模式须由 root 运行并设置 `XILINX_XRT=/usr`。例如短测命令如下；`<当前boot-ID>`、`<实际设备>` 和 `<新运行ID>` 在上述检查后填入，输出必须未存在：

```bash
sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 \
  /home/xilinx/gray8-dma-20261027/scripts/gray8/run_gray8_counter.py \
  --manifest /home/xilinx/gray8-dma-20261027/export/manifest.json \
  --manifest-sha256 b81e6ea09cba48d643b3f09ae88b9ea784176c81ebc53799f1af38aedb0cd484 \
  --mode camera --frames 30 --device <实际设备> \
  --profile lighting-calibrated --lighting-threshold 100 \
  --execute --boot-id <当前boot-ID> \
  --output /home/xilinx/gray8-dma-20261027/camera-<新运行ID>.json
```

该示例绑定本次构建的 manifest SHA；后续更换构建须重新验证并替换。任一步失败先保留证据并查明原因，不连续自动重试；发生 `FAIL_HOLD` 时先确认硬件不再持有内存，再处理进程和缓冲区，不直接 kill 或重载。
