# ADR-0004 · Interface A Native Gray8 Active Baseline

| 项 | 内容 |
|---|---|
| **状态** | **Accepted / Frozen / Active Baseline** |
| **日期** | 2026-10-27 |
| **决策者** | 项目负责人明确授权 |
| **范围** | 接口 A 的 MM2S 图像包格式及其当前 PL 解包路径；不冻结摄像头或回放输入路线 |
| **关联** | `docs/02_interfaces.md`、WO-0015 Gray8 RTL、WO-0015 PS 预检和运行包 |

## 背景

Gray8 已完成 PS 封包、PL 解包与阈值处理的离线 XSim bit-exact 验证，并完成 PYNQ-Z2 目标器件的 Vivado implementation。负责人于本日明确批准将该接口格式设为正式活动基线。

## 决策

1. 接口 A 使用 320×240 Gray8。像素 payload 为 76,800 字节，紧邻 16 字节 Header 与 16 字节 Footer；MM2S 总包为 76,832 字节，即 19,208 个 AXI4-Stream 32-bit beats。
2. 像素按行优先连续排列；每个 32-bit payload beat 以 little-endian 顺序承载 4 个 Gray8 像素（byte 0 到 byte 3）。Header 和 Footer 各 4 个 word；Footer 第四个 word 设置包尾 TLAST。包校验、尺寸、frame ID、payload 长度及 checksum 按 `docs/02_interfaces.md` 定义执行。
3. PL 解包器按序输出 8-bit 灰度像素，提供 SOF/EOL 并严格遵守 TREADY 反压；灰度流直接接入 `axis_gray2binary`。当前 Active 路径不实例化 `axis_rgb2gray`。
4. S2MM 输出继续为 Binary8，76,800 字节/帧、19,200 个 32-bit beats；其既有回收与安全状态机不因接口 A 格式冻结而改变。
5. 默认构建工件位于 `data/results/wo0015-gray8-baseline-20261027/export/`。本次冻结工件：
   - `wo15.bit` SHA-256 `dfe50c82731139c59f71b22386187bf39012ff1dd2ccd2efe1f5d9489e486d56`
   - `wo15.hwh` SHA-256 `844af1c52c5f5b4a290501952206ce1d2e52157c9447aca8ed8cf1c97a9f2cb7`
   - `pair-manifest.json` 使用仓库相对路径；默认板端运行包 manifest 为 `board-package/run-manifest.json`，SHA-256 `df769072750cbe7f2674b6473be95797127f840fb61c6a2feb6dc9153793a64d`。

## 验证证据与边界

- Vivado 2026.1，器件 `xc7z020clg400-1`，50 MHz；实现时序 WNS 8.160 ns、TNS 0 ns、WHS 0.014 ns，时序 failing endpoint 数为 0；DRC Error 与 Critical Warning 数为 0。
- Gray8 XSim A/B/C 及 4 个随机反压种子均为 bit-exact（`mismatch_bytes=0`）；短包、长包、checksum 错误、复位中止、packer 和 wrapper guard 用例通过。原始 JSON、输入向量和日志保存在 `data/results/wo0015-gray8-baseline-20261027/xsim-final/`。
- 以上是本机离线仿真和 Vivado implementation 证据。**未进行板端 BIT 下载、DMA 传输或连续运行验收。**

## 后果

- PS 与 PL 的新集成以 Gray8 为默认接口 A；旧 RGB888 包不再是本活动基线。
- `board_preflight.py`、`board_once.py`、`run_stream_counter.py` 默认读取仓库内的 Gray8 运行 manifest；显式 `--manifest` 仍可覆盖。板端执行仍必须独立提供已审阅的 manifest SHA，并通过既有 boot、stage、一次性授权和 CMA 安全检查。
- 具体相机格式、采集适配和回放设备路径仍开放。接口格式冻结不构成真实摄像头或 PYNQ 板端通过证明。
- 后续协议修改仍按接口治理流程提交 PR、三人确认并形成新 ADR。
