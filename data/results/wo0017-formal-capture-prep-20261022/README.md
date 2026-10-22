# WO-0017 formal B1/B2 preparation and current resource hold

Final verification date: 2026-10-23, Asia/Shanghai. This directory continues the 2026-10-22 calibration handoff; its original directory name is retained.

## Delivered and verified

- Existing calibration source, preview/counter changes, tests and five evidence directories were committed as `834bad58638d12a8870364e2163051a8c4450f5f` on `codex/wo0017-algo-accuracy`. The 487 ignored H3 PNG files were not staged. `main` and `origin/main` remain `1d32593610c2643b3f022ce048d31f70fd7a4417`.
- Verified PID 7009's command line and Boot, sent TERM once, and observed `STOPPED` with `standard_profile_restored=true`. Manual exposure 50, brightness 0, MJPG 640x400/60 readback passed. `/dev/video0` and ports 8080/8081 were released. Immediately after exit, CmaFree was 42512 KiB; both DMA status registers were `0x00000001`.
- Added explicit WO-0017 profiles while retaining the old WO-0016 165-frame/five-nut contract. B1 is 115 frames, expected count 0, `empty_belt`; B2 is 220 frames, expected count 5, `M6_nut_no_washer`. Both require three seconds of stabilization, a 15-second preparation countdown, threshold 128 and an independent single attempt.
- Added current-boot manifest admission and verification of the original `stage1-attempt-root-02` PASS receipt. No historical receipt was rewritten or converted into a new PASS.
- Final offline regression: `710 passed, 4 skipped, 5 subtests passed`; see `pytest-full.xml`. The additional skips cover POSIX UID/mode checks unavailable on Windows. Static independent review found no READY/Permit identity mismatch. No new hardware stream was tested.

## Current hold: not READY, no operator passcode issued

The later idle-board resource check recorded CmaFree 18124 KiB with no camera/FPGA device owner. One execution of the preparation script then rejected admission at 436 KiB, before writing any manifest, READY Guard or Permit. After its process exited, the final snapshot recorded CmaFree 21656 KiB, still below the required 32768 KiB watermark. MemAvailable was 430020 KiB. Boot remained `35983d66-bb1d-4a8a-85e5-802170825515`, manual exposure remained 50 and brightness 0, and DMASR remained `0x00000001` for both channels.

This was a software resource admission rejection, with no DMA transfer, camera stream, conveyor action or Overlay reload. No standby service is running; 8080 and 8081 remain unused. No caches were cleared, no process was killed to recover memory and no resource threshold was lowered. The cause of the later idle-board CMA values is not established by these measurements.

Raw evidence: `preview06-term-and-health.txt`, `dma-and-stage1-readonly.txt`, `stage1-root02-readonly.txt`, `cma-recheck-20261023.txt`, `prepare-b1-admission-20261023.txt`, `unissued-state-20261023.json`, `final-health-20261023.txt`.

Raw terminal logs retain their original trailing spaces and blank lines. Source and authored documentation pass the scoped diff whitespace check; the raw logs are preserved without cosmetic rewriting.

## Required new records

| Stage | New READY Guard | New single-attempt Permit | Status |
|---|---|---|---|
| B1 | `/var/lib/wo0017/<boot>/b1/ready.json` | `/var/lib/wo0017/<boot>/b1/attempts/b1-empty-01/attempt-permit.json` | Not issued: CMA hold |
| B2 | `/var/lib/wo0017/<boot>/b2/ready.json` | Separate B2 attempt and evidence/capture directories | Not authorized or issued; review B1 first |

Each READY binds Boot, the new manifest, BIT/HWH, original Stage 1 receipt SHA-256, the complete 12-file runtime hash map, counting configuration and capture path/capacity/queue/schema. Each Permit additionally binds the exact READY file SHA-256, unique attempt ID, runner/launcher/counter hashes and preparation delay. A separate `consumed.json` is created exclusively on actual runner admission, after browser authorization in preflight-standby mode. Existing records are never removed/reset to allow a retry.

The new manifest references the unchanged WO-0015 BIT/HWH and golden vectors by absolute paths and validates their hashes. It is a runtime admission manifest, not a new Overlay or hardware PASS.

Raw capture is mandatory: `frames_gray8_binary8.npz` contains `raw_gray8` and `raw_binary8`, uint8 arrays of shape `(115,240,320)` for B1 or `(220,240,320)` for B2, plus metadata. Queue depth is 2 and the bound budget is 100663296 bytes (96 MiB). The conservative required capacities are 35831808 bytes and 68517888 bytes respectively; a 64 MiB budget does not admit 220 frames. Completion, hashes, continuity and replay must still be checked after sampling. B1 requires zero raw components/qualified detections/events; B2 requires independently reviewed Ground Truth and entity-to-event correspondence, beyond cumulative total 5.

## Prepared startup procedure, not executed

The isolated runtime is deployed under `/home/xilinx/wo0017/formal-b1-20261022-01/`; all 12 runtime source hashes were verified. Existing `/home/xilinx/wo0017/app`, the Stage 1 files and the original WO-0015 deployment remain unchanged. `formal-b1-runtime.zip` packages the reviewed source and preparation/launch scripts, with no H3/BIT/HWH data.

After resource diagnosis and a fresh current-boot resource admission, the preparation command is:

```powershell
ssh -o BatchMode=yes xilinx@192.168.2.99 "sudo -n /usr/local/share/pynq-venv/bin/python3 -u /home/xilinx/wo0017/formal-b1-20261022-01/prepare_b1.py"
```

It refuses low CMA before writing admission records. Do not rerun merely to hunt for a passing watermark. Current resources must be understood and revalidated first. If Boot changes, these fixed-Boot scripts and the Stage 1 receipt cannot be reused; prepare a new reviewed package and current-boot Stage 1 evidence.

Only after successful preparation, starting `launch_b1.py` creates the 8081 loopback preflight-standby gate. It validates the bound records without importing PYNQ, opening a camera, accessing the DMA driver or consuming the sample Permit. A one-time operator passcode is generated then and printed only in its private launch log. No passcode currently exists.

For that future gate, forward both loopback ports from the PC:

```powershell
ssh -o BatchMode=yes -N -L 127.0.0.1:18081:127.0.0.1:8081 -L 127.0.0.1:18080:127.0.0.1:8080 xilinx@192.168.2.99
```

Then visit `http://127.0.0.1:18081/`, enter the supplied one-time passcode, and click the single-run confirmation only with the belt empty. The click authorizes the actual runner: it rechecks resources/identities, consumes this attempt, validates CMA physical spans and the loaded Overlay with `download=False`, opens the camera and proceeds through the countdown to the 115-frame capture. B1's page instructs the operator to keep the belt empty throughout. Motor operation is not implemented by this launcher.

A preflight rejection after `launch_b1.py` starts retains `standby-launch-once.json`; do not delete it or retry the same package. Preserve all evidence and request a separately reviewed new attempt/package. B2 needs new authorization after B1 evidence is checked; this delivery does not launch or preauthorize B2.
