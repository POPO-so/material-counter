# WO-0014 独立 DMA18 图像链路离线硬件集成

日期：2026-10-14。分支 `feat/pl-stream-skeleton`，PYNQ-Z2 / `xc7z020clg400-1`，Vivado 2026.1。结论：50 MHz 和 100 MHz 两个独立工程均完成 BD Validation、综合、布线、BIT/HWH 导出及离线脚本预检，满足申请一次合法包诊断板测的离线前置条件。推荐首测使用 50 MHz。尚未进行新 Overlay 的真实板测。

本轮未连接开发板、加载 Overlay 或发送 DMA，没有 push/合并。生产 RTL、四份受保护文档、额外 README、WO-0013 已跟踪内容及原 BIT/HWH 保持。没有打开、覆盖或重建 WO-0013 原 BD/工程，没有改变 G1/Route/ADR、正式 PL CCL 或 Phase B。

## 优化成果保存

时序优化独立本地 commit：`6264b324385eb8d90033d14eec3f471c1317273f`，标题 `perf: close WO-0014 adapter OOC timing at 100 MHz`。其父基线为 `ca6a68c7e454d87aeda414c65579271ffa948362`。36 个明确的优化源文件和精选证据纳入提交，大型向量、DCP、缓存、无关未跟踪文件未纳入。

提交前核对适配器 SHA-256 与已测试快照相符，重新运行交付核查：15 个 XSim 场景逐周期接口对照、487 帧 H3 / 37,401,600 像素零差异、134 项 Python 和四组旧模块回归证据一致。优化 OOC 为 668 LUT / 377 FF、100 MHz WNS +2.354 ns。该 OOC 数值不代替下面的完整实现结果。新增硬件集成源码、脚本及结果保持独立未提交。

## 独立 BD 与控制寄存器

两个工程分别为 `data/results/wo0014-hw-50mhz-20261014/project/wo0014_image_50m.xpr` 和 `data/results/wo0014-hw-100mhz-20261014/project/wo0014_image_100m.xpr`。均使用官方 PYNQ-Z2 PS7 preset，RTL 从仓库复制为各自不可混用的 `rtl/` 快照，最终校验快照与当前交付源码一致。

```text
PS7 GP0 → SmartConnect → AXI DMA S_AXI_LITE (0x40400000)
                      → image_pipeline s_axi (0x43C20000)
PS7 HP0 ← AXI4→AXI3 converter ← AXI DMA M_AXI_MM2S
AXI DMA M_AXIS_MM2S → 32→24 RGB packet adapter → RGB→Gray→Binary
                                                    → output checker
                                                    → drain_enable sink
```

两条 AXI-Lite 窗口各 64 KiB；DMA `Data_MM2S` 的 HP0 DDR 窗口为 `[0x00000000,0x20000000)`。GP0、HP0、DMA、转换器、控制 fabric、流水线和 reset controller 均由各工程自己的 PS `FCLK_CLK0` 驱动。`FCLK_RESET0_N` 接低有效 ext_reset；`C_AUX_RESET_HIGH=1` 的 aux_reset 接低，dcm_locked 接高；interconnect/peripheral reset 分别连接对应逻辑。转换器明确为 AXI4→AXI3，64 位数据宽度，没有直接将 DMA AXI4 接到 PS AXI3 HP0。

实际 Tcl readback 与生成 HWH 双重确认：`c_include_sg=0`、MM2S=1、S2MM=0、Micro DMA=0、`c_sg_length_width=18`、地址 32 位、MM2S memory 64 位、AXI-Stream 32 位、DRE=0、burst=16。长度上限 262,143 字节，正式 RGB 包 230,432 字节在范围内。参数覆盖账本 `reports/ip-requirement-coverage.txt` 为 FULL，原始配置/Validation receipts 在 `ipcfg_evidence/`，并保留 PYNQ PS preset 和工程 settings。

实验 wrapper `axis_dma_image_pipeline_bd.v` 提供只读统计及一个输出 drain 控制。首次审查修复了绝对 AXI 地址译码：同时接受系统基址 `0x43C200xx` 与离线测试的局部偏移，错误基址/未对齐/未知地址返回 SLVERR。AW/W 分别锁存，B/R 响应在反压时保持。寄存器表：

| 偏移 | 内容 |
|---|---|
| 0x00 | drain_enable，RW，复位默认为 1 |
| 0x04 | magic `0x574F3134` |
| 0x08 | bit0 packet_done_seen，bit1 packet_error_seen，bit2 当前 checker_error，bit3 输出计数/边界完整 |
| 0x0C / 0x10 | error_code / frame_id |
| 0x14 / 0x18 | packet_done_count / packet_error_count |
| 0x1C / 0x20 / 0x24 | 输入 accepted 字节数 / 拍数 / TLAST 数 |
| 0x28 / 0x2C / 0x30 | 输入首字 / 末字 / accepted TKEEP lane 字节校验和 |
| 0x34 / 0x38 / 0x3C / 0x40 | 当前帧输出像素 / SOF / EOL / Binary8 校验和 |
| 0x44 / 0x48 | 累计输出 accepted 像素 / 输出 stalled 周期 |

