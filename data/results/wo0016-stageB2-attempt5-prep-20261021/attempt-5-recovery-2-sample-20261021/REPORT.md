# WO-0016 Stage B-2 — 第一次有效采样数据报告

## 结果

Attempt-5 Recovery-2 完成了 165 帧实测。记录真值为 5 颗 M6 螺母，跨线计数为 4 次，准确率 80%，少计 1 次。该结果作为本轮采用的唯一有效实体采样数据；它是一轮测量，不代表多轮重复性。

| 项目 | 实测 |
|---|---:|
| Boot ID | d9f65950-d59b-4ea4-bee4-b28656cb176f |
| Attempt ID | attempt-5-recovery-2 |
| 执行状态 | BOARD_STREAM_COMPLETE |
| 目标 / 计数 | 5 / 4 |
| 准确率 | 80% |
| 处理帧数 / 固定帧数 | 165 / 165 |
| 执行器端到端吞吐 | 22.132 FPS |
| 实测运行时间 | 7.455 s |
| 相机输入格式 / 分辨率 | MJPG 640×400，转换为 Gray8 320×240 |
| 相机报告帧率 | 60 FPS |
| 阈值 | 128 |
| PYNQ 导入 / 板卡访问 | 成功 / 成功 |
| DMA 缓冲区释放 | 全部释放 |
| 执行错误 | 无 |

## 计数事件

浏览器点击被接受，之后记录到 4 次非缺帧推断的跨线事件：

| 帧 | 轨迹 ID | 质心 X (px) | 质心 Y (px) | 缺帧推断 |
|---:|---:|---:|---:|---|
| 64 | 1 | 172.18 | 133.59 | 否 |
| 84 | 2 | 165.15 | 135.24 | 否 |
| 118 | 4 | 162.13 | 130.79 | 否 |
| 150 | 5 | 172.46 | 112.34 | 否 |

记录未能把漏计归因到某一颗具体螺母，因此这里只报告总计数差异，不推断漏计实体身份。

## 计数配置与资源状态

采用 M6_nut_no_washer、X 轴 X=160 计数线、Y=120、min_area=12、max_step=60 px、max_lateral_step=18 px。近邻聚类阈值为 Δx=24 px、Δy=12 px；跨线冷却为 3 帧、Y 走廊 15 px；边缘缺帧保持上限为 4 帧。

CmaFree 在执行前为 118,796 KiB，缓冲区分配后为 65,460 KiB，释放后为 64,220 KiB。4 个 DMA 缓冲区均完成分配与释放；最终 MM2S/S2MM 原始状态值均为 4098。

READY Guard 保持 consumed=false；本次一次性许可由独立 consumed marker 记录消耗。Stage 1 回执绑定同一 Boot ID，状态为 OVERLAY_LOAD_PASS。

## 审计文件与 SHA-256

- 结果 JSON：72a8bd7c2ab084b5e8176bb4f836f31ee7a12321db9face1975e0a28bfa7cb39
- 逐帧记录：4c533b1b70a8337820cf31343cd790008958ff4b8670496a6269ef12fe6c56ec
- 一次性许可：1efb4a63450c1c5449e7e7a79ce0d51e4ad66673898055c5e959174fcf7f7571
- consumed marker：f82b96f4dbb9f986a0e5219d9c98a72bb7a06446ea564781e9cdee72b51d9801
- 执行器 SHA-256：8f6c1fe14e3d67b607eb728635076a782f7fa868a713be05b590abf1af3ae158
- 计数器 SHA-256：83d3bb62e32f778e8c800eac08a415aa3f85bc27290ead3f458f96daa1e3a8df
- Manifest SHA-256：517debdc3a7e94d58dde1d8db32995d498a9fc53acc48623d46dc39061dd08c0
- 原始证据归档 SHA-256：f4d54eaa0dd6b800fd72a4ec3a840d59adcb32cc9df6257839f7d43187146a60

原始证据包包含执行结果、165 帧记录、许可、consumed marker、里程碑、退出状态及操作者控制事件。