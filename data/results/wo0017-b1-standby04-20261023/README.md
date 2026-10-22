# WO-0017 B1-04 preflight standby

Verification date: 2026-10-23, Asia/Shanghai. Status: **READY, waiting for the operator browser click**. This is a preflight standby, not a completed hardware sample.

The new isolated deployment is `/home/xilinx/wo0017/formal-b1-20261023-04/`. It contains the canonical CLI entry fix from `2fb8c5ee`, plus an optional named READY selector needed to issue a fresh Guard without overwriting the existing `ready.json`. All twelve runtime files were checked against local SHA-256 values; outer preparation and launch wrapper hashes are separately recorded in `deployment04.json`. The original Stage 1 root-02 receipt is copied as identical bytes into a new root-owned private evidence directory; its pinned SHA remains unchanged.

## Admission and standby verification

- Boot remains `35983d66-bb1d-4a8a-85e5-802170825515`; `main` and `origin/main` remain `1d32593610c2643b3f022ce048d31f70fd7a4417`.
- Predeployment CmaFree was 112080 KiB; after runner import 107300 KiB; after preparation 106124 KiB; running-standby verification recorded **104300 KiB**. The 32768 KiB threshold was retained. No cache recovery was performed in this operation.
- Both MM2S/S2MM DMASR registers read `0x00000001`. `/dev/video0` and `/dev/fpga0` have no owner. V4L2 reads manual exposure mode 1, exposure 50 and brightness 0.
- PID **30071** listens only on `127.0.0.1:8081`; 8080 is not opened during standby. `/status` reports `READY`, no countdown and no sampling elapsed time.
- Python import tracing records no `pynq`/`pyxrt`/`xrt` imports; the standby process has no camera/FPGA/DMA device file descriptors. Permit remains unconsumed, `capture` does not exist, and no browser POST was sent by the agent.
- All 93 pre-existing files under old deployments `-01/-02/-03` and the prior B1 Guard/attempt namespace retain their SHA-256 values. No history file or marker was deleted, overwritten or reset. The new records use exclusive file creation.

## New immutable records

READY Guard:

```text
/var/lib/wo0017/35983d66-bb1d-4a8a-85e5-802170825515/b1/ready-b1-empty-04.json
8311d639b3bfd3daef4150f1258939040333cde53df8f6ead2b5960425c51266
```

Single-attempt Permit:

```text
/var/lib/wo0017/35983d66-bb1d-4a8a-85e5-802170825515/b1/attempts/b1-empty-04/attempt-permit.json
5122deb15964ff5d26aa13589dfb7e10f5423601554919e31db72fcd836202fc
```

The new `--wo0017-ready-id b1-empty-04` selects `ready-b1-empty-04.json` and binds that ID into the READY identity. ID syntax is limited to 1-32 ASCII letters, digits, `_` and `-`; omitting the option retains the legacy path. Preparation, preflight standby and actual execution use the same context/path. The Permit binds the exact READY file bytes, all twelve runtime hashes, unique attempt ID, current Boot/manifest/BIT/HWH/Stage 1 receipt and capture/counting configuration.

B1 is fixed at 115 frames, expected count zero and `empty_belt`. Raw capture is required at the new deployment's `capture/` path, with queue depth 2, schema 1 and budget **100663296 bytes (96 MiB)**. `frames_gray8_binary8.npz` must contain the paired `raw_gray8` and `raw_binary8` arrays. No B2 Permit was issued.

## Operator handoff

The fresh one-time passcode is provided separately in chat; it is excluded from this repository. Its board launch log is root-owned mode 0600. Keep the PC's port-forward command running:

```powershell
ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -N -L 127.0.0.1:18081:127.0.0.1:8081 -L 127.0.0.1:18080:127.0.0.1:8080 xilinx@192.168.2.99
```

Open `http://127.0.0.1:18081/`, refresh the page, enter the new passcode and confirm the belt is empty before clicking. The click authorizes the actual runner to revalidate identities/resources, consume this attempt, admit DMA buffers and access the loaded Overlay with `download=False`, open the camera and proceed through the countdown to the finite B1 capture. Keep the belt empty throughout. Preview becomes available at `http://127.0.0.1:18080/` after the actual runner starts. Motor control is not performed by this launcher.

Standby has a 3600-second timeout. On timeout or rejection, retain the new marker/records and stop; do not reset or retry this package automatically. No physical light or camera position was changed during this deployment.

## Validation and evidence

Fresh focused regression: **143 passed, 3 skipped** (`pytest-gates.xml`). This includes the subprocess script-entry gate-identity regression from `2fb8c5ee`, named/default READY paths, invalid path characters and named READY identity-drift rejection. The skipped cases concern POSIX UID/mode checks unavailable on Windows; real board admission separately validated the private root-owned files. Independent read-only review found no signing blocker and confirmed legacy READY preservation and exact READY hash binding.

`predeploy-readonly.json`, `deployment04.json`, `prepare04.txt`, `preparation-receipt.json`, `standby-verification.json` and its terminal copy record the operation. `history-hashes-before.json` records old file hashes; old private log contents are not exported. Local raw READY/Permit copies match their board SHA-256 values. Standby verification does not claim that the subsequent hardware run or physical accuracy acceptance has passed.
