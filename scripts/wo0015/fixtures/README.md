# WO-0014 H3 source inventory fixture

`wo0014-h3-source-manifest.json` is a reconstructed offline test fixture. The original
ignored file at `data/results/wo0014-offline-20261014/vectors/manifest.json` was not
present in the main checkout, Git history, or any inspected archive. The tracked
WO-0015 generated vector manifest records the original source-manifest SHA-256 as
`8d123469853ad2425e03d91590aa7473de4ca72a04fffaa6bc2d8964b377317d`.

The fixture inventory was rebuilt from the 487 existing H3 Gray8 PNGs. Each record
contains the path, SHA-256 of the actual PNG bytes, and verified 320x240 `L` image
metadata. Offline vector tests re-read and hash all 487 files. The fixture file
SHA-256 is `63b307d5e6bd811e08a2f2558a30cbae7acf5bd513f3683f0abf74d2abc1dc86`.

This is not the original manifest's bytes and is not experimental evidence. It is
only a hash-checked input inventory for deterministic offline vector regression.
