# G2 集成构建与门禁记录

**当前结论：G2 静态集成构建、实现报告和制品身份检查通过；同一 G2 BIT 下，HDMI/camera 运行与 RLE 12 帧自测已串行完成。额外读取 RLE pipeline AXI-Lite 页 `0x43C20000` 时发生 Linux external abort，根因未定，完整板端读回门禁仍未通过。G2 整体不放行；G2.5、G3、G4 尚未执行。** 本候选不冻结 Route、视频输入或正式接口。

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

设备 `192.168.2.99`（hostname `pynq`），本轮 boot ID `e48ff153-bd85-4dd6-98f6-438bd51da08d`。板端 G2 BIT/HWH 位于 `/home/xilinx/standalone-g2-smoke-20261030/`。板端 BIT SHA-256 `2ec1f1dec5a2df2e07044e94ecacd5d20aeef19ec6787077c0728d24053a8b18`、HWH SHA-256 `a663072734f2b16be9f37be0699ba897b5902c0b46398a4be914f6ee40fa852f`，均与集成构建产物相同。板端 wall clock 显示 2025-05-07，运行时长以 monotonic 计时为准。

### Fail-Hold 恢复与隔离导入

最初的 `g1-run-01-failhold.json` 记录缺失 `scripts.rle` 后的 Fail-Hold，scope 是 G1 independent display；当时 PYNQ 仍报告旧 G1 BIT。用户授权安全回收与重试后，重新检查发现旧 runner PID `28352` 已不存在，因此没有发送终止信号。`/proc` 设备 FD 审计无 owner，boot ID 未变，VDMA halted 且无错误，CMA 可用；G1 BIT 在加载 G2 前仍是当时已加载位流。原报告和只读复核保留在 `g1-run-01-failhold.json` 与 `post-failhold-readback.json`，不计作 G2 通过证据。

将 `scripts.rle` 最小运行依赖放入独立目录 `/home/xilinx/standalone-g2-smoke-20261030/python-modules/` 后，板端无硬件触碰导入检查通过：display runner 与 RLE runner 可导入，未导入 PYNQ、未打开 `/dev/mem`/video 设备、未调用相机 API。首个 RLE 尝试发生在 G1 BIT 仍加载时，误探 RLE DMA 地址 `0x40400000` 导致 external abort；此后改为先加载 G2，再在已加载 G2 上运行 RLE。

### HDMI/camera 串行冒烟

使用已有 display runner 与 G2 display smoke manifest（SHA-256 `c115950a342535961f3de32b3b3e038d892e4bd8ef7491871a1c49291f47c4d9`）执行 60 秒 camera 冒烟。报告 `board/g2-display-run-01.json` 的状态为 `STOPPED`，monotonic duration `61.2964 s`，提交 `589` 帧，正常释放相机，关闭后 VDMA halted。32 个 monitor snapshot 的错误标志均为 false；最后读数 `unique=589`、`frame=588`，上下行 marker 一致。报告中的 scope 字符串沿用 G1 runner 的 `G1 independent display; no RLE counting`，所以它只证明显示链路本次运行，不代表已有 G2 联合 runner。

报告同时记录板端 FCLK0/1/2 为 `50 / 100 / 142.857143 MHz`，HP2 读回为 `HP2_READ_32BIT_SYNC_PASS`；后续独立 `pynq.Clocks` 查询得到相同 FCLK 值。G2 loaded-bit 路径及 SHA-256 在 HDMI 后复核与本次 BIT 匹配。

### RLE 串行自测

RLE runner 使用独立的严格 schema；因此从完全相同的 G2 BIT/HWH 生成板端 adapter manifest `board/rle-g2-adapter/export/manifest.json`（SHA-256 `9eeb76496f3bbc2a90992b0d2030078daea2b01430e69ac8b536e66377cbaa15`）。`board/rle-g2-adapter/board/rle-run-01.json` 是安全拒绝：PYNQ 已加载的 BIT 与 adapter 路径不同，虽内容相同，旧检查只比较路径，0 帧启动且未持有 buffer。为允许“显式 opt-in 且 SHA-256 相同”的路径等价，`board_preflight` 和 RLE runner 加了窄范围兼容检查；`fix/允许同哈希位流路径` 的 25 项板端 preflight 测试通过，修改已合入当前 G2 分支（`2d64e9b`）。哈希不同仍拒绝。

