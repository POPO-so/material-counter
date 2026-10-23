# WO-0015 PS board executor

This package implements the PS half of the WO-0015 S2MM Binary8 frame-return loop. It is offline-first: `board_preflight.py`, `--mode offline`, `--mode mock`, and `--help` do not import PYNQ or touch MMIO. Only an explicitly authorized board stage imports PYNQ.

The live executor accepts a single reviewed manifest containing three frames. The source-tree default binds every stage to historical boot ID `cb087bbb-932d-4a6e-9600-98dd1c7e5f8d`, 50 MHz, the BIT/HWH hashes, the source hashes for `board_once.py` and `board_preflight.py`, and the exact packet and Binary8 golden hashes. A reauthorized deployment must bind both its manifest and packaged authorization constant to the approved boot; the Retry 3 package uses `822a9c29-37c0-438c-81c4-da998d57073b`. S2MM is 32-bit memory / 32-bit stream with an 18-bit length field; each return frame is 76,800 bytes / 19,200 beats. MM2S remains 64-bit memory / 32-bit stream and sends 230,432 bytes.

## Manifest inputs

The example below shows the historical source-tree default. For an actual deployment, use its reviewed manifest and source hashes rather than copying the example boot identity. Place the following together in one directory, or use paths relative to the manifest:

- `wo15.bit` and its same-basename `wo15.hwh`.
- Exactly three packet files. Each packet is the fixed 230,432-byte WO-0014 RGB packet.
- Three independently generated Binary8 goldens, each exactly 76,800 bytes, containing only `0x00` and `0xFF`.
- `wo0015-manifest.json` with this exact top-level shape:

```json
{
  "schema": 1,
  "work_order": "WO-0015",
  "stage": "S2MM_BINARY8_FRAME_RETURN",
  "build_id": "<finalized-build-id>",
  "boot_id": "cb087bbb-932d-4a6e-9600-98dd1c7e5f8d",
  "part": "xc7z020clg400-1",
  "clock_mhz": 50,
  "bit_path": "wo15.bit",
  "bit_sha256": "<64 hex characters>",
  "hwh_path": "wo15.hwh",
  "hwh_sha256": "<64 hex characters>",
  "dma_base": 1077936128,
  "checker_base": 1136787456,
  "contract": {
    "dma_mode": "simple",
    "include_mm2s": true,
    "include_s2mm": true,
    "include_sg": false,
    "micro_dma": false,
    "dma_length_width_bits": 18,
    "dma_addr_width_bits": 32,
    "mm2s_memory_width_bits": 64,
    "mm2s_stream_width_bits": 32,
    "s2mm_memory_width_bits": 32,
    "s2mm_stream_width_bits": 32,
    "mm2s_dre": false,
    "s2mm_dre": false,
    "hp0_enabled": true,
    "hp1_enabled": true,
    "hp0_data_width_bits": 64,
    "hp1_data_width_bits": 32,
    "hp0_range": [0, 536870912],
    "hp1_range": [0, 536870912],
    "checker_signature": 1464807732,
    "packer_signature": 1464807733
  },
  "executor_sha256": {
    "board_once.py": "<SHA-256 of this exact source>",
    "board_preflight.py": "<SHA-256 of this exact source>"
  },
  "ready_for_board": true,
  "frames": [
    {
      "frame_id": 1,
      "packet_path": "frame-1.packet",
      "packet_sha256": "<64 hex characters>",
      "binary_path": "frame-1.bin",
      "binary_sha256": "<64 hex characters>"
    },
    {
      "frame_id": 2,
      "packet_path": "frame-2.packet",
      "packet_sha256": "<64 hex characters>",
      "binary_path": "frame-2.bin",
      "binary_sha256": "<64 hex characters>"
    },
    {
      "frame_id": 3,
      "packet_path": "frame-3.packet",
      "packet_sha256": "<64 hex characters>",
      "binary_path": "frame-3.bin",
      "binary_sha256": "<64 hex characters>"
    }
  ]
}
```

The hexadecimal address/signature values above are shown as decimal JSON integers because the manifest parser requires JSON numbers. The checker signature is `0x574F3134`, and the packer signature is `0x574F3135`. `executor_sha256` must be regenerated after any source edit. The preflight also re-derives each Binary8 golden independently from its packet before admitting a stage.

## Offline validation

Run from the repository root with the project Python environment:

