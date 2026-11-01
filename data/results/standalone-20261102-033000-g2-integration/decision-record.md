# G2 SW1 snapshot control candidate build decision

- User direction: add PYNQ-Z2 SW1 bit 5 as a hardware gate for crossing snapshots while preserving BTN1 diagnostic mode and the CLI switch.
- Build variant: `OFFLOAD_CANDIDATE=1`, preserving BTN0–BTN3 on GPIO bits 0–3 and mapping SW0/SW1 to bits 4–5 through the two-stage switch synchronizer.
- Source: task worktree `feat/材料计数SW1快照开关`, source implementation commit `a90d2c1`, plus the current SW1 test/helper changes captured by the build's source hash records.
- PYNQ source: `C:\FPGA\dependencies\pynq-v3.1-sw1`, locked commit `19ed17d4fd4ea71f95a97d30f8ccd0d62b8dbaaa`; 32/32 locked file hashes verified.
- Video IP: `.runtime/builds/sw1-video-ip-20261102`; Vitis 2026.1 C simulation, synthesis, and IP export completed for `color_convert` and `pixel_unpack`.
- Build output: this fresh directory. Monitor simulation output: `.runtime/builds/sw1-monitor-sim-20261102`.
- Build settings: Vivado 2026.1, one implementation job, one maximum thread. This produces a candidate BIT/HWH only; it does not authorize or perform PL deployment.
