# Repaired WO-0013 artifact: not board-tested

Old AUX reset polarity was active low while its input was tied low. This new build explicitly makes AUX reset active high while retaining tie-low, so it is inactive.

Do not run dma_replay_verify.py, run_replay(), or the old PYNQ_README.md replay example as a diagnostic step. Hardware access still requires the user approvals documented in docs/verification/wo-0013-phase-a-safe-diagnostic-20261014.md.

Prepared loader defaults to SHA256/HWH checks only:

    python load_reviewed_overlay_only.py .

After the user separately approves one load, upload this complete export into a NEW board directory and verify hashes. The user must enter sudo credentials themselves. Only then use the loader with --load-reviewed-overlay. It stops after Overlay download returns and never constructs Monitor/DMA MMIO or runs DMA. Capture its output in the user's terminal. Do not automatically repeat it or recycle its marker after a failure.

Any later Monitor MMIO read needs separate informed approval. A single AXI read may block the CPU/kernel; Python/SSH/ping timeouts do not make it safe.
