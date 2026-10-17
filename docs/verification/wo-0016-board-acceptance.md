# WO-0016 板端验收记录

## 当前状态

**只读预检通过；WO-0016 Stage A 尚未运行。** 目前没有部署、Overlay/DMA 操作或计数帧采集。

### 只读预检记录（2026-10-18）

对 `xilinx@192.168.2.99` 使用严格主机密钥校验和密钥认证读取状态：

| 检查项 | 结果 |
|---|---|
| `/proc/sys/kernel/random/boot_id` | `f8b24aee-ed7e-430a-b3f3-d26056746bfd`，与获准启动 ID 一致 |
| `/proc/meminfo` `CmaFree` | `113940 KiB`，高于 `32768 KiB` 门槛 |
| `fuser /dev/video0` | 无占用进程输出 |
| 曝光控件 | 驱动不识别 `exposure_auto`，实际控件名为 `auto_exposure`；`sudo -n v4l2-ctl -d /dev/video0 --get-ctrl=auto_exposure,exposure_time_absolute` 返回 `auto_exposure: 1 (Manual Mode)`、`exposure_time_absolute: 50` |

Stage 1 与部署门禁的只读复核：板端 manifest SHA-256 `2c24709bbe60711c095b5ccbdf4b3582e93e65d98a9538ad2b413136ad942e8b`，BIT SHA-256 `a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420`，HWH SHA-256 `bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6`；同一 boot 的 `/var/lib/wo0015-s2mm/overlay-only-pass.json` 为 `OVERLAY_LOAD_PASS` 且三项哈希和 boot ID 均匹配。WO-0015 `overlay-only-consumed.json` 已存在，`terminal-failure.json` 不存在；WO-0016 目标目录和一次性 Guard 在部署前均不存在。

本仓库现存 WO-0015 记录报告了 50 MHz 单帧/三帧传输与一帧相机输入结果，同时注明其中两个板端脚本的执行哈希与当前源码不同。因此该记录可作为历史输入基线，不能替代 WO-0016 的计数实测，也不能证明本轮脚本在板端运行。[WO-0015 报告](../../data/results/wo0015-final-20261017/acceptance-report.md)

## 待执行的单次受控实测

| 记录项 | 实测值 |
|---|---|
| 日期、操作者、板卡编号 | 2026-10-18；操作者/板卡编号 NOT RUN |
| 启动 ID、50 MHz BIT/HWH SHA-256、WO-0015 manifest SHA-256 | 已在只读 SSH 预检确认，详见上表 |
| 摄像头型号、分辨率、实际输入 FPS、曝光/增益 | 只读曝光值已确认；采集分辨率、输入 FPS、增益待 Stage A |
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

待完成 WO-0016 的单次审批门控板端运行和 M6 螺母/垫片人工真值对照后填写。不得用 WO-0015 传输通过或本地 Mock 结果推导本节结论。
