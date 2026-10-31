# HDMI 系统资源监测视图设计记录

日期：2026-10-31
状态：候选实现；尚未板端验收，未走 PR／三人签核，未冻结正式接口或 Route；HDMI 显示增量仍为候选，G3 未放行。

## 目标与核心口径

在现有 1280×720 HDMI 画面中加入系统资源环形图，同时保留相机画面、原有指标卡、操作提示、检测目标和详细状态文本。画面严格区分实时运行时数据与构建时静态数据。下面四类指标来源和含义不同，不得混作同一类“资源实时占用”。

### A 类：静态（Vivado 报告）

Vivado 报告中的 LUT、FF、BRAM、DSP 用量与占比，以及 WNS、TNS、WHS、THS、Fmax 均为该构建／位流的静态属性。运行期间不会随 CPU 或数据流变化，只有换 bitstream 才会改变。板端将它们作为常量读入，并明确标注“静态”。

本次构建 manifest 给出的 LUT 为 12,221，FF 为 16,174，BRAM 为 4.5 tiles，DSP 为 9；WNS 为 0.188 ns，WHS 为 0.029 ns。根据器件总量计算，LUT、FF、BRAM、DSP 占比分别为 23.0%、15.2%、3.2%、4.1%。本次给定静态值中没有 TNS、THS、Fmax 数值，不在本记录中推算或补填。

### B 类：运行时·PL 已有计数器

- `axis_dma_rle_pipeline_bd.v`：64 位 clk ticks、active ticks、in/out beats、in/out stalls；AXI-Lite 地址为 `0x40400000`，读取采用原子快照。
- `video_frame_monitor.v`：扫描、新身份、重复、无效、截断帧计数；AXI-Lite 地址为 `0x43c40000`。

这些是 PL 数据流／帧监测计数器。RLE 的 `active_ticks` 表示 pipeline 活动周期，`stalls` 表示 AXI-Stream 握手停顿；它们不等于 PS CPU 负载，也不等于整机 DDR 带宽。

### C 类：运行时·PS Linux 直读

以下由 Linux 运行时读取，普通用户可读，读取开销为微秒至毫秒级：

- `/proc/stat`：系统总 CPU 与每核 CPU 计时增量。
- `/proc/meminfo`：`MemTotal`、`MemAvailable`、`CmaTotal`、`CmaFree`。
- `/proc/<pid>/stat`：进程 CPU ticks 与 RSS。
- `/proc/loadavg`：系统负载平均值。
- `/sys/class/thermal/*` 或 XADC IIO：温度。
- cpufreq sysfs 或 SLCR：PS 频率。

其中画面 LIVE 行的 PS CPU、CPU0、CPU1 和内存使用 `/proc` 直读运行值。内存容量占用率定义为 `1 - MemAvailable / MemTotal`。它表示可用内存容量的变化，不是 DDR 控制器的数据传输带宽。

### D 类：必须新增硬件计数器（当前未做）

DDR 带宽利用率可由 DDRC 计数器（`0xF8006000`）或 AXI PMON 采集；AXI HP0/1/2 吞吐、FIFO 水位及溢出／欠载也需要 AXI PMON 或自建 AXI-Lite 寄存器。新增这些观测需要 AXI PMON 或自建寄存器，属于架构／接口变更。本次未实现 DDR 带宽利用率、AXI HP 吞吐或 FIFO 水位／溢出计数器；用户已决定不增加这部分 PL 资源开销。这些项目保留为 D 类的额外计数器候选。

**口径结论：LUT、FF、BRAM、DSP 不能像 CPU 占用率那样实时变化。DDR 内存占用（容量，`1 - MemAvailable / MemTotal`）与 DDR 带宽利用率是两件不同的事，不能混画或互相替代。**

## 器件与本次构建数据

目标器件为 XC7Z020（PYNQ-Z2），器件资源总量如下：

