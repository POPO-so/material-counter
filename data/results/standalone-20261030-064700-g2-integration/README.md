# G2 集成构建与门禁记录

**当前结论：静态构建与实现检查通过；G2 板端门禁未通过，本次未确认 G2 BIT 已加载。G2 整体不得标记为通过。** 本次实现用于验证已审查的集成候选，不构成 Route、视频输入或正式接口冻结；G2.5、G3、G4 仍未执行。

## 构建身份

- 构建目录：`standalone-20261030-064700-g2-integration`
- Vivado：2026.1，Build 6511674；器件 `xc7z020clg400-1`；顶层 `display_wrapper`
- 构建分支提交：`fix/初始化G2原生日志`，`b989cd1e81457ed71e970171ea7593139d7f78d2`
- 集成入口：`scripts/standalone/create_g2_integration.tcl`；G1 入口 `create_display.tcl` / `build_display.tcl` 未改动
- Vivado 构建以 `DISPLAY_BUILD_COMPLETE:CDC_review_and_board_acceptance_pending` 和 `G2_BUILD_COMPLETE:BIT_HWH_PRESENT;manifest_and_board_smoke_pending` 结束；原生 MCP 命令退出码 0；原始 Vivado MCP 日志已归档为 `evidence/vivado-mcp-integration.log`
- 架构候选及边界见 [decision-record.md](decision-record.md)。拓扑保留为候选，未冻结 Route、视频源或 ADR-0005 接口。

## 集成拓扑与读回

保留 G1 M00–M08 和 HDMI HP2；新增 RLE DMA M09、pipeline M10。PS FCLK0 为 RLE 50 MHz，FCLK1 为控制/GP0 100 MHz，FCLK2 为 HDMI/VDMA 142.857132 MHz。RLE 使用专用 `reset_rle`，同步释放于 FCLK0；RLE IRQ 未连接，软件轮询。RLE DMA 输入/输出缓冲与显示帧缓冲分离，压缩 RLE stream 不接入 HDMI；三条通路仍共享 DDR，竞争需由 G2.5 量化。

| MI | 控制端点 | 64 KiB 基地址 |
|---|---|---:|
| M00 | VDMA | `0x43000000` |
| M01 | HDMI dynamic clock | `0x43C10000` |
| M02 | HDMI VTC | `0x43C30000` |
| M03 | color convert | `0x43C60000` |
| M04 | pixel unpack | `0x43C70000` |
| M05 | HDMI HPD GPIO | `0x41210000` |
| M06 | buttons GPIO | `0x41200000` |
| M07 | video interrupt controller | `0x40000000` |
| M08 | frame monitor | `0x43C40000` |
| M09 | RLE AXI DMA | `0x40400000` |
| M10 | RLE image pipeline | `0x43C20000` |

Vivado Tcl readback、HWH 均确认 11 个窗口大小为 64 KiB 且互不重叠；M00–M08 与显示 HP2 的角色保留。RLE MM2S 经 HP0/64-bit，RLE S2MM 经 HP1/32-bit，HDMI VDMA MM2S 经 HP2/32-bit。readback 原件：`evidence/g2-architecture-readback.tcldict`。

## 回归结果

- RLE 640×400 RTL 回归：`baseline`、`roi`、`erode`、`dilate`、`empty`、`overflow`、`bad_checksum`、`bad_header` 共 8 项通过，记录 `rtl-regression/rtl-vivado.log`。
- **完整尺寸的集成 RLE wrapper 功能网表回归：8/8 通过。** 使用本次集成工程中 `display_image_pipeline_0` 的 Vivado 2026.1 `funcsim` 网表；网表在该 IP 的综合参数下为 640×400 / MAX_RUNS=16384。测试台通过 AXI-Lite 配置和 640×400 AXI-Stream 输入检查输出握手、数据及 TLAST。结果与逐场景日志在 `rtl-regression/full-pipeline-netlist-20261030-074800/`，总标记 `RLE_FULL_PIPELINE_POST_SYNTH_NETLIST_PASS cases=8`。
- 该网表回归覆盖集成设计中的 RLE pipeline IP/wrapper，不模拟完整 `display_wrapper`、PS、AXI DMA、HP 互连或 HDMI 扫描链；不能据此宣称这些模块已做联合仿真。
- 5×3 encoder OOC 网表回归另有 `RLE_POST_SYNTHESIS_PASS`，仅作为较小单模块结果；不替代上面的完整尺寸 wrapper 回归。
- frame monitor RTL 仿真通过，`monitor-sim/monitor_sim.log` 含 `VIDEO_MONITOR_PASS`。

## 静态实现报告

