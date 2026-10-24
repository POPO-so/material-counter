"""Verify an offline-built source package; never opens devices or starts a run."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def verify(root, manifest_sha256):
    root = Path(root).resolve(strict=True)
    raw = (root / "PACKAGE.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest_sha256:
        raise ValueError("PACKAGE.json SHA-256 differs from the independently pinned receipt")
    manifest = json.loads(raw)
    for name, expected in manifest["files"].items():
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("unsafe package path: " + name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("source SHA-256 mismatch: " + name)
        if path.suffix == ".py":
            compile(path.read_bytes(), str(path), "exec")
    return {"status": "OFFLINE_PASS", "scope": "package byte hashes and host syntax only",
            "source_main": manifest["upstream_main_commit"], "files": len(manifest["files"]),
            "python": sys.version, "hardware_status": "NOT_VERIFIED", "hardware_actions": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.root, args.manifest_sha256), indent=2))
    except (OSError, ValueError, SyntaxError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
