# WO-0016 Stream Counter

This package adds a host-testable two-slot Binary8 DMA consumer, ROI crossing counter, and MJPEG preview to the WO-0015 320×240 frame path.

## Offline use

From the repository root:

```powershell
python -m pytest -q scripts/wo0016
python scripts/wo0016/run_stream_counter.py --mode mock --mock-frames 8
```

The default CLI mode is Mock. It creates synthetic moving components, filters a small noise blob, runs the actual counter and JPEG renderer, and briefly serves the latest frame over a local ephemeral HTTP port. Output labels this as offline synthetic evidence and leaves `hardware_fps` unset.

## Board mode

Board mode is a finite, one-shot operation against the already loaded WO-0015 50 MHz Overlay. It requires a manifest whose BIT/HWH hashes are reviewed, a same-boot WO-0015 Stage 1 Overlay PASS receipt, the manifest SHA-256, a new absolute evidence JSON path, camera device, and all three explicit opt-in flags:

```text
--mode board --execute --authorize-stage wo0016-stream --approve-once
```

Also supply `--manifest`, `--manifest-sha256`, `--max-frames`, and `--evidence-json` (or `--output-json`). The runner reuses the already loaded BIT with `Overlay(download=False)` and requires live AFI1 readbacks of exactly RD `0x00000001` and WR `0x00000F01` before creating the DMA driver. The `CmaFree` admission watermark is 32,768 KiB before and after each buffer allocation. Never reuse a consumed guard or evidence path. The program does not load/reload the Overlay, reset or retry DMA, or free buffers if DMA ownership is uncertain. It binds the MJPEG server to `0.0.0.0:8080` by default; override `--host`/`--port` when the network setup requires it.

For an isolated board deployment, place the required WO-0015 Python modules in a sibling `wo0015/` directory inside the package directory. The runner prefers that bundle and falls back to the repository's `scripts/wo0015/` while running from source. Required modules: `board_once.py`, `board_preflight.py`, `camera_dma_oneshot.py`, `camera_adapter.py`, and `prepare_vectors.py`. Preserve the exact executor hashes bound by the selected WO-0015 manifest: do not edit or patch `board_preflight.py` to bypass its boot check. This keeps deployed helpers isolated without replacing the board's existing WO-0015 files.

PYNQ uses `channel.transfer(buffer, nbytes=...)` to start a nonblocking transfer; it returns `None`, and completion is reported by the channel object. The local `transfer_async()` adapter prefers a daemon thread around `channel.wait()` because PYNQ's `wait_async()` requires a configured interrupt handle. Capture requests MJPG 640×400 at 60 FPS, requests a one-frame V4L2 buffer, and checks that V4L2 reports at least 50 fps. Two warm-up frames run before steady-state timing; a single worker reads and resizes at most one next frame while the current frame is encoded, transferred, and counted, then is joined before normal camera release. Measured color frames use the monochrome sensor's first channel and nearest-neighbor resizing. After `sync_from_device()`, each completed RX mask is copied once into a preallocated cached NumPy array; Packer validation and ROI counting then use that cached copy. The counter processes every received mask. Preview callbacks copy a quality-70 snapshot every fourth frame; JPEG encoding runs on a low-priority worker only while an MJPEG client is connected. Timing reports separate RX synchronization/copy, ROI counting, preview handoff, and background JPEG encoding.

ROI extraction uses the cached 320-pixel-wide slice spanning 49 rows around the counting line. Row and column projections are computed from that slice; a C-level foreground count skips connected-component labeling when the band has fewer foreground pixels than `min_area`. Nonempty bands use 16-bit labels with OpenCV `CCL_GRANA` when available. NumPy filters out below-threshold component labels before Python creates detection records; older OpenCV builds fall back to `connectedComponentsWithStats`.

For a board run, launch the PYNQ interpreter with `sudo -n env XILINX_XRT=/usr`; the selected value is included in the JSON evidence.

## Phase 1.6 board entry and read-only gates

`phase16_board_entry.sh` is the only supported Phase 1.6 host entry point. Copy the reviewed `board-runtime-final/` package to a fresh directory under `/tmp` with `scp`, then invoke it with `ssh -o BatchMode=yes ... "sudo -n bash /tmp/<run>/board-runtime-final/phase16_board_entry.sh precheck <authorized-boot-id>"`. Do not compose privileged scripts with nested `sudo -S -i bash -c` quoting or send a password from the host. The wrapper requires root, sources every readable `/etc/profile.d/*.sh`, checks the exact boot, the 32,768 KiB CMA watermark, FPGA Manager state, Python/DMA zombie and device ownership gates, and then bounds PYNQ import plus explicit `EmbeddedDevice` setup to 10.5 seconds in the main thread with a catchable `SIGALRM`; the shell retains a 12-second hard timeout. Device readiness comes from the legal FPGA Manager sysfs state; the flow skips the potentially blocking global `Device.devices` scan. The Stage B runner passes the same explicit device to `Overlay(download=False)` and MMIO and uses it for CMA allocation. A load/setup timeout aborts before the Stage B one-shot guard is consumed. It does not call `xbutil`. The `diagnose` action collects root-only CMA, FPGA-manager, process-ownership, and camera readbacks without importing PYNQ; `precheck` remains read-only and reports low CMA as `BLOCKED`.

For `smoke3` only, if the pre-guard CMA sample is below 32,768 KiB, the entry wrapper permits one recovery attempt after confirming the same boot, an unconsumed guard, an operating FPGA Manager, and no process/device owner. It runs Python `gc.collect()`, writes `3` once to `/proc/sys/vm/drop_caches`, and rereads CMA before dispatch. This only discards clean page cache and reclaimable slab; it cannot release buffers held by another process. Any owner finding, failed cache drop, or still-low watermark stops before the Stage B guard. There is no process killing, repeated cache drop, or guard reset. DMA allocations run garbage collection before each buffer allocation, explicitly release all partial allocations before DMA starts, and explicitly free all completed-run buffers only after both DMA channels are idle; uncertain DMA ownership keeps buffers held.

After a successful three-frame smoke, `camera_preview_server.py` can run as a transient systemd service. It rechecks the authorized boot, FPGA Manager, process/device ownership, and camera gate, then publishes live MJPEG from V4L2 without loading PYNQ, an Overlay, or DMA. It binds `0.0.0.0:8080`; the service status JSON must report `PREVIEW_READY` and at least one published frame before handing the URL to an operator. Stop this camera-only service before starting a later Stage B DMA batch because it owns `/dev/video0`.

The camera gate is read-only: measured V4L2 format must be MJPG 640×400, `exposure_time_absolute=50`, `power_line_frequency=1`, and `gamma=100`; `systemctl show ov9281-init.service -p ExecMainStatus` must return `0`. Since the unit is `Type=oneshot`, `ActiveState=inactive` after a successful run is accepted and recorded as telemetry. Before execution, the wrapper also checks the current-boot Stage 1 and Stage B one-shot guards, receipts, and failure marker without changing them. It only dispatches the Overlay or the exact three-frame smoke when their same-boot artifacts are in the required state.

## Evidence boundary

The offline simulation is not camera, conveyor, M6 nut/washer accuracy, or FPGA board evidence. Record physical measurements only in [`WO-0016 board acceptance`](../../docs/verification/wo-0016-board-acceptance.md). Current offline results are in [`verification report`](../../data/results/wo0016-offline-20261017/verification-report.md).
