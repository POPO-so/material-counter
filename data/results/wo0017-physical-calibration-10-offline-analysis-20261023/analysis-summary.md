# WO-0017 -10 离线几何与轨迹特征分析

- Capture: COMPLETE，300/300 帧，丢帧 0。
- Baseline replay: 12 计数，12 个过线事件；逐帧一致=True，事件一致=True。
- 原始相机帧：18 帧，ID=[0, 1, 2, 3, 4, 5, 10, 20, 30, 60, 90, 120, 150, 180, 210, 240, 270, 299]；映射帧=300。
- 图像几何检查：IMAGE_GEOMETRY_VERIFIED_PHYSICAL_SCALE_UNRESOLVED；正式 physical_calibration_status=NOT_MEASURED，pixels_per_mm_y=None。300帧灰度/二值上下padding全零=True，18张源帧至映射ROI逐点一致=True。200 行 active ROI 属于 letterbox 映射，未作为 180 mm 物理标尺。
- 过线轨迹数：12；跟踪 ID 不等同于物理物料身份。
- 完整生命周期：157 条实际关联记录；±5 帧窗口：126 条实际关联记录。fulltrack-observations.csv 与 track-summary.csv 分别保留观测和每个 track 的完整生命周期/过线子窗口统计，含面积、宽高、质心Y、bbox Y、边界触碰及合并次数。
- 完整生命周期多组件观测帧：25，Σ(raw_component_count−1)=25 次union；±5帧为15帧/Σ=15次。每track帧数/union数见track-summary.csv。
- 实际合并门禁裕度：完整生命周期 applied=25，gap/area/span最小剩余=3.0/127.0/2.0 px；±5帧 applied=15，最小剩余=3.0/133.0/2.0 px。
- 合并门禁按每帧记录配置（含 backward_tolerance=4.0）逐帧一致地重放。

## 几何边界人工复核

请查看 contact-source640x400.png 与 contact-mapped320x240.png，并结合两份 row-profile CSV/PNG 核验图像几何。当前正式物理标定状态保持 NOT_MEASURED / pixels_per_mm_y=null：原图未显示可确认的皮带两端或180 mm标记，不能把200行映射有效区间解释为实测物理宽度。

## 轨迹特征

- 合并检测面积 min/mean/max: {'n': 157, 'min': 12.0, 'mean': 194.0828025477707, 'max': 443.0}
- 合并框宽 min/mean/max: {'n': 157, 'min': 3.0, 'mean': 19.248407643312103, 'max': 28.0}
- 合并框高 min/mean/max: {'n': 157, 'min': 7.0, 'mean': 18.363057324840764, 'max': 30.0}
- raw CCL 前景面积 min/mean/max: {'n': 182, 'min': 12.0, 'mean': 167.42307692307693, 'max': 443.0}

参数的 observed_gate_margins 覆盖本次12个算法过线track的157条实际关联（并单列±5帧窗口），不构成独立物理样本的普遍安全性证明。260 px限制的是拟合并组，不限制单个raw CCL；max_area=900是单个检测面积上限。实际物料数12为操作员确认，无逐物料人工标注。
