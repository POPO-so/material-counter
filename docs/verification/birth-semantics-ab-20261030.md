# 出生语义 A/B：baseline 与 band_gate 候选（2026-10-30）

## 1. 背景与开关

LJ-01 基线与 `main` 对「轨迹出生点」的处理是互斥语义：

- **baseline（默认）**：出生点 `side >= -hysteresis` 的轨迹在出生时即被标记 `counted`，
  永不产生计数事件；只有从 arming 区（`side < -hysteresis`）出生的轨迹才可能计数。
- **band_gate（候选）**：带内出生（`-h <= side <= h`）的轨迹可以 armed，但必须在运动
  方向上确认离开带（`band_exit_observed`）后才允许计数；过线区出生
  （`side > hysteresis`）同样在出生时标记 `counted`。

两者以构造参数并存：`MaskCounter(birth_semantics="baseline" | "band_gate")`，默认
`baseline`。`RleCounter` 通过 `**settings` 透传，默认同样是 `baseline`。候选特性
**未默认开启**。

## 2. 单变量控制

两个实例除 `birth_semantics` 外参数完全一致（见
`data/results/birth-semantics-ab-20261030-01/summary.json` 的 `settings`）：
`count_axis=x, line_x=160, hysteresis=4, confirm_hits=2, max_missing=1, max_step=40,
max_x_step=40`。差异只可能来自出生判定本身。

## 3. 合成场景矩阵

脚本：`scripts/wo0016/birth_semantics_ab.py`；结果：
`data/results/birth-semantics-ab-20261030-01/summary.json`。每个场景是一串 10×10
检测框（`update_detections` 回放），真值由构造决定。

| 场景 | 真值 | baseline | band_gate | 说明 |
|---|---|---|---|---|
| late_birth_in_band | 1 | 0（漏） | 1 | 真实件首次出现在带内 |
| birth_inside_count_zone | 0 | 0 | 0 | 伪影直接出现在过线区 |
| duplicate_reentry | 1 | 1 | 1 | 真实件越线后又在过线区出现 |
| in_band_flicker | 0 | 0 | 0 | 伪影在带内来回振荡，从未离带 |
| band_birth_then_forward | 不可判定 | 0 | 1 | 带内出生→回退→前向越线；规则无法区分「晚期真实件」与「闪烁后恢复」 |
| two_late_items | 2 | 0（漏 2） | 2 | 两件真实件都在带内首次出现 |
| fragment_after_crossing_in_band | 1 | 1 | 2（多 1） | 真实件越线后，带内出现碎片并再次越线 |

绝对值误差（不含不可判定场景）：baseline 3，band_gate 1。

## 4. 解读

- band_gate 的收益来自「带内出生仍可计数」：在 `late_birth_in_band` 与
  `two_late_items` 中修复了 3 个漏计。基线在这些场景必漏，因为出生即被判 `counted`。
- band_gate 的代价来自同一机制：`fragment_after_crossing_in_band` 中，真实件已经
  计过一次，随后带内出现的碎片又被允许越线计数，产生 1 个确定重复计数；基线因为
  带内出生一律不计而对该形态免疫。
- `band_birth_then_forward` 说明该语义本身有不可判定边界：跟踪状态无法区分「真实件
  进入视野较晚」与「伪影闪烁后恢复」，band_gate 会把它计为 1，基线计 0。
- 结论：合成数据只能证明语义差异，不能证明实料收益。**建议保持默认关闭**。

## 5. 启用条件（若将来要在产线开启）

按 `real-material-calibration-transfer-20261030.md` 第 3 节采集 640×400 带人工真值
的连续过料序列，用同一单变量方式做现场 A/B，同时统计漏计与重复计数。只有漏计减少量
显著大于新增重复计数（且重复计数为 0）时才建议把默认值改为 `band_gate`。

## 6. 验证与未验证项

已执行：

- `scripts/wo0016/test_birth_semantics_ab.py`：把上表矩阵钉死为回归测试。
- `scripts/wo0016/test_count_observability.py`：baseline 与 band_gate 的出生记录
  （`born_past_line`、`born_counted_without_event`、`born_in_hysteresis_band`）
  和默认值校验。

未验证：

- 没有任何实料或板端数据；全部为合成检测序列。
- 未在 RLE 通路做端到端 A/B（RLE 与 Gray8 共用同一 tracker，出生判定一致，但未实测）。
- 未评估 CPU/内存影响：差异只在出生判定，无新增分配，但未测量。