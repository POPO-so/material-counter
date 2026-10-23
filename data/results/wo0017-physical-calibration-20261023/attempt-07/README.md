# WO-0017 Physical Calibration Attempt -07

- Commit: `3e88de7f67817cf20547d7183570059167ab13cd`
- Calibration ID: `physical-calibration-20261023-07`
- Worker Python: `/usr/local/share/pynq-venv/bin/python3`
- Deployment target: `/home/xilinx/wo0017/physical-calibration-20261023-07`
- Calibration lock: `/var/lib/wo0017/calibration/35983d66-bb1d-4a8a-85e5-802170825515/physical-calibration-20261023-07/calibration-lock.json`
- Frame limit: 300; full-width-mixed; source Gray8 and Binary8 raw arrays enabled.
- The session is calibration-only and does not issue or consume a formal acceptance Permit.
- Attempt -06 and its consumed marker are retained unchanged.

`payload/` contains only the reviewed runtime Python files. `runtime-sha256.json` and `SHA256SUMS.txt` record their SHA-256 values.
## -07 standby verification

- Fix root cause: the `-06` launcher pinned the worker to `/usr/bin/python3`, where `pydantic` is absent. The PYNQ virtualenv contains both PYNQ 3.1.1 and pydantic 1.9.1; the new pre-lock gate runs the exact `/usr/local/share/pynq-venv/bin/python3` and `audit_environment()` before issuing the lock.
- Fix commit: `3e88de7f67817cf20547d7183570059167ab13cd`.
- Board dependency/audit check passed with exit code 0 and `board_accessed=false`, `mmio_opened=false`, `overlay_loaded=false`.
- `-07` calibration lock SHA-256: `a4dedadf25bfb51f76971354fa44dfbfd7dd4f1ab313da206f653c2f8d85882a`; it remains READY and unconsumed. No formal Permit was issued or consumed.
- Standby reached READY on loopback port 8081. The existing SSH tunnel serves `http://127.0.0.1:18081/`. Sampling has not started. The one-time passcode is intentionally not stored in this evidence directory.
- Latest standby readback: Boot ID unchanged; `standby-verification.json` records CmaFree 67,184 KiB; MM2S/S2MM DMASR `0x00000001`; 640x400 MJPG at 60 FPS, manual exposure 50 (5 ms), brightness 0; camera and FPGA device unowned.
