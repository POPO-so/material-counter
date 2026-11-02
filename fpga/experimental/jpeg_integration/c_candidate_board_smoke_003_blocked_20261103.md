# C candidate — smoke-003 blocked after reboot: pre-load state UNKNOWN

Date: 2026-11-03
Board: PYNQ-Z2 at `192.168.2.99` (`xilinx`).
Recovery: one normal board reboot after the smoke-002 HOLD; no kill/reset/
reload was issued first. New boot ID:
`6b0d11ee-d9cb-4513-afc8-fee3296448df` (previous HOLD boot
`b8034c58-663f-4f86-b68c-37e4bf7cdca2`).
Result: **smoke-003 NOT STARTED — pre-load classification UNKNOWN.** No
package upload to `jpeg-candidate-20261103c`, no candidate load, no camera
transfer, no frames. `count_accuracy` remains `NOT_MEASURED`.

## 1. HOLD recovery

Before the reboot:

- boot ID was still `b8034c58...`;
- smoke-002 HOLD process PID 14240 (wrapper 14234) was alive;
- report `FAIL_HOLD`, display started, buffers held;
- no other material/FPGA task and no camera owner.

A single normal `sudo -n reboot` was issued. The connection closed; after
reboot the board returned with the new boot ID above. The old HOLD process is
gone, and no `run_jpeg_counter`/preview/FPGA task is present.

## 2. Post-reboot state

| Item | Value |
|---|---|
| FPGA manager | `operating`, flags `0` |
| Boot bitstream (dmesg) | `base.bin` |
| PYNQ state | `GLOBAL_STATE_PRESENT`, `shutdown_ips = {}` |
| PYNQ loaded bit | `/usr/local/share/pynq-venv/lib/python3.10/site-packages/pynq/overlays/base/base.bit` |
| PYNQ bit SHA-1 | `39b937b490015ec0e1d29f18a9143292821f74ad` |
| PYNQ bit SHA-256 | `3239e9cc37ee4993c25f02ae6c97da0b3372f0e722e267d10073e20adc5d6717` |
| FPGA/camera owners | none |
| Camera | HSK USB Camera; MJPG 640x400; no owner |
| CMA | 131,072 KiB total / 30,536 KiB free at evidence time |

Camera raw MJPEG precheck: 3 consecutive frames, all 640x400, 8-bit, baseline
SOF0, 4:2:2, DRI=40, single scan, header 597 bytes — **PASS**.

## 3. Three-state result: UNKNOWN

The read-only classifier returned:

```json
{
  "kind": "UNKNOWN",
  "reason": "loaded overlay identity is not in the known table",
  "loaded_bit_sha256": "3239e9cc37ee4993c25f02ae6c97da0b3372f0e722e267d10073e20adc5d6717"
}
```

After reboot the PYNQ service loads the base overlay and writes
`GLOBAL_STATE_PRESENT`. The current policy's `COLD_BASE` accepts only
`COLD_BOOT_NO_GLOBAL_STATE`; it does not include the PYNQ `base.bit` in the
known table. Per the authorization, **UNKNOWN must stop**, so:

- no upload to `/home/xilinx/jpeg-candidate-20261103c` (directory absent);
- no `Overlay.download`, no candidate load;
- no camera transfer, no frames;
- no candidate DMA/overlay MMIO.

The board is left in the PYNQ base overlay (`base.bit`), no HOLD, no candidate
resources, no other task. No further reboot is needed for this blocked state.

## 4. Policy gap and recommended offline fix (not applied)

The post-reboot state is a **verified boot base**, not an unknown design:

- boot evidence `base.bin`;
- PYNQ `base.bit` at the standard path with pinned SHA-1/SHA-256;
- `shutdown_ips` empty;
- no FPGA/camera owner.

Recommended offline change before smoke-003:

1. Add a pinned PYNQ base entry to the known base table:
   path `/usr/local/share/pynq-venv/lib/python3.10/site-packages/pynq/overlays/base/base.bit`,
   SHA-1 `39b937b4...`, SHA-256 `3239e9cc...`, active name `Pynq-Z2`.
2. Classify `GLOBAL_STATE_PRESENT` + this base.bit + boot `base.bin` +
   `shutdown_ips={}` + no owner as `COLD_BASE`; issue no candidate MMIO before
   `Overlay.download`.
3. Keep the existing UNKNOWN refusal for any other loaded overlay, and keep
   the candidate/RLE known-old path and ownership checks.
4. Add fake tests for the base.bit COLD_BASE acceptance, unknown-overlay
   refusal, and the no-candidate-MMIO-before-load property; repackage with a
   new SHA.

This is a policy extension, not a bypass. Do not hand-edit the PYNQ state or
probe the base overlay's candidate addresses.

## 5. Evidence

Directory: `data/results/jj2026p-board-smoke-003-20261103/`

| File | SHA-256 |
|---|---|
| `postreboot-precheck.json` | `03b3a82d...` |
| `state.txt` | `854ad432...` |
| `dmesg-tail.txt` | `3604f7a7...` |
| `journal-tail.txt` | `51aa9cbb...` |
| `precheck/camera-frame-0.jpg` | `2e34621f...` |
| `precheck/camera-frame-1.jpg` | `ee2cdc6a...` |
| `precheck/camera-frame-2.jpg` | `33655516...` |

The host package remains the offline fix package (manifest
`9a50d991...`, SHA256SUMS `1a6bf853...`); it was not uploaded to the board in
this round. BIT/HWH remain `75577d74...` / `06cf10ca...`.

## 6. Status and limitations

- smoke-003 did not run; no 60-frame report, no display/VSYNC result, no
  resource-recovery result is claimed.
- The board is safely in the PYNQ base overlay; no HOLD and no candidate load.
- `count_accuracy` remains `NOT_MEASURED`.
- No code/package change in this round; no deployment, push, or merge.
- smoke-003 remains pending an offline base.bit policy extension, a new
  package SHA, and separate board authorization.