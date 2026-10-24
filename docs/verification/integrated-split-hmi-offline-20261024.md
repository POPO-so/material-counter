# Hollow-nut split and live monitor integration

Date: 2026-10-24, Asia/Shanghai. Branch: `codex/integrate-split-hmi-20261024`.
This work uses Windows host processing and synthetic or saved image inputs.
No board connection, camera acquisition, conveyor action, Overlay, MMIO or DMA
operation is part of this verification.

## Integrated sources

| Development line | Source commit | Integration |
|---|---|---|
| Adjacent hollow nuts | `39bba492d5e56e58abda8e1d511189266fe3253d` | Starting commit, fetched from remote main |
| Persistent live monitor | `87788922` | Merge commit `143f5489` |
| Conveyor telemetry and visualization | `034d980e` | Merge commit `e9d73f43` |
| Teammate's complete camera captures | `59eea586` | Merge commit `5be92d83` |

The original checkout was clean at `6ba03813` on the conveyor branch. Local
`main` was at `a745f9f1`; fetching remote `main` supplied the requested split
commit. The integration branch starts at that exact commit. All three source
commits remain ancestors. Local `main` and the remote are not updated by this
integration. The named conveyor tag was absent locally; the explicit commit
was used. Existing board reports inherited through the merges remain historical
evidence and do not authorize new hardware operations. The subsequent data
commit supplies three full 1800-frame archives through Git LFS, each
138,277,036 bytes; downloaded bytes match all three pinned archive hashes.

## Conflict resolutions and interface checks

The live-monitor merge conflicted in `split_touching.py`, `stream_counter.py`
and `run_stream_counter.py`. The conveyor and data merges had no conflicts.

- Splitting remains explicitly enabled with `--enable-touching-split`; the
  default is off. `min_split_area` stays at least 280 px. The candidate upper
  bound is 900 px, independent of the 260 px proximity-fragment merge limit.
  The Python counter defaults to a 900 px split cap; explicit
  `max_split_area=None` retains the historical merge-cap fallback.
- The enclosed-hole method may return two or three children. It runs before
  the elongation check used by the narrow-neck fallback, so a compact
  three-hole cluster remains eligible. The counter retains a minimum child
  area of half the configured split threshold, alongside the hole method's
  relative-area and shape checks.
- Each child's foreground pixels supply its area, full-frame centroid and
  bounding box. Sibling origins prevent proximity clustering from rejoining
  them. Tracking assigns its own integer IDs; the monitor resolves crossing
  events against the corresponding same-frame track.
- The full-width profile keeps active scan rows `[20,220)`, logical frame
  coordinates and the X=160 crossing line. Finite and persistent runners use
  the same counting configuration. Preview and telemetry consume child tracks.
- Measured preview text bounds now keep adjacent child labels apart and
  within the image at its right edge. Count/PIP/line annotations reserve their
  space. Track geometry and connectors are drawn before label backgrounds
  and text. This covers the two/three-child layouts; a generic overcrowded
  frame can still exhaust available label rows.
- Existing camera-mask regression tests now opt into splitting explicitly.
  The historical sealed-mask verifier excludes the new split cap when
  comparing current defaults with the older manifest configuration. The
  sealed data and its hashes are not changed.

## Test scope and evidence

The first merged source run completed **915 passed, 5 skipped, 5 subtests
passed** in 206.33 seconds, including browser, HTTP, counter, visualization,
injected board-contract and bounded-retention tests. These are host tests;
injected board contracts do not access hardware. The initial conflict-resolution
regression completed 95 passed. The sealed-manifest compatibility regression
completed 16 passed and 1 skipped in its focused scope.

The first unresolved integration run retained 76 passed and 19 failed;
the split cap and explicit opt-in expectations were then aligned. Label
collision tests reproduced both the overlap and right-edge clipping before
the rendering fix. Two intermediate layering attempts each retained 20 passed
and 1 failed because a trail endpoint was hidden; the corrected rendering
order and label search passed all 21 integration/preview tests. The replay
verifier's eight focused tests pass, including hash rejection, missing inputs,
successful replay, single-nut controls and output-path preservation. Earlier
failures remain in the local audit directory.

Dedicated integration tests use the committed 553 px pair and 885 px triple
camera-derived masks. They exercise child geometry through clustering and
tracking, event areas, observed velocity, recent crossing area, preview labels,
CLI compatibility, split bounds and gap recovery. Moving a fixed saved mask is
a geometry regression, not a repeat of the full camera recording.

