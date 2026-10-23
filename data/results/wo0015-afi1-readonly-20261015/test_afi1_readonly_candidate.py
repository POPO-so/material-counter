"""Offline fakes only; never open /dev/mem or connect to a board."""
import importlib.util
import pathlib
import struct
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location("afi_candidate", pathlib.Path(__file__).with_name("afi1_readonly_candidate.py"))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def fake_io(monkeypatch, wr=0xF01, rd=1, fail_second=False):
    calls = []
    class Page:
        def __enter__(self): return self
        def __exit__(self, *args): calls.append("unmap")
        def __getitem__(self, sl):
            calls.append((sl.start, sl.stop))
            if fail_second and sl.start == 0: raise OSError("simulated read failure")
            return struct.pack("<I", wr if sl.start == 0x14 else rd)
    opened = Mock(return_value=7)
    mapped = Mock(return_value=Page())
    closed = Mock()
    monkeypatch.setattr(probe.os, "open", opened)
    monkeypatch.setattr(probe.os, "close", closed)
    # Host Windows has no POSIX mmap constants; fakes supply them offline.
    monkeypatch.setattr(probe.mmap, "MAP_SHARED", 1, raising=False)
    monkeypatch.setattr(probe.mmap, "PROT_READ", 1, raising=False)
    monkeypatch.setattr(probe.os, "O_SYNC", 0x1000, raising=False)
    monkeypatch.setattr(probe.mmap, "mmap", mapped)
    return calls, opened, mapped, closed


def test_no_verifier_never_opens(monkeypatch):
    _, opened, _, _ = fake_io(monkeypatch)
    assert probe.read_once()["status"] == "STOPPED_PRECONDITION_UNVERIFIED"
    opened.assert_not_called()


@pytest.mark.parametrize("missing", probe.REQUIRED)
def test_each_missing_gate_blocks_before_open(monkeypatch, missing):
    _, opened, _, _ = fake_io(monkeypatch)
    checks = dict.fromkeys(probe.REQUIRED, True)
    checks[missing] = False
    assert probe.read_once(lambda: checks)["status"] == "STOPPED_PRECONDITION_UNVERIFIED"
    opened.assert_not_called()


@pytest.mark.parametrize("wr,rd,widths,consistent", [(0xF01, 1, [32, 32], True), (0xF00, 0, [64, 64], False), (0xF01, 0, [32, 64], False)])
def test_exact_reads_decode_and_cleanup(monkeypatch, wr, rd, widths, consistent):
    calls, opened, mapped, closed = fake_io(monkeypatch, wr, rd)
    result = probe.read_once(lambda: dict.fromkeys(probe.REQUIRED, True))
    assert result["status"] == "READ_COMPLETE"
    assert result["static_32_consistent"] is consistent
    assert [x["width_bits"] for x in result["registers"]] == widths
    assert calls == [(0x14, 0x18), (0, 4), "unmap"]
    opened.assert_called_once_with("/dev/mem", probe.os.O_RDONLY | probe.os.O_SYNC)
    mapped.assert_called_once_with(7, probe.mmap.PAGESIZE, flags=probe.mmap.MAP_SHARED, prot=probe.mmap.PROT_READ, offset=0xF8009000)
    closed.assert_called_once_with(7)


def test_read_failure_no_retry_preserves_partial_evidence(monkeypatch):
    calls, opened, _, closed = fake_io(monkeypatch, fail_second=True)
    result = probe.read_once(lambda: dict.fromkeys(probe.REQUIRED, True))
    assert result["status"] == "FAIL_STOPPED"
    assert len(result["registers"]) == 1
    assert calls == [(0x14, 0x18), (0, 4), "unmap"]
    opened.assert_called_once()
    closed.assert_called_once_with(7)
