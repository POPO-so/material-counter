from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import board_preflight as preflight  # noqa: E402
import prepare_board_bundle as packager  # noqa: E402


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_packager_rebinds_manifest_and_receipt_to_explicit_boot(tmp_path):
    boot = "35983d66-bb1d-4a8a-85e5-802170825515"
    package = tmp_path / "package"

    result = packager.prepare(package, boot_id=boot)

    manifest_path = package / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    alias = json.loads((package / "wo0015-manifest.json").read_text(encoding="utf-8"))
    assert manifest["boot_id"] == alias["boot_id"] == boot
    assert result["manifest_sha256"] == _sha(manifest_path)
    assert _sha(package / "wo0015-manifest.json") == result["manifest_sha256"]
    assert preflight.validate_manifest(
        manifest_path, result["manifest_sha256"], approved_boot_id=boot
    ).boot_id == boot


def test_packager_cli_exposes_boot_id_argument():
    args = packager._parser().parse_args([
        "--out", "package", "--boot-id", "35983d66-bb1d-4a8a-85e5-802170825515"
    ])
    assert args.boot_id == "35983d66-bb1d-4a8a-85e5-802170825515"
