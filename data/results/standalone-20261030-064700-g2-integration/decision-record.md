# G2 集成架构审查记录

- 日期：2026-10-30（Asia/Shanghai）
- 工作区：`C:\w\mc-g2`
- 分支：`feat/集成RLE与HDMI位流`
- 基线：`cc4bbdf`（G2 独立 RLE 基线来自 `8987964`）
- 状态：**Manager 已审查，准许作为本分支 G2 实例化候选；不是 Route、视频输入或正式接口冻结。**
- 集成入口：[`scripts/standalone/create_g2_integration.tcl`](../../../scripts/standalone/create_g2_integration.tcl)
- 构建入口：[`scripts/standalone/build_g2_integration.tcl`](../../../scripts/standalone/build_g2_integration.tcl)

## 审查范围与证据边界

对照了 `scripts/standalone/create_display.tcl`、`scripts/standalone/build_display.tcl`、`scripts/gray8/create_bd.tcl`、ADR-0005、脱机显示候选设计、G2 baseline README 和本分支 handoff。G1 独立显示入口保持原样；本记录只批准在新的 G2 输出目录中组合现有候选结构。

G2 baseline 只证明 8 个 RLE RTL 场景及小参数 OOC 综合后功能回归。下面的拓扑是待实例化候选；Vivado BD 读回、综合/实现、时序、CDC、DRC、BIT/HWH/manifest 一致性和板端行为仍未由本记录证明。此前出现的 RLE FCLK0 31.25 MHz 读回偏差必须在新 BIT 的 HWH 和板端寄存器中复核，不能以 Tcl 配置代替实测。

## 采用的 G2 实例化候选

### PS 时钟、复位与 AXI 控制互连

| 域 | 时钟 | 复位与负载 |
|---|---:|---|
| PS GP0 与控制互连公共端/SI | FCLK1，100 MHz | 既有 `reset_control`；保留 G1 M00–M08 控制路径 |
| HDMI AXI-Lite MI（既有 M01/M02） | FCLK2，142.857 MHz | `reset_stream`；随 G1 的显示子系统配置保留 |
| RLE DMA、pipeline 与新增 MI M09/M10 | FCLK0，50 MHz | 新增 `reset_rle`，以 FCLK0 同步释放；外部复位仍来自 PS `FCLK_RESET0_N` |
| HDMI 视频流/HP2/VDMA | FCLK2，142.857 MHz | 既有 `reset_stream` |
| HDMI 像素输出/monitor 像素采样 | 动态时钟，目标 74.25 MHz | 既有 `reset_pixel`，等待动态时钟锁定 |

配置 control AXI Interconnect 为 11 个 MI：M00–M08 的端点、地址和时钟保持不变；M09 连接 DMA `S_AXI_LITE`，M10 连接 `image_pipeline/s_axi`。新增 MI 时钟从 FCLK0 输入，公共 ACLK 与 GP0 S00_ACLK 仍为 FCLK1。既有 M01/M02 已处于 FCLK2 域；Vivado 自动时钟转换单元和异步标记必须以生成的 BD、综合网表、CDC 报告和时序约束读回确认。若自动推导不成立，不得强行通过静态检查，先修正拓扑。

`reset_rle` 的 `slowest_sync_clk` 为 FCLK0；`ext_reset_in` 接 PS `FCLK_RESET0_N`，`dcm_locked` 接常高，辅助复位输入接常低。其 `peripheral_aresetn` 驱动 RLE DMA、pipeline、HP0 协议转换器、HP1 interconnect 及 M09/M10 对应 reset。不能复用面向 FCLK2 的 `reset_stream` 作为 RLE 复位。

### DDR 高性能端口与数据所有权

