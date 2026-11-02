# 最终 route CDC 复核

## 绑定的构建证据

本复核使用 `T:/jj1102l` 的最终 routed design，而不是早期综合恢复报告。route 收据为 `joint-route.tcldict`（SHA-256 `bed39b72907148b156f2fdf30999c26d15493b7080ebfe641119dd5f6b926069`），状态 `ROUTE_PASS`，收据 slack 为 max `+0.032 ns`、min `+0.020 ns`，violations 为空。配套原始报告及约束已复制到本目录：

- `cdc.rpt`：最终 route CDC 报告，SHA-256 `205969bdabc4f3ba6bd2c2667c8f7cb4b7de5652132fe03d3db7701be8f807a7`。
- `timing.rpt`：最终 route timing 报告，SHA-256 `613a2488abaf5fcb482b701595fa3118f895ecc4734a4a21c680b1a0a18f2d82`。
- manifest SHA-256：`b2d3cab16dfd975a1e8654f8ca6de4513818a8951a3b8e95067bc2499c8877bc`。
- `joint_monitor_cdc.xdc` SHA-256：`bd430eeaeba6c9192b0c5b391c61617f705df8e926d814b17420bf759e01beac`。
- `offload_cdc.xdc` SHA-256：`614750b75f05cba5bc9b260e89487326ae3464aedbc5780f8fde911f8c491f5c`。
- `video_frame_monitor.v` SHA-256 `85568ff91ecc0b718ca391bc9c21533c96dbcab18c46788c9d19214acd16166e`，与构建快照一致；`video_binary_overlay.v` SHA-256 `e44969b0fc2367f1194ff9b9d2adafcde0e235975f678ffb41313aa41a5e8871`。

此前的 `T:/jj1102l/route-review.json` 记录的是综合阶段 CDC 报告哈希，不能直接代表最终 routed CDC 报告。本记录绑定并复核了 route 阶段的 `cdc.rpt`。

## Findings 与处置

最终 route 报告汇总为 998 条 CDC-15 warning、1 条 CDC-6 warning，没有 CDC-13 critical。逐项检查 CDC-15 报告行后，来源与约束覆盖如下：

- 965 条来自 `video_frame_monitor` 的 `mailbox_*` 到 `snap_*` 数据总线。像素域 mailbox 在请求/应答 toggle 握手期间保持稳定，AXI 域只在应答同步回来后原子采样。`joint_monitor_cdc.xdc` 对这些端点施加 10 ns `set_max_delay -datapath_only`；没有把 payload 路径 false-path 掉。
- 33 条来自 `video_binary_overlay` 的 `publish_bank`/`publish_id` 到 `display_bank`/`displayed_frame_id`。发布字段在 ack 返回前保持稳定；`offload_cdc.xdc` 对这些端点施加同样的 10 ns datapath-only 上限，并仅将 toggle 同步器首级排除在常规时序路径之外。
- 两组 998 条报告行都标注 `Max Delay Datapath Only`，且均与对应约束端点匹配。它们是已识别的握手式 bundled-data CDC warning，保留报告供跟踪；没有通过添加 false path 来消除告警。

CDC-6 的唯一报告路径位于 AMD VTC 内部 AXI-Lite read-data 到 `SYNC2PROCCLK_I/data_sync_reg`，深度 3，带 `ASYNC_REG` 属性并受 datapath-only 最大延迟约束。它属于生成的 VTC IP 内部跨域路径；保留此 vendor-IP warning，不对生成逻辑加项目级 false path。

## Hold 检查边界

`set_max_delay -datapath_only` 不生成这些异步 payload 路径的 hold 检查。route timing 报告整体为 0 个 setup/hold failing endpoints，WNS `+0.032 ns`、WHS `+0.020 ns`；该全局 WHS 不应被解读成上述 mailbox payload 例外路径经过 hold 检查。payload 路径的处置依据是 request/ack 握手期间源数据稳定、应答往返后才采样，以及已核对的 10 ns 最大数据路径约束。

**结论：CDC-13 critical 已为 0；998 条 CDC-15 与 1 条 CDC-6 均已按当前 routed 报告完成归因，仍保留为 warning。没有为清零 warning 修改 RTL 或放宽 CDC 约束。**
