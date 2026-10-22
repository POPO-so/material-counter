# B1-03 browser-start rejection and offline entry-point fix

The operator clicked Start, but no formal frame was sampled. Board evidence records exit code 2, zero frame records, no capture directory and no `consumed.json`. The launcher failed before any PYNQ import or board access with:

```text
ValueError: Stage B-2 board mode requires a live operator control server session
```

The source defect was a duplicate Python module identity. `launch_b1.py` executed `operator_gate_launcher.py` as a script, creating `__main__.OperatorGate` and its own registration table. The runner's validation then imported the same source as `operator_gate_launcher`, creating a second class and registration table. Its strict type/registration check rejected the actual live gate. This was a software admission rejection, not a DMA hardware result, material count, timeout or CMA failure.

The existing tests imported the launcher by its module name and missed the script-execution boundary. A new subprocess regression executes the actual launcher source under `__main__`, creates its real control server and invokes the real runner gate validator. Before the fix it reproduced the same rejection; afterward it passes. The hardware runner is replaced by a gate-validation-only test callback, so the test performs no board access.

The CLI now delegates to the canonical `operator_gate_launcher.main`, so the runner and launcher share the class and live registration table. The authentication, type/registration checks and launcher-hash validation are retained. The operator page now labels rejected/failed executions `FAILED` rather than `COMPLETE`; missing or unknown status also fails closed. All three new regressions first failed, then passed with the fix. Focused regression: **89 passed**, recorded in `pytest-launcher.xml`. Independent read-only review confirmed the module-identity fix and retained authentication checks; its missing-status finding is covered by the third regression.

Read-only board follow-up confirmed the same Boot `35983d66-bb1d-4a8a-85e5-802170825515`, CmaFree 112048 KiB, both DMASR values `0x00000001`, no camera/FPGA device owner and no remaining standby process. No cache recovery, Overlay reload, camera operation, DMA transfer, Permit consumption or launch retry was performed during this diagnosis.

The fix is local/offline only. The original `-03` deployment, its exited launch marker, READY Guard, Permit and private log remain unchanged. The old passcode is no longer usable because its server exited. A future deployment requires a new reviewed launch package and newly authorized identity records matching the changed launcher hash; do not overwrite/reset the old launch or reuse the old bound hash.

Evidence: `exit-status.json`, `operator-control-events.jsonl`, `launcher-failure-redacted.txt`, `board-state-after-rejection.json`. The private passcode and bulk import trace are excluded from the exported failure log. The earlier READY report remains a historical pre-click snapshot; this record documents the later failed start.
