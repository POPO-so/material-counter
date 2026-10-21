"""Build reviewable offline diagnosis summary without changing raw evidence."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil

out = Path(__file__).resolve().parent
repo = out.parents[2]
boot = "6f13f0f0-4433-445f-a023-fa403685aeee"
history = out/"board-evidence/var/lib/wo0016/history"/("boot-"+boot)
replay = json.loads((out/"replay.json").read_text())
patch = []
for name in ("run_stream_counter.py","stream_counter.py","mjpeg_preview.py"):
    old = history/"attempt-3/scripts/wo0016"/name
    new = repo/"scripts/wo0016"/name
    patch.extend(difflib.unified_diff(old.read_text().splitlines(True),new.read_text().splitlines(True),
                                     fromfile="attempt3/"+name,tofile="attempt4/"+name))
    (out/"tested-source").mkdir(exist_ok=True)
    shutil.copy2(new,out/"tested-source"/name)
(out/"fixes-since-attempt3.diff").write_text("".join(patch),encoding="utf-8")
rows=[]
for event in replay["events"]:
    observations=[row for row in replay["associations"] if row["track_id"]==event["track_id"]]
    rows.append(f'| {event["track_id"]} | {observations[0]["frame_id"]}–{observations[-1]["frame_id"]} | {event["frame_id"]} | {event["centroid_x"]:.3f}, {event["centroid_y"]:.3f} | {max(row["area"] for row in observations)} |')
text="""# Stage B-2 Attempt #3 离线诊断与 Attempt #4 预备

状态：165 帧真实 CCL 回放完成，`replayed_total_count=5`；WO-0016 单元测试 **92 passed**。Attempt #4 已准备，**NOT RUN / 等待用户明确启动指令**。本轮没有打开相机、执行 Overlay 或 DMA，也没有恢复 HUD。

## 原始证据与解释边界

主结果 JSON 只有汇总；逐帧数据来自同轮 `frame-results.json` 侧录，SHA-256 为 `bcacadae888ec5166bea40b00bcd39db6857a0dc61e1143cdee1bfa0821b77dd`。已与板端历史归档逐字节复核。主结果 SHA-256 为 `7e4e24afb36fb78716e1bed12e8273c807fa33ad42feef1b8c066a2c773f3471`。

完整帧 24–30、114–135 的前景数和所有**已保存合格连通域**见 [逐帧表](raw-ccl-frames-24-30-and-114-135.md)，以及 raw-ccl JSON/CSV。bbox 使用 320×240 处理图坐标，端点均包含；不是原始 640×400 坐标。原始 `min_area=12`、无 `max_area`，低于 12 的组件没有持久化，无法补列。未保留视频/二值掩膜，不能反推像素连通形状或五颗物料的一一身份。

- 帧 24–30：前景峰值 4292，合格组件观测 7 次，单帧最多 2。主块面积 686→1373→2511→3878→4283→2932；宽度增长、上下触及 ROI 截断边界，质心 X 从 50.62 增至 112.29 后几乎停住。它并没有以质心穿过 X=160。可疑大遮挡/团块，但不能确定是手、五颗粘连或接缝。
- 帧 114–135：前景峰值 812，组件观测 77 次，单帧最多 7，组件面积 15–463，存在持续向右的序列；更符合小物料经过的候选段。回放的五个事件全部在此段。**这支持候选判断，不能证明该段恰好对应五颗物理螺母。**
- 当前 Python CCL 是 8 连通，不执行闭运算；不能把大块归因为这一层的闭运算。上游掩膜是否已粘连，需要原始掩膜证据。完整环形内孔本身不会把 8 连通环拆成多个域；反光、阴影或阈值造成断开的弧段可能分裂，但本轮不能确认这种原因。
- `最多7组件` 是同帧合格二值块数，不是实体数。新回放中 T5 在帧125以 area=33 触发，T7 也有面积和关联跳变，存在碎片轨迹、串轨或重复实体的未消除风险。不能仅因结果等于5便宣称每颗稳定对应1轨迹。

## 修复与面积门限评估

共享 `MaskCounter` 增加 count_axis/line_x；本次许可固定 `count_axis=x, line_x=160, direction=+1`。ROI 仍为 Y=96…144 的输送带条带，`line_y=120` 此时仅确定 ROI 中心。

关联条件：每间隔 dt 帧，正向 `-2 <= dx <= 60*dt`，`abs(dy)<=10*dt`，面积比 0.25…4，允许缺失1帧；采用原有距离贪心一对一匹配。60 是轴向步长门限，不是欧氏距离半径。确认2次、迟滞±2、同轨仅触发一次，计数线后新生轨迹不补计，保留反向抖动容差。预览红线也改为垂直线。

面积敏感性（同一套原始 CCL）：min_area 12/24/32→5；48→4；64→3；80→4；120→2。增加 max_area=600 在这些设置下均不改变计数。单纯提高面积下限可能破坏有效轨迹；没有实体标注依据选择新阈值。因此 Attempt #4 保留 `min_area=12, max_area=null`，只增加可配置上限。未凭目标5调整形态学或把相邻组件强行合并，避免把真实相邻螺母合并。

