# WO-0014 diagnostic executor Implementation Plan

**Goal:** Make the isolated 50 MHz executor fail closed at every stage, with offline fault evidence.

**Architecture:** A workstation supervisor owns health gates and deadlines. A persistent board child owns PYNQ objects, a fixed experiment guard and the DMA buffer. JSON commands advance exactly one state; failures are terminal. A worker heartbeat does not extend a stage deadline. Timeouts never reset, reload, kill or release uncertain hardware resources.

**Tech Stack:** Python standard library, existing validated WO-0014 helpers, injected mock PYNQ, pytest; SSH BatchMode and sudo -n only in a separately authorized future board execution.

1. Preserve the byte-identical original as `baseline_pynq_image_once.py`; record original, protected document and production RTL hashes. Audit actual WO-0013 PASS artifacts rather than relying on the earlier failed run.
2. Write `test_diagnostic.py` first. Assert monotonic stage order, post-load health gate, fail terminal with no later hardware calls, persistent send/overlay claims, no unsafe cleanup, and normal buffer release. Run RED before implementing `core.py`.
3. Implement `core.py` using only existing pure validation/address/cache/result helpers. Fixed 50 MHz BIT/HWH/packet identities. Before each hardware call check the stage deadline; log begin and returned values separately. Do not call the baseline hardware runner.
4. Implement `child.py` and `supervisor.py`: JSON commands, flushed/fsynced event logs, heartbeat, external per-stage timeout, exit-code monitoring, no terminate/kill/reset/retry. Fault subprocess tests cover blocking, crash and successful path. Supervisor refuses restart of a consumed session; board guard refuses a consumed experiment.
5. Re-run original script mock tests and WO-0014 vector tests. Save full pytest and dry-run outputs; verify artifact/HWH/RTL protocol identities and no protected/original changes. Request independent review and repair any material findings.
6. Deliver offline `BOARD_READY` only after tests and review pass. Document deployment requirements, persistent guard semantics, buffer retention and residual AXI risks. No board connection, upload, MMIO, DMA, production RTL edits or Git commit/push in this workflow.
