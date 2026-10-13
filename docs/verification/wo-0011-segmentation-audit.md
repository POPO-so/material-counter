# WO-0011 · 分割失败归因与最小改进评估

日期：2026-10-13。结论：合法的轻量处理候选尚未提高原H3的19/31召回。
保留全部失败序列；不按GT物体数/固定宽度切条，不用未来帧补计，不调整主评分去迁就候选。
本轮只新增隔离PC实验和决策材料，没有改变生产RTL或正式架构。

## 1. WO-0010先审查、后提交

WO-0010独立代码审查没有阻塞问题，GT只用于推理后评分；fresh pytest68通过（23.86s）。
原634文件哈希一致，四份受保护文档未改，索引为空后使用明确路径暂存。
已创建本地提交 `db552eaf5b3abf781c09978614183fe012afea9a`：
`feat: add WO-0010 isolated PS counting reference`，分支 `feat/pl-stream-skeleton`，未push。

提交14文件：`python/refmodel/ps_reference/{regions,tracking,evaluate_h3}.py`、
`python/refmodel/tests/test_ps_{regions,tracking,evaluation,pipeline}.py`、
`docs/verification/wo-0010-ps-reference.md`、`docs/work-orders/wo-0010-ps-reference.md`、
`docs/wo-0010-binary-frame-transport-proposal.md`，以及最终主结果目录中的
`summary.json`、`conditions.csv`、`manager-verification.json`、`g1-projection-diagnostic.json`。
未收录逐帧原始轨迹、图像、缓存、XSim日志或其他已有未跟踪文件。
以下WO-0011工作发生在提交之后，不再暂存、commit或push。

## 2. 12例漏计的证据

逐对象分类表为 [wo-0011-missed-events.csv](wo-0011-missed-events.csv)，
对应的事件帧/实际PNG哈希/原Binary连通域/灰度证据保存在
[wo-0011-failure-evidence.json](wo-0011-failure-evidence.json)。
每例保留condition、object_id（仅审计身份）、中心穿越帧、严格退出迟滞帧、
首个可见帧、观测缺失和区域证据，不以五个条件的总数代替12个事件。

| 失败条件 | 原可确认事件漏计 |
|---|---:|
| backbone_fast_small_01 | 4 |
| backbone_nominal_small_01 | 3 |
| backbone_nominal_small_02 | 3 |
| backbone_slow_small_01 | 1 |
| backbone_slow_small_02 | 1 |

全部事件的逐例分类如下（ID为原GT的0起始审计标识，绝不输入算法）：

| condition | ID | 中心穿越帧 | 迟滞退出帧 | 首个可见帧 | 附近掉帧 | 主分类 |
|---|---:|---:|---:|---:|---|---|
| backbone_fast_small_01 | 15 | 20 | 21 | 21 | 无 | C1 |
| backbone_fast_small_01 | 16 | 14 | 16 | 16 | 无 | C1 |
| backbone_fast_small_01 | 17 | 9 | 10 | 10 | 无 | C1 |
| backbone_fast_small_01 | 18 | 4 | 5 | 5 | 无 | C1 |
| backbone_nominal_small_01 | 16 | 21 | 23 | 23 | 无 | C1 |
| backbone_nominal_small_01 | 17 | 13 | 15 | 15 | 无 | C1 |
| backbone_nominal_small_01 | 18 | 5 | 7 | 7 | 无 | C1 |
| backbone_nominal_small_02 | 16 | 21 | 23 | 23 | 19 | C1，掉帧为次要上下文 |
| backbone_nominal_small_02 | 17 | 13 | 15 | 15 | 无 | C1 |
| backbone_nominal_small_02 | 18 | 5 | 7 | 7 | 6 | C1，掉帧为次要上下文 |
| backbone_slow_small_01 | 18 | 10 | 14 | 14 | 无 | C1 |
| backbone_slow_small_02 | 18 | 10 | 14 | 14 | 无 | C1 |