```powershell
& 'C:\fpga\material-counter\.venv\Scripts\python.exe' scripts/wo0015/board_preflight.py --manifest <manifest.json> --manifest-sha256 <reviewed-manifest-sha256>
& 'C:\fpga\material-counter\.venv\Scripts\python.exe' scripts/wo0015/board_once.py --mode offline --manifest <manifest.json> --manifest-sha256 <reviewed-manifest-sha256>
& 'C:\fpga\material-counter\.venv\Scripts\python.exe' scripts/wo0015/board_once.py --mode mock --manifest <manifest.json> --manifest-sha256 <reviewed-manifest-sha256>
```

The first two commands validate files and the manifest. `mock` exercises the same register sequence and buffer checks against a Python register model; its PASS is simulation-only and is never board evidence. The real generated `export/wo15.hwh` is also covered by an offline validator smoke test when that build artifact is present in the checkout.

## Explicit board stages

The following are exact command forms for a separately approved board run. They were not run while preparing this executor. Run each stage once from the same three-frame manifest and boot, replacing the directory and reviewed digest with the signed values for the packaged artifacts:

```bash
WO=/home/xilinx/wo0015
MANIFEST="$WO/wo0015-manifest.json"
MANIFEST_SHA256='<reviewed 64-character manifest SHA-256>'

sudo python3 "$WO/board_once.py" --mode overlay-only --manifest "$MANIFEST" --manifest-sha256 "$MANIFEST_SHA256" --execute --authorize-stage overlay-only --approve-once --evidence-json "$WO/overlay-only-result.json"
sudo python3 "$WO/board_once.py" --mode dma-single --manifest "$MANIFEST" --manifest-sha256 "$MANIFEST_SHA256" --execute --authorize-stage dma-single --approve-once --evidence-json "$WO/dma-single-result.json"
sudo python3 "$WO/board_once.py" --mode dma-three --manifest "$MANIFEST" --manifest-sha256 "$MANIFEST_SHA256" --execute --authorize-stage dma-three --approve-once --evidence-json "$WO/dma-three-result.json"
```

Run the sequence only while it is within the captain's approved scope; one approval may cover the full ordered sequence. Each invocation still names its exact stage with the matching `--mode` and `--authorize-stage` pair. The stages use three fixed, distinct consumed-once guards under `/var/lib/wo0015-s2mm`: Overlay load, DMA single-frame, and DMA three-frame. There is no guard-directory override. The single-frame PASS receipt is required before the three-frame stage; each receipt is bound to the same boot, manifest, build, BIT, and HWH hashes. Any terminal stage failure leaves a persistent marker that blocks later stages.

`overlay-only` means one `Overlay.download()` followed by exactly two read-only signature MMIO reads at Checker `0x43C20004` and packer `0x43C20060`. It does not open DMA registers or start a transfer. The DMA stages attach to that already loaded BIT and do not create or download another Overlay. The single-frame stage and the three-frame stage are separate runs, so each allocates one MM2S CMA buffer and one S2MM CMA buffer, reuses that pair serially for all selected frames, then releases them only after both channels are Idle with new IOC indications and packer completion.

Before hardware access, the runner checks boot ID, `CmaFree`, live CMA and HP-visible ranges, process device FDs/maps, FPGA-manager state/flags, and PYNQ global state. Before Overlay download, `shutdown_ips` must be an explicit empty dictionary; an absent PYNQ state file is recorded as a cold-boot/no-global-state case. After each stage, the runner rechecks boot ID, manager/process state, loaded BIT identity and the incremental `dmesg` health. It writes the PASS receipt only after these checks pass, then checks boot ID again.

The DMA sequence arms S2MM before MM2S for every frame. RX memory is filled with `0xA5` and synchronized to the device before S2MM starts. TX and RX physical intervals must be disjoint and contained in the admitted range and their assigned HP windows. The runner does not reset the DMA, reload the Overlay, retry a frame, or free buffers while DMA may still own them. If the DMA may be active after an error/timeout, the process enters `FAIL_HOLD` with the buffer objects retained. Failure-marker and evidence-file writes are best effort on this path and cannot bypass HOLD. A completed transfer with a length, Checker, Packer, or pixel mismatch is reported as a started-transfer failure and releases buffers only after the channels have proven idle. Each frame reports foreground count as `Checker output_checksum / 255`, after the Binary8 golden comparison passes. The timeout must be finite and positive and bounds Python register-poll loops; it cannot interrupt a synchronous PYNQ MMIO read that itself blocks.

## Completed RX mismatch diagnostics

