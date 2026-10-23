# WO-0015 cold-start offline increment Implementation Plan

**Goal:** Create reviewable, unapproved cold-start candidate and passive UART capture cores without touching a board or sealed evidence.

**Architecture:** B1 requires an injected POSIX API and has no real adapter or launcher. UART requires an injected receive transport and never opens a device. Separate host parsers report observations; no state promotes a boot ID or chains B2.

**Tech Stack:** Python 3 standard library, pytest, in-memory fakes, AST/static checks, SHA-256.

1. Record explicit-manifest historical hashes and both checkout HEAD/tracked/staged diff hashes using integrity_check.py. No historical tree walk.
2. Write test_coldstart.py, observe RED; implement coldstart_b1.py and host_mountinfo.py; run explicit-file tests to GREEN.
3. Worker owns uart_readonly.py, test_uart_readonly.py and uart-design.md, with isolated RED/GREEN logs. Manager reviews code and tests.
4. Freeze B1 text behind NOT_APPROVED_DO_NOT_EXECUTE; statically verify source, never execute the frozen draft. Preserve v2 B2 unchanged and document its complete future contract and SSH options.
5. Run only named new test files; verify 364 sealed entries, 320 historical indexed entries, 389 historical non-sensitive entries, BIT/HWH and both Git diff baselines again.
6. Write reports, review brief, approval draft, summary and manifest covering all new deliverables except the manifest itself. No commit or merge.

Current state: STOPPED_PRECONDITION_UNVERIFIED / NOT_APPROVED_DO_NOT_EXECUTE. Candidate success remains STOPPED_CANDIDATE_BOOT_UNAPPROVED.
