# 02 · 接口契约（唯一真理）

> **★ 本文件是接口定义的唯一真理。后续接口变更仍须走 PR + 三人确认 + 一次 ADR。**
> 接口 A 本次 Gray8 冻结依据项目负责人于 2026-10-27 的明确批准，详见 ADR-0004。

## 状态

| 项 | 状态 |
|---|---|
| 起草 | Day 0 |
| 接口 A Gray8 协议 | **Frozen / Active Baseline —— ADR-0004，2026-10-27** |
| 硬件实现 | Vivado 2026.1 build / implementation 通过；离线证据已归档 |
| 板端验收 | **未运行；不得表述为板端通过** |
| 输入源路线 | 摄像头与回放源接入保持开放，不由本协议冻结 |

### Gray8 Active Baseline 验证记录（2026-10-27）

- Vivado/XSim 2026.1：Gray8 端到端 suite 通过。单帧输出为 76,800 B；C 类三帧运行累计 230,400 B。A/B/C 有效帧及随机反压向量共 6 次运行，均与 Golden bit-exact（`mismatch_bytes=0`）；短包、长包、校验错误、复位中止、packer 与 wrapper guard 均通过。
- Vivado 2026.1：PYNQ-Z2 `xc7z020clg400-1`、50 MHz 构建和导出通过；实现时序 WNS **8.160 ns**、TNS **0 ns**、WHS **0.014 ns**，setup/hold/pulse-width failing endpoints 均为 0；DRC Error/Critical Warning 均为 0。
- 正式工件：[wo15.bit](../data/results/wo0015-gray8-baseline-20261027/export/wo15.bit)（SHA-256 `dfe50c82731139c59f71b22386187bf39012ff1dd2ccd2efe1f5d9489e486d56`）、[wo15.hwh](../data/results/wo0015-gray8-baseline-20261027/export/wo15.hwh)（SHA-256 `844af1c52c5f5b4a290501952206ce1d2e52157c9447aca8ed8cf1c97a9f2cb7`）、[pair-manifest.json](../data/results/wo0015-gray8-baseline-20261027/export/pair-manifest.json)。实现报告和绑定日志位于同一 baseline 目录的 `reports/` 与根目录。
- 默认板端运行包：[run-manifest.json](../data/results/wo0015-gray8-baseline-20261027/board-package/run-manifest.json)，SHA-256 `df769072750cbe7f2674b6473be95797127f840fb61c6a2feb6dc9153793a64d`。硬件执行仍需单独复核并显式提供该 SHA。
- 可复核的 XSim 输入、输出、结果 JSON 与日志位于 [xsim-final](../data/results/wo0015-gray8-baseline-20261027/xsim-final/)。板端部署与验收尚未运行。

---

## 接口 A · 视频流（PS MM2S 包 → PL 流水线）

| 信号 | 宽度 | 含义 |
|---|---|---|
| MM2S TDATA | 32 bit | 一个 beat 按 Little-Endian 顺序装入 4 个连续 Gray8 像素（byte 0 → byte 3） |
| MM2S TKEEP | 4 bit | 320×240 目标帧的 header、payload、footer beat 均为 `4'hf` |
| MM2S TLAST | 1 | 只在包尾 footer 第四个 word 置位；不是像素行末 |
| MM2S TVALID / TREADY | 1 / 1 | **必须严格实现反压** |
| PL Gray8 stream TDATA | 8 bit | 解包后每次传一个 Gray8 像素 |
| PL Gray8 stream TUSER[0] | 1 | 第一像素的 SOF |
| PL Gray8 stream TLAST | 1 | 每行最后一个像素的 EOL |

- Header：4 个 32-bit little-endian words，依次为 `0x574f3134`、`(HEIGHT<<16)|WIDTH`、payload 字节数、`frame_id`。
- Payload：**76,800 B Gray8**，行优先连续排列；每行 320 像素，4 像素正好组成一个 MM2S beat。
- Footer：4 个 32-bit words，依次为 `0x454e4434`、`frame_id`、payload 所有 Gray8 字节和的低 32 位、`0xc0dec014`；只在最后一个 word 上置 MM2S TLAST。
- 总包长：**76,832 B / 19,208 个 32-bit beats**。PL 输出 Binary8 继续为 **76,800 B / 19,200 个 32-bit S2MM beats**。
- 处理分辨率：**320×240 @ ≥30fps**（第一版，`[Target]`）；640×480 作为 stretch
- 像素时钟：100 MHz