- Setup：WNS `+0.054 ns`，TNS `0`；hold WHS `+0.024 ns`，THS `0`；setup/hold failing endpoints 均为 `0`。最终用户时序约束满足。
- 资源：12,068 LUT、15,437 FF、4.5 BRAM tiles、9 DSP。
- 综合：566 warnings、0 critical warnings、0 errors。主要合成类别：Synth 8-7129 479、8-7071 72、8-7023 14；另有 IP_Flow 警告。指定日志中未发现 latch、多驱动或位宽类别告警。
- DRC：33 checks、0 errors、0 critical warnings；仍保留 warning/advisory。报告中包括 DPIP-1、DPOP-2、PDCN-1569、REQP-1839/1840、RTSTAT-10 等；RTSTAT-10 列出 HP0/HP1 converter FIFO 内部 44 条无可路由负载的网。
- 方法学：8 项、0 critical warnings；保留 LUTAR-1（4）、TIMING-9、XDCB-5、XDCC-1、XDCC-7。
- CDC：CDC-3 Info 102、CDC-6 Warning 1、CDC-15 Warning 965。965 条 CDC-15 指向 `frame_monitor` 的 `mailbox_*` 到 `snap_*` 数据路径；RTL 使用 request/ack toggle 保持 mailbox 稳定至快照完成，XDC 对同步器寄存器 false-path 并对数据路径加 10 ns max-delay。报告仍保留这些 warning，未将它们视为已消失。单条 CDC-6 是 VTC AXI4-Lite `ipif_RdData_reg` 到 `SYNC2PROCCLK_I/data_sync_reg` 的厂商 IP 内部路径（depth 3、Max Delay Datapath Only），作为残留 warning 记录。
- 完整日志还出现 Timing 38-249（RGB2DVI generic PixelClk×5 时钟加载）与 Constraints 18-619（后续覆盖同名时钟）。最终 XDC/Tcl 读回确认有效 `SerialClk` period 为 2 ns / 500 MHz，master/source 指向 dynamic-clock MMCM 的实际 `CLKOUT0`，pixel clock 为其 sibling。已验证最终有效属性及 routed timing，但仍记录这两条初始/覆盖告警。
- DRC、时序、CDC、方法学和 bus-skew 原始报告位于 `reports-g2-integrated/`；构建与综合完整日志在 `project/display.runs/`（Vivado 工程目录仅留在本机结果树，不属于最小提交证据）。

## 制品身份

| 文件 | 大小 | SHA-256 |
|---|---:|---|
| `export/g2-integrated.bit` | 4,045,770 bytes | `2ec1f1dec5a2df2e07044e94ecacd5d20aeef19ec6787077c0728d24053a8b18` |
| `export/g2-integrated.hwh` | 811,935 bytes | `a663072734f2b16be9f37be0699ba897b5902c0b46398a4be914f6ee40fa852f` |
| `export/g2-manifest.json` | 4,564 bytes | `43a894610c7ce33fb9baf4aa560e14511f94a13ba5d3b74221115590e796276e` |
| `export/g2-display-smoke.manifest.json` | 1,737 bytes | `c115950a342535961f3de32b3b3e038d892e4bd8ef7491871a1c49291f47c4d9` |

本地 `bundle.verify_bundle()` 检查显示 manifest、BIT/HWH 哈希、9 个显示控制窗口和 VDMA 参数通过；HWH 与 Tcl readback 另确认完整 G2 11 窗口。文件摘要和构建数据记录于 `export/g2-manifest.json`。

## 板端冒烟状态与限制

设备 `192.168.2.99`（hostname `pynq`），boot ID `e48ff153-bd85-4dd6-98f6-438bd51da08d`。BIT/HWH/manifest 上传到新目录 `/home/xilinx/standalone-g2-smoke-20261030/`；板端 SHA-256、bundle 校验和 PYNQ runtime/font 源文件哈希检查均通过。板端 wall clock 显示 2025-05-07，验收时长不得依赖该系统时间。

尝试复用板端 SHA 为 `94b13d8e591e0b6d1747ee3b1283ee836e62c5129a2393b0b795540bc13069ea` 的 G1 runner 时，首次因旧部署缺少 `scripts.wo0015.board_preflight` 而在硬件操作前退出；该只读模块随后仅放到新 G2 隔离目录，板端 `/proc` owner 审计通过。按 `--duration 60 --fps 10 --source camera` 再次启动后，runner 报 `ModuleNotFoundError: No module named 'scripts.rle'` 并进入 `FAIL_HOLD`。原始报告为 [g1-run-01-failhold.json](board/g1-run-01-failhold.json)，独立寄存器/进程复核记录在 [post-failhold-readback.json](board/post-failhold-readback.json)。该报告声明范围是 G1 independent display，samples 为空；**不能作为 G2 冒烟结果，也不能证明 G2 BIT 已下载**。

后续独立只读复核确认：PYNQ 仍报告旧 G1 BIT `/home/xilinx/standalone-20261029-g1/export/display-diag.bit`，SHA-256 `c1829e1af595a22daf85da35449c93fe47808888ddf33b262a46252f7bc1e73f`；FPGA manager 为 operating；VDMA CR `0x00011000`（run/stop=0）、MM2S SR `0x00011001`（halted、无错误）、S2MM SR `0`；monitor signature `0x4D435631`。runner PID 28352 仍处于 fail-hold，报告 driver job finished、无 pending operation、retained_count=0；根进程 FD 只见锁文件、pipe/tty/eventpoll/socket，无 `/dev/mem` 或 video FD。当前 board recovery 仍待用户确认，未启动第二个硬件任务。

因此 G2 的“新 BIT 上复核 FCLK/地址/DMA 所有权、相机/显示启动和正常停止”及“最小板端 RLE+HDMI 集成冒烟”仍未完成；G2 不能放行，G2.5/G3/G4 不得开始或标记通过。失败后硬件状态读回只表明旧 G1 remains loaded，不替代 G2 板测。

## 后续放行条件

1. 按确认的安全恢复步骤释放旧 fail-hold runner，并验证相机、VDMA、锁和 CMA 资源状态。
2. 补齐隔离 runner 的 Python 依赖；先做无硬件触碰的 import/runtime 检查，再核对 BIT/HWH/manifest 和 boot ID。
3. 重新执行新 G2 BIT 上的最小 HDMI/相机/monitor 冒烟并记录正常停止；另需按 G2 目标执行 RLE 自检或板测，证明 RLE DMA 控制签名/几何及 DMA idle。
4. 独立复核板端 FCLK0/1/2、M00–M10 地址、HP0/HP1/HP2 与显示/ RLE buffer ownership。通过后再评估是否放行 G2；G2.5 并发压力仍是单独门禁。