| 资源 | 器件总量 | 本次构建用量 | 本次占比 |
|---|---:|---:|---:|
| LUT | 53,200 | 12,221 | 23.0% |
| FF | 106,400 | 16,174 | 15.2% |
| BRAM | 140 个 36Kb 块（4.9Mb） | 4.5 tiles | 3.2% |
| DSP | 220 | 9 | 4.1% |

板卡 DDR3 器件为 Micron MT41K512M16HA-125:A，单 rank、16-bit、512MB，最高 525MHz／1050Mbps。容量信息不表示 DDR 带宽监测已接入。

## 数据来源矩阵

| 指标 | 类别与来源 | 读取方式 | 权限 | 开销 | 是否实时／口径 |
|---|---|---|---|---|---|
| LUT／FF／BRAM／DSP 用量、占比 | A：本次 bitstream 的 build-manifest／display-manifest `resources` | integrated runner 读取 manifest 中的静态常量 | 板端读 manifest | 启动／载入级；非周期硬件采样 | 否，静态；换 bitstream 后才变 |
| WNS／TNS／WHS／THS／Fmax | A：Vivado 构建报告 | 随构建记录；本次 manifest 资源对象只明确含 WNS、WHS | 构建环境 | 构建期 | 否，静态；未提供的数值保持未提供 |
| RLE clk／active ticks、in/out beats、in/out stalls | B：`axis_dma_rle_pipeline_bd.v`，`0x40400000` | AXI-Lite 原子快照 | 通过既有板端硬件访问路径 | 快照读取；不是 Linux `/proc` 采样 | 是，PL 运行期累计计数；active/stall 不代表 CPU 或 DDR 带宽 |
| 帧扫描／新身份／重复／无效／截断计数 | B：`video_frame_monitor.v`，`0x43c40000` | AXI-Lite 快照 | 通过既有板端硬件访问路径 | 快照读取 | 是，PL 帧监测运行期计数 |
| 系统总 CPU、CPU0、CPU1 | C：`/proc/stat` | 读取计数并按相邻样本差计算占用率 | 普通用户 | 微秒至毫秒级 | 是，采样估算 |
| 内存容量占用、MemTotal／MemAvailable、CMA 总量／空闲量 | C：`/proc/meminfo` | 读取容量字段，内存占用按 `1 - MemAvailable / MemTotal` 计算 | 普通用户 | 微秒至毫秒级 | 是，采样值；不是 DDR 带宽 |
| 进程 CPU ticks、RSS | C：`/proc/<pid>/stat` | 读取进程计数及常驻集 | 普通用户可读的进程 | 微秒至毫秒级 | 是，进程运行期样本 |
| Load average | C：`/proc/loadavg` | 读取系统负载平均值 | 普通用户 | 微秒至毫秒级 | 是，内核提供的运行值 |
| 温度 | C：`/sys/class/thermal/*` 或 XADC IIO | 读取可用 thermal／IIO 通道 | 普通用户可读节点 | 微秒至毫秒级 | 是，传感器采样值 |
| PS 频率 | C：cpufreq sysfs 或 SLCR | 读取 cpufreq／SLCR 可用信息 | 普通用户可读接口 | 微秒至毫秒级 | 是，运行期频率读数 |
| DDR 带宽利用率 | D：DDRC `0xF8006000` 或 AXI PMON | 需新增计数器读取与带宽换算 | 依计数器接口配置 | 当前未接入 | 当前未实现；不能从内存容量占用推得 |
| AXI HP0／HP1／HP2 吞吐 | D：AXI PMON 或自建计数器 | 需新增计数器及接口 | 依硬件接口配置 | 当前未接入 | 当前未实现 |
| FIFO 水位、溢出／欠载 | D：AXI PMON 或自建 AXI-Lite 寄存器 | 需新增寄存器和软件读取 | 依硬件接口配置 | 当前未接入 | 当前未实现 |

## manifest 契约与集成边界

`material-counter.display-build.v1` 已含扁平 `resources` 对象：`{lut, ff, bram_tiles, dsp, wns_ns, whs_ns}`。`integrated_run.py` 直接读取该对象。本次不新增生成器、不修改 schema，也不改 delivery／bundle 校验。静态资源数据作为本次位流的构建常量消费，不包装成运行时硬件读数。