C1：同排同色目标无缝接触，目标8×14框112像素全为前景，但20目标仅形成一条160×14、
面积2240、flags5的区域；没有独立目标匹配轨迹/事件。五序列22或24个实际观测帧均如此，
奇偶帧间距都为0、接触边缘灰度差0。整条带质心上限在慢/中/快三档分别99.5/110.5/121.5，
均未到162，所以取消宽度拒收也无法按原事件协议补计。
12个首次可见退出帧均有PNG；两例附近缺帧的前后图像仍合并，不能将根因改写为漏帧。
Manager独立校验CSV的12个ID/帧号与严格baseline TP集合差集一致，39个引用哈希匹配。

逐像素实测无孔、无孤立前景噪点：487张PNG中的最小原连通域面积112，封闭孔填充改变0像素。
12例不是min_area16滤掉小目标造成。基线的独立GT几何诊断31/31说明在给定观测下关联可工作，
但逐例归因仍以原图与逐帧特征为证据，不能仅靠该诊断推断所有分割原因。
H3的同色实心接触区域不能靠缩小整条外轮廓创造原本不存在的内部边界。
取消宽度拒收标志也没有恢复物体：一个合并Region不等于多条独立轨迹。
这些结论限于冻结H3，真实物料轮廓/亮度/纹理是否保留边界仍须真实图像确认。

## 3. 全H3同协议对照

所有14组都跑完整21条件、504个GT帧/487个PNG/17个实际掉帧，保留35个中心穿越、31个可确认、4个终端待定。
主计数线160、迟滞2、阈值128、8连通、默认min_area16、max_width24；仅改变表中单一因素。
GT身份只在推理后以IoU>=0.5匹配，事件必须属于正确对象且发生在首个可见退出迟滞帧。
错时事件仍按WO-0010协议记FP和对应FN，不能用最终总数相等替代逐事件正确。

| 策略/消融 | TP | FN | 重复FP | 其他FP | 精确率 | 召回率 |
|---|---:|---:|---:|---:|---:|---:|
| Binary直接 | 19 | 12 | 0 | 0 | 100% | 61.29% |
| Morph旁路（基线） | 19 | 12 | 0 | 0 | 100% | 61.29% |
| 3×3腐蚀 | 19 | 12 | 0 | 0 | 100% | 61.29% |
| 3×3膨胀 | 17 | 14 | 2 | 14 | 51.52% | 54.84% |
| opening（腐蚀→膨胀） | 19 | 12 | 0 | 0 | 100% | 61.29% |
| 封闭孔填充 | 19 | 12 | 0 | 0 | 100% | 61.29% |
| 取消宽度保护 | 19 | 12 | 0 | 0 | 100% | 61.29% |
| min_area=1 | 19 | 12 | 0 | 0 | 100% | 61.29% |
| min_area=128 | 0 | 31 | 0 | 0 | 无预测，未定义 | 0% |
| max_missing=0 | 11 | 20 | 0 | 0 | 100% | 35.48% |
| max_missing=4 | 19 | 12 | 0 | 0 | 100% | 61.29% |
| max_step=2 | 19 | 12 | 0 | 0 | 100% | 61.29% |
| confirm_hits=3 | 19 | 12 | 0 | 0 | 100% | 61.29% |
| 质心向下截断为整数 | 5 | 26 | 0 | 14 | 26.32% | 16.13% |

Binary直接与旁路的全部逐帧特征/轨迹摘要完全一致。opening/孔填充未恢复任何原漏计。
降低min_area没收益；提高到128滤掉原112面积物体；漏帧容忍降0新增8次漏计，放宽到4无收益。
膨胀的其他14FP为10个无可匹配对象、2个对象未完成迟滞、2个错时；另有2个重复FP。
在medium间距工况中，灰度栅格取整使近邻间隙变化，膨胀后区域数随帧在20和10/11间切换，
导致反复局部合并/分离和498次已匹配观察的ID切换，不能把33个输出事件当作召回提高。
相关条件包括 `backbone_fast_medium_01`、`backbone_slow_medium_01/02`。

