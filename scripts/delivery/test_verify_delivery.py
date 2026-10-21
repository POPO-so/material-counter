from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_delivery


def test_check_enforces_declared_byte_count(tmp_path, monkeypatch):
    payload = b"intact record\n"
    (tmp_path / "record.txt").write_bytes(payload)
    monkeypatch.setattr(verify_delivery, "ROOT", tmp_path)

    digest = hashlib.sha256(payload).hexdigest()
    verify_delivery.check("record.txt", digest, expected_bytes=len(payload))
    with pytest.raises(RuntimeError, match="Byte count mismatch"):
        verify_delivery.check("record.txt", digest, expected_bytes=len(payload) - 1)
