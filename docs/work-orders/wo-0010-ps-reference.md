# WO-0010 · 隔离的区域、跟踪与计数参考

状态：用户批准 PC/PS 参考算法、G1补证和临时二值帧路径设计；正式系统职责、Route、DMA接入均未更改。
日期：2026-10-13。不修改四份受保护文档，不暂存/提交/推送。

## Goal / Architecture / Tech stack

Goal：跑通已有 H3 PNG → Gray阈值/Binary8 → 区域特征 → 确定性一对一跟踪 → 越线事件 → 独立GT评分。
Architecture：`python/refmodel/ps_reference/` 下独立模块，区域逻辑字段兼容接口B的含义，
不做字节打包、DMA驱动、真实编码器或PL区域实现。GT身份只进评估器，不进检测和跟踪器。
Tech stack：现有 numpy/OpenCV/Pillow/pytest，无新增依赖；MCP执行原RTL回归。

## 检查与参考契约

H3共21序列，每条24帧20对象；504个GT帧、487张PNG、17个明确整帧掉帧。
没有已有PS tracker。H3 `visible=false` 是整帧丢失，不是单物料消失；出画/重现另加隔离人工测试，
不改变冻结生成器/原PNG/GT。间距6/8会造成同一行矩形重叠/接触，分割失败必须如实记下。

参考参数明确为假设，未校准或冻结：threshold128、8连通、min_area16、宽度>24标疑似合并，
Morph默认旁路；centroid计数、+X方向、line_x160、hysteresis2、确认2次、容忍2个缺失帧，
最大每帧位移6、纵向门限8像素。速度只来自已观测特征，不能读取GT速度或借用未实现的encoder_speed。

计数按MASTER_PLAN：观察到 x<line-h 才armed，确认轨迹在 x>line+h 后一次计数；
从线后出生标born_counted，未来不计。不得在漏帧期间仅靠预测发事件。重复帧号拒收；一轨最多一次，
完全重复的特征记录去重并记诊断。短暂消失可保留轨迹，长期消失身份无法保证，必须记录失败边界。
右→左用direction=-1的对称规则测试。弱合并处理只拒收疑似合并特征/暂停计数，不拆分真实粘连物体。

独立评分同时给：中心线物理穿越真值（全21序列复核为35），h=2且完成严格迟滞退出的可确认真值（31），
以及终端尚未退出迟滞带的4个待定事件。此前25/22/3初审统计已被完整遍历纠正，原GT和规则未改。
真值用全部GT轨迹计算，包含缺失PNG帧；不以最终总数相等
替代逐对象事件、miss/duplicate/spurious/ID-switch审计。GT几何输入只作诊断，与图像端到端结果分开。

## Implementation plan and boundaries

1. 新增 `regions.py` 与单元测试，先运行缺模块RED，再实现OpenCV区域/零边界Morph与随机RTL黄金模型对照。
2. Manager新增 `tracking.py` 与测试，先RED；明确confirmed/lost/finished、方向/迟滞/一次计数和预测最近邻。
3. 新增独立 H3 evaluator 和端到端测试；参数、阈值、真值定义写入结果，打乱GT几何输入防顺序泄漏。
4. 对21个实际序列运行并保存每帧特征、轨迹、事件、ID映射及失败原因；重复运行验证确定性。
5. 人工可观测/不可观测用例覆盖速度、间距、漏帧、重复检测/帧、目标消失重现、噪点与出生线后；
   长期消失跨线、像素已粘连等保留为已知失败，不改GT伪造恢复。
6. 另写二值帧→PS的候选接口设计和验证矩阵：TLAST语义、SOF/帧ID、缓存所有权、失效与丢帧策略；不接生产。
7. 跑全部新增pytest、原H3 7项、既有Morph84/Binary20/RGB2的真实MCP回归；核对源文件/原数据哈希与Git状态。
8. 输出PC实测耗时、物理/可确认计数指标、失败列表、候选架构与待审批项，不声称PS板端实时性或G1已通过。

新增路径仅限隔离的 `python/refmodel/ps_reference/`、`python/refmodel/tests/test_ps_*`、
本工作单、新报告/接口候选设计以及 `data/results/wo0010-*`。
本工作单承接WO-0004的参考约定，但不改写旧OPEN状态或使其计划冒充已实现交付。

## 本轮验收

隔离实现和验证已完成；结果见 `docs/verification/wo-0010-ps-reference.md`。
68项pytest通过；图像事件TP19/FN12、精确率100%/召回率61.29%，GT几何诊断31/31。
既有Morph84、Binary20、RGB2项回归通过。已记录粘连、长期跨线失踪和大幅逆向重生的失败边界。
G1仅补诊断证据，Route未冻结；DMA具体方案待审批，未接入生产，未上板。
