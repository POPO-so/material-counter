# Live Monitor — authorized board startup

Host date: 2026-10-24, Asia/Shanghai. The board clock is inconsistent with host time; use Boot IDs and hashes to bind this evidence.

## Result

The monitor is running at **http://192.168.2.99:8080/** with `--mode board --execute --no-auth --counting-profile full-width-mixed --enable-touching-split`. At the recorded latest status it had completed 4,966 frames, displayed count 9, reported fresh camera/FPGA/DMA frames, and continued beyond both historical 165/300-frame limits. No independent material ground truth was recorded; count 9 is not an accuracy score. The service was deliberately left running. Neither reset nor stop was invoked during browser QA.

The annotated local `v1.0-live-monitor-offline` tag remains at `ee55267e288ccf05781545544391213a40dfe491`; it was verified, not replaced or pushed. The deployed code is that commit's tracked WO-0015/0016 package, with 80 file hashes verified against [deployment-index.json](deployment-index.json). Tested functional source remains `87788922add1513761a970b3686ef0991e866c9f`; subsequent local commits add reports/ledger only. Deployment path:

`/home/xilinx/wo0017/live-monitor-20261024-ee55267e`

## Recovery and one-shot Stage 1

The initial Boot `53ba3093-b260-4218-86de-5db4edfffa5b` was blocked by low CMA; its [blocked record](../live-monitor-deployment-blocked-20261024.md) remains unchanged. The user explicitly approved one normal reboot and continuation. The new Boot is:

`cf2ea1a2-f00e-4769-82bb-4f3e2a9e4d76`

After reboot CmaFree was 84,336 KiB. A later Stage 1 preflight was rejected below 32,768 KiB before its guard or hardware access. The board had approximately 299 MiB file cache and no inspected competing device owner. Following the existing project's preparation practice, one `sync` plus `drop_caches=3` was performed: CmaFree changed from 28,424 to 110,272 KiB. See [cache-reclaim.json](cache-reclaim.json). These are global telemetry snapshots, not leak or allocator-release proof.

A new current-Boot manifest was derived from the unchanged historical artifact contract. Only Boot binding, deployed executor hashes and absolute artifact paths were rebound. Static manifest validation independently checked the 50 MHz contract, BIT/HWH hashes, helper source hashes, packets and golden masks. No old manifest, guard or receipt was rewritten.

| Binding | Value |
|---|---|
| Manifest | `boot-cf2ea1a2-f00e-4769-82bb-4f3e2a9e4d76/run-manifest.json` below the deployment root |
| Manifest SHA-256 | `8e36403318041714d4d54c638957b32ad103ff55cebc7a47f7bbbd2cdc55125c` |
| Stage 1 PASS receipt | `/var/lib/material-counter-live/cf2ea1a2-f00e-4769-82bb-4f3e2a9e4d76/stage1/overlay-pass.json` |
| Receipt SHA-256 | `d284fa0d1f669206bf7b7d9d2e65ff88e0f37aa86abcab6bddfe320682b84567` |
| BIT SHA-256 | `a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420` |
| HWH SHA-256 | `bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6` |

The unchanged historical WO-0015 CLI pins an older Boot. A separately reviewed operational [Stage 1 adapter](stage1_current_boot.py) used the existing explicit `approved_boot_id` validator, PYNQ adapter, AFI synchronization, post-audit and official receipt writer. It did not patch deployment source or authorization constants. It required explicit execution/stage/once flags, root/XRT, pinned deployment-index hash and all deployed source hashes, current Boot/manifest, CMA/HP checks, ownership and empty PYNQ shutdown state. An initial cold manager `unknown` is allowed only with flags 0 before this load; the actual admission saw `operating`, and post-audit strictly requires operating/flags 0. Seven host-only adapter gate checks passed; they are separate from physical Stage 1 checks.

The adapter created a new private Boot-scoped directory and durable O_EXCL consumed guard before exactly one Overlay load. It performed the existing two-register AFI1 32-bit synchronization after checking reset release/HWH width, read Checker/Packer signatures and the allowlisted DMA status registers, and did not instantiate the DMA driver or allocate frame buffers. Stage 1 passed with both DMASR values exactly `0x1`, AFI1 RD `0x1`, WR `0xf01`, and a clean post-load kernel/ownership/BIT audit. Stage 1 CmaFree was 100,992 KiB before and 61,604 KiB afterward. The official same-Boot PASS receipt was written and revalidated. The first CMA rejection did not consume a guard or load an Overlay; no failed hardware attempt was retried.

## Active monitor and browser checks

The monitor was launched once as PID **1822**, with the exact command saved in [monitor-launch.json](monitor-launch.json). `/proc/1822/cmdline` was independently checked for `--enable-touching-split` and `--no-auth`. The runtime rechecked manifest/receipt hashes, live boot, camera/process ownership, loaded BIT/signatures, AFI, watermark and physical buffer spans before camera/DMA operation. No conveyor command was issued.

Real Microsoft Edge QA against the board verified the visible MJPEG, the status indicators, and switching to Binary8 and back to overlay without changing count state. JavaScript errors: 0. A 15-sample window at two-second intervals ended at 2,267 frames, with sliding completed-frame throughput 24.6881 FPS and preview publication 6.0649 FPS. The preview is intentionally published every fourth processing frame. CmaFree during that window ranged 45,860–46,484 KiB; RSS ranged 145,404–146,276 KiB. See [browser-live-evidence.json](browser-live-evidence.json).

The later [latest-status.json](latest-status.json) snapshot had 4,966 frames, sliding processing 20.6099 FPS, preview 5.6317 FPS, count 9, CmaFree 46,076 KiB and RSS 146,516 KiB. Both DMA channels reported `0x00001002`. These are sliding completed-frame and preview rates, not a bounded full-run average or a per-stage latency benchmark. Later statuses may differ because the monitor remains active.

The initial visible screen and Binary8 screen are preserved as [overlay](board-overlay.png) and [Binary8](board-binary.png). Evidence is bounded to small metadata snapshots and two host screenshots; no camera-frame archive, 300-frame buffer or NPZ was enabled.

## Limits and operation

There is no physical accuracy or per-item identity acceptance in this run, and no hours-long soak claim. Buffer allocation count is fixed by the runtime, but allocation-time CMA was not separately sampled here; release CMA is N/A because the user requested an active service. Global CmaFree variation cannot by itself prove a leak or release. No new full pytest regression was run during hardware startup; the offline tag retains its original 896-pass scoped evidence. The seven operational-adapter checks are recorded separately in [adapter-tests.log](adapter-tests.log).

To stop normally, use **停止监控** on the page. If stopping from SSH, send one SIGTERM to PID 1822 after verifying its command and Boot. The runtime drains completed work and releases CMA only when idle is proven; uncertain ownership enters FAIL_HOLD and must not be force-freed. Do not run a second monitor or reuse this Boot's Stage 1 guard. Read [run-context.json](run-context.json), [stage1-evidence.json](stage1-evidence.json), [stage1-pass.json](stage1-pass.json) and [stage1-consumed.json](stage1-consumed.json) for exact bindings. The receipts apply only to this Boot and manifest.
