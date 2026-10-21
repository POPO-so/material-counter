from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from stageb1_hwh import read_threshold


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_HWH = REPO_ROOT / "data/results/wo0015-hw-50mhz-20261015/export/wo15.hwh"
EXPECTED_HWH_SHA256 = "bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6"


def main() -> int:
    parser = argparse.ArgumentParser(description="Local-only Stage B-1 HWH threshold dry-run")
    parser.add_argument("--hwh", type=Path, default=DEFAULT_HWH)
    parser.add_argument("--expected-sha256", default=EXPECTED_HWH_SHA256)
    args = parser.parse_args()
    raw = args.hwh.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != args.expected_sha256:
        raise SystemExit(f"HWH SHA-256 mismatch: {digest}")
    threshold = read_threshold(args.hwh)
    if threshold != 128:
        raise SystemExit(f"expected THRESHOLD=128, got {threshold}")
    print(json.dumps({
        "status": "LOCAL_HWH_DRY_RUN_PASS",
        "hwh_path": str(args.hwh.resolve()),
        "hwh_sha256": digest,
        "threshold": threshold,
        "board_accessed": False,
    }, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
