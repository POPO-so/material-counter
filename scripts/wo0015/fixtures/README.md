# WO-0014 H3 source manifest fixture

`wo0014-h3-source-manifest.json` is the original WO-0014 source manifest recovered byte for byte from the third parent of dangling stash commit `71c3917dc2db8b5724c5cb6cff639912edff569f` (third parent `04b3b8ee481c5bf332b5c864c5c36729bfdcc99b`). Its original repository path was `data/results/wo0014-offline-20261014/vectors/manifest.json`, Git blob `64fe82a671cb885046d4b0f6e845a4be68aa676b`, size 192452 bytes, SHA-256 `8d123469853ad2425e03d91590aa7473de4ca72a04fffaa6bc2d8964b377317d`. The same bytes and source proof are archived under `docs/verification/wo0016-asset-archive-20261022/`.

The offline WO-0015 vector test pins the fixture byte count and SHA-256, then validates all 487 referenced H3 PNG paths, image metadata, and file hashes against the local dataset. This is a canonical source input for offline regression; it does not change or supplement any Stage B-2 physical-material acceptance evidence.
