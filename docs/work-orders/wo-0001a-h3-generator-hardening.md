# WO-0001a · H3 synthetic generator hardening

| 项 | 内容 |
|---|---|
| **状态** | `OPEN` —— 待 Codex 执行 |
| **发起** | Claude（Planning / Review Agent） |
| **执行** | Codex（Implementation Agent） |
| **前置** | WO-0001 `FUNCTIONALLY ACCEPTED`（commit `d9a281e`，独立验收通过） |
| **性质** | **Metadata + regression-test hardening only.** 不重新开放 WO-0001，不扩大 scope。 |

---

## Objective

在不改变数据集设计的前提下，做两件最小加固：

1. 恢复 `dropped_frame_ids` 元数据，使 config / PNG / ground truth 三者可交叉验证
2. 把本次人工验收的 3 项 + R2 断言强度，固化为回归测试

**Patch 仅此两部分。不允许顺手优化其他代码。**

---

## Part 1 · 恢复 `dropped_frame_ids`

### 要求在

每个 sequence 的 `config.json` 必须显式记录 `dropped_frame_ids`：

| 情形 | 取值 |
|---|---|
| 无故意丢帧 | `[]` |
| 有故意丢帧 | 按 frame id 显式列出，**升序** |
| 与**实际缺失 PNG** | **完全一致** |
| 与 `ground_truth.json` 中对应帧 | `visible=false` 的 fault-injection 语义**一致** |

### 三方可交叉验证契约（写进 docstring 或 config 注释）

```
config.json       = fault injection specification  （哪些帧被故意拿掉）
frames/*.png      = observed dataset               （实际存在的观测）
ground_truth.json = reference truth                （真值，含被丢帧的 visible=false 记录）
```

### 必须新增的自动测试

对**每个 sequence**：

```
set(config["dropped_frame_ids"]) == expected_frame_ids - actual_png_frame_ids
```

其中 `expected_frame_ids = range(num_frames)`，`actual_png_frame_ids` 由**扫描 frames 目录实际存在的 PNG 文件名**得出（不得由 generator 内部状态推断）。

并额外断言：

- 每个 `dropped_frame_ids` 中的帧 id，在 `ground_truth.json` 对应帧里**全部 object 均为 `visible=false`**
- 不在 `dropped_frame_ids` 中的帧，在 ground truth 中**至少有一个 object `visible=true`**
- 非丢帧 sequence 的 `dropped_frame_ids == []`

---

## Part 2 · 把人工验收的 3 项变成 regression tests

### A. Pillow scope

确认 synthetic generator 中 **Pillow 仅用于 PNG I/O**，不承担图像算法。

- **优先使用静态源码 / AST 级检查**
- **不得为了测试这一点引入新的第三方依赖**（用 Python 标准库 `ast`）
- 断言内容：`PIL` 的 import 只出现在允许的模块；`Image.<attr>` 的调用只允许 I/O 类（`fromarray` / `open` / `save`）；禁止出现 `filter` / `resize` / `convert` / `point` / `ImageOps` / `ImageFilter` 等算法类调用

### B. Dependency declaration

断言 `synth/requirements.txt` 的第三方依赖集合**严格等于**：

```
numpy
Pillow
pytest
```

（顺序固定；不得有多余项；不得有缺失项）

### C. Ground-truth ↔ rendered pixels

自动抽取**具有代表性的 sequence / frame**，对实际 PNG 与 ground truth 做**独立一致性验证**。

至少检查：

- `320×240`
- Gray8 / `uint8`
- **visible bbox 对应实际前景**（bbox 内像素显著亮于背景）
- **bbox 外存在正确背景**

**★ 反 self-verification 约束**：测试**不得**调用 generator 内部的渲染函数重新计算期望值。必须直接从 PNG 像素与 ground truth 的数值关系独立判定。

**★ 必须处理的两个边界**（否则测试会假失败）：

1. **截断 bbox**：物体可部分移出画布，真值 bbox 可能越界。断言前须将其**裁剪到画布内**。
2. **噪声**：`noise_sigma > 0` 的序列会使单像素判定不可靠。应使用**鲁棒统计判据**（如 bbox 内中位数/多数像素高于背景），或选取噪声较低的代表性序列并说明理由。

判据需对 `brightness_offset` 正确取模（背景期望值 = `background_gray + brightness_offset`）。

---

## Part 3 · 顺手增强 R2 regression assertion

当前真实数据已达到：**24/24 有效帧均存在 x projection overlap，且所有相关 pair 均 2-D non-overlap**。

将现有测试从 `saw_valid_projection_overlap` 的「**至少一帧成立**」，增强为**对该专项序列的全部有效帧进行断言**：

- 每一帧至少存在一对 x 区间相交的对象
- 每一对 x 区间相交的对象，其二维 bbox **均不相交**
- 断言覆盖全部有效帧，而非 `>= 1` 帧

**这不改变 dataset**，只是让自动测试强度与已独立验证的数据性质一致。

---

## 严格 Out of Scope

**不允许**：

- ❌ 改变现有 21 条 dataset 设计
- ❌ 改变 420 physical objects
- ❌ 改变 320×240
- ❌ 修改 Route
- ❌ 修改算法
- ❌ 修改 MASTER_PLAN
- ❌ 开始 H2 / H3 算法
- ❌ 开始 C2 / C3
- ❌ 重构 generator
- ❌ 新增第三方依赖
- ❌ 顺手优化其他代码

---

## Acceptance

- [ ] 原有测试继续**全绿**
- [ ] 新增测试**全绿**
- [ ] `dropped_frame_ids` 与实际缺失 PNG **一一对应**
- [ ] 非 drop sequences 的 `dropped_frame_ids == []`
- [ ] R2 **全有效帧** assertion 自动通过
- [ ] dataset 仍为 **21 sequences / 420 physical objects / 320×240**
- [ ] Pillow scope 测试**不引入新依赖**（仅标准库 `ast`）
- [ ] Ground-truth ↔ pixels 测试**不调用 generator 内部渲染函数**
- [ ] protected docs diff = **0**（`MASTER_PLAN.md` / `docs/00_scope.md` / `docs/02_interfaces.md` / `docs/07_routes.md`）
- [ ] Git workspace **clean**

---

## 执行命令

```bash
cd C:/fpga/material-counter/python
.venv/Scripts/python.exe -m synth.generate --spec synth/specs/h3_matrix.json --out ../data/synthetic/h3_full
.venv/Scripts/python.exe -m pytest synth/tests/ -v
cd .. && git diff --stat -- MASTER_PLAN.md docs/00_scope.md docs/02_interfaces.md docs/07_routes.md
git status --short
```

---

## 交付后

由 **Claude 独立复跑验收**，**不直接采信汇报**。

通过后：**WO-0001 + WO-0001a = FINAL ACCEPTED**，synthetic generator 进入**冻结状态**。除非后续实验发现真实 blocker，否则不再继续优化。