| PS 端口 | 用途 | 数据路径与宽度 |
|---|---|---|
| HP0 | RLE MM2S 读取 | AXI DMA 64-bit AXI4 → protocol converter → 64-bit AXI3 → HP0；FCLK0 50 MHz |
| HP1 | RLE S2MM 写回 | AXI DMA 32-bit AXI4 → 1×1 AXI Interconnect → 32-bit AXI3 → HP1；FCLK0 50 MHz |
| HP2 | HDMI VDMA 扫描读取 | VDMA 经既有 memory interconnect 读取 PS 合成的 Gray8 帧；PS 口配置 32-bit，FCLK2 142.857 MHz |

RLE 输入包、RLE 输出包和 HDMI 显示帧由 PS 分配为不同缓冲区。RLE DMA 持有其输入/输出缓冲期间，CPU 不访问 DMA 所有的区域；显示帧按 PYNQ 视频驱动的四槽调度交接给 VDMA，交接后 CPU 不读写或释放该槽，待驱动确认退役再复用。显示像素由 PS 合成并经 HP2 扫描；压缩 RLE 流不直接接入 HDMI AXIS。三条通路仍共享 DDR，吞吐隔离不能靠拓扑宣称，需由 G2.5 实测。

### AXI-Lite 地址和控制端点

每段控制空间为 64 KiB，均映射于 PS `Data` address space：

| MI | 目标 | 起始地址 |
|---|---|---:|
| M00 | VDMA | `0x43000000` |
| M01 | HDMI dynamic clock | `0x43C10000` |
| M02 | HDMI VTC | `0x43C30000` |
| M03 | color convert | `0x43C60000` |
| M04 | pixel unpack | `0x43C70000` |
| M05 | HDMI HPD GPIO | `0x41210000` |
| M06 | 四按钮 GPIO | `0x41200000` |
| M07 | 视频 interrupt controller | `0x40000000` |
| M08 | 视频 frame monitor | `0x43C40000` |
| M09 | RLE AXI DMA | `0x40400000` |
| M10 | RLE image pipeline | `0x43C20000` |

这些 64 KiB 控制段两两不重叠。DMA 的 MM2S/S2MM DDR address spaces 分别映射到 HP0/HP1 的 DDR 段；显示 VDMA 的 DDR address space 保持 HP2。各 master 的 DDR aperture 重叠是同一 DDR 物理空间的寻址方式，不是 AXI-Lite 控制地址冲突；运行时必须用不同的已分配缓冲区。

### 按钮、monitor、IRQ 与制品隔离

- 保留四按钮 GPIO、外部 `btns_4bits_tri_i`、视频 frame monitor、HDMI 输出及其 G1 控制/视频中断连接；monitor 仍以 FCLK1 读 AXI-Lite 快照、以动态像素时钟采样输出端身份。
- RLE DMA IRQ 在本 G2 候选中保持未连接，软件继续按现有轮询方式工作。若后续需要接入 interrupt controller，必须先设计并验证 FCLK0→FCLK1 CDC。
- `create_display.tcl` 与 `build_display.tcl` 不修改。G2 使用独立 `G2_OUT`，默认 `data/results/standalone-20261030-g2-integration/`；项目、RTL 快照、日志、报告和导出文件只写入该目录。已存在的 `standalone-20261030-g2-integrated/.vivado-ai/` MCP 诊断日志不覆盖、不迁移。

## Manager 审查结论和放行条件

拓扑把现有显示 G1 设计作为基底，保持其 M00–M08、HP2 和输出端监测边界，只在预留的 FCLK0/HP0/HP1 上加入 RLE，并新增一条 FCLK0 时钟域复位。地址表无重叠，压缩流与 HDMI 像素流不混接。因此准许创建独立 G2 BD 并进入 Vivado 实际校验。

以下仍是 G2 实现门禁，任何一项失败都撤回或修正候选，不得改写为“架构已验证”：

1. BD 确认 control M00–M08 全部保留，M09/M10 新增，所有 AXI、AXIS、时钟和复位端口连接无悬空/多驱动。
2. 读回 DMA/MM2S/S2MM/HP0/HP1/HP2 的协议、位宽、时钟、地址；验证控制地址不重叠。
3. 综合与实现报告覆盖新增时钟转换，时序、CDC、DRC 和方法学严重告警逐条归因。
4. HWH、BIT、manifest 指向同一设计和构建；板端确认 FCLK0 实际 50 MHz、HP2 32-bit、DMA/VDMA 正常启动与停止。
5. G2.5 单独验证 HDMI 720p60 与有目标/高游程 RLE 并发；本记录不构成 G2.5、G3、G4 或物料准确率通过。

## 本记录生成时的验证状态

- Vivado MCP 已恢复至 2026.10.1 服务端；`vivado_start` 冷启动成功，`version -short` 返回 Vivado `2026.1`，会话正常停止。MCP 修复不等于集成 BD 验收。
- 本记录完成 Manager 架构审查；下一步运行独立 G2 入口的 dry-run/静态检查，再实际创建 BD。
- 尚未运行集成 BD、综合/实现、时序/CDC/DRC、8 组 RTL 回归、综合网表回归或板端测试。

## 后续实现验证增补（2026-10-30）

- Vivado 2026.1 集成 block design、综合/实现、HWH/BIT 及 11 个控制窗口 readback 已完成；静态报告和制品身份见同目录 `README.md`、`export/g2-manifest.json` 与 `reports-g2-integrated/`。
- 从本次集成工程生成的 640×400 `display_image_pipeline_0` funcsim 网表运行全尺寸 wrapper 回归，8 个基线/ROI/形态/空图/overflow/坏包场景全部通过。证据位于 `rtl-regression/full-pipeline-netlist-20261030-074800/`。此结果只覆盖 RLE pipeline IP/wrapper，不覆盖整机 top 联合仿真。
- 截至本节初次复核时，板端冒烟尚未成功：复用 G1 runner 后因依赖缺失进入 `FAIL_HOLD`；只读复核显示旧 G1 BIT 仍加载且 VDMA halted，详见 `board/g1-run-01-failhold.json` 与 README。故本记录的 G2 候选未获板端放行，Route/视频输入/正式接口仍保持候选状态；G2.5/G3/G4 均未通过。

## 2026-10-30 板端续验增补

- G2 集成 BD/BIT/HWH、静态时序/CDC/DRC 与制品身份检查已完成；本轮 board boot ID 为 `e48ff153-bd85-4dd6-98f6-438bd51da08d`，实际加载 BIT SHA-256 为 `2ec1f1dec5a2df2e07044e94ecacd5d20aeef19ec6787077c0728d24053a8b18`。
- HDMI/camera 使用 G2 display manifest 串行运行 61.296 s、提交 589 帧，报告正常停机并释放相机；32 个 monitor snapshot 无灰度、marker、pair 或上下路 invalid 标志。板端 FCLK0/1/2 为 50/100/142.857143 MHz；HP2 32-bit readback 通过。
- RLE 使用内容与 G2 BIT/HWH 同哈希的专用 adapter manifest，在已加载 G2 BIT 上完成 12 帧 synthetic self-test；12/12 像素重建通过、0 invalid、缓冲释放，HP1 32-bit AFI1 检查通过。HP0 有 HWH/构建读回和成功 MM2S 功能路径证据，仍无独立 AFI0 板端宽度读回。
- 退出后，VDMA halted、RLE DMA 两通道 idle 且无错误位（run/stop control bit 保持置位）、PYNQ `shutdown_ips={}`、owner FD audit 为空。额外单独探读 RLE pipeline AXI-Lite `0x43C20000` 时 Linux 报 external abort；根因未定，没有再次访问该页，也未写寄存器。详情与原始 report 位于集成记录的 `board/`。
- 这两项是不同 runner 的先后功能冒烟，不含联合/并发执行；G2 完整板端门禁暂不放行，G2.5 DDR/反压/延迟压力测试仍未开始。上方原始审查结论与既有静态报告保留，本增补不冻结 Route、视频输入或正式接口。