包格式保持 16 字节头 + 230,400 字节 RGB888 + 16 字节尾，共 57,608 个 32 位拍。320×240 包所有拍 TKEEP=F，仅最终 trailer 拍 TLAST=1；SOF/EOL 在适配器中生成，输入 TLAST 不是视频 EOL。

## 完整实现验收

| 独立构建 | 50 MHz | 100 MHz |
|---|---:|---:|
| 实际 `clk_fpga_0` 周期 | 20.000 ns | 10.000 ns |
| WNS / TNS | +9.526 / 0.000 ns | +1.167 / 0.000 ns |
| WHS / THS | +0.068 / 0.000 ns | +0.066 / 0.000 ns |
| WPWS / TPWS | +8.750 / 0.000 ns | +3.750 / 0.000 ns |
| 失败 setup / hold endpoint | 0 / 0 | 0 / 0 |
| LUT / FF | 1,941 / 2,095 | 1,941 / 2,095 |
| BRAM / DSP | 1 RAMB36 + 1 RAMB18 / 0 | 1 RAMB36 + 1 RAMB18 / 0 |
| BD Validation | PASS | PASS |
| synth_1 / impl_1 | Complete / write_bitstream Complete | Complete / write_bitstream Complete |
| 导出配对与实际 HWH 核验 | PASS | PASS |

两个频率分别完成自己的综合和实现，不从一个约束推断另一个。check_timing 的 no_clock、unconstrained_internal_endpoints、no_input_delay、no_output_delay、loops 均为 0。完整顶层没有额外 PL 外部数据 IO；DDR/FIXED_IO 由 PS7 preset 接入。PS FCLK clock XDC 分别为 20 ns / 10 ns，没有人为加 IO delay、降频、false path 或 multicycle 来掩盖图像链路失败；所用 IP 自带的合法约束和 waiver 保留原状。

每个综合阶段汇总为 25 Warnings、0 Critical Warnings、0 Errors；完整实现/bitgen 完成，未出现严重 DRC。**DRC 不为零**：每套有 14 Warning、2 Advisory；Methodology 有 LUTAR-1 Warning 1 项。规则/数量与已验收 WO-0013 resetfix 实现一致：PDCN-1569×1、REQP-1839×8、REQP-1840×4、RTSTAT-10×1，另有 REQP-165/181 writefirst Advisory 各 1。

