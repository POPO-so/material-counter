"""Regression coverage for the explicitly selected bounded capture writer."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import capture_dataset as capture_module  # noqa: E402
from capture_dataset import CaptureError, CaptureWriter, load_dataset  # noqa: E402


def _metadata(frame_id: int) -> dict:
    return {
        "frame_id": frame_id,
        "source_sequence": frame_id,
        "capture_monotonic_ns": 1000 + frame_id,
        "capture_wall_time_ns": 3000 + frame_id,
        "camera_read_ms": 0.5,
        "capture_timestamp_kind": "host-after-read",
        "dma_complete_monotonic_ns": 2000 + frame_id,
        "dma_slot_index": frame_id % 2,
        "software_pairing": {
            "input_sequence_equals_dma_frame_id": True,
            "input_and_output_use_same_slot": True,
            "mask_echoes_frame_id": False,
        },
        "resolution": {"width": 320, "height": 240},
        "threshold": 128,
        "algorithm_config": {"line_y": 120, "min_area": 12},
        "roi": {"top": 96, "bottom_exclusive": 145},
        "dma_status": {"frame_id": frame_id, "state": "IDLE"},
        "packer_status": {"frame_id": frame_id, "validation": "PASS"},
        "raw_detections": [], "detections": [], "associations": [], "tracks": [],
        "events": [], "suppressed_events": [], "total_count": 0,
        "frame_result": {},
    }


def _frame(value: int) -> tuple[np.ndarray, np.ndarray]:
    gray = np.full((240, 320), value % 256, dtype=np.uint8)
    mask = np.zeros((240, 320), dtype=np.uint8)
    mask[value % 220, value % 300] = 255
    return gray, mask


def _bounded(tmp_path, *, frames=3, **kwargs):
    return CaptureWriter(
        tmp_path / "capture", expected_frames=frames,
        config={"line_y": 120, "min_area": 12},
        write_mode="bounded_stream", **kwargs,
    )


@pytest.mark.parametrize("extra_budget", [0, 65536])
def test_bounded_estimate_covers_real_files_and_close_reserve(tmp_path, extra_budget):
    # Exercise metadata near the documented 8 KiB whole-record allowance.
    estimate = capture_module.estimated_capture_bytes(8, write_mode="bounded_stream")
    writer = _bounded(tmp_path, frames=8, max_bytes=estimate + extra_budget,
                      submit_timeout_s=30.0)
    for frame_id in range(8):
        metadata = _metadata(frame_id)
        metadata["detail"] = "x" * 6200
        gray, mask = _frame(frame_id)
        writer.submit(frame_id, gray, mask, metadata)
    result = writer.close()
    assert result["status"] == "COMPLETE"
    actual = sum(p.stat().st_size for p in writer.output_dir.rglob("*") if p.is_file())
    assert actual == writer._bounded_disk_usage()
    assert actual + 8192 <= estimate
    assert result["owned_bytes_peak"] + 8192 <= estimate
    assert result["submit_wait_log_bytes"] > 0
    assert result["raw_frame_archive"]["size_bytes"] > 2 * 8 * 240 * 320
    load_dataset(writer.output_dir)


def test_bounded_underbudget_rejected_before_creating_output(tmp_path):
    estimate = capture_module.estimated_capture_bytes(8, write_mode="bounded_stream")
    with pytest.raises(CaptureError, match="below estimated requirement"):
        _bounded(tmp_path, frames=8, max_bytes=estimate - 1)
    assert not (tmp_path / "capture").exists()
    with pytest.raises(CaptureError, match="below estimated requirement"):
        _bounded(tmp_path, frames=8, max_bytes=4 * 8 * 240 * 320)


def test_legacy_estimate_formula_is_unchanged():
    for mode in ("streaming_queue", "deferred_memory"):
        expected = 8 * (4 * 240 * 320 + 4096) + 32768 + 2 * (400 * 640 + 4096)
        assert capture_module.estimated_capture_bytes(8, source_frame_count=2) == expected
        assert capture_module.estimated_capture_bytes(
            8, source_frame_count=2, write_mode=mode) == expected


def test_bounded_free_space_preflight_uses_bounded_estimate(tmp_path, monkeypatch):
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    estimate = capture_module.estimated_capture_bytes(8, write_mode="bounded_stream")
    monkeypatch.setattr(capture_module.shutil, "disk_usage", lambda _: usage(estimate, 1, estimate - 1))
    with pytest.raises(CaptureError, match="insufficient free disk space"):
        _bounded(tmp_path, frames=8, max_bytes=estimate)


def test_bounded_stream_uses_fixed_pool_and_round_trips_archive(tmp_path):
    writer = _bounded(tmp_path, frames=3, ring_depth=2)
    assert writer.write_mode == "bounded_stream"
    assert writer.raw_gray8 is None and writer.raw_binary8 is None
    assert len(writer._slots) == 2

    source = []
    for frame_id in range(3):
        gray, mask = _frame(frame_id + 1)
        source.append((gray.copy(), mask.copy()))
        writer.record_raw_frame(frame_id, gray, mask)
        writer.submit(frame_id, gray, mask, _metadata(frame_id))
        gray.fill(99)
        mask.fill(0)

    manifest = writer.close()
    assert manifest["status"] == "COMPLETE"
    assert manifest["write_mode"] == "bounded_stream"
    assert manifest["ring_depth"] == 2
    archive_path = tmp_path / "capture" / "frames_gray8_binary8.npz"
    with np.load(archive_path, allow_pickle=False) as archive:
        np.testing.assert_array_equal(archive["raw_gray8"], np.stack([pair[0] for pair in source]))
        np.testing.assert_array_equal(archive["raw_binary8"], np.stack([pair[1] for pair in source]))
        embedded = json.loads(str(archive["archive_metadata_json"].item()))
    assert embedded["raw_gray8_sha256"] == manifest["raw_frame_archive"]["raw_gray8_sha256"]
    assert embedded["raw_binary8_sha256"] == manifest["raw_frame_archive"]["raw_binary8_sha256"]
    sidecar = archive_path.with_suffix(".npz.sha256").read_text(encoding="ascii").strip()
    assert sidecar == f"{hashlib.sha256(archive_path.read_bytes()).hexdigest()}  {archive_path.name}"
    loaded, frames = load_dataset(tmp_path / "capture")
    assert loaded["status"] == "COMPLETE"
    assert len(frames) == 3
    on_disk = sum(path.stat().st_size for path in (tmp_path / "capture").rglob("*")
                  if path.is_file())
    assert on_disk <= manifest["owned_bytes_peak"]


def test_bounded_stream_waits_for_slot_and_records_blocked_time(tmp_path, monkeypatch):
    writer = _bounded(tmp_path, frames=2, ring_depth=1, submit_timeout_s=2.0)
    entered, release = threading.Event(), threading.Event()
    original = writer._write_frame

    def slow_write(item):
        entered.set()
        assert release.wait(5)
        original(item)

    monkeypatch.setattr(writer, "_write_frame", slow_write)
    gray0, mask0 = _frame(1)
    writer.submit(0, gray0, mask0, _metadata(0))
    assert entered.wait(2)

    gray1, mask1 = _frame(2)
    result = {}
    submitter = threading.Thread(
        target=lambda: result.setdefault("ok", writer.submit(1, gray1, mask1, _metadata(1)))
    )
    submitter.start()
    time.sleep(0.08)
    assert submitter.is_alive(), "second submit should wait for the only pool slot"
    release.set()
    submitter.join(5)
    assert not submitter.is_alive()
    assert "ok" in result
    manifest = writer.close()
    assert manifest["status"] == "COMPLETE"
    assert manifest["submit_wait_count"] == 2
    assert manifest["submit_wait_total_ms"] > 0
    wait_lines = (tmp_path / "capture" / "capture-submit-waits.jsonl").read_text().splitlines()
    assert [json.loads(line)["frame_id"] for line in wait_lines] == [0, 1]
    assert json.loads(wait_lines[1])["blocked_ms"] > 0


def test_bounded_stream_timeout_marks_incomplete_without_silent_drop(tmp_path, monkeypatch):
    writer = _bounded(tmp_path, frames=2, ring_depth=1, submit_timeout_s=0.05)
    entered, release = threading.Event(), threading.Event()
    original = writer._write_frame

    def slow_write(item):
        entered.set()
        assert release.wait(5)
        original(item)

    monkeypatch.setattr(writer, "_write_frame", slow_write)
    gray0, mask0 = _frame(1)
    writer.submit(0, gray0, mask0, _metadata(0))
    assert entered.wait(2)
    gray1, mask1 = _frame(2)
    with pytest.raises(CaptureError, match="timed out waiting for capture slot"):
        writer.submit(1, gray1, mask1, _metadata(1))
    release.set()
    manifest = writer.close()
    assert manifest["status"] == "INCOMPLETE"
    assert manifest["dropped_frames"] == 1
    assert "timed out waiting for capture slot" in manifest["error"]


def test_bounded_stream_rejects_deferred_contract_but_supports_selected_source_frames(tmp_path):
    with pytest.raises(ValueError, match="cannot defer"):
        CaptureWriter(tmp_path / "deferred", expected_frames=1, config={},
                      write_mode="bounded_stream", defer_writes_until_close=True)
    writer = CaptureWriter(tmp_path / "source", expected_frames=1, config={},
                           write_mode="bounded_stream", ring_depth=1,
                           source_frame_ids=(0,))
    assert writer.raw_gray8 is None and writer.raw_binary8 is None
    assert writer.source_gray8.shape == (1, 400, 640)
    assert writer.close()["status"] == "INCOMPLETE"


def test_bounded_stream_array_allocation_depends_on_ring_depth_not_run_length(
        tmp_path, monkeypatch):
    allocations = []
    original_empty = np.empty

    def record_empty(shape, *args, **kwargs):
        allocations.append(shape)
        return original_empty(shape, *args, **kwargs)

    monkeypatch.setattr(capture_module.np, "empty", record_empty)
    writer = _bounded(tmp_path, frames=600, ring_depth=4,
                      max_bytes=256 * 1024 * 1024)
    assert writer.raw_gray8 is None and writer.raw_binary8 is None
    assert len(writer._slots) == 4
    assert (600, 240, 320) not in allocations
    assert allocations.count((240, 320)) == 8
    manifest = writer.close()
    assert manifest["status"] == "INCOMPLETE"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux /proc RSS assertion")
def test_bounded_stream_600_frame_constructor_rss_growth_is_under_two_mib(tmp_path):
    def rss_bytes() -> int:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
        raise AssertionError("/proc/self/status did not report VmRSS")

    before = rss_bytes()
    writer = _bounded(tmp_path, frames=600, ring_depth=4,
                      max_bytes=256 * 1024 * 1024)
    after = rss_bytes()
    assert after - before < 2 * 1024 * 1024
    assert writer.raw_gray8 is None and writer.raw_binary8 is None
    assert len(writer._slots) == 4
    manifest = writer.close()
    assert manifest["status"] == "INCOMPLETE"


def test_bounded_stream_close_waits_for_submitters_before_sentinel(tmp_path, monkeypatch):
    writer = _bounded(tmp_path, frames=2, ring_depth=1, submit_timeout_s=2.0)
    entered, release = threading.Event(), threading.Event()
    original = writer._write_frame

    def slow_write(item):
        entered.set()
        assert release.wait(5)
        original(item)

    monkeypatch.setattr(writer, "_write_frame", slow_write)
    gray0, mask0 = _frame(1)
    writer.submit(0, gray0, mask0, _metadata(0))
    assert entered.wait(2)
    gray1, mask1 = _frame(2)
    submitter = threading.Thread(target=lambda: writer.submit(1, gray1, mask1, _metadata(1)))
    submitter.start()
    time.sleep(0.05)
    closed = {}
    closer = threading.Thread(target=lambda: closed.setdefault("result", writer.close()))
    closer.start()
    time.sleep(0.05)
    assert closer.is_alive(), "close must wait until an in-flight submit leaves the queue"
    release.set()
    submitter.join(5)
    closer.join(5)
    assert not submitter.is_alive() and not closer.is_alive()
    assert closed["result"]["status"] == "COMPLETE"
    assert closed["result"]["captured_frames"] == 2


def test_bounded_stream_disk_failure_is_incomplete_and_cleans_archive_temps(
        tmp_path, monkeypatch):
    writer = _bounded(tmp_path, frames=1)

    def fail_frame(_item):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(writer, "_write_frame", fail_frame)
    gray, mask = _frame(1)
    writer.submit(0, gray, mask, _metadata(0))
    manifest = writer.close()
    assert manifest["status"] == "INCOMPLETE"
    assert "simulated disk failure" in manifest["error"]
    assert manifest["raw_frame_archive"] is None
    assert not list((tmp_path / "capture").glob("*.tmp"))


def test_bounded_stream_capacity_error_and_archive_rename_failure_are_explicit(
        tmp_path, monkeypatch):
    capacity = capture_module.estimated_capture_bytes(1, write_mode="bounded_stream")
    writer = CaptureWriter(tmp_path / "capacity", expected_frames=1,
                           config={"line_y": 120, "min_area": 12},
                           max_bytes=capacity, write_mode="bounded_stream")
    gray, mask = _frame(1)
    metadata = _metadata(0)
    metadata["frame_result"]["padding"] = "x" * 400_000
    writer.submit(0, gray, mask, metadata)
    manifest = writer.close()
    assert manifest["status"] == "INCOMPLETE"
    assert "capacity limit" in manifest["error"]

    second = _bounded(tmp_path, frames=1)
    original_replace = capture_module.os.replace

    def fail_archive_rename(source, target):
        if Path(source).name == "frames_gray8_binary8.npz.tmp":
            raise OSError("simulated archive rename failure")
        return original_replace(source, target)

    monkeypatch.setattr(capture_module.os, "replace", fail_archive_rename)
    gray2, mask2 = _frame(2)
    second.submit(0, gray2, mask2, _metadata(0))
    manifest2 = second.close()
    assert manifest2["status"] == "INCOMPLETE"
    assert "archive rename failure" in manifest2["error"]
    assert manifest2["raw_frame_archive"] is None
    assert not (tmp_path / "capture" / "frames_gray8_binary8.npz.tmp").exists()


@pytest.mark.skipif(os.environ.get("M8_BOUNDED_600_EQ") != "1",
                    reason="set M8_BOUNDED_600_EQ=1 to run the ~80-second legacy comparison")
def test_bounded_stream_600_frames_match_git_show_legacy_writer(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    old_source = subprocess.check_output(
        ["git", "show", "ed1e2a3d:scripts/wo0016/capture_dataset.py"], cwd=repo)
    old_module_path = tmp_path / "old_capture_dataset.py"
    old_module_path.write_bytes(old_source)
    spec = importlib.util.spec_from_file_location("old_capture_dataset", old_module_path)
    old_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old_module
    assert spec.loader is not None
    spec.loader.exec_module(old_module)

    config = {"line_y": 120, "min_area": 12}
    capacity = 256 * 1024 * 1024
    old_writer = old_module.CaptureWriter(
        tmp_path / "old", expected_frames=600, config=config,
        max_bytes=capacity, queue_depth=128)
    new_writer = CaptureWriter(
        tmp_path / "new", expected_frames=600, config=config,
        max_bytes=capacity, write_mode="bounded_stream", ring_depth=4,
        submit_timeout_s=30.0)
    for frame_id in range(600):
        gray = np.full((240, 320), frame_id % 256, dtype=np.uint8)
        mask = np.zeros((240, 320), dtype=np.uint8)
        mask[frame_id % 240, (frame_id * 7) % 320] = 255
        mask[(frame_id * 3) % 240, (frame_id * 11) % 320] = 255
        metadata = _metadata(frame_id)
        old_writer.submit(frame_id, gray, mask, metadata)
        new_writer.submit(frame_id, gray, mask, metadata)
    old_manifest = old_writer.close()
    new_manifest = new_writer.close()
    assert old_manifest["status"] == new_manifest["status"] == "COMPLETE"

    for frame_id in range(600):
        for suffix in ("gray", "mask"):
            old_file = tmp_path / "old" / "frames" / f"{frame_id:06d}.{suffix}.npy"
            new_file = tmp_path / "new" / "frames" / f"{frame_id:06d}.{suffix}.npy"
            assert hashlib.sha256(old_file.read_bytes()).digest() == hashlib.sha256(
                new_file.read_bytes()).digest()
    assert (tmp_path / "old" / "frame-records.jsonl").read_bytes() == (
        tmp_path / "new" / "frame-records.jsonl").read_bytes()
    counter_keys = ("expected_frames", "captured_frames", "accepted_frames",
                    "dropped_frames", "submitted_frames", "missing_frames",
                    "bytes_written", "frame_records_sha256")
    assert {key: old_manifest.get(key) for key in counter_keys} == {
        key: new_manifest.get(key) for key in counter_keys}
    old_archive_path = tmp_path / "old" / "frames_gray8_binary8.npz"
    new_archive_path = tmp_path / "new" / "frames_gray8_binary8.npz"
    with zipfile.ZipFile(old_archive_path) as old_archive, zipfile.ZipFile(
            new_archive_path) as new_archive:
        array_digests = {}
        for key, expected_dtype in (("raw_gray8", np.dtype(np.uint8)),
                                    ("raw_binary8", np.dtype(np.uint8))):
            old_digest, new_digest = hashlib.sha256(), hashlib.sha256()
            with old_archive.open(f"{key}.npy") as old_member, new_archive.open(
                    f"{key}.npy") as new_member:
                for member in (old_member, new_member):
                    assert np.lib.format.read_magic(member) == (1, 0)
                    shape, fortran_order, dtype = np.lib.format.read_array_header_1_0(member)
                    assert shape == (600, 240, 320) and not fortran_order
                    assert dtype == expected_dtype
                for _frame_id in range(600):
                    old_bytes = old_member.read(240 * 320)
                    new_bytes = new_member.read(240 * 320)
                    assert old_bytes == new_bytes
                    old_digest.update(old_bytes)
                    new_digest.update(new_bytes)
            assert old_digest.hexdigest() == new_digest.hexdigest()
            array_digests[key] = old_digest.hexdigest()
        old_embedded = np.load(io.BytesIO(old_archive.read(
            "archive_metadata_json.npy")), allow_pickle=False).item()
        new_embedded = np.load(io.BytesIO(new_archive.read(
            "archive_metadata_json.npy")), allow_pickle=False).item()
        assert old_embedded == new_embedded
    assert array_digests["raw_gray8"] == old_manifest["raw_frame_archive"][
        "raw_gray8_sha256"] == new_manifest["raw_frame_archive"]["raw_gray8_sha256"]
    assert array_digests["raw_binary8"] == old_manifest["raw_frame_archive"][
        "raw_binary8_sha256"] == new_manifest["raw_frame_archive"]["raw_binary8_sha256"]
    loaded_manifest, loaded_frames = load_dataset(tmp_path / "new")
    assert loaded_manifest["status"] == "COMPLETE"
    assert len(loaded_frames) == 600
    evidence_dir = os.environ.get("M8_BOUNDED_EQ_EVIDENCE_DIR")
    if evidence_dir:
        evidence_path = Path(evidence_dir)
        evidence_path.mkdir(parents=True, exist_ok=True)
        report = {
            "status": "PASS",
            "frames": 600,
            "legacy_reference": "ed1e2a3d:scripts/wo0016/capture_dataset.py",
            "legacy_source_sha256": hashlib.sha256(old_source).hexdigest(),
            "test_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "per_frame_npy_sha_equal": True,
            "frame_records_bytes_equal": True,
            "manifest_counters_equal": True,
            "npz_array_payloads_and_embedded_metadata_equal": True,
            "both_datasets_loaded": True,
            "raw_gray8_sha256": array_digests["raw_gray8"],
            "raw_binary8_sha256": array_digests["raw_binary8"],
            "old_bytes_written": old_manifest["bytes_written"],
            "new_bytes_written": new_manifest["bytes_written"],
            "new_submit_wait_total_ms": new_manifest["submit_wait_total_ms"],
        }
        (evidence_path / "result.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
