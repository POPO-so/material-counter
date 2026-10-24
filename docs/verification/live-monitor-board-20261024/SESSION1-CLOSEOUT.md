# Session 1 — operator-tested, then intentionally stopped

Host record date: 2026-10-24. This supplements the earlier [startup snapshot](README.md); it does not alter that historical running-state evidence.

The operator confirmed that they opened the dashboard, performed a test, found the effect satisfactory, and then turned off the camera. The normal stopped state was therefore expected. The Manager had not launched a second session when that clarification arrived, and the camera/service remains off. No second Overlay load, DMA run, reboot or count reset was performed.

The final monitor summary records:

| Field | Final value |
|---|---|
| Boot ID | `cf2ea1a2-f00e-4769-82bb-4f3e2a9e4d76` |
| Process | PID 1822, subsequently exited |
| Phase | `stopped` |
| Completed frames | 22,035 |
| Displayed count | 71 |
| Session elapsed | 916.930653 s, including initialization; not a dedicated throughput timing boundary |
| Last in-session CmaFree | 43,800 KiB |
| Last in-session RSS | 147,136 KiB |
| Last frame DMA status | MM2S/S2MM `0x00001002` |
| Count resets | 0 |
| Final selected view | Binary8 |

Separate read-only checks after exit confirmed that PID 1822 was absent, port 8080 was no longer listening locally, both DMA channels were idle with status `0x1002` and no DMA error bits, and CmaFree was 69,864 KiB at the process/port check. The final stdout summary was reached after the normal cleanup path; no FAIL_HOLD was entered. Allocation-time and exact release-time CMA were not measured. Global CmaFree changes are telemetry, not independent proof of allocator release or absence of leaks.

The complete [monitor stdout log](session1-monitor.log) preserves several HTTP `BrokenPipeError` traces from disconnected clients as well as the final normal summary. The request-handler errors were not DMA errors and did not prevent normal shutdown. They are retained as a limitation for HTTP error-log handling, not hidden or relabeled as a perfectly clean log.

The operator's qualitative feedback is not an independent labeled accuracy measurement. Count 71 is the displayed algorithm total; physical accuracy and per-item identity acceptance remain NOT_VERIFIED. No hours-long soak was performed. Final processing FPS is N/A because the stopped summary reports null and session elapsed includes setup; earlier running-window FPS remains in its original startup ledger row.

Evidence: [session1-closeout.json](session1-closeout.json), [session1-monitor.log](session1-monitor.log). The earlier 80-file deployment, current-Boot manifest and Stage 1 PASS stay valid for their recorded scope; a future startup must re-read live state and must not reuse this Boot's consumed Stage 1 guard. No service should be restarted solely because the dashboard now reports disconnected.