After a completed transfer passes DMA/Checker/Packer completion checks, a Golden mismatch saves the full RX bytes, matching Golden bytes, and a JSON diagnostic before either buffer is freed. Files use a stage/frame-specific name and exclusive creation; existing evidence is never overwritten. The JSON includes hashes, the first 64 bytes as sixteen 32-bit RX/Golden words, the first 32 byte mismatches, and separate even/odd word counts for mismatching bytes, all-zero words and all-0xA5 Poison words. It also preserves the failed frame's completion registers and Checker snapshots.

Live stages put these files beside their evidence JSON in a `<stage>-diagnostics` directory. Direct `run_frames` callers can pass `diagnostic_dir`; otherwise the manifest's directory gets `mismatch-diagnostics`. Complete raw, Golden and JSON files are flushed, fsynced and readback-hashed before cleanup. If persistence fails, completed buffers are retained in a separate evidence HOLD: the record keeps `dma_may_be_active=false` rather than calling an idle DMA active. No retry, reset or free is allowed until the evidence failure is resolved by an authorized operator. This is distinct from the existing uncertain-DMA HOLD.

The diagnostics cannot reconstruct earlier failures whose RX bytes were never saved. HWH-declared PS HP width also does not establish the running AFI channel width; any AFI register inspection or change requires separate board authorization.

## CMA source provenance and physical buffer gate

Admission prefers device-tree CMA intervals, then kernel `cma: Reserved ... at 0x...` records or read-only debugfs `base_pfn`/`count`. Exact sources must agree, their total size must match `CmaTotal`, and they must fit the measured 512 MiB System RAM boundary without conflicting with kernel/reserved/peripheral iomem regions. Debugfs alloc/free controls are never accessed.

The captain's Retry 3 Two-Tier decision authorizes `MEMINFO_IOMEM_VERIFIED` when precise CMA metadata is unavailable: `/proc/iomem` must prove System RAM exactly `[0, 0x20000000)`, `CmaTotal` must equal 131072 KiB, and `CmaFree` must be at least 32768 KiB. The permitted DDR safety envelope is `[0x00100000, 0x20000000)`. It is not a measured CMA subregion; audit output keeps `physical_cma_range_known=false`. A missing `cma=` argument is allowed under this decision, while malformed or contradictory explicit boot evidence is rejected. Kernel code/data inside this broad envelope are not treated as proof of CMA placement; actual allocated buffers must separately pass Tier 2.

After allocation and before any register write, both physical spans must fit the admitted ranges and HP windows. Addresses below `0x00100000`, addresses beyond `0x1fffffff`, overlapping spans and incorrect actual byte lengths are rejected. MM2S remains 8-byte aligned; S2MM is 4-byte aligned. PYNQ `physical_address` and `device_address` must both exist and agree. Missing runtime ranges cannot skip this gate. Evidence records physical intervals, actual bytes and CmaFree before allocation, after MM2S allocation, after S2MM allocation and after release. The authorized watermark revision requires CmaFree >= 16384 KiB before allocation, after each allocation, and after release. Global CMA differences and the page-rounded byte count are telemetry only: free pages may rise or stay flat during allocation, and final CmaFree need not equal its earlier value. Allocator-issued physical addresses, exact byte lengths, alignment, DDR bounds and non-overlap remain mandatory before any DMA write. PYNQ addresses must be nonzero integer values; strings, floats and booleans are rejected. Tier 1 still requires CmaFree >= 32768 KiB at stage admission.

The source-tree default boot remains the historical binding shown above. The separately authorized Retry 3 Two-Tier package carries the approved `822a9c29-37c0-438c-81c4-da998d57073b` configuration with regenerated source/manifest hashes. The metadata-only probe lacks precise CMA metadata and `cma=` but meets the newly authorized kernel-accounting Tier 1 conditions. Previous reports retain the former stricter decision. Retry 3 still requires fresh boot, file, ownership, memory and per-stage evidence, and every hardware stage remains one shot.

## Local tests

```powershell
& 'C:\fpga\material-counter\.venv\Scripts\python.exe' -m pytest -q scripts/wo0015/test_board_preflight.py scripts/wo0015/test_board_once.py
```

These tests cover manifest/HWH gates, the real HWH export when present, RX sentinels and cache synchronization, one/three-frame register ordering, CMA range/overlap guards, stage receipts and one-shot guards, process/manager/PYNQ/dmesg audits, and failures with uncertain versus completed DMA state. They do not exercise a physical board or validate a synchronous MMIO call's wall-clock bound.
