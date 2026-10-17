# WO-0016 板端验收记录

## 当前状态

**`STOPPED_PRECONDITION_FAIL` — 已完成只读 SSH 预检，但未部署 WO-0016、加载/复用 Overlay、启动 DMA 或采集计数帧。** 传送带计数验收仍为 `NOT RUN`。

### 只读预检记录（2026-10-18）

对 `xilinx@192.168.2.99` 使用严格主机密钥校验和密钥认证读取状态：

| 检查项 | 结果 |
|---|---|
| `/proc/sys/kernel/random/boot_id` | `f8b24aee-ed7e-430a-b3f3-d26056746bfd`，与获准启动 ID 一致 |
| `/proc/meminfo` `CmaFree` | `113940 KiB`，高于 `32768 KiB` 门槛 |
| `fuser /dev/video0` | 无占用进程输出 |
| `sudo -n v4l2-ctl -d /dev/video0 --get-ctrl=exposure_auto,exposure_time_absolute` | **失败：`unknown control 'exposure_auto'`**；未能验证手动曝光及 `exposure_time_absolute=50` |

曝光条件是部署与运行前置门禁。由于目标返回未知控制项，本次在只读预检阶段停止；没有创建 `/home/xilinx/wo0016/`、上传文件或执行 Stage A。不得把该错误改写成曝光通过，也未尝试替代控制名称。

本仓库现存 WO-0015 记录报告了 50 MHz 单帧/三帧传输与一帧相机输入结果，同时注明其中两个板端脚本的执行哈希与当前源码不同。因此该记录可作为历史输入基线，不能替代 WO-0016 的计数实测，也不能证明本轮脚本在板端运行。[WO-0015 报告](../../data/results/wo0015-final-20261017/acceptance-report.md)

## 待执行的单次受控实测

| 记录项 | 实测值 |
|---|---|
| 日期、操作者、板卡编号 | 2026-10-18；操作者/板卡编号 NOT RUN |
| 启动 ID、50 MHz BIT/HWH SHA-256、WO-0015 manifest SHA-256 | 启动 ID 已在只读 SSH 预检确认；BIT/HWH 与 manifest 本轮未复核 |
| 摄像头型号、分辨率、实际输入 FPS、曝光/增益 | NOT RUN；曝光查询报未知控制项 |
| 传送带速度、照明、视野、虚拟线位置 | NOT RUN |
| M6 螺母真值/预测/误差 | NOT RUN |
| 垫片真值/预测/误差 | NOT RUN |
| 混合物料真值/预测/误差 | NOT RUN |
| 样本数、漏计、重复计数、误计 | NOT RUN |
| 总计数准确率 | NOT RUN |
| 连续采集帧数、端到端处理 FPS、浏览器预览 FPS | NOT RUN |
| DMA/Checker/Packer 每帧通过数与错误数 | NOT RUN |
| 浏览器 URL、证据 JSON、测试/脚本 SHA-256 | NOT RUN |
| 板卡操作后健康检查 | NOT RUN |

每个计数测试批次应由人工记录独立真值，并保存原始计数结果。报告准确率时注明公式：`100 × max(0, 1 - |预测数 - 真值| / 真值)`；真值为零时仅预测为零记为 100%。分类计数还需分别列出每种物料的误差矩阵或逐批误差。FPS 必须说明计时范围、丢帧数和样本时长；CPU 合成 Mock FPS 不可填入实板 FPS。

## 实测结论

待解决曝光控制项预检失败，再完成 WO-0016 的单次审批门控板端运行和 M6 螺母/垫片人工真值对照后填写。当前没有部署、DMA 或计数实测结果。不得用 WO-0015 传输通过或本地 Mock 结果推导本节结论。
