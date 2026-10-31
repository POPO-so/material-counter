# 2026-10-31 现场计数优化与 v7 候选

用户授权根据 v6 真实快照制定计划、委派 Worker 并执行。计划沿用 [PLAN.md](../../../PLAN.md) 顶部本轮章节；分析基线为 [v6 现场分析](../standalone-20261031-crossing-snapshots/ANALYSIS.md)。本轮应用源码提交 `527258736b9b29bb37a118fd3023cc2436394a5d`。

## 改动及证据

1. `field-v1` 运行配置启用实际采集时间、连续帧速度预测、严格横向门限，以及同一横向区域内保持前后次序的一对一动态规划匹配，解决固定48像素关联门限和目标间串接。候选速度上限200像素/0.1秒、横向范围12像素、最大关联间隔0.5秒；长断档不凭空补计。旧 `MaskCounter` 和 `make_runtime_counter` 默认仍为legacy，脱机集成入口默认field-v1；相机恢复时沿用所选配置。
2. 局部粘连拆分启用，单体面积上界380、拆分上界1200、最小子面积80。复用既有局部拆分函数和连通/覆盖校验；PL、BIT/HWH、阈值128、输入640×400、计数线x=320/左→右/滞回4不变。
3. 默认轻量快照在板端只存Gray8/RLE/完整元数据，共享帧数据只编码和写盘一次，各事件硬链接保留独立导出兼容性；电脑端校验摘要和行列投影后生成二值/标注图。full模式仍可选。
4. SIGUSR1大报告先复制有界状态，再由有界后台线程执行JSON序列化/写盘。后台不遍历主循环仍在改动的deque；复制仍占主线程CPU、序列化仍可能竞争GIL，不声称完全无开销。
5. 诊断源码身份范围加入实际跟踪/拆分/运动模块所在的`wo0016`，避免只绑定外层运行器而遗漏计数实现。

关联工具和精简fixture由原生 `luna_worker`（角色配置 gpt-6-luna/high）实现；同一Worker随后完成轻量存储/离线渲染主体，Manager检查实际diff、修正可移植证据读取要求、补齐逐帧回归并负责跟踪设计、现场拆分配置和集成。最初Worker已返回A任务证据；后续工具状态变化后其句柄不再可查询，因此D的最终验证由Manager直接完成，不虚构Worker最终验收回复。Worker没有板端权限或二次委派。

另创建原生只读Luna审查，但返回 `You've hit your usage limit. Try again later.`，未获得独立审查结论；后续关闭句柄时提示不存在。没有改模型重试。Manager已检查实际代码与关键边界并执行下述验证，不能写成独立审查通过。

## 本地验证

[相关回归命令和结果](local-tests.json)：**481 passed，18.67秒**。包含`standalone`、`rle`、既有跟踪/时间/多排/拆分回归及新预测匹配用例。新增实现均有缺少功能时失败的测试；反向/轴向检查最初发现测试本身未传direction，修正测试参数后全部通过，不能将其描述为算法缺陷。

固定保存检测结果的[关联层重放](association-replay.json)：

| 连续窗口 | v6新增计数 | v7关联候选新增计数 |
| --- | ---: | ---: |
| 2310–2317 单体 | 0 | 1 |
| 2325–2331 单体 | 0 | 1 |
| 2380–2387 单体 | 0 | 1 |
| 2645–2649 同排双体 | 1 | 2 |
| 2485–2489 单个合并检测（仅关联层） | 1 | 1 |

时间模式初始化不推测初始轨迹的历史速度；缺少实际时间的旧missed轨迹从候选初始化中排除，并逐窗列明。legacy复现保留原初始全部轨迹；逐帧事件及累计与现场相同。时间候选重放不是完整视频或整体准确率。双体最终2计数由后述完整二值RLE链检查另行证明。

[固定9组现场窗口的本地存储对照](storage-comparison.json)：45次帧引用、13个不同源帧；full写入9,319,267字节、0.738秒，compact写入1,389,732字节、0.174秒。测试以同样输入同步调用writer隔离编码/写盘成本，不包含板端捕获或HDMI，不能换算整机FPS。采集状态仍可能报告队列丢弃，不保证每个事件都能落盘。

## 板端 CPU 检查及包身份

