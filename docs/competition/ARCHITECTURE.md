# 系统架构与代码对应

[作品首页](../../README.md) · [文档目录](../README.md)

## 数据通路

`LJ-01` 包含两条解码路径。已有 RLE 基线由 PS 解码 MJPEG；JPEG 硬解候选改由 PL 解码。两者处理 640×400 图像，不沿用旧 `main` 的 320×240 全帧 Binary8 往返架构。

```mermaid
flowchart LR
    CAM["USB 摄像头<br/>640×400 MJPEG"] --> SELECT{"选择与工件匹配的入口"}
    SELECT -->|"RLE 基线"| PSDEC["PS 灰度解码"]
    SELECT -->|"JPEG 候选"| PLDEC["PL JPEG 解码"]
    PSDEC --> SEG["PL ROI / 灰度偏移<br/>阈值 / 可选形态学"]
    PLDEC --> SEG
    SEG --> RLE["PL 游程、投影与帧状态<br/>候选联合路径含 FRG"]
    RLE --> ARM["PS 包校验、区域分析<br/>候选拆分、跟踪、越线计数"]
    ARM --> DISPLAY["匹配帧身份的画面与结果<br/>VDMA / HDMI 输出"]
```

图中汇总功能分工；独立 RLE 测试入口和带 HDMI 的集成包仍需分别核对其构建与 manifest。JPEG 候选的实现和板测不自动覆盖当前合并后的源码。

## 职责与源码

| 层级 | 职责 | 主要入口 |
| --- | --- | --- |
| PS 输入与运行 | 相机读帧、入口选择、参数与结果报告 | [run_counter.py](../../scripts/rle/run_counter.py) |
| PS 灰度解码 | RLE 基线的 MJPEG → Gray8 | [RLE 运行说明](../../scripts/rle/README.md) |
| PL 像素处理与 RLE | ROI、灰度偏移、阈值、可选腐蚀/膨胀、游程和投影 | [RLE 构建清单](../../scripts/rle/build.tcl) |
| PL JPEG 候选 | 压缩流解码及联合数据通路 | [联合构建](../../fpga/experimental/jpeg_integration/joint_build.tcl) · [JPEG 运行器](../../scripts/experiments/run_jpeg_counter.py) |
| PS 协议与传输 | 严格包校验、DMA 长度和帧身份 | [protocol.py](../../scripts/rle/protocol.py) · [transport.py](../../scripts/rle/transport.py) |
| PS 区域与计数 | 原生 8 连通游程合并、局部掩膜、轨迹与越线事件 | [components.py](../../scripts/rle/components.py) · [native](../../scripts/rle/native/rle_components.cpp) · [counter.py](../../scripts/rle/counter.py) |
| 显示与运行控制 | 帧关联、显示集成、所有权及部署 | [standalone](../../scripts/standalone/) · [部署记录](../../scripts/standalone/DEPLOYMENT.md) |

## 关键边界

PL 输出游程不等于 PL 已完成通用二维 CCL。RLE 基线中的二维连通关系由 PS 原生模块合并；疑似粘连目标可以局部重建掩膜。异常密集前景可能导致游程溢出，溢出帧应按协议拒绝更新计数，平均约 4.1 KB 的样本结果不能代替最大容量设计。

`run_counter --mode camera` 当前默认转入 JPEG PL 候选；显式 `--software-decode` 才选择旧 PS 解码路径。这个软件默认值不意味着任意 BIT/HWH 都可互换。JPEG v8 包绑定独立候选快照，合并到 LJ-01 后的设计需要重新构建并复验，详见[证据边界](../../data/results/jpeg-pl-board-20261102/README.md#合入-lj-01-后的证据边界)。

接口和路线治理以[接口文档](../02_interfaces.md)、[路线记录](../07_routes.md)及相关 ADR 为准，本页不作新的冻结决策。
