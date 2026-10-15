# WO-0011 · 分割失败归因与最小改进评估

Goal：逐例解释WO-0010的12次漏计，在全部21个H3序列、同一严格事件评分协议下比较最小策略。
Architecture：只在 `python/refmodel/experiments/` 增加离线实验，复用已经提交的区域/Tracker/评分；
不改WO-0010基线、生产RTL、原H3、四份受保护文档，不冻结Route。WO-0011不提交、不推送。
Tech stack：现有根目录Python虚拟环境、NumPy/OpenCV/Pillow/pytest。

## 前置与执行步骤

1. WO-0010代码独立审查无阻塞；fresh pytest68通过；634原文件哈希不变。
   已按用户要求先本地commit `db552eaf5b3abf781c09978614183fe012afea9a`，只收录14个明确文件，未push。
2. Worker仅新增 `docs/verification/wo-0011-missed-events.csv` 和 `wo-0011-failure-evidence.json`：
   遍历每个漏计的GT事件，列出事件帧、原PNG/哈希、像素域连通域和跟踪状态，分类依据保留。
3. Manager新增 `python/refmodel/experiments/wo0011_ablation.py` 与测试：
   Binary直接/旁路/3×3腐蚀/膨胀，PS-only opening/孔填充，独立宽度、面积、漏帧、速度、确认数、整数质心消融。
   每个候选都跑完整H3；输入仅当前图像或过去的Region；GT只在推理后评分。
4. Worker新增 `wo0011_supplements.py` 与测试：孔/薄环/小目标/细桥/近间距/触边/投影重叠/漏帧。
   人工场景单独评分，不混入原31个可确认真值，也不宣称实际垫圈准确率。
5. 新目录 `data/results/wo0011-ablation-20261013/` 保存参数、输入哈希、全条件指标和逐帧证据。
   断言复现原19TP/12FN/0dup/0spurious；直接Binary与旁路的输出应完全相同。
6. 全部新旧Python测试；核对原RTL/文档/数据哈希与索引。RTL无修改，不重复昂贵回归冒充新硬件验证。
7. 新增 `docs/verification/wo-0011-segmentation-audit.md`：全部12例表、全H3对照、剩余失败与成本收益；
   另新增 `docs/wo-0011-g1-feature-requirements.md`：特征数量/面积/坐标/异常/容量/量化需求及待决策点。

## 约束与验收

不按H3固定宽度/物体数切条，不从GT恢复ID，不用未来帧补计，不删除失败条件。
opening需两次形态学操作，孔填充是PS参考，均不是现有单次Morph模式的生产实现。
如果所有轻量策略均无合法提升，应明确报告“无提升”和信息丢失边界，而不承诺通用拆分。
迟滞、事件时机和line160主评分保持不变；任何补充线/图像条件单列，不替换基线。
G1仍须正式Route比较、资源/吞吐证据与真实图像；本轮只提供决策材料。
