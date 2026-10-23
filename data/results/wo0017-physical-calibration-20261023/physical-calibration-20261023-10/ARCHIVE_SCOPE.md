# -10 evidence archive scope

Git retains the calibration ruler preview, capture manifest, original JSON
receipts and frame results, consumed-lock records, and the complete local-sync
SHA-256 inventory. Derived analysis and replay receipts are retained in the
adjacent offline-analysis and offline-regression directories.

The original raw arrays, NPZ archives, frame-records.jsonl, source snapshots
and auxiliary logs remain unchanged in the local evidence directory at
`C:/wt/wo0017-physical-calibration-20261023/data/results/wo0017-physical-calibration-20261023/physical-calibration-20261023-10`.
They are not included in this Git commit. The SHA-256 inventory describes the
complete local evidence, not a promise that every listed file is in Git.
The 487 temporary H3 input images remain excluded by the existing ignore rules.

A receipts-only checkout skips the optional sealed 300-frame replay test.
Replay requires the complete original evidence directory; its loader still
validates frame continuity, pixel hashes and configuration identity. Historical
verification receipts describe their recorded source hashes and runs; they do
not certify later integration changes or new board behavior.