- [新包完整性](package-integrity.json)：`/opt/material-counter/releases/standalone-20261031-v7-count-5272587`，42个应用模块与提交Git blob逐字节一致，索引SHA-256 `ca9348788e98efe187752a29dcbe3ddc0970efa8ff85a1f8e43bbee9142ae58a`，61文件核验PASS。
- [板端纯CPU检查](board-cpu-check.json)：目标Python/NumPy/OpenCV和现有native库下，上表关联结果一致；真实二值帧经过RLE区域分析→拆分→跟踪，两组双体连续窗口均输出两个目标并计数2；22个现场单体裁剪均不误拆。未开启相机、访问PL控制寄存器或操作DMA/VDMA。
- 板端轻量采集CPU检查保存2个重叠组、51,916字节，硬链接同inode和所有文件摘要核验通过；生成5帧离线图；无采集/写盘错误、无丢弃。这是特定合成输入兼容性，不是现场物料准确率。

## 受控切换

[部署前基线](baseline.json)是同boot `f1462f32-61d2-44c7-9b42-d46d7d4d64c6` 的v6/PID5020，service active、enabled disabled。

[v6安全停止](old-safe-stop.json)及[原始最终报告](v6-final-session.json)：STOPPED，processed=36,241、count=43；RLE DMA `0x1002`（idle，无错误）、VDMA `0x11001`（halted，无错误），相机/缓冲释放、两锁可取得。诊断触发190组、保存113组、队列丢弃77组、写入/采集错误0，正常排空。这份总数没有人工投入真值，不能判断准确率。

[激活](activation.json)在核对同boot、同BIT及READY标志后切换current/env，只复用已加载overlay，未重新下载；旧包和env备份保留。新service已请求启动、PID9860，初始观察时startup文件尚未写入，未发送SIGUSR1。最终运行检查需见后续补记，不能只凭active推断计数成功。

### 当前实际运行（已核验）

[运行身份](running-identity.json)和[完整运行快照](session-snapshot.json)确认 PID9860、内部session `7de02772-5999-4a2d-97f6-c1b4bdc3c4c8`。报告文件名为 `session-30c27c09-d91e-40de-a121-a45684ad2351`；两个UUID是不同字段，已对应记录。

[77.31秒运行摘要](running-summary.json)：RUNNING，processed=876、submitted=712、count=16、invalid_frames=0、recoveries空；`counting_profile=field-v1`、`storage_mode=compact`。当时触发19组、保存19组、队列深度0，丢弃/写盘/采集错误均0，保存内容4,125,573字节。启动后61文件完整性再次PASS，实际加载32个应用源码摘要与新包逐项一致，包括`stream_counter.py`和`motion_association.py`。运行快照内report_exports显示本次提交前的0，不代表导出失败；已生成的完整快照本身证明该请求成功完成，不额外触发报告只为刷新计数器。

[真实相机文件核验](live-artifact-check.json)与[下载/渲染记录](downloaded-live-event.json)：从持续运行的新增组 `event-000020-e0-f1219` 下载5帧、10个Gray8/RLE文件，SHA-256及板端硬链接同inode均核对通过；独立复制到电脑后无需共享目录即可重建二值与标注图。实际生效预测关联、拆分参数已从组JSON核对。[实际JSON](live-event.json)、[1219帧标注图](live-frame-1219.png)保存供回溯；该帧累计21、有14个检测，5个计数事件分别关联不同track ID。图像存在碎片候选，不能把检测框数等同于真实螺母数，也不能以这个画面直接认定密集物料精度通过。

服务保持active供用户继续过料，自动启动仍disabled。现有 `sudo systemctl start material-counter-integrated.service` 在服务停止时启动v7；服务active时不会重新加载代码。没有改变服务自启动策略。

## 限制

本轮不冻结Route/接口，不放行G3/G4；没有新的人工已知数量准确率试验。12像素横向门限和380/1200面积门限来自当前摄像头/螺母样本，更换照明/视角/尺寸需要重新验证。稠密横向区域可能合并为同一个顺序约束区，组内过多候选会保守拒绝匹配；不同排交叉、强遮挡和三体粘连仍需更多真实样本。已计数ID继续跟踪以避免重复计数；超过0.5秒断档则重新建轨，不承诺跨长断档恢复。