这次已修复可证实的轴向与关联门限问题；分裂/粘连的通用解决与每颗一轨保证仍待带图像证据验证。单元测试覆盖跨线后分裂不重计，但不能代表跨线前任意分裂或真实粘连均已解决。

## 真实数据回放

直接将保存的 Detection 送入与在线相同的 `_associate_and_update`，没有重建假掩膜、重新分割或补造组件。`update_detections` 与在线 CCL 共用面积过滤条件。完整输出见 [关联表](associations.csv)、[跨线事件](crossing-events.csv)、[165帧总表](frame-totals.csv)、[完整回放与敏感性](replay.json)。

| 轨迹ID（非物理螺母编号） | 观测帧范围 | 跨线帧 | 跨线质心 (X,Y) | 本轨面积峰值 |
|---|---|---|---|---|
"""+"\n".join(rows)+"""

消融复核见 ablation.json：原 Y轴/24px/20px → 0；仅改 X轴、保留24px正向门限 → 0；X轴/60px/10px → 5。两项修正均有直接回放依据。`T7` 从帧125的 (182.310,118.887) 跳到帧127的 (181.226,108.798)，可能串到后续目标；数值5可能包含碎片重复与漏计相抵，不能当作准确率100%。\n\n数值回放门禁 PASS；实体一一对应门禁 UNVERIFIED；历史 Attempt #3 原始 total_count=0 与 FAIL 状态保持不变，离线结果不会覆盖原记录。

## 测试、部署与待命

`C:/fpga/material-counter/.venv/Scripts/python.exe -m pytest scripts/wo0016 -q` → **92 passed in 5.64s**。新增 +X、32px/帧五目标测试先出现预期失败后实现；覆盖方向、纵向跳变、面积上限、在线/Detection回放事件一致、跨线后分裂不重计、垂直预览和许可参数/源码失配拒绝。`verify_evidence.py` 重新计算真实回放并验证两个历史归档全部文件、当前 Guard、源码与许可绑定，通过；`git diff --check` 通过。

本轮改动：`scripts/wo0016/stream_counter.py`、`run_stream_counter.py`、`mjpeg_preview.py`、相应两个测试文件；新增 `replay_recorded_components.py` 与本诊断目录。已有其他未提交文件保留，主 checkout 未修改，未提交或推送。相对 Attempt #3 实际部署代码的差异见 [修复专用 diff](fixes-since-attempt3.diff)，新源码快照在 tested-source/。

| 门禁 | 复核结果 |
|---|---|
| Boot ID | 6f13f0f0-4433-445f-a023-fa403685aeee，未变 |
| FPGA Manager | operating / flags=0；本轮未加载镜像 |
| Attempt #2 | 既有未完成有效投料归档逐文件哈希全部通过，原 marker 保留 |
| Attempt #3 | 原始结果、完整 attempt 记录、源码、Package、Stage1、Guard 快照归档，原 marker 保留 |
| READY Guard | SHA f15464025abeabc2e52681aaa000caf25da217cdf5505781c3d7fcba45cd5b9b，不改动，consumed=false |
| Attempt #4 | root:0600 许可已签发，绑定本 Boot、Guard、runner/counter SHA、计数配置；consumed.json 不存在 |
| CMA | 只读首次78132 KiB；准备时78172 KiB；打包后77844 KiB，均高于32768；未执行 drop_caches |
| 摄像头/端口 | video0/video1/devmem 无持有者；8080/8081 未监听；未恢复 HUD |
| 硬件采样 | NOT RUN，停在用户指令门禁 |

Attempt #4 准备命令存于 preparation-payload.json 和板端 preparation.json，尚未执行。保留原 PYNQ3.1.1 进程内 MMIO / Overlay(download=False)、THRESHOLD128、每帧双 DMASR=0x00001002、CMA 安全释放逻辑。下一轮仍使用人直接点击的8081控制页：先等相机就绪，再由用户点击启动，15秒预备+3秒稳姿后绿色信号，按1颗/秒分开投料；以页内实时信号为准，避免依赖对话工具延迟。当前尚未启动该页。

## SHA-256 证据链

- Attempt2 archive-manifest：`6e7bd38d6fc9dc57a0d88500a77d754ef593e7c667e7fb735ad09aaef263a068`
- Attempt3 archive-manifest：`a6d134f50f9ee4d9c825f8c6d35413b3ea3c5c923c2af0aa110109e3818f4206`，内含上一个归档哈希。
- Attempt4 permit：`7272a5ebe90d92d1b9ddb7ff80abe8108de1e1715ed8af2913b684f58910a4b3`
- 新 Package：`3d4a1920f01e921511db3ab7ae4d9ec203a4d47abc2e0582bcac57f1a8f07d95`
- 板端归档/许可取证包：`ef513bb9af8458a0960bd1c24f33434d9597b97b63c9fff62b3da795b81b223e`，本地 SHA 与板端一致。

归档路径：`/var/lib/wo0016/history/boot-6f13f0f0-4433-445f-a023-fa403685aeee/attempt-{2,3}/`；新许可在 `/var/lib/wo0016/attempts/6f13f0f0-4433-445f-a023-fa403685aeee/attempt-4/`。全部文件级哈希见归档内 archive-manifest.json 及本目录 SHA256_MANIFEST.json。
"""
(out/"REPORT.md").write_text(text,encoding="utf-8")