整数截断仍总计19，其中14个是越线时机不符；不是14个新的随机对象误计。
这说明候选PL整数质心与当前浮点参考需要明确量化/计数时机合同，见G1需求建议。
不据此改变受保护接口、迟滞或评分窗口。

结果：`data/results/wo0011-ablation-final-20261013/summary.json`、`comparison.csv`、
每策略每条件逐帧JSON；独立重复运行在 `wo0011-ablation-final-rerun-20261013`。
两次完整摘要和指标一致：`9bb9c415b16b6ae4eec4285f8b912b5de8c0517fa7beaa534e80b288fa5b293a`。
原19TP/12FN、特征6940TP/2800FN/140FP已与提交的WO-0010结果逐字段断言一致。

## 4. 策略是否适用于实际链

| 策略 | 实现关系 | 本轮结论 |
|---|---|---|
| Binary直接/旁路 | 对应当前Binary8与Morph旁路逻辑 | H3等价；保留基线，不代表已测摄像头连续输入 |
| 腐蚀/膨胀 | 现有单次Morph模式，3×3外部零背景 | 腐蚀无H3收益，膨胀破坏近邻分离；薄壁风险需实物验证 |
| opening | 需要先腐蚀再膨胀两次，现有一个Morph模式不直接提供 | 仅PC候选；未新增PL两级结构或资源/吞吐承诺 |
| 孔填充 | padded外部背景洪泛，背景4连通 | 仅PS候选；改变前景面积和孔信息，H3无收益 |

