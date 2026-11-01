# SW1 G2 CDC 报告人工复核

复核日期：2026-11-02
对象：本目录 `reports-g2-integrated/cdc.rpt`，对应 `export/build-manifest.json` 中 `source_commit=a4fa257` 的 G2 候选构建。

## 结论

本次 CDC 告警已逐类完成设计语义和约束审查：两条 CDC-6 均有端点特定的接受理由；1,703 条 CDC-15 均属于受请求/应答握手保护的 bundled-data 路径，并有数据路径最大延迟约束。**人工审查结论为接受当前实现和约束，不创建 Vivado waiver 对象，也不从原始报告中隐藏告警。** 原始 `cdc.rpt` 仍保留 2 条 CDC-6 Warning 和 1,703 条 CDC-15 Warning。

此结论仅覆盖该 G2 候选网表、端点、RTL 和约束。它不代表 BIT/HWH 已部署或板端验收通过；板端验收按用户要求暂缓。

## CDC-6：两条多比特同步告警

| 报告行 ID | 端点与宽度 | 报告信息 | 人工处置 |
|---|---|---|---|
| 1 | `button_debounce/inst/stable_state_reg[3:0]` → `btns_gpio/.../syncstages_ff_reg[0][3:0]`，4 位 | Depth=4；False Path | 按独立按钮控制信号接受，仅此端点适用 |
| 970 | `hdmi_out/frontend/vtc_out/.../ipif_RdData_reg[31:0]` → `...SYNC2PROCCLK_I/data_sync_reg[0][31:0]`，32 位 | Depth=3；Max Delay Datapath Only | v_tc 6.2 厂商 IP 内部 CDC，有厂商文档和 IP 时钟约束依据 |

### ID 1：BTN0–BTN3 是独立控制位

`button_debounce_irq.v` 对每个按键分别同步、消抖并更新 `stable_state[i]`；PS 的 `ButtonDecoder` 按位比较按下/释放状态并生成独立命令。四位没有被解释为一个编码值、one-hot 状态或要求同拍原子更新的数据字。因此，独立同步造成的位间时差只会让各按钮事件在不同采样时刻被观察到，不会把一个合法编码转换成另一个编码。

据此可接受该 CDC-6 的**端点特定语义理由**。这不是 CDC-6 规则本身安全的通用豁免；若将这些位改为编码字段、互相关联状态或要求原子采样的总线，必须重新审查。报告中的 False Path 也不单独构成语义安全证明。

证据：[`button_debounce_irq.v`](../../../fpga/experimental/standalone/button_debounce_irq.v)、[`controls.py`](../../../scripts/standalone/controls.py)、原始 [`cdc.rpt`](reports-g2-integrated/cdc.rpt)。

### ID 970：VTC v6.2 的 AXI-Lite/视频域 CDC

该端点为 32 位读数据；报告里的 **Depth=3 表示同步级数，不是 3 个数据位**。HWH 将实例识别为 `xilinx.com:ip:v_tc:6.2`。AMD PG016 说明 VTC 在视频 `CLK` 与 AXI4-Lite `S_AXI_ACLK` 之间包含时钟域跨越逻辑，并说明控制事务在视频处理停顿时仍需完成。该路径在报告中标记为 `Max Delay Datapath Only`；构建生成的 [VTC 时钟约束](project/display.gen/sources_1/bd/display/ip/display_vtc_out_0/display_vtc_out_0_clocks.xdc)对两个方向采用 datapath-only 最大延迟约束，而非把整条路径 blanket false-path。

