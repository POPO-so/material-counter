# G2 fresh build decision record

- Worktree: `feat/优化PS与PL分工`
- Launch HEAD: `063e22c09737048728c08da8b2dbfad7ef805cf7`; implementation RTL/Tcl/XDC has intentional uncommitted S5 remediation and is snapshotted in this result.
- User authorized a new routed validation after freeing memory. At launch, Windows reported about 1.5 GiB free physical memory and 15 GiB free virtual memory; build uses `DISPLAY_BUILD_JOBS=1` and `DISPLAY_MAX_THREADS=1`.
- Fresh, dedicated output: `C:\Users\14173\Documents\material-counter\.runtime\worktrees\ps-pl-offload-20261101\data\results\standalone-20261101-163250-g2-integration`. Existing outputs remain untouched.
- Flow: Vivado 2026.1, `create_g2_integration.tcl` then `build_g2_integration.tcl`, `OFFLOAD_CANDIDATE=1`; PYNQ source and standalone IP from `P:\pynq-v3.1` and `P:\standalone-ip`; monitor sim `Q:\.runtime\monitor-clean-mt2`.
- Build uses the unchanged nonnegative setup/hold timing gate, DRC/methodology gates, exact 4-pin button synchronizer cardinality assertion, exception coverage report, and CDC report. No board deployment.
- RTL/Tcl/XDC/source dependency hashes are recorded in `source-hashes-before.sha256` and will be compared after the run. Candidate RTL snapshots are created in `rtl\`.