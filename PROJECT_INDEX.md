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

- **Implemented:** synthetic dataset generator and regression tests in `python/synth/` (`generate.py`, `scene.py`, `specs/`, `tests/`).
- **Planned:** PL image pipeline and PS tracking/counting, plus encoder, camera, display and board integration, as summarized in README and the relevant Work Orders.
- **Not implemented in this checkout:** `fpga/rtl/` and `fpga/constraints/` contain only `.gitkeep`; there is no RTL simulation entry point or PS tracking implementation. Work Order target paths describe deliverables, not existing code.
- **Candidate:** Route A, Route B, Hybrid and real video-input choices are not frozen; consult `docs/07_routes.md` and ADR-0001 for their exact status.
- **Current working baseline:** ADR-0002 records project identity and mechanical-speed baseline; it says team review is still required for effect, and hardware implementation remains Planned. **Accepted/frozen:** WO-0001a records `FINAL ACCEPTED` and the synthetic generator's frozen status. Check each record before applying its status elsewhere.

## Task → minimum reading path

| Task | Read first |
|---|---|
| Modify PL RTL | README current status → `docs/02_interfaces.md` → `docs/work-orders/wo-0003-pl-stream-skeleton.md` (or assigned WO) → actual `fpga/rtl/`; check route status in `docs/07_routes.md` if algorithm-specific. |
| RTL simulation | Relevant WO and `docs/02_interfaces.md` → inspect actual RTL and simulation files. No RTL/simulation implementation currently exists; establish scope with Manager before creating beyond assigned deliverables. |
| Modify PS tracking | README current status → `docs/00_scope.md` → `docs/02_interfaces.md` → `docs/work-orders/wo-0004-tracking-baseline.md` → actual PS code (currently absent). |
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
