# WO-0013 Phase A Level 2 accepted evidence

**Board-tested Level 2 PASS**: one 76,816-byte MM2S send on PYNQ-Z2, 19,204 accepted PL beats, one TLAST, matching first/last words and 32-bit additive checksum, zero DMA/Monitor errors. Post-test Ping/SSH/Linux checks passed; no reset, reload or retry occurred in this run.

Authoritative current evidence is [the measured report](../../data/results/wo0013-pagemap-policy-20261014-120205/verification-report.md), [raw registers](../../data/results/wo0013-pagemap-policy-20261014-120205/raw-registers.json) and [terminal record](../../data/results/wo0013-pagemap-policy-20261014-120205/complete-terminal-transcript.txt). The report separates executed script versions from the locally hardened pagemap policy. Older FAIL/NOT RUN reports and the export-time `board_tested=false` manifest remain historical records, not current verdicts.

The reviewed BIT/HWH pair is in `data/results/wo0013-resetfix-20261014/export/`. BIT SHA-256: `47e6cd65c552244236f2564c11cba3e81a9a0c6aced0c9974c04de78256da3f7`; HWH: `a31d71f8e7004de710fa4da07078b80e2a0a8b40ff52ac523d322f21362df1fb`.

The generic `python/boardtests/wo0013/dma_replay_verify.py` is an earlier reset/recovery implementation and **was not the entry point for this measured run**. It downloads an Overlay and resets/re-arms on errors; do not invoke it under a no-reset/no-reload authorization. The measured one-shot source and its dependencies are preserved with the evidence. No credentials or SSH authentication/dispatch helpers are committed. Hardware actions require a new explicit authorization.

This experiment's payload has no header: 76,800 Binary8 bytes followed by four 32-bit metadata words, little-endian DDR byte order; DMA TLAST marks the entire packet, not image EOL. It is not an RGB888 image packet. No S2MM full-frame readback, production-chain validation, repeated-run endurance, Route decision or Phase B acceptance is implied.

Recreation through `scripts/wo0013/run_batch.tcl` requires Vivado 2026.1, the installed PYNQ-Z2 board repository and the separately installed Vivado IP configurator skill library at `.agents/skills/vivado-ip-configurator/lib/ipcfg.tcl`. Generated Vivado projects/cache and the machine-local skill installation are excluded from this commit. HDL and exported BD recreation Tcl are retained.
