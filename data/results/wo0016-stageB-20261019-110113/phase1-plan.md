# WO-0016 Stage B Phase 1 Implementation Plan

**Goal:** Audit the cold-boot environment and prepare calibration/batch analysis without bypassing hardware admission.

**Architecture:** Reuse the clean Stage A worktree at 5926820. Preserve deployed firmware, historical executors, manifests, receipts and guards. Collect OS/V4L2/file evidence read-only; keep new analysis offline until same-boot admission is established.

**Tech Stack:** PowerShell/OpenSSH, POSIX shell, Python, pytest, NumPy/OpenCV.

1. Audit HEAD, source SHA-256 and the WO-0015/0016 regression suite with existing fixtures. Record missing fixtures as failures; do not reconstruct provenance.
2. Pin authorized boot 58e6af6c-6660-4d44-b0c5-e18f4a564202. Read CMA, process ownership, camera controls, firmware hashes, service properties, receipts and guard state. Never import PYNQ or access MMIO for this audit.
3. Add isolated stageb_analysis.py, its focused tests and STAGEB_PHASE1.md. Use test-first verification for grayscale metrics, ROI/FPS/threshold overlays and timestamped batch reports. No live camera/DMA integration or changes to existing executors.
4. Review the actual diff and run focused tests. Write phase1-audit.md/json and preserve raw evidence beside them.
5. Execute 3-frame smoke, 5-second empty-belt sampling and live preview only if all real hardware gates pass. Otherwise report NOT RUN with exact blockers, without resetting guards, changing the pinned boot constant, or downloading Overlay.

The new analysis component must label offline inputs, absent hardware observations and short sampling intervals explicitly. Physical addresses 0x15880000 / 0x15860000 are requested buffer gates, not evidence of current allocations. No material feeding is authorized before operator visual confirmation.
