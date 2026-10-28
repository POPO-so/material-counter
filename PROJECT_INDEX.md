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

- **2026-10-28 full-resolution RLE candidate:** User-authorized `scripts/rle/` and `fpga/experimental/rle/` now process 640×400 Gray8 without resizing, with PL ROI/intensity offset/threshold/optional morphology, bounded RLE and projections, native PS 8-connected run analysis and existing tracking. 118 relevant tests, eight full-resolution RTL vectors and a post-synthesis projection regression passed. The final 50 MHz build passed timing/DRC gates; board evidence includes pixel comparisons, overflow rejection and a final 300-frame nonzero-foreground camera run at 20.5651 FPS with 4088–4176 returned bytes/frame. This independent candidate entrypoint has not replaced the desktop GUI; 30 FPS has not been reached and physical counting accuracy remains unverified and Route is not frozen. See [RLE acceptance](data/results/rle-20261028-board/README.md) and [usage](scripts/rle/README.md).

- **2026-10-27 Gray8 migration and board test:** By explicit user authorization, all active production paths use the sole `GRAY8_PACKET_V1` protocol; old RGB encoder/RTL/build/replay code has been removed and no runtime RGB fallback remains. The 320×240 Gray8 packet is 76,832 bytes; S2MM Binary8 is 76,800 bytes. Three XSim suites, the 50 MHz BIT/HWH build and offline delivery verification passed. On boot `442a249b-1a20-41a1-bbc6-46debb0f4af8`, the board passed 3 synthetic frames, a 30-frame camera run, a 30-frame warmup, and three 300-frame camera runs: all 963 frames passed full Binary8 pixel comparison, with idle DMA, zero faults and released buffers. The three long runs measured 19.1063, 19.3684 and 20.1920 FPS; their 900-frame weighted rate was 19.5448 FPS including startup/warmup, pixel verification and PS counting. The camera produced all-black masks (zero foreground pixels), so object accuracy and busy-scene throughput remain unverified. The current relevant software set passed 108 tests in 4.14 s outside the Windows sandbox. A previous wider suite recorded 706 passed, 8 skipped, 2 known failures; do not report the wider suite as all green. See [camera board-test report](data/results/gray8-camera-20261027-122633/README.md), [cleanup record](data/results/gray8-cleanup-20261027/README.md), [build timing/resources](data/results/gray8-dma-20261027/README.md), [implementation state](PLAN.md), and [ADR-0004](docs/adr/adr-0004-gray8-dma-input.md). The camera rate is not an object-accuracy or YOLO result.

- **Implemented with bounded evidence:** `python/synth/`, frontend/morphology RTL in `fpga/rtl/`, testbenches in `fpga/sim/`, PS offline reference in `python/refmodel/ps_reference/`, and isolated WO-0013/0014 DMA experiments. These are distinct from a complete board counting system.
- **Planned / Proposed:** production PL feature extraction and ABI, sustained board-side tracking/counting/display, real-material accuracy acceptance, and encoder speed acceptance. ROI is documented in its handoff; it is not evidence of a complete system.
- **Current progress and source versions:** the [2026-10-15 delivery audit](docs/verification/submission-status-20261015.md) and [2026-10-18 status report](docs/verification/submission-status-20261018.md) are historical snapshots. Current WO-0016 progress is in the README and [board acceptance record](docs/verification/wo-0016-board-acceptance.md). Offline integrity entry: `python scripts/delivery/verify_delivery.py`; relocatable replay: `scripts/delivery/REPLAY.md`.
- **Latest WO-0016 status (2026-10-21):** [PR #10](https://github.com/POPO-so/material-counter/pull/10) merged to `main` at `5926820`, covering WO-0015 S2MM and WO-0016 Stage A Attempt 8. Stage A reached 15.157 FPS, while ROI timing averaged 17.657 ms against a 2.5 ms target and CCL ran on all 30 frames. Stage B-2 Attempt-5 Recovery-2 completed 165/165 frames at 22.132 FPS and counted 4/5 (80%); material-accuracy acceptance failed and no retest is planned. See [WO-0016 board acceptance](docs/verification/wo-0016-board-acceptance.md).
- **Workspace evidence preservation (2026-10-22):** [Asset archive and zero-loss verification](docs/verification/wo0016-asset-archive-20261022/README.md) records the original 144 untracked experimental files, four modified documents and October 18 historical status report. Raw snapshots are retained on the dedicated archive branch; current Stage B-2 status remains above.
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