据此将其作为**厂商拥有的 IP 内部 CDC**接受，理由限于 VTC 6.2 的已识别身份、PG016 所述接口行为和该 IP 的跨域时序约束。本项目没有 VTC 内部 RTL，故本复核不声称独立证明其内部电路实现。AMD 资料：[PG016 S_AXI_ACLK](https://docs.amd.com/r/en-US/pg016_v_tc/S_AXI_ACLK)、[PG016 Clocking](https://docs.amd.com/r/en-US/pg016_v_tc/Clocking)。

## CDC-15：1,703 条 clock-enable controlled CDC

AMD UG906 将 CDC-15 描述为 clock-enable controlled CDC 结构；在 CDC 规则优先级表中该类 warning 标为 Safe。仍逐项核对了本设计的握手、payload 稳定条件和时序约束，而不是只依据规则类别接受。

报告中的 1,703 个 bit-path 归为 19 组：`video_frame_monitor` 10 组、965 位；`video_binary_overlay` 9 组、738 位。以下宽度为报告实际列出的跨域端点位数：

| 模块 / 源 → 目的 | 位数 |
|---|---:|
| `mailbox_bottom_marker` → `snap_bottom_marker` | 224 |
| `mailbox_top_marker` → `snap_top_marker` | 224 |
| `mailbox_session` → `snap_session` | 128 |
| `mailbox_frame` → `snap_frame` | 64 |
| `mailbox_unique` → `snap_unique` | 64 |
| `mailbox_scans` → `snap_scans` | 64 |
| `mailbox_repeat` → `snap_repeat` | 64 |
| `mailbox_invalid` → `snap_invalid` | 64 |
| `mailbox_partial` → `snap_partial` | 64 |
| `mailbox_diag_flags` → `snap_diag_flags` | 5 |
| **frame monitor 小计** | **965** |
| `cfg_mb_boxes` → `boxes_p` | 640 |
| `cfg_mb_id` → `id_p` | 32 |
| `publish_id` → `displayed_frame_id` | 32 |
| `cfg_mb_box_valid` → `box_valid_p` | 16 |
| `cfg_mb_line` → `line_p` | 10 |
| `cfg_mb_count` → `count_p` | 5 |
| `cfg_mb_enable` → `enable_p` | 1 |
| `cfg_mb_line_valid` → `line_valid_p` | 1 |
| `publish_bank` → `display_bank` | 1 |
| **binary overlay 小计** | **738** |
| **合计** | **1,703** |

`video_frame_monitor` 通过 request toggle/双触发器同步/acknowledge toggle 传递采样请求。视频域观察到新请求后，将完整结果复制到 mailbox 并返回 acknowledge；AXI 域在 acknowledge 经同步后才将 mailbox 原子复制到 `snap_*`。忙状态阻止上一笔握手完成前启动下一次采样，因此 mailbox 在目的域读取期间保持稳定。

`video_binary_overlay` 的配置 payload 先锁存在 `cfg_mb_*`，通过 request/acknowledge toggle 握手传到 pixel 域，并在帧边界采样；`cfg_ready` 在 ack 返回前保持关闭，避免覆盖未完成 payload。显示 bank/id 也由 pending/acknowledge 协议保护，发布内容在 pixel 域确认前不被替换。

约束与 RTL 相符：[`offload_cdc.xdc`](../../../scripts/standalone/offload_cdc.xdc) 只对控制同步器首级设 false path，并对配置 payload、发布 id/bank 设 `Max Delay Datapath Only`；本构建的 [`monitor-cdc.xdc`](monitor-cdc.xdc) 对 frame monitor 的 request/ack 首级设 false path，对 mailbox 到 snapshot payload 设 10 ns datapath-only 最大延迟。**没有对这 1,703 个 payload bit 使用 blanket false path。**

复位前提：本次集成连接中 `reset_rle` 与 `reset_pixel` 的复位输入来自共同的 PS `FCLK_RESET0_N`，各时钟域再分别同步复位；可在 [集成 HWH](export/g2-integrated.hwh)、[create_g2_integration.tcl](../../../scripts/standalone/create_g2_integration.tcl) 和 [create_display.tcl](../../../scripts/standalone/create_display.tcl) 核对。`video_binary_overlay.v` 明确要求运行期间发生独立复位时协调三个域。因此本结论以共同复位来源和协调复位为前提；运行中单独复位某一个域不在接受范围内。

依据：[`video_frame_monitor.v`](../../../fpga/experimental/standalone/video_frame_monitor.v)、[`video_binary_overlay.v`](../../../fpga/experimental/standalone/video_binary_overlay.v)、[`offload_cdc.xdc`](../../../scripts/standalone/offload_cdc.xdc)、[`monitor-cdc.xdc`](monitor-cdc.xdc)、[AMD UG906 Understanding CDC Rules](https://docs.amd.com/r/en-US/ug906-vivado-design-analysis/Understanding-the-Clock-Domain-Crossings-Report-Rules)、[AMD UG906 CDC Rules Precedence](https://docs.amd.com/r/en-US/ug906-vivado-design-analysis/CDC-Rules-Precedence)。

## 保留事项

- 两类 CDC 告警仍会显示在 Vivado 原始报告中；本记录是人工 disposition，不是 `create_waiver` 或报告消音。
- 未来若改变按钮向量语义、mailbox 握手、payload 更新时序、相关约束或复位方式，应重新跑 CDC 并重新审查。
- 板端 SW1 实物验证和 bitstream 部署未执行。
