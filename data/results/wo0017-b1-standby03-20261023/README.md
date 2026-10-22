# WO-0017 B1 preflight standby, 2026-10-23

Status at verification: **READY, waiting for the operator browser click**. PID 27585 listens only on `127.0.0.1:8081` on the board. No browser start request was sent. The sampling Permit is unconsumed, no capture directory exists, and both MM2S/S2MM DMASR registers remain `0x00000001`.

Boot remains `35983d66-bb1d-4a8a-85e5-802170825515`. Local `main` and `origin/main` remain `1d32593610c2643b3f022ce048d31f70fd7a4417`. The original workspace and old preparation directories were preserved.

## Authorized cache recovery and import evidence

One authorized cache recovery was executed using `os.sync()` followed by writing `3` to `/proc/sys/vm/drop_caches` as root through passwordless `sudo -n`. No system process was killed. `/dev/video0` and `/dev/fpga0` had no owner before recovery.

| KiB | Before recovery | Immediately after recovery |
|---|---:|---:|
| MemFree | 42716 | 375144 |
| Cached | 330272 | 59204 |
| MemAvailable | 430192 | 430604 |
| CmaTotal | 131072 | 131072 |
| CmaFree | 21404 | 121556 |

This supports recoverable cache/movable-page occupancy of CMA frames. It does not identify every page or establish the cause of every earlier CMA change.

The new preparation process recorded CmaFree 114564 KiB before importing the runner, 111444 KiB after import, and 110340 KiB after preparation. At all three checkpoints, `pynq`/`pyxrt`/`xrt` module lists and hardware device file-descriptor lists were empty. The import did not reproduce the historical approximately 18 MiB CMA drop and did not open an XRT/PYNQ device. The final running-standby snapshot recorded CmaFree **108504 KiB**, with MemAvailable 396700 KiB. Python import tracing likewise showed no PYNQ/XRT module imports; the standby process had no camera/FPGA/DMA device handles. The 32768 KiB threshold remains unchanged.

The authorized new `-02` preparation was rejected before READY/Permit issuance because the original root-owned Stage 1 receipt had a UID1000 parent directory. Both `-01` and `-02` remain intact. The successful `-03` package stores a root-owned private snapshot of the original receipt at `stage1-evidence/receipt.json`; its original bytes and pinned SHA-256 `b3dfa4788dac8ac3b1b867846fe7f654cc41ef1e49de29f5facd161994fa7d74` are unchanged. It is the existing Stage 1 evidence, not a newly manufactured PASS. The validator was not relaxed. Recorded SHA-256 checks confirm all previously existing files under `-01` and `-02` were preserved during `-03` preparation and launch.

## Issued records

READY Guard:

```text
/var/lib/wo0017/35983d66-bb1d-4a8a-85e5-802170825515/b1/ready.json
387f2baf4542711c4459d60d7977a103e65ef7bd79293378fc1f50eff18fedff
```

Single B1 Permit:

```text
/var/lib/wo0017/35983d66-bb1d-4a8a-85e5-802170825515/b1/attempts/b1-empty-03/attempt-permit.json
c229a1ef7926828c06744e93959c60daedb09393e5a23123662506d3a5ee8405
```

The records bind the current Boot, manifest/BIT/HWH/Stage 1 receipt, unchanged twelve-file runtime hash map, B1 115-frame/zero-object profile, counting configuration and new absolute capture path. Capture budget is 100663296 bytes (96 MiB), queue depth 2, schema 1. The paired `raw_gray8` and `raw_binary8` arrays are mandatory in `frames_gray8_binary8.npz`. B2 has not been issued or started.

Runtime directory: `/home/xilinx/wo0017/formal-b1-20261023-03/`.

Outer wrapper hashes, recorded independently of the twelve-file runtime map:

- `prepare_b1.py`: `77247b08f1814086b6a9a3f6763329e295f0596f15ad58784069d65d2a4d192e`
- `launch_b1.py`: `d54091fd50745fe6ebe17b9d247d3b0af726351bd92cbd9919dc3c20a0748a01`

## Operator handoff

The one-time passcode is delivered separately in the chat. It is not stored in this repository or returned by the HTTP status API. The board launch log is root-owned mode 0600.

On the PC, keep this PowerShell command running:

```powershell
ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -N -L 127.0.0.1:18081:127.0.0.1:8081 -L 127.0.0.1:18080:127.0.0.1:8080 xilinx@192.168.2.99
```

Open `http://127.0.0.1:18081/`, enter the separately supplied passcode, confirm that the belt is empty, then click the single-run confirmation. Clicking authorizes the actual hardware runner; it revalidates resources and identities, consumes this attempt, admits CMA buffers and uses the loaded Overlay with `download=False`, then opens the camera and runs the preparation/stabilization countdown before 115-frame B1 sampling. Keep the belt empty throughout. The launcher does not control the conveyor motor. Preview will become available at `http://127.0.0.1:18080/` only after the hardware runner starts.

The standby waits up to 3600 seconds from reaching READY. On timeout or any failure, preserve the launch marker and evidence; do not delete markers, retry this launch package or automatically start sampling. Any new launch requires separately reviewed authorization.

## Verification and evidence

Fresh focused gate regression: **138 passed, 3 skipped**; the skips are Windows-inapplicable POSIX UID/mode cases. Successful board-side validation separately exercised the real root-owned private files. No production runtime source changed in this operation.

- Cache recovery and the rejected `-02` import/ownership evidence: sibling `wo0017-b1-standby02-20261023/`.
- New deployment and import checkpoints: `deployment03.txt`, `prepare03.txt`.
- READY/Permit raw copies: `ready-guard.json`, `attempt-permit.json`; their byte hashes match board files.
- Final standby/Boot/CMA/DMA/FD/ownership verification: `standby-verification.json`, `standby-verification.txt`.

Raw terminal logs retain original whitespace; no private passcode log is included.
