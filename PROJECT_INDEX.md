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

- **Implemented with bounded evidence:** `python/synth/`, frontend/morphology RTL in `fpga/rtl/`, testbenches in `fpga/sim/`, PS offline reference in `python/refmodel/ps_reference/`, and WO-0013/0014/0015/0016 experiments. WO-0015 includes Binary8 S2MM board receipts; WO-0016 Stage A has empty-belt throughput evidence. These are distinct from a complete board counting system.
- **Planned / Proposed:** production PL feature extraction and ABI, sustained board-side tracking/counting/display, Stage B real-material accuracy acceptance, and encoder speed acceptance. ROI remains separately documented in its handoff.
- **Current progress and source versions:** [2026-10-18 current delivery status](docs/verification/submission-status-20261018.md) is the current summary; [2026-10-15 delivery audit](docs/verification/submission-status-20261015.md) is a historical snapshot for source inventories and earlier evidence. Offline integrity entry: `python scripts/delivery/verify_delivery.py`; relocatable replay: `scripts/delivery/REPLAY.md`.
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