板端运行环境为 Python 3.10.4、Pillow 9.0.1、numpy 1.21.5、OpenCV 4.5.4、PYNQ 3.1；绘制只使用板端已有 Pillow 9.0.1 API。

## 布局与绘制

采用布局方案 B：上下两行分离实时值与静态值。

- 第 1 行标题为 `LIVE`，显示四个环形图：`PS CPU`（系统总 CPU）、`CPU0`、`CPU1`、`内存`。数据来自 `/proc` 直读实时值。
- 第 2 行标题为 `STATIC`，显示四个环形图：`LUT`、`FF`、`BRAM`、`DSP`。数值来自 manifest 常量，占比为用量／器件总量；每个标签以 `· ST` 标识静态属性。
- 圆环由灰色底环、亮色 pieslice 前景和深色内圆组成，环旁显示一位小数百分比。
- 数据不可用时显示灰色空环和 `--`，不显示为 0%。数值超过 100% 时圆环最多画满，旁边文字仍显示真实数值。

本次实现采用的画布几何：

| 元素 | 几何／位置 |
|---|---|
| 画布 | 1280×720 |
| 相机视图 | `DashboardComposer.VIEW=(320,146,640,400)`；640×400 一比一等比显示，不拉伸 |
| 媒体面板 | `(24,136)-(1256,556)` |
| LIVE 行 | `y=562..637`，中心 `y=599` |
| STATIC 行 | `y=640..716`，中心 `y=677` |
| 环形图 | 外环半径 22px，内圆半径 14px |
| 四列起点 | `x=116/396/676/956` |

保留原有顶部三张指标卡（累计计数、显示 FPS、处理延迟）、左侧按钮提示、右侧检测目标和详细状态页文本。

### 显示身份保护区

画布的 `MARKER_X=640`、`MARKER_WIDTH=224`。`y=0` 与 `y=719` 两行的 `x=640..864` 各承载 224 位会话标识（magic16 + UUID128 + frame_id64 + CRC16），PL 帧监测器依赖该标识。任何新绘制都不得覆盖这两行的该区间；现有合成在绘制界面后写入标识。

## 采样、缓存与过期

当前遥测采样周期为：资源（CPU、内存、进程）3 秒；XADC／IIO 温度 5 秒；性能（FPS／延迟）2 秒。采样和 UI 更新本身会消耗 PS 资源，因此展示和记录采样周期及过期语义，解释运行值时不能忽略监测开销。

沿用现有 `TelemetryCache` 语义：当 `age > 2 * period + 1` 时标记 stale。字段缺失或 stale 时统一显示 `--`，不得回退显示成 0%。`_redraw_chrome` 的缓存 key 包含所有新实时与静态字段；值不变时不重绘。

## 离线预览与验证记录

新增 `scripts/standalone/render_dashboard_preview.py` 可生成 1280×720 PNG 和自包含 HTML。该工具提供的是离线预览（合成／回放帧 + 演示遥测值），用于查看绘制布局，不代表板端实测或实际 HDMI 输出。

本地已完成验证：`test_compositor.py`、`test_delivery.py`、`test_bundle.py`、`test_integrated_run.py` 共 82 passed，环境为本机 Pillow 12.3.0。未在 PYNQ 板上运行，也未检查实际 HDMI 输出。

## 本次范围外与当前状态

DDR 带宽利用率、AXI HP 吞吐、FIFO 水位／溢出计数器均属于 D 类，需要额外计数器及架构／接口工作，本次未实现；本次决定不增加此部分 PL 资源开销。

本增量仍是候选实现，尚未板端验收，未走 PR／三人签核，不冻结正式接口或 Route；HDMI 显示增量仍为候选状态，G3 未放行。离线预览与本地软件验证不构成板端 HDMI 验收，也不构成 DDR／AXI 硬件监测已完成的证据。
