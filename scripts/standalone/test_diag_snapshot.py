import json
import time

import pytest

from scripts.standalone.diag_snapshot import (
    SNAPSHOT_SCHEMA, DiagnosticSnapshotWriter, compact_diagnostic_snapshot,
)


def _report(samples=100):
    return {
        "schema": "material-counter.integrated-run.v1",
        "boot_id": "boot",
        "session_id": "session",
        "processed": 10,
        "samples": [{"monotonic": index, "payload": "x" * 40}
                    for index in range(samples)],
        "recoveries": [{"kind": index} for index in range(50)],
        "events": [{"frame": index} for index in range(50)],
        "display_health_events": [{"reason": index} for index in range(50)],
        "frame_trace": [{"frame": index} for index in range(600)],
        "timings_ms": {"transfer_ms": {"p50": 1.0}},
    }


def test_compact_snapshot_is_bounded_and_excludes_frame_trace():
    snapshot = compact_diagnostic_snapshot(_report(), max_samples=40, max_events=20)
    assert snapshot["schema"] == SNAPSHOT_SCHEMA
    assert snapshot["snapshot"] is True
    assert len(snapshot["samples"]) == 40
    assert len(snapshot["recoveries"]) == 20
    assert len(snapshot["events"]) == 20
    assert len(snapshot["display_health_events"]) == 20
    assert "frame_trace" not in snapshot
    assert snapshot["processed"] == 10


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_writer_writes_atomic_json_without_tmp_file(tmp_path):
    path = tmp_path / "session.diag.json"
    writer = DiagnosticSnapshotWriter(path, max_bytes=8192)
    try:
        assert writer.submit({"schema": SNAPSHOT_SCHEMA, "value": 1})
        assert _wait_for(lambda: writer.stats["written"] == 1)
    finally:
        stats = writer.close()
    assert stats["written"] == 1
    assert json.loads(path.read_text(encoding="utf-8"))["value"] == 1
    assert not (tmp_path / "session.diag.json.tmp").exists()


def test_writer_trims_samples_to_the_byte_limit(tmp_path):
    path = tmp_path / "session.diag.json"
    writer = DiagnosticSnapshotWriter(path, max_bytes=2048)
    try:
        snapshot = compact_diagnostic_snapshot(_report(samples=200),
                                               max_samples=200, max_events=20)
        assert writer.submit(snapshot)
        assert _wait_for(lambda: writer.stats["written"] + writer.stats["dropped"] == 1)
    finally:
        stats = writer.close()
    assert stats["written"] == 1
    assert path.stat().st_size <= 2048
    assert not (tmp_path / "session.diag.json.tmp").exists()


def test_writer_drops_a_snapshot_that_cannot_fit(tmp_path):
    path = tmp_path / "session.diag.json"
    writer = DiagnosticSnapshotWriter(path, max_bytes=1024)
    try:
        assert writer.submit({"schema": SNAPSHOT_SCHEMA, "blob": "x" * 4096})
        assert _wait_for(lambda: writer.stats["dropped"] == 1)
    finally:
        stats = writer.close()
    assert stats["dropped"] == 1
    assert not path.exists()


def test_writer_records_write_errors(tmp_path):
    path = tmp_path / "missing" / "session.diag.json"
    writer = DiagnosticSnapshotWriter(path, max_bytes=8192)
    try:
        assert writer.submit({"schema": SNAPSHOT_SCHEMA})
        assert _wait_for(lambda: writer.stats["errors"] == 1)
    finally:
        stats = writer.close()
    assert stats["errors"] == 1


def test_writer_rejects_submit_after_close(tmp_path):
    writer = DiagnosticSnapshotWriter(tmp_path / "x.json")
    writer.close()
    with pytest.raises(RuntimeError, match="closed"):
        writer.submit({"schema": SNAPSHOT_SCHEMA})
    writer.close()


def test_snapshot_schema_differs_from_the_final_report():
    snapshot = compact_diagnostic_snapshot(_report())
    assert snapshot["schema"] != "material-counter.integrated-run.v1"
    assert snapshot["schema"] == SNAPSHOT_SCHEMA


def test_snapshot_carries_bounded_display_diagnostics():
    report = _report()
    report.update(vdma_diag_mmio=True,
                  video_pump_diagnostics={"first_failure": None},
                  video_owner_diagnostics={"state": "FAIL_HOLD"})
    snapshot = compact_diagnostic_snapshot(report)
    assert snapshot["vdma_diag_mmio"] is True
    assert snapshot["video_pump_diagnostics"] == {"first_failure": None}
    assert snapshot["video_owner_diagnostics"] == {"state": "FAIL_HOLD"}