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

PYNQ uses `channel.transfer(buffer, nbytes=...)` to start a nonblocking transfer. The local `transfer_async()` adapter waits using `wait_async()` if available or a daemon thread around `wait()`. Capture explicitly requests and verifies the negotiated MJPG 640×400 V4L2 mode. A successful transfer is followed by Packer/length checks and `sync_from_device()` before CPU reads the RX mask.

For a board run, launch the PYNQ interpreter with `sudo -n env XILINX_XRT=/usr`; the selected value is included in the JSON evidence.

## Evidence boundary

The offline simulation is not camera, conveyor, M6 nut/washer accuracy, or FPGA board evidence. Record physical measurements only in [`WO-0016 board acceptance`](../../docs/verification/wo-0016-board-acceptance.md). Current offline results are in [`verification report`](../../data/results/wo0016-offline-20261017/verification-report.md).
