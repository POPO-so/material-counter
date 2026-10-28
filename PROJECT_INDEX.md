# Project Index

Agent navigation map for `material-counter`. Use linked documents as the source of project facts; this file only points to them.

## Start here

`PROJECT_INDEX.md` → [README current status](README.md#当前状态) → task's Work Order / interface / ADR → actual code → relevant `MASTER_PLAN.md` sections only when needed. Do not read the full Master Plan by default.

## Sources of truth

| Need | Read |
|---|---|
| Project purpose, current Implemented / Planned / Exploratory summary | [README.md](README.md) |
| Scope and exclusions | [docs/00_scope.md](docs/00_scope.md) |
| Interface definitions and freeze/sign-off status | [docs/02_interfaces.md](docs/02_interfaces.md) |
| Candidate routes and route-freeze record | [docs/07_routes.md](docs/07_routes.md) |
| Why a decision was made | [docs/adr/](docs/adr/) (check each ADR's status; Proposed is not accepted) |
| Task requirements and acceptance evidence | [docs/work-orders/](docs/work-orders/) |
| Risk register / hardware feasibility evidence | [docs/05_risks.md](docs/05_risks.md), [hw/pin_budget.md](hw/pin_budget.md) |
| Full plan or rationale not covered above | [MASTER_PLAN.md](MASTER_PLAN.md), only the relevant section |

## Current code boundary

- **Implemented with bounded evidence:** `python/synth/`, frontend/morphology RTL in `fpga/rtl/`, testbenches in `fpga/sim/`, PS offline reference in `python/refmodel/ps_reference/`, and isolated WO-0013/0014 DMA experiments. These are distinct from a complete board counting system.
- **Planned / Proposed:** production PL feature extraction and ABI, sustained board-side tracking/counting/display, real-material accuracy acceptance, and encoder speed acceptance. ROI is documented in its handoff; it is not evidence of a complete system.
- **Current progress and source versions:** the [2026-10-15 delivery audit](docs/verification/submission-status-20261015.md) and [2026-10-18 status report](docs/verification/submission-status-20261018.md) are historical snapshots. Current WO-0016 progress is in the README and [board acceptance record](docs/verification/wo-0016-board-acceptance.md). Offline integrity entry: `python scripts/delivery/verify_delivery.py`; relocatable replay: `scripts/delivery/REPLAY.md`.
- **Current WO-0016 status (2026-10-28):** The 2026-10-21 Attempt-5 Recovery-2 result of 4/5 is a pre-calibration historical sample, not the current system verdict. The 2026-10-25 Attempt-03 B-2 run completed 220/220 frames and counted its specific five-item touching-M6 sample 5/5 at 12.248 FPS; two 50-item exact-count runs are operator-reported, while later misses have no per-run denominator. Those board samples used the older RGB BIT/HWH. The approved current interface is Gray8 (MM2S 76,832 B / S2MM 76,800 B); its 2026-10-27 BIT/HWH evidence is offline and does not establish board behavior. See [current README status](README.md), [board acceptance](docs/verification/wo-0016-board-acceptance.md), and [lighting-calibration record](docs/verification/lighting-calibration-20261025.md).
- **Capture and memory policy (2026-10-28):** Explicit bounded_stream capture is approved for guarded board evidence and uses four fixed ordinary-RAM Gray8/Binary8 image pairs; auto remains the default. Physical calibration additionally retains only its pinned 18 full-resolution source frames (4,608,000 B) in fixed ordinary RAM, and that mode/ring/source-frame capacity is bound into preflight and one-shot permit identity. The M8 preflight model accounts for its four-slot pool and retains the existing system reserve, RSS proxy, per-frame metadata allowance, and CMA thresholds. This policy authorization is not board memory acceptance; new runtime hashes, Boot-bound permits, and measured board evidence are still required for a future run.
- **Workspace evidence preservation (2026-10-22):** [Asset archive and zero-loss verification](docs/verification/wo0016-asset-archive-20261022/README.md) records historical untracked experiment files and status reports. Raw snapshots remain preserved; consult the current WO-0016 status above rather than the dated snapshot.
- **Candidate:** Route A, Route B, Hybrid and real video-input choices are not frozen; consult `docs/07_routes.md` and ADR-0001 for their exact status.
- **Current working baseline:** ADR-0002 records project identity and mechanical-speed baseline; it says team review is still required for effect, and hardware implementation remains Planned. **Accepted/frozen:** WO-0001a records `FINAL ACCEPTED` and the synthetic generator's frozen status. Check each record before applying its status elsewhere.

## Task → minimum reading path

| Task | Read first |
|---|---|
| Modify PL RTL | README current status → `docs/02_interfaces.md` → `docs/work-orders/wo-0003-pl-stream-skeleton.md` (or assigned WO) → actual `fpga/rtl/`; check route status in `docs/07_routes.md` if algorithm-specific. |
| RTL simulation | Relevant WO and `docs/02_interfaces.md` → actual RTL/TB → matching verification/source-hash record. Existing frontend and experimental simulations are present; use fresh output directories for replay. |
| Modify PS tracking | README → scope/interfaces → `docs/work-orders/wo-0010-ps-reference.md` → `python/refmodel/ps_reference/`. It is an offline reference, not a board DMA receiver. |
| Video source / camera / pin I/O | `docs/work-orders/wo-0002-video-input-feasibility.md` or WO-0005 → `docs/adr/adr-0001-video-source.md` → `hw/pin_budget.md` → relevant interface sections. |
| Synthetic data / its tests | `docs/work-orders/wo-0001a-h3-generator-hardening.md` → `python/synth/` implementation and tests. |
| Understand architecture or scope | README current status → `docs/00_scope.md` → relevant sections of `MASTER_PLAN.md` Part 2 / Part 3. Read more only if the task needs it. |
| Understand a past decision | Relevant file in `docs/adr/`; then follow its cited evidence and related WO as needed. |
| Determine current progress | README current status → assigned WO status and acceptance record → current branch's files and verification evidence. Do not infer completion from planned paths or stale WO headers alone. |

## Status, long documents, and change boundaries

- Treat **Implemented** as code/artifacts present with applicable verification; **Planned** as intended but not delivered; **Candidate** as under evaluation; **Accepted/frozen** only where the governing record explicitly says so. Check the actual checkout and evidence when documents differ.
- `MASTER_PLAN.md` is the long, authoritative plan; use targeted sections for planning/rationale questions, not as a default onboarding read. Read long ADR evidence, `docs/ai_collaboration_log.md`, and `hw/pin_budget.md` only for tasks that need that history or evidence.
- `docs/00_scope.md` has a controlled scope-change boundary. Interface changes are controlled by `docs/02_interfaces.md` (PR and three-person confirmation plus ADR); do not silently change interfaces, architecture, route status, or protected plan/decision records. Escalate material changes to the Manager.
- Preserve evidence labels and history in ADRs and Work Orders. A task assignment or expected deliverable does not itself authorize changing project scope or a frozen decision.
