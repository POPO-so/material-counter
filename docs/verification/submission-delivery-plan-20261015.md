# Competition delivery implementation plan

**Goal:** Publish existing verified work in reviewable dependency batches, preserving the original checkout and evidence.

**Architecture:** Keep PR #1 and its dependent ROI PR #2 unchanged. Build a separate delivery chain from PR #1's exact head, selecting source and evidence by explicit path. Keep proposals Proposed and board receipts historical.

**Tech Stack:** Git/GitHub CLI, Python/pytest, existing Vivado reports and SHA-256 manifests.

1. Snapshot original HEAD/index/status; fetch GitHub default branch and PR heads. Audit actual source and evidence versions.
2. Deliver extended threshold verification and morphology; deliver PS reference plus WO-0011/0012 candidate studies; deliver WO-0013 MM2S; deliver WO-0014 offline, timing, missing hardware build dependency files and selected board evidence. Each batch has a machine-readable path/hash inventory and explicit limitations. Do not publish caches, personal configuration or dispatch archives.
3. Implement the one new fix in `python/synth/generate.py` and `python/synth/tests/test_generate.py`: reject nonempty destinations before writing. RED must demonstrate old-frame retention/overwrite risk; GREEN must preserve all existing destination bytes. Fresh generation must match the old generator byte for byte. Preserve H3 rules/spec/scene.
4. Verify curated source bytes against their originals; check prior report/source hashes, both hardware pair identities and RTL pins. Run only portability/dependency and changed-scope tests; do not repeat sufficiently evidenced XSim or implementation builds.
5. Produce the progress matrix, unresolved audit items, reproducible commands and milestone priorities. Commit explicitly selected files, push new branches, create draft PRs and attach them. Never push main, merge, rewrite existing branches or access PYNQ.

Acceptance: original checkout/index untouched; explicit source-version evidence for each claim; each published branch has only its batch changes; new generator regression passes with fresh-output equivalence; remaining board/architecture gates are stated without implying approval.
