# 2026-10-31 过线诊断采集与 v6 部署

用户现场报告漏记、两个螺母落在同一个框，以及“有目标框但不计数”。本轮增加可回溯证据采集，未改变检测、关联、拆分或计数规则；现场根因和准确率仍未确认。

**后续现场分析补记：** 用户完成分批过料后，已取回 59 组、164 个不同处理帧，确认有框漏计的位移超限换 ID、同排目标 ID 串接，以及双螺母形成单连通检测且拆分未启用三类具体实例。已有拆分函数可离线把两个选定合框样本分成两个连通且守恒的子区域；仅改变关联距离的离线对照修复了三个单体窗口和一个同排窗口，但不能解决合框。此为限定实例证据，不是总体准确率；用户不记得各批投入数。没有部署算法修改，详细图像、数值和限制见[现场分析](ANALYSIS.md)。下文“尚无现场样本”是部署初始快照的历史状态。

## 实现与本地验证

应用源码提交 `7facb3b16e318a5fc14630711a1d881a32488a49`。脱机集成入口默认开启有界异步快照，触发包含框接触线、质心越线、实际/抑制计数事件、在线后新建轨迹和线上超限拒绝区域。同帧多个目标合并为一组，每组按目标列明触发，保存前两帧、当帧和后两帧的原始灰度图、二值图、标注图、RLE/投影与 JSON。JSON 记录有效参数、检测和跟踪状态、关联位移、命令、单调时间及源码/制品身份。

默认上限为每会话 1,000 组、512 MiB，保留 256 MiB 剩余磁盘；后台队列 8 组，队列满记录丢弃，容量不足或写盘错误停止采集，计数继续。详见[使用与排查说明](../../../scripts/standalone/CROSSING_SNAPSHOTS.md)。完全没有检测框的目标无法独立触发，此采集不等于连续录像。

本地命令：

```powershell
.\.venv\Scripts\python.exe -m pytest scripts/standalone/test_crossing_snapshots.py scripts/standalone/test_integrated_run.py scripts/standalone/test_count_controls.py scripts/standalone/test_boot_policy.py scripts/standalone/test_delivery.py -q
```

结果：**64 passed**。10 个新增用例覆盖无计数事件的可见框、跨线跳帧、暂停、代次切换、尺寸拒绝区域、线上游关联丢失后在线后换 ID、三类存储限额、慢盘队列和编码失败。首次失败验证确认模块缺失；新增的“换 ID 后在线后出现”用例曾独立失败，再补齐触发后通过。默认 `python` 指向 LibreOffice 且缺少 pytest，正式验证使用项目 `.venv`；未把该环境错误当作软件测试结果。

代码及合成序列确认：关联步长超限可使可见目标获得新 ID，首次出现在线后的轨迹会按防重复逻辑跳过计数。当前脱机默认纵向步长 48、横向步长 40、连续确认 2 次、滞回 4，`guarded_merge=false`。这些是待比对现场样本的线索，不是本轮已确认的现场根因。

## 板端包及受控切换

- [部署前基线](baseline.json)：旧 v5 service active、PID 2747，自动启用 disabled。
- [新包完整性与源码](package-integrity.json)：release `/opt/material-counter/releases/standalone-20261031-v6-crossing-7facb3b`，38 模块对应上述提交 Git blob，索引 SHA-256 `e04569785eab2a5f80717ee4e729ed95a0138089b374fcc380acf198b9483515`，57 文件完整性 PASS。
- [板端纯 CPU 合成检查](board-cpu-selfcheck.json)：PYNQ 自带 Python 3.10.4/OpenCV 4.5.4/NumPy 1.21.5，使用既有预编译区域分析库；不打开相机或 DMA。5 帧计数 1，保存完整 1 组、69,846 字节，20 个图像/NPZ 摘要核对通过，采集/写入错误均 0。样本独立标为 `SYNTHETIC_CPU_ONLY`，不能当成现场证据。
- [旧会话正常停止](old-safe-stop.json)、[旧会话最终原始报告](old-session-final.json)：STOPPED，processed=17,076、total_count=64；RLE DMA 两个状态均 `0x1002`（idle，无错误），VDMA `0x11001`（halted，无错误），相机及缓冲释放，两个 owner 锁可取得。新会话累计重新从 0 开始。
- [激活记录](activation.json)：同一 boot `f1462f32-61d2-44c7-9b42-d46d7d4d64c6` 的 READY marker 复用已加载、相同摘要的 BIT，未重新加载 overlay；环境文件和 current 切到新包后启动 service，自动启用继续 disabled。旧包与旧 env 备份均保留。

最初组装尝试发现旧 release 没有打包离线复制 helper，随后从本地上传该 helper；复制包继承的旧索引也被构建器拒绝覆盖。只在新 release 目录中核对旧索引摘要后移除该副本，再生成新索引。这些准备过程未停止或修改旧运行包。首次启动观察发生在 startup 文件尚未生成时，没有发送信号；随后核对文件存在后才用 SIGUSR1 取得运行快照。

## 当前运行证据及限制

[运行身份](running-identity.json)：PID 5020、session `9615976e-c741-4ac6-9b95-48e8370a8f6b`，报告基名 `session-c1e82607-8fc1-4210-8649-57aa35c274af`。报告文件的随机 UUID 与内部 session ID 是两个不同字段，二者已在记录中对应。

[首个运行快照](session-snapshot-0.json)及[摘要](running-summary-0.json)：RUNNING，16.88 秒处理 170 帧、提交 25 帧、累计 0、无效帧 0、recoveries 空；快照 writer alive/accepting，观察 170 帧、触发 0、保存 0、丢弃/采集错误/写盘错误均 0。这一观察窗口没有现场过线样本，不据此宣称现场保存链路或物料准确率已验收；板端合成落盘仅证明兼容性和限定采集行为。初始显示含进程启动预热，不能用于稳定 FPS 比较。

现场样本目录：

```text
/var/lib/material-counter/f1462f32-61d2-44c7-9b42-d46d7d4d64c6/session-c1e82607-8fc1-4210-8649-57aa35c274af.crossings/
```

服务保持运行供用户过料。板卡时钟未校准，journal 的五月时间不能作为真实日期；使用 boot/session、帧号和单调时间对齐。未执行冷启动、真实物料真值或 G3/G4 完整门禁；采集尚未接入桌面 GUI 的独立入口。

`sudo systemctl start material-counter-integrated.service` 只启动 `package.env` 指定的已部署包；服务已 active 时不会重新加载代码，也不自动拉取电脑/Git 的最新版本。完成本轮包路径和摘要切换后，该命令才会启动本轮 v6。
