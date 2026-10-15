# WO-0012 · 架构决策准备与有界特征设计

Goal：形成可审批的最小PL后端、接口与板级验证方案，不把准备工作当作G1已通过。
Architecture：隔离PC参考验证行程/固定容量设计、整数质心、打包合同；正式PL/PS链不改。
Tech stack：现有Python环境，OpenCV参考，仅新增模型/测试/草案和紧凑证据。
采用writing-plans流程；保留项目work-orders位置，不新增其他计划目录。

## 执行顺序与边界

1. WO-0011独立只读审查无阻塞，fresh89测试通过；明确暂存13文件后本地commit
   `a67fe87ff89c6b992652192496ae8a19d6dc38d4`，未push。缓存、2MB补充原轨迹和无关文件未收录。
2. 已读取四份受保护文档、ADR0001/0002、WO9/10/11证据：G1为R1+R2双门，Route-specific后端
   需先有正式决策/ADR；接口变更要求PR+三人确认+ADR。当前尚缺证据，不宣称冻结。
3. 只评估候选：行内前景run与上一行run的8邻接关系，固定run/活动统计/已完成记录容量；
   允许同一ID分叉及再汇合（孔），不同ID相遇整帧拒收，不做通用等价标号合并。
   验证H3精确面积/bbox/sum_x/sum_y，以及孔、U形多根汇合、噪点与容量溢出的边界。
4. 按H3实测run/区域/活动对象高水位，分开输出物理工作包线、缓存容量和待批准安全余量。
   数学位宽/BRAM/调度预算全部标Derived或Hypothesis，不冒称综合/FPS实测。
5. Manager增加隔离整数质心/16字节特征与帧尾打包参考，明确padding/实际长度/EOF与原子校验。
   不改WO10/11参考代码；同协议比较floor、nearest-half-up对全部H3关联/越线影响。
6. 交付 `docs/wo-0012-g1-decision-package.md`、`docs/adr/adr-0003-bounded-feature-proposal.md`、
   `docs/wo-0012-feature-transport-draft.md`、`docs/work-orders/wo-0013-bounded-feature-hardware.md`，
   均标Proposed/待审批；对正式Route、接口、工作包线及DMA选择列出决策点。
7. 全部新旧Python回归、哈希保护和Git检查。WO-0012不提交，不改生产RTL、四份保护文档或环境。

## 推荐的待评估设计（非已批准架构）

输入320×240 Binary8，00/FF，SOF/EOL保留坐标；输入可反压。按行收集run、暂停匹配统计，EOF关闭全部对象。
固定标签槽无union-find；一个run若连接多个不同活动ID，拒收该帧，提供诊断与可选PS帧兜底。
无法分开无缝同色接触物料；不在POC中承诺所有任意孔/多分支形状均可硬件化。
开发生产RTL的授权不由本工作单自动产生；下一工作单只能在G1/ADR/接口批准后执行。
