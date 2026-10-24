# Touching split remediation and offline acceptance

## Implementation plan

- Preserve baseline `6568a278` and incoming `8c603799` in the review worktree.
  Do not change sealed evidence or access board hardware.
- Add failing regression tests for default-off splitting, independent opt-in,
  a 280-pixel minimum split area, large touching positive masks and 150–250-pixel
  single-object negatives. Include CLI/config identity and preview overlay tests.
- Implement explicit `enable_touching_split=False` and `min_split_area=280`
  options. Bind options into counting configuration and the existing lock gates;
  include the helper in runtime hashes and active deployment/package manifests.
- Compare all non-timing FrameResult fields against baseline on the sealed
  300-frame Binary8 archive, both disabled and explicitly enabled. Inspect frame
  167 and preserve the original NPZ SHA-256. Keep physical accuracy unverified.
- Run focused tests, full `scripts python` regression and delivery verification.
  Commit validated sources on a dedicated branch, record fixed-source evidence,
  append the ledger, then merge into local main and verify the resulting state.

## Acceptance results

- Runtime binding now includes `split_touching.py`. No separate active package
  file-list builder was found; `scripts/wo0016/README.md` lists the dependency.
- `enable_touching_split=False` and `min_split_area=280` are explicit counting
  configuration fields and therefore enter the existing permit/lock identities.
  CLI options are `--enable-touching-split` and `--min-split-area`.
- The helper requires area >=280, bounding-box aspect ratio >=1.35, a narrow
  solid neck, and two connected children. The counter requires child area at
  least half the configured threshold (140 at the default threshold).
- The preview keeps the X=160 annotation and draws active rows `[20,220)`;
  FrameResult retains logical ROI `[0,240)` for compatibility.
- Synthetic labeled positives cover solid and hollow touching pairs in both
  orientations, with guarded and legacy clustering. Negatives include areas
  150, 160, 170, 210 and 250. Sibling split children cannot be clustered back
  together by either legacy clustering path.
- Focused regression: **159 passed**. JUnit and log are local at
  `C:/wt/review8c-evidence/remediation-unit-final.xml` and `.log`.
- The sealed NPZ SHA-256 is
  `1cac1bdfe0c85677cb4f723757cd1609093dd93e00697c41f31b08d013e432aa`.
  Its manifest is independently pinned to
  `1eab3ebb7775cd4cdacc6a3b41213c1ce394c9c12636c1088f70b5676cfb66df`.
  The original algorithm settings must match current full-width-mixed defaults
  after removing the new opt-in fields; otherwise the verifier rejects replay.
- All 300 frames match baseline `6568a278` with splitting disabled **and** with
  splitting enabled at threshold 280: **0 differing frames, total 12** in each
  mode. Comparison includes every non-timing FrameResult field, associations,
  and rejected detection/contour geometry. Frame 167's area-160 component stays
  intact. The verifier fails if either mode changes this sealed sample.
- Seven alternating host-only timing rounds measured medians of baseline
  0.167418, default-off 0.167087 and enabled 0.171461 ms/frame in the precommit
  replay. These are mask algorithm costs, not board throughput or 15 FPS proof.
- `verify_delivery.py`: **PASS**, 319 files / 5 batches, hardware_actions=0.
- The initial development full-suite run retained **831 passed, 2 failed,
  6 skipped, 5 subtests passed**: one missing ignored H3 image set and one
  Windows HTTP rejection connection reset. All 487 H3 images were subsequently
  copied read-only and hash checked, remaining ignored. The HTTP handler now
  drains only bounded small rejected POST bodies before returning the original
  403/415 response, preserving disabled controls and origin checks. The focused
  regression above includes the formerly failing HTTP test.

Full fixed-source regression and final integration results are recorded as a
separate append-only benchmark ledger entry after the source commit. Local
development failures remain in `C:/wt/review8c-evidence/remediation-full.xml`
and `.log`; they are not replaced by later successful runs.

## Reproduction

```powershell
python scripts/wo0016/verify_touching_split_replay.py --evidence-root C:/wt/wo0017-physical-calibration-20261023/data/results/wo0017-physical-calibration-20261023/physical-calibration-20261023-10 --baseline-ref 6568a278 --output C:/wt/review8c-evidence/remediation-replay-fixed --rounds 7
python -m pytest -q scripts python
python scripts/delivery/verify_delivery.py
```

No board deployment, camera sampling, DMA, Overlay or lock modification was
performed. Physical object accuracy remains NOT_VERIFIED; the new mode remains
experimental and opt-in. Preserving the sealed sample does not prove improved
accuracy on independently labeled real touching materials.