腐蚀/膨胀黄金规则沿用现有零背景边界。opening显式使用同一边界两次。
孔填充使用外部padding种子，不假定左上角是背景，避免把触边物体当作外部种子。
实现语义参考 [OpenCV形态学文档](https://github.com/opencv/opencv/blob/5.x/doc/tutorials/imgproc/opening_closing_hats/opening_closing_hats.markdown)
和 [OpenCV floodFill文档](https://docs.opencv.org/5.0/main_modules/imgproc_misc.html)。
本机依赖版本以实际环境为准，文档不构成PYNQ环境已可运行的证据。

## 5. 独立补充场景与剩余失败

`wo0011_supplements.py` 使用独立图像场景，不改冻结H3生成器；参数和全部失败保留在
`data/results/wo0011-supplements-20261013/`。孔/薄环/细桥/1～3px间隙/触边/小目标/
不同y但同投影/1～3帧中断，均使用WO-0010严格事件评分；人工场景不混入原31真值。
补充结果只说明具体几何条件下的作用，不是实际垫圈/螺母准确率。

独立补充共14场景×5策略=70组合，全部组合（包括失败）写入 `cases.csv`。

| 补充场景 | 实测结果 |
|---|---|
| 分离方块、不同y同投影 | 五策略均2TP/0FN |
| 无缝完全接触 | 五策略仍1区域、0TP/2FN/1其他FP |
| 一像素细桥 | 旁路0TP/2FN/1其他FP；腐蚀与opening均2TP/0FN/0FP |
| 厚环 | 保孔或填孔均1TP；填孔后孔数1→0、面积改变 |
| 一像素薄环 | 旁路可计数；腐蚀和opening删除目标，1FN |
| 1/2px间隙 | 旁路可分；膨胀合并，0TP/2FN/1其他FP |
| 3px间隙 | 五策略均保持两目标计数 |
| 小于min_area的3×3目标 | 旁路漏检；膨胀虽产生区域但bbox IoU不足，仍1FN并有1其他FP |
| 触边目标 | 初始记录带触边标志，五策略均完成计数 |
| 缺1/2帧观察 | 五策略均1TP/0FN |
| 缺3帧跨线 | 五策略均0TP/1FN，保留为能力边界 |

这不是“H3 improved”：细桥与无缝接触是不同几何条件，收益不能转移到原12例。
补充确定性摘要：`01ea1cd734f4fe36ccea208ea6584c8162b02c44034f404b893c98e8d0b89264`。

剩余问题：无缝同色接触/重叠无法可靠拆分；薄壁目标可能被腐蚀或opening删除；
膨胀易形成近邻连接；孔填充改变面积且会抹除有意义孔结构；长失踪跨线无法仅凭观测恢复计数；
大幅逆向重生/复杂交叉仍超出受约束最近邻能力。原失败序列和候选新增失败全部保留。

## 6. 性价比建议与G1待决策

本轮没有可合法替换基线并提高全H3召回的候选，建议保留旁路参考和max_missing2，
优先收集真实物料、固定光照与供料间距证据。只在可观察的细桥/噪点连接情形评估opening，
不把它推广为完整接触拆分，也不先堆两级RTL。避免为假想的固有宽度分割引入复杂通用算法。

完整特征数量/面积/bbox/量化/异常需求见 [G1特征建议](../wo-0011-g1-feature-requirements.md)：
H3同帧20对象、20区域；可评估64记录容量但未冻结；min_area16需按实际图像标定；
面积16位溢出、触边、frame_id/seg_count原子校验、质心量化及记录超限需验证。
正式PL目标仍为轻量特征输出，PS做关联计数，临时二值帧通路仅参考兜底。

G1尚缺独立Route A完整共同序列比较、真实录播人工真值、机械工作包线、候选PL资源和吞吐证据。
本轮61.29%不足以声称达到H3/G1准确率目标；不得因完成消融就批准Route或修改正式接口。
下一工作单建议：真实图像/独立GT与可分离工作包线、Route A/B共同事件窗口，
以及明确量化协议后的特征黄金向量。DMA具体设计和任何Route/接口变更仍按已列待审批事项处理。

## 7. 文件、验证和保留

新增：两份隔离实验 `python/refmodel/experiments/wo0011_{ablation,supplements}.py`、
两份测试 `python/refmodel/tests/test_wo0011_{ablation,supplements}.py`、本报告、12例CSV/证据JSON、
`docs/work-orders/wo-0011-segmentation-audit.md`、`docs/wo-0011-g1-feature-requirements.md`；
生成结果在新 `data/results/wo0011-*` 目录，不覆盖旧证据。

WO-0010的157个当前tracked文件与之前634个原文件分别核对哈希不变。
最终Python回归 **89 passed in 28.21s**：原68项、主消融7项、补充14项。
新策略测试先因缺实现失败，再实现通过；目录保护测试也实际经历缺实现失败。
完整H3及独立补充保留全部失败，并验证重复运行一致。`git diff --check`及新增文件空白检查通过。
复现命令（输出目录均须为新的目录，不覆盖本报告证据）：

```powershell
.\.venv\Scripts\python.exe -m pytest python/refmodel/tests python/synth/tests -q
.\.venv\Scripts\python.exe python/refmodel/experiments/wo0011_ablation.py --out data/results/wo0011-reproduce-new
.\.venv\Scripts\python.exe python/refmodel/experiments/wo0011_supplements.py --out data/results/wo0011-supplements-reproduce-new
```

WO-0011不修改生产RTL、四份受保护文档、WO-0010源码/测试、原H3与环境配置。
旧RTL回归证据是WO-0010实际执行的Morph84/Binary20/RGB2通过，
本轮无RTL修改，没有把读取旧报告称为重新执行RTL仿真。
没有新的PL资源、板端DMA、ARM实时性、上板计数或时序收敛结果。

## 8. 后续本地提交授权

以上记录WO-0011执行和验收时状态。2026-10-13用户在WO-0012明确要求先审查、单独本地提交WO-0011。
提交前复核89项测试通过（28.79s）、157个已有tracked文件及9个WO-0011交付哈希一致，审查无阻塞。
仅入库上述9个新增文件及4个紧凑证据：最终消融的summary/comparison/manager-verification与补充cases.csv。
不收录2MB补充原始轨迹、逐帧大JSON、缓存、图像或无关内容。具体commit以Git为准，不push。
