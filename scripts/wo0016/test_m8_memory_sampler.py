"""Offline tests for the read-only M8 process memory sampler."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m8_memory_sampler as sampler  # noqa: E402


BOOT = "9552f3cf-5fb6-4b97-b5c3-5640e708a598"


def _proc_tree(root: Path, *, pid: int = 123, starttime: int = 9876,
               boot: str = BOOT, meminfo: str | None = None,
               status: str | None = None, comm: str = "runner with spaces ) ok") -> Path:
    proc = root / "proc"
    (proc / str(pid)).mkdir(parents=True)
    # Field 22 follows fields 3 through 21 after the final comm delimiter.
    fields = ["S"] + [str(index) for index in range(4, 22)] + [str(starttime)]
    assert len(fields) == 20
    (proc / str(pid) / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")
    (proc / str(pid) / "status").write_text(status or (
        "Name:\trunner\nVmPeak:\t999 kB\nVmRSS:\t12000 kB\nVmHWM:\t15000 kB\n"))
    (proc / "meminfo").write_text(meminfo or (
        "MemTotal: 503464 kB\nMemFree: 100000 kB\nMemAvailable: 375000 kB\n"
        "CmaTotal: 131072 kB\nCmaFree: 56000 kB\n"))
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text(boot + "\n")
    return proc


def test_parse_starttime_handles_comm_spaces_and_parentheses():
    text = "123 (runner with spaces ) inside) S " + " ".join(
        [str(index) for index in range(4, 22)] + ["9876"])
    assert sampler.parse_starttime(text) == 9876


@pytest.mark.parametrize("text", ["", "123 no-parentheses S", "123 (x) S 1 2"])
def test_parse_starttime_rejects_malformed_stat(text):
    with pytest.raises(ValueError):
        sampler.parse_starttime(text)


def test_sample_once_reads_identity_and_required_memory_fields(tmp_path):
    proc = _proc_tree(tmp_path)
    sample = sampler.sample_once(123, 9876, BOOT, proc_root=proc,
                                 clock_ns=lambda: 44_000_000)
    assert sample == {
        "status": "OK", "errors": [], "pid": 123,
        "starttime_ticks": 9876, "boot_id": BOOT,
        "monotonic_ns": 44_000_000,
        "process_kib": {"VmRSS": 12000, "VmHWM": 15000},
        "meminfo_kib": {"MemAvailable": 375000, "MemFree": 100000,
                         "CmaFree": 56000, "CmaTotal": 131072},
    }


def test_sample_once_keeps_available_values_when_one_field_is_missing(tmp_path):
    proc = _proc_tree(tmp_path, status="VmRSS:\t12000 kB\n")
    sample = sampler.sample_once(123, 9876, BOOT, proc_root=proc)
    assert sample["status"] == "ERROR"
    assert "missing_VmHWM" in sample["errors"]
    assert sample["process_kib"] == {"VmRSS": 12000, "VmHWM": None}


@pytest.mark.parametrize("damage,expected", [
    ("pid_reuse", "target_starttime_changed"),
    ("boot_change", "boot_id_changed"),
    ("missing_cma", "missing_CmaFree"),
])
def test_sample_once_fails_closed_on_identity_or_meminfo_failure(tmp_path, damage, expected):
    proc = _proc_tree(tmp_path)
    if damage == "pid_reuse":
        fields = ["S"] + [str(i) for i in range(4, 22)] + ["9877"]
        (proc / "123" / "stat").write_text("123 (reused comm) " + " ".join(fields) + "\n")
    elif damage == "boot_change":
        (proc / "sys/kernel/random/boot_id").write_text(
            "11111111-2222-3333-4444-555555555555\n")
    else:
        (proc / "meminfo").write_text(
            "MemAvailable: 375000 kB\nMemFree: 100000 kB\nCmaTotal: 131072 kB\n")
    sample = sampler.sample_once(123, 9876, BOOT, proc_root=proc)
    assert sample["status"] == "ERROR"
    assert expected in sample["errors"]
    if damage == "pid_reuse":
        assert sample["process_kib"] == {"VmRSS": None, "VmHWM": None}
    if damage == "missing_cma":
        assert sample["meminfo_kib"]["CmaFree"] is None


@pytest.mark.parametrize("field,value,error", [
    ("MemAvailable", 122879, "memavailable_below_122880_kib"),
    ("CmaFree", 32767, "cmafree_below_32768_kib"),
])
def test_sample_once_latches_existing_memory_floor_and_keeps_observed_value(
        tmp_path, field, value, error):
    proc = _proc_tree(tmp_path)
    meminfo = (f"MemAvailable: {value if field == 'MemAvailable' else 375000} kB\n"
               "MemFree: 100000 kB\n"
               f"CmaFree: {value if field == 'CmaFree' else 56000} kB\n"
               "CmaTotal: 131072 kB\n")
    (proc / "meminfo").write_text(meminfo)
    sample = sampler.sample_once(123, 9876, BOOT, proc_root=proc)
    assert sample["status"] == "ERROR"
    assert error in sample["errors"]
    assert sample["meminfo_kib"][field] == value


def test_sample_once_accepts_memory_values_at_existing_floors(tmp_path):
    proc = _proc_tree(tmp_path)
    (proc / "meminfo").write_text(
        "MemAvailable: 122880 kB\nMemFree: 32768 kB\n"
        "CmaFree: 32768 kB\nCmaTotal: 131072 kB\n")
    sample = sampler.sample_once(123, 9876, BOOT, proc_root=proc)
    assert sample["status"] == "OK"


@pytest.mark.parametrize("damage,expected", [
    ("cma_free_exceeds_total", "cmafree_exceeds_cmatotal"),
    ("vmhwm_below_vmrss", "vmhwm_below_vmrss"),
])
def test_sample_once_marks_inconsistent_memory_fields_but_keeps_readings(
        tmp_path, damage, expected):
    if damage == "cma_free_exceeds_total":
        proc = _proc_tree(tmp_path, meminfo=(
            "MemAvailable: 375000 kB\nMemFree: 100000 kB\n"
            "CmaTotal: 131072 kB\nCmaFree: 131073 kB\n"))
    else:
        proc = _proc_tree(tmp_path, status=(
            "Name:\trunner\nVmRSS:\t15001 kB\nVmHWM:\t15000 kB\n"))
    sample = sampler.sample_once(123, 9876, BOOT, proc_root=proc)
    assert sample["status"] == "ERROR"
    assert expected in sample["errors"]
    if damage == "cma_free_exceeds_total":
        assert sample["meminfo_kib"]["CmaFree"] == 131073
        assert sample["meminfo_kib"]["CmaTotal"] == 131072
    else:
        assert sample["process_kib"]["VmRSS"] == 15001
        assert sample["process_kib"]["VmHWM"] == 15000


def test_session_persists_identity_checkpoint_and_labeled_final(tmp_path):
    proc = _proc_tree(tmp_path)
    output = tmp_path / "samples.jsonl"
    stdout = io.StringIO()
    code = sampler.run_protocol(
        pid=123, starttime=9876, boot_id=BOOT, output=output,
        proc_root=proc, input_stream=io.StringIO(
            '{"command":"checkpoint","label":"after-camera-ready"}\n'
            '{"command":"stop","label":"post_close"}\n'),
        output_stream=stdout, sampler_pid=456, clock_ns=iter([1, 2, 3]).__next__,
    )
    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert code == 0
    assert [(event["event"], event["status"]) for event in events] == [
        ("READY", "OK"), ("CHECKPOINT", "OK"), ("FINAL", "OK")]
    assert events[1]["label"] == "after-camera-ready"
    assert events[2]["label"] == "post_close"
    assert events[2]["summary"]["status"] == "OK"
    assert events[2]["summary"]["final_valid"] is True
    assert events[2]["summary"]["peak_observed_vmrss_kib"] == 12000
    assert events[2]["summary"]["max_observed_vmhwm_kib"] == 15000
    assert events[2]["summary"]["claims_exact_process_peak"] is False
    assert records[0]["event"] == "IDENTITY"
    assert records[0]["sampler_pid"] == 456
    assert [record["event"] for record in records[1:]] == [
        "SAMPLE", "CHECKPOINT", "FINAL_SAMPLE", "SUMMARY"]
    if os.name == "posix":
        assert output.stat().st_mode & 0o777 == 0o600


def test_session_fails_immediately_after_bad_baseline_without_ready(tmp_path):
    proc = _proc_tree(tmp_path, status="VmRSS:\t12000 kB\n")
    output = tmp_path / "samples.jsonl"
    stdout = io.StringIO()
    code = sampler.run_protocol(pid=123, starttime=9876, boot_id=BOOT,
        output=output, proc_root=proc, input_stream=io.StringIO(
            '{"command":"stop","label":"post_close"}\n'), output_stream=stdout,
        sampler_pid=456, clock_ns=lambda: 10)
    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert code != 0
    assert [event["event"] for event in events] == ["ERROR"]
    assert events[0]["status"] == "ERROR"
    assert events[0]["sample"]["process_kib"]["VmHWM"] is None
    assert records[-1]["event"] == "ERROR"


def test_periodic_sample_failure_is_reported_and_process_exits(tmp_path, monkeypatch):
    proc = _proc_tree(tmp_path)
    output = tmp_path / "samples.jsonl"
    values = iter(["pass", "fail"])
    original = sampler.sample_once

    def sample(*args, **kwargs):
        if next(values) == "fail":
            (proc / "sys/kernel/random/boot_id").write_text("wrong-boot\n")
        return original(*args, **kwargs)

    monkeypatch.setattr(sampler, "sample_once", sample)
    ticks = iter([0.0, 1.0])
    monkeypatch.setattr(sampler.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(sampler.select, "select", lambda *_args: ([], [], []))
    stdout = io.StringIO()
    code = sampler.run_protocol(pid=123, starttime=9876, boot_id=BOOT,
        output=output, proc_root=proc, input_stream=io.StringIO(
            '{"command":"stop","label":"post_close"}\n'), output_stream=stdout,
        sampler_pid=456, clock_ns=lambda: 20)
    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert code != 0
    assert [event["event"] for event in events] == ["READY", "ERROR"]
    assert events[-1]["sample"]["status"] == "ERROR"
    assert events[-1]["sample"]["meminfo_kib"]["MemAvailable"] == 375000
    assert records[-1]["event"] == "ERROR"


def test_select_failure_on_non_stringio_exits_without_readline(tmp_path, monkeypatch):
    proc = _proc_tree(tmp_path)
    output = tmp_path / "samples.jsonl"

    class TrackedInput(io.BytesIO):
        read_calls = 0

        def readline(self, *args, **kwargs):
            self.read_calls += 1
            return super().readline(*args, **kwargs)

    input_stream = TrackedInput(b'{"command":"stop","label":"post_close"}\n')
    monkeypatch.setattr(sampler.select, "select",
                        lambda *_args: (_ for _ in ()).throw(OSError("select failed")))
    stdout = io.StringIO()
    code = sampler.run_protocol(pid=123, starttime=9876, boot_id=BOOT,
        output=output, proc_root=proc, input_stream=input_stream,
        output_stream=stdout, sampler_pid=456, clock_ns=lambda: 20)
    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert code != 0
    assert input_stream.read_calls == 0
    assert [event["event"] for event in events] == ["READY", "ERROR"]
    assert "parent_input_select_failed:OSError" in events[-1]["errors"]


def test_session_rejects_existing_output_without_truncating_it(tmp_path):
    proc = _proc_tree(tmp_path)
    output = tmp_path / "samples.jsonl"
    output.write_text("preserve\n")
    stdout = io.StringIO()
    code = sampler.run_protocol(pid=123, starttime=9876, boot_id=BOOT,
        output=output, proc_root=proc, input_stream=io.StringIO(""),
        output_stream=stdout, sampler_pid=456)
    assert code != 0
    assert output.read_text() == "preserve\n"
    event = json.loads(stdout.getvalue().splitlines()[0])
    assert (event["event"], event["status"]) == ("ERROR", "ERROR")


def test_session_marks_eof_as_incomplete_and_keeps_samples(tmp_path):
    proc = _proc_tree(tmp_path)
    output = tmp_path / "samples.jsonl"
    stdout = io.StringIO()
    code = sampler.run_protocol(pid=123, starttime=9876, boot_id=BOOT,
        output=output, proc_root=proc, input_stream=io.StringIO(""),
        output_stream=stdout, sampler_pid=456)
    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert code != 0
    assert events[0]["event"] == "READY"
    assert events[-1]["event"] == "ERROR"
    assert records[-1]["event"] == "ERROR"
    assert "input_eof_before_stop" in records[-1]["errors"]


def test_long_summary_aggregation_retains_no_per_sample_collection():
    aggregate = sampler._SampleSummary()
    for index in range(20_000):
        aggregate.add({
            "monotonic_ns": index * 1_000_000_000,
            "process_kib": {"VmRSS": index, "VmHWM": index + 5},
            "meminfo_kib": {"MemAvailable": 200_000 - index,
                             "CmaFree": 50_000 - index, "MemFree": 1,
                             "CmaTotal": 60_000},
        })
    summary = aggregate.as_dict(
        status="OK", final_valid=True, sampler_pid=456, target_pid=123,
        starttime=9876, boot_id=BOOT, errors=[])
    assert summary["sample_count"] == 20_000
    assert summary["observed_start_monotonic_ns"] == 0
    assert summary["observed_end_monotonic_ns"] == 19_999_000_000_000
    assert summary["peak_observed_vmrss_kib"] == 19_999
    assert summary["max_observed_vmhwm_kib"] == 20_004
    assert summary["minimum_observed_memavailable_kib"] == 180_001
    assert summary["minimum_observed_cmafree_kib"] == 30_001
    assert not hasattr(aggregate, "samples")


def test_long_protocol_keeps_aggregate_only_and_journals_every_sample(
        tmp_path, monkeypatch):
    proc = _proc_tree(tmp_path)
    output = tmp_path / "samples.jsonl"
    checkpoint_count = 2048
    commands = "".join(
        f'{{"command":"checkpoint","label":"cp-{index}"}}\n'
        for index in range(checkpoint_count))
    commands += '{"command":"stop","label":"post_close"}\n'
    aggregate_instances = []
    original_summary = sampler._SampleSummary

    class ObservedSummary(original_summary):
        def __init__(self):
            super().__init__()
            aggregate_instances.append(self)

    monkeypatch.setattr(sampler, "_SampleSummary", ObservedSummary)
    monkeypatch.setattr(sampler.os, "fsync", lambda _fd: None)
    stdout = io.StringIO()
    code = sampler.run_protocol(pid=123, starttime=9876, boot_id=BOOT,
        output=output, proc_root=proc, input_stream=io.StringIO(commands),
        output_stream=stdout, sampler_pid=456,
        clock_ns=iter(range(checkpoint_count + 2)).__next__)
    assert code == 0
    assert len(aggregate_instances) == 1
    assert aggregate_instances[0].sample_count == checkpoint_count + 2
    assert not hasattr(aggregate_instances[0], "samples")
    journal_sample_count = 0
    final_summary = None
    with output.open(encoding="utf-8") as journal_file:
        for line in journal_file:
            record = json.loads(line)
            if "sample_event" in record:
                journal_sample_count += 1
            if record["event"] == "SUMMARY":
                final_summary = record["summary"]
    assert journal_sample_count == checkpoint_count + 2
    assert final_summary["sample_count"] == checkpoint_count + 2


def test_close_failure_prevents_final_event(tmp_path, monkeypatch):
    proc = _proc_tree(tmp_path)
    output = tmp_path / "samples.jsonl"
    stdout = io.StringIO()
    close = sampler._JsonlJournal.close

    def close_then_fail(journal):
        close(journal)
        raise OSError("simulated close failure")

    monkeypatch.setattr(sampler._JsonlJournal, "close", close_then_fail)
    code = sampler.run_protocol(pid=123, starttime=9876, boot_id=BOOT,
        output=output, proc_root=proc, input_stream=io.StringIO(
            '{"command":"stop","label":"post_close"}\n'),
        output_stream=stdout, sampler_pid=456, clock_ns=lambda: 99)
    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert code != 0
    assert [event["event"] for event in events] == ["READY", "ERROR"]
    assert all(event["event"] != "FINAL" for event in events)
    assert "journal_close_failed:OSError" in events[-1]["errors"]


def test_cli_does_not_offer_proc_root_or_interval_bypass():
    with pytest.raises(SystemExit):
        sampler._parser().parse_args([
            "--pid", "123", "--starttime", "9876", "--boot-id", BOOT,
            "--output", "/tmp/sample.jsonl", "--proc-root", "/fake/proc"])
    with pytest.raises(SystemExit):
        sampler._parser().parse_args([
            "--pid", "123", "--starttime", "9876", "--boot-id", BOOT,
            "--output", "/tmp/sample.jsonl", "--interval", "0"])