具体风险在 AXI4→AXI3 converter 的 read command/FWFT FIFO 和 DMA MM2S I_DATA_FIFO：异步复位控制可影响 BRAM 写使能/数据，LUT 驱异步 PRE/CLR，且同址 write-first 有数据风险。RTSTAT-10 位于 converter 内部 FIFO 状态网，不是遗漏 BD 外部端口。这些警告没有被本轮新建 waiver；完整 setup/hold PASS 也不能证明复位过程中安全。[AMD REQP 说明](https://adaptivesupport.amd.com/s/article/73290?language=en_US)、[UG949 复位与 BRAM 风险](https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Synchronous-Reset-vs.-Asynchronous-Reset)。

完整 child run 原始日志还保留 3 条启动阶段 Windows 路径 ERROR（Common 17-1257/17-354，目标为 `C`），发生在综合命令之前，未阻断实际综合/实现或产物生成；原因未定位，不将整份原始日志称为“零 ERROR”。未改动全局 Vivado_init 或环境配置。一次辅助工程流探测对 routed leaf 查询 HD.RECONFIGURABLE 产生属性 Warning，已从复现脚本移除该无关探测，重新 settings 捕获无此 Warning；旧原始日志保留。

中断时进程已退出，100 MHz route checkpoint 完整而 bitgen 未完成，故只重置其 write_bitstream 步骤恢复；50 MHz 在 init_design 阶段中断，故只重置并重跑该独立工程 impl_1。综合源快照保持，原始和恢复日志保留。报告及 BIT 均取恢复后的最终完成 run。

## 离线功能与板测脚本

Manager 新鲜 XSim 回归通过：5×3 两合法帧及坏包恢复，包含 AW/W 次序、局部/绝对地址、SLVERR、B/R 反压、持续输出停顿及复位；320×240 首帧完整黄金比对通过，输入 230,432 字节/57,608 拍，输出 76,800 像素，输入字节校验和 `0x00650602`，输出 Binary8 校验和 571,200。包从有 SHA-256 记录的 H3 PNG 重新生成，golden 与原 H3 黄金首帧相同。此仿真驱动模块输入流，不冒充真实 DMA 或整套 PS/DMA IP 动态仿真。

PYNQ 脚本：`C:\fpga\material-counter\scripts\wo0014_hw\pynq_image_once.py`，并复制到各 `export/`。11 项 mock 全部通过，覆盖默认模式不导入 PYNQ、审批参数配对、包/HWH/manifest 变异、非法地址阻止发送、复位后 HALTED→RUNSTOP→IDLE、唯一 LENGTH 写入、超时无重试与活动缓冲区保留。实际两套导出文件 dry-run 均为 DRY_RUN_READY、hardware_attempted=false、send_attempt_count=0。

脚本先核对 BIT/HWH 同构建 SHA、地址和参数、正式包内容及独立 Binary 校验和。未来运行时申请连续 uint8 buffer，核验 8 字节基址对齐、32 位地址和完整区间在 HP0 DDR 内；CPU 复制/回读哈希后 sync_to_device/flush。DMA reset 后可接受干净 HALTED，启动后必须到无错误 IDLE，再核验零初始 checker 统计，才写一次 LENGTH。5 秒默认期限涵盖发送和输出排空；超时/错误记录现场、停止且不 reset/retry，DMA 活动时不释放 buffer。失败后的进程可保留分配等待人工确认 idle，避免活动 DMA 使用已释放内存。

导出门拒绝负 setup/hold、严重 DRC/Methodology；finalizer 使用显式异常，额外验证 TNS/THS、脉宽、clock、无未约束端点和配对/源哈希。已做负控制：注入 −0.001 ns WNS，即使 `python -O` 也拒绝；真实两频率在 `-O` 下均通过。finalizer 缺失时导出脚本报错，不打成功标记。

## 产物与复现

50 MHz（推荐首测）：

- BIT：`C:\fpga\material-counter\data\results\wo0014-hw-50mhz-20261014\export\wo0014_image.bit`
- HWH：`C:\fpga\material-counter\data\results\wo0014-hw-50mhz-20261014\export\wo0014_image.hwh`
- 配对清单/正式包/测试脚本：同目录 `pair-manifest.json`、`rgb_packet.bin`、`pynq_image_once.py`。

100 MHz：

- BIT：`C:\fpga\material-counter\data\results\wo0014-hw-100mhz-20261014\export\wo0014_image.bit`
- HWH：`C:\fpga\material-counter\data\results\wo0014-hw-100mhz-20261014\export\wo0014_image.hwh`
- 同样各自独立 `pair-manifest.json`、`rgb_packet.bin`、`pynq_image_once.py`，不得交叉配对。

各构建保留 `export/wo0014_image_recreate.tcl`、`project_settings.tcl`、routed DCP；`reports/` 包含综合/完整实现资源、timing min_max、clock、完整 XDC、DRC/Methodology、参数读回、配对清单验证和 dry-run；`.vivado-ai/` 与 `.runs/*/runme.log` 为原始运行证据。哈希总表和保护审计位于 `data/results/wo0014-hw-verification-20261014/`。

复现前先用仓库 `.venv\Scripts\python.exe scripts/wo0014_hw/prepare_packet.py` 生成固定首帧。Batch Tcl：`scripts/wo0014_hw/run_batch.tcl`，参数为 **全新**输出目录、`F:/FPGA/vivado_board_repos`、50 或 100；通过本机 Vivado 2026.1 `vivado.bat -mode batch -source ... -log <out>/vivado.log -journal <out>/vivado.jou -tclargs <fresh-out> <board-repo> <clock>` 执行。配置 helper 依赖仓库已安装的 `.agents/skills/vivado-ip-configurator/lib/ipcfg.tcl`，复现设置捕获依赖 vivado-revision-control helper；源码、快照和 helper 要随工作环境保留，不是独立于 Vivado/board files 的免依赖包。

在各 export 目录可运行默认离线命令：

```text
python pynq_image_once.py --bit wo0014_image.bit --hwh wo0014_image.hwh --packet rgb_packet.bin --manifest pair-manifest.json --evidence-json dry-run.json
```

本轮没有使用 `--execute --approve-once`；两参数只是记录已经取得的人工批准，不能自己产生批准。

## 验证边界及下一步

现有 checker 仅统计 accepted 输出，不提供逐像素 PS 回读；最后一帧欠长没有独立 EOF 检测，提前 SOF 会重启统计窗口，错误帧可能已经输出前缀。packet_done 只确认输入包，不能单独证明输出排空。新增 wrapper/脚本通过包事件、输入字节/拍数/TLAST、输出像素/SOF/EOL/校验和及 deadline 联合验收单合法包，仍不能声称 checker 完整覆盖所有异常。

已具备提交人工审批的一次 **50 MHz、单合法 RGB 包、无重试诊断板测** 的离线条件；有上述未豁免复位/BRAM 风险，未取得异常恢复或新硬件通过证据。审批后才核对板端身份、分配/地址/cache、初始状态并执行。传输期间不得复位或重载；失败停止并保留现场。100 MHz 有独立完整实现时序证据，但建议第二个单独批准的硬件阶段再评估。正式 PL CCL、Phase B、G1/Route 不在范围。

授权离线工作完成后停止，等待人工批准单次板级验证。