> **★ Active 路径**：`axis_dma_gray_adapter` 校验包并从每个 32-bit payload word 按 byte 0 → byte 3 输出 4 个 Gray8 像素，随后直连 `axis_gray2binary`。PL 路径不实例化 `axis_rgb2gray`；WO-0015 S2MM frame packer 的 Binary8 输出格式不变。
>
> **待定**：Gray8 源数据的具体摄像头/回放接入方式仍取决于 C2（HDMI IN）与 C3（引脚预算）的实测结论；本协议修订本身未冻结视频源路线。

---

## 接口 B · 特征记录流（PL → PS，经 AXI DMA S2MM）

**★ 字段集按两条 Route 的超集定义** —— Route 切换不得触发接口变更（那是付费动作）。

| 字段 | 位宽 | 含义 | Route A | Route B |
|---|---|---|---|---|
| `x_min` | 10 | 段/区域起始列 | 使用 | 使用 |
| `x_max` | 10 | 段/区域结束列 | 使用 | 使用 |
| `centroid_x` | 10 | 质心列 | 可由 x_min/x_max 导出 | 使用 |
| `area` | 16 | 前景像素总数 | 使用 | 使用 |
| `peak` | 8 | 列投影峰值 | 使用 | 填 0 |
| `row_min` | 8 | 纵向最小行 | 填 0 | 使用 |
| `row_max` | 8 | 纵向最大行 | 填 0 | 使用 |
| `centroid_y` | 8 | 质心行 | 填 0 | 使用 |
| `flags` | 8 | bit0=疑似合并（宽度超阈值）；bit1=触边；**bit2=本记录含二维字段** | | |

**帧尾元数据**（与记录分开的固定位置，用于原子校验）：

| 字段 | 位宽 | 含义 |
|---|---|---|
| `frame_id` | 32 | 帧序号，单调递增 |
| `timestamp` | 32 | PL 侧像素时钟计数 |
| `total_fg` | 32 | 全帧前景像素数 |
| `seg_count` | 8 | 本帧记录数 |
| `flags` | 8 | bit0=溢出（记录数超上限） |

### ★ 原子性规则（PS 强制校验）

> **PS 只有在「帧尾元数据中的 `frame_id` 与 `seg_count` 与收到的记录数一致」时才接受该帧；
> 否则丢弃并计入 `frame_drop` 计数器。**
>
> 这条规则是防止「随机 ±1 计数错误」的唯一有效手段。初学者极易踩的坑：
> 这帧的记录被算进下一帧 → 计数随机 ±1 → 极难复现和定位。

---

## 接口 C · 控制 CSR（PS → PL，AXI4-Lite）

| 寄存器 | 用途 |
|---|---|
| `CTRL` | 使能、软复位、背景采集触发 |
| `THRESH` | 二值化阈值 |
| `LINE_X` | 计数线所在列（供 overlay 绘制；**判定在 PS 做**） |
| `ROI_*` | 处理窗口 |
| `STATUS` | 帧计数、溢出标志、当前状态 |

---

## 接口 D · 编码器带速（Planned，尚未实现）

编码器用于获取**单条可调速主输送带的实际运行速度**，作为 PS 侧运动状态和后续送料控制的正式速度输入。当前仓库尚未包含编码器硬件、PL 计数器、驱动或标定实现，因此以下是接口预留，不是已完成接口。

目标数据路径：

```text
encoder pulse/count input → PL/PS capture and counter → calibration → encoder_speed → PS tracking/counting/control
```

| 逻辑字段/信号 | 当前状态 | 说明 |
|---|---|---|
| `encoder_speed` | **Planned** | 标定后的实际带速；单位、缩放和位宽待编码器型号与采样周期确定 |
| `encoder_valid` | **Planned** | 当前速度样本是否有效；失效行为待硬件接口确定 |
| `encoder_timestamp` | **Planned** | 速度样本对应的时间基准；不得与现有 `frame_id` 或 `timestamp` 混用 |

这些字段暂不加入接口 B 的特征记录固定布局，以免在实现证据不足时改动已冻结的 PL→PS 记录契约。视觉位移速度可以作为诊断或预测量，但不能冒充已实现的编码器实际带速。

---

## 签字

| 成员 | 日期 | 确认 |
|---|---|---|
| A | | ☐ |
| B | | ☐ |
| C | | ☐ |

**G0（Day 2）判据**：三人各自口头复述本契约，且都能说清自己模块的输入输出。