The final source commit, final counts, commands, before/after runtime hashes,
JUnit SHA-256 and delivery-integrity result are recorded in the appended
benchmark ledger. Local raw logs and screenshots remain under
`C:/Users/POP/Documents/Codex/repo-audits/integrate-split-hmi-20261024/` and are
not synchronized by Git. This report and the ledger are synchronized artifacts.

The initial root collection failed with five errors from historical test
copies in `data/results/` and generated `output/` checkouts. `pytest.ini` now
defines the maintained source suite as `python/` and `scripts/`. No historical
files were edited or removed. The separately audited vendored skill suite
collected 130 tests and reported two `StopIteration` errors in
`test_catalog_refresh.py` and `test_relevance_evaluator.py`, whose upstream
repository-location assumptions do not hold here. That package is outside
the maintained application suite; this is not an all-files/all-third-party
green claim. Inherited whitespace warnings in vendored skill source were
also retained unchanged.

The five maintained-suite skips are:

| Test | Reason and remaining limit |
|---|---|
| `scripts/wo0015/test_cma_compat.py:205` | Local live metadata artifact absent; no new hardware validation |
| `scripts/wo0016/test_active_rows.py:122` | Sealed -10 raw capture absent; manifest/configuration bridge is tested separately |
| `scripts/wo0016/test_wo0017_capture_profile.py:257` | POSIX permission semantics unavailable on Windows |
| `scripts/wo0016/test_wo0017_capture_profile.py:270` | POSIX ownership semantics unavailable on Windows |
| `scripts/wo0016/test_wo0017_capture_profile.py:282` | POSIX permission semantics unavailable on Windows |

## Full camera replay gate

The originals were initially absent. A later remote refresh found the teammate's
new `59eea586` commit and retrieved all three LFS archives plus their manifests.
The local capture root is `data/recorded/wo0017-touching-20261024/`.

| Capture directory | Window, inclusive frame IDs | Required count | Current integration result |
|---|---|---|---|
| `wo_touching_pair_motion_20261024_03` | 0–900 | 2/2 | PASS; events 834, 834 |
| `wo_touching_three_motion_20261024_02` | 0–99 | 3/3 | PASS; events 34, 37, 37 |
| `wo_touching_three_motion_20261024_04` | 0–79 | 3/3 | PASS; events 25, 28, 28 |

The diagnostic windows contain 1081 frames in total. Later unannotated frames,
including near-camera hands, are outside these counting windows. The disabled
split comparison counts one event in each window. Replay validates each
crossing log's child area, velocity and track ID against its same-frame track.
Actual Gray8 overlay images were rendered and visually inspected. Two
26-frame synthetic single-nut sequences (round and hexagonal hollow shapes)
each retain one entity, count once and have zero false-split frames.

Pinned archive SHA-256 values, in the same order:

```text
0b55c6e1d5a43a4c69702dd89ef14be09c1673cdbc7b8d22b7bf5b8fbe40ce9e
7edb8fcc077a0592b957f485ae6d5b99989bdf61b2d0c17cff7d71afaa2ad9fa
c534ad4334afa725337a03d5ed849e5ae81857b5c8f5bf1c1686b465884ed59d
```

To reproduce with Git LFS files already retrieved, run:

```powershell
.venv\Scripts\python.exe scripts/wo0016/verify_split_hmi_replay.py --capture-root data/recorded/wo0017-touching-20261024 --output C:/Users/POP/Documents/Codex/repo-audits/integrate-split-hmi-20261024/camera-replay
```

The verifier checks the original input hashes, thresholds Gray8 at 128 and
runs the counter, live state and preview renderer. Its software replay rate
is not board throughput. The original Gray8 is not PL Binary8. Reported totals
are aggregate counts against the operator's stated object counts; they do not
establish independent per-object identity accuracy. Synthetic single-nut
controls and the fixed saved masks do not substitute for complete single-nut
camera sequences.

The requested candidate `v1.0-integrated-offline-ready` tag is limited to the
maintained source suite, the specified camera replay windows and synthetic
single-nut controls. Final creation depends on the fixed-source checks and
append-only ledger receipt. The local tag does not authorize deployment or
imply a full independent real-material single-nut/identity acceptance. No push
is authorized in this session.
Board FPS, CMA before/allocation/release measurements, physical scale and new
board latency are N/A in this offline integration.
