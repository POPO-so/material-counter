# Live Monitor deployment — CMA admission blocked

Host record date: 2026-10-24, Asia/Shanghai. This is deployment and read-only board admission evidence, not a camera/DMA test.

## Tag and deployed source

The annotated local tag `v1.0-live-monitor-offline` already existed when the authorized tag command ran. Its target is `ee55267e288ccf05781545544391213a40dfe491`, with tested functional source `87788922add1513761a970b3686ef0991e866c9f`. The annotation includes the scoped 896-pass/5-skip host results and known limitations. It was verified and preserved, not overwritten or pushed.

SSH connected to the PYNQ-Z2 at `192.168.2.99`. Current Boot ID:

`53ba3093-b260-4218-86de-5db4edfffa5b`

An isolated deployment was created at:

`/home/xilinx/wo0017/live-monitor-20261024-ee55267e`

The archive contains the current tracked `scripts/wo0015/` and `scripts/wo0016/` runtime and tests. All 80 files matched their source SHA-256 values after transfer and extraction. Archive SHA-256:

`c2fa1167fdd0095726a46c3088054f7b78189319aa267333655d65c627fd4925`

The runtime imported successfully under board Python 3.10.4, NumPy 1.21.5 and OpenCV 4.5.4 without importing PYNQ or opening any device. `deployment-index.json` and `deployment-status.json` are retained in that deployment directory. A local evidence copy is at `C:/wt/live-monitor-board-20261024/deployment-status.json` and is not synchronized by Git; its hash is recorded in the benchmark ledger.

## Admission result

`CmaTotal=131072 KiB`. Observed CmaFree was 17964 KiB initially, 4352 KiB during the NumPy/OpenCV import probe, and 15776 KiB after that probe exited. All are below the Live Monitor minimum of 32768 KiB. These are global telemetry snapshots; their differences do not establish a leak or identify an owner.

The inspected 50 MHz manifests and PASS receipts under the maintained WO-0015/0016/0017 deployment and receipt directories were historical and did not match the current Boot. In particular, the calibration manifest at `/home/xilinx/wo0017/physical-calibration-20261023-07/calibration-run-manifest.json` has SHA-256 `d02b04e495ad80f0256f35002cda474bcc6af16590b8085a8044f86f52e5f782` and belongs to Boot `35983d66-bb1d-4a8a-85e5-802170825515`. It is not a current-Boot launch authorization. No historical record, Guard or Permit was modified or consumed.

Existing artifacts were read and hashed:

| Artifact | SHA-256 |
|---|---|
| `/home/xilinx/wo0015/wo15.bit` | `a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420` |
| `/home/xilinx/wo0015/wo15.hwh` | `bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6` |

No Stage 1 was attempted because the known low CMA condition already prevents the requested subsequent monitoring session. No Overlay was downloaded/reloaded, no MMIO or DMA was accessed, no camera was opened, and no conveyor command was issued. The requested `--enable-touching-split` has not yet been run on the board. Frame count is 0; FPS, physical accuracy, allocation/release CMA and latency are N/A.

The board clock reported an epoch inconsistent with the host date; retain that raw timestamp in evidence and use the host date for this report. Runtime admission relies on the freshly read Boot ID, not historical dates.

## Remaining work

Restore sufficient CMA headroom without bypassing the watermark. A normal reboot is a possible recovery action but requires separate authorization because it interrupts board services and creates another Boot ID; recovery is not guaranteed. Re-read the new live state, create a fresh current-Boot manifest and same-Boot Stage 1 PASS under the existing one-shot protocol, then launch the deployed monitor with `--enable-touching-split`. Do not reuse or rewrite historical receipts. No real FPGA monitoring URL is active from this task.