随后不重新下载位流，在已经加载的 G2 BIT 上完成 `board/rle-g2-adapter/board/rle-run-02.json`：`RLE_BOARD_COMPLETED`，640×400 moving synthetic pattern 12 帧，12/12 像素重建通过，0 invalid frame，`buffers_held=false`；运行报告的 DMA 实际回传长度与硬件长度一致，HP1 32-bit AFI1 同步检查通过。`count_accuracy=NOT_MEASURED`；该短自测不是计数准确率或持续性能验收。RLE 报告记录的 loaded-bit SHA-256 与 G2 BIT 相同；adapter 文件路径不同是有意的。

### 退出后状态与未解决读回

最后读回确认 PYNQ global state 指向 G2 BIT、`shutdown_ips={}`；RLE DMA MM2S/S2MM status 均为 `0x00001002`（idle、无错误位），control run/stop bit 仍置位，因此状态应称 **idle**，不称 halted。HDMI VDMA MM2S status 为 `0x00010001`（halted、无错误），S2MM 通道未启用；frame monitor signature 为 `0x4d435631`。设备 FD owner 审计 `findings=[]`、`denied=[]`，runner 结束后未发现活动 runner，CMA 在额外探读进程退出后为 `46236 KiB / 131072 KiB`。汇总见 `board/g2-post-smoke-readback.json`。

尝试额外独立读取 RLE pipeline control 页 `0x43C20000` 时，PYNQ MMIO 进程触发 kernel external abort（`dmesg`：`Unhandled fault: external abort on non-linefetch (0x1018)`；映射 PTE 对应 `0x43c2xxxx` 页），未生成完整快照。根因尚未确定；没有再次访问该页，也未写寄存器。RLE runner 在 12 帧传输期间通过其 preflight 与每帧状态轮询完成签名/几何/状态访问，因此本事件与自测完成报告并存，需作为独立的退出后读回问题调查，不能隐去或推断为设计已无故障。

本轮确认了 FCLK0/1/2、HP1 和 HP2；HP0 的宽度目前只有 HWH/构建读回及成功 RLE MM2S 功能路径证据，没有独立 AFI0 板端读回。Vivado 集成报告仍记录 CDC-6/CDC-15 warning；并发 DDR、反压和延迟未测。两条 runner 是先后独立运行，没有同一运行 session、并发协调或统一停机证据。因此本轮只支持 G2 功能冒烟的部分证据，不构成 G2 完整放行。

## 后续放行条件

1. 分析额外读取 `0x43C20000` 后发生的 external abort；先从构建 HWH、地址译码、时钟/复位和 Linux 映射证据定位根因，再决定如何安全复核。当前不要重复访问该页。
2. 在原因消除后补齐退出后 pipeline signature/geometry/status 的安全只读读回，并保存寄存器、内核日志、owner、CMA、boot ID 和 BIT hash 一致的同轮证据；确认 RLE DMA idle、VDMA halted、缓冲释放。
3. 补独立 HP0/AFI0 板端宽度读回；FCLK0/1/2、HP1/2 本轮已有板端读回。地址 M00–M10 仍以已通过的 HWH/Tcl readback 为静态证据，不把它当作所有寄存器的板端实读。
4. G2 放行前明确记录 RLE 与 HDMI 的调用/所有权协调以及正常停机边界；本轮已有功能测试是两个 runner 的串行执行。
5. G2.5 仍须另测 HDMI 720p60 扫描期间有目标及高游程 RLE 并发，量化共享 DDR 竞争、反压、错误率及 RLE 延迟分位数；之后才评估 G3/G4。G2.5、G3、G4 当前未开始。
