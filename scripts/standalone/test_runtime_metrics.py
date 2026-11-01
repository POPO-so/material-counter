import json
from pathlib import Path

from scripts.standalone.runtime_metrics import (
    counter_rates, RuntimeSampler, ExternalProcessSampler, cpu_source_breakdown,
)


def test_counter_rates_validate_window_and_calculate_six_metrics():
    before = {"sequence": 1, "counters": dict(total_cycles=100, active_cycles=40,
        input_beats=10, output_beats=5, input_stall_cycles=20, output_stall_cycles=10)}
    after = {"sequence": 2, "counters": dict(total_cycles=200, active_cycles=90,
        input_beats=30, output_beats=25, input_stall_cycles=40, output_stall_cycles=30)}
    rates = counter_rates(before, after, 2.0)
    assert rates["fresh"] is True
    assert rates["rle_activity_pct"] == 50.0
    assert rates["input_mb_s"] == 0.00004
    assert rates["output_mb_s"] == 0.00004
    assert rates["input_stall_pct"] == rates["output_stall_pct"] == 20.0
    assert rates["channel_activity_includes_wait"] is True


def test_counter_rates_do_not_treat_reset_as_uint64_wrap():
    snap = lambda seq, total: {"sequence": seq, "counters": dict(total_cycles=total,
        active_cycles=0, input_beats=0, output_beats=0,
        input_stall_cycles=0, output_stall_cycles=0)}
    assert counter_rates(snap(10, 100), snap(11, 10), 1.0)["fresh"] is False


def test_counter_rates_marks_zero_denominators_unknown():
    snap = {"sequence": 1, "counters": dict.fromkeys(("total_cycles", "active_cycles",
        "input_beats", "output_beats", "input_stall_cycles", "output_stall_cycles"), 0)}
    result = counter_rates(snap, {**snap, "sequence": 2}, 1)
    assert result["fresh"] is True
    assert result["rle_activity_pct"] is None
    assert result["input_stall_pct"] is None


def test_sampler_reads_proc_only_when_sampled_and_never_cmdline(tmp_path):
    proc = tmp_path / "proc"
    (proc / "self/task/12").mkdir(parents=True)
    (proc / "stat").write_text("cpu 10 0 5 85\ncpu0 5 0 2 43\n")
    (proc / "softirqs").write_text("NET_RX: 4\n")
    (proc / "interrupts").write_text(" 7: 11 test\n")
    (proc / "self/task/12/stat").write_text("12 (worker thread) S " + " ".join(["0"]*11 + ["3", "4"]) + "\n")
    (proc / "self/task/12/comm").write_text("worker thread\n")
    (proc / "self/task/12/schedstat").write_text("100 200 3\n")
    ticks = iter([0.0, 3.0])
    sampler = RuntimeSampler(proc_root=proc, clock=lambda: next(ticks), pid="self")
    assert sampler.sample() is None
    first = sampler.sample()
    assert first["interval_s"] == 3.0
    assert first["threads"]["12"]["name"] == "worker thread"
    assert first["threads"]["12"]["utime_ticks_delta"] == 0
    assert "utime_ticks" not in first["threads"]["12"]
    assert "cmdline" not in json.dumps(first)


def test_counter_rates_marks_abnormal_windows_unknown():
    snap = {"sequence": 1, "counters": dict(total_cycles=1, active_cycles=1,
        input_beats=1, output_beats=1, input_stall_cycles=0, output_stall_cycles=0)}
    assert counter_rates(snap, {**snap, "sequence": 2}, 20)["fresh"] is False


def test_sampler_marks_missing_schedstat_unknown(tmp_path):
    proc = tmp_path / "proc"
    task = proc / "55/task/55"
    task.mkdir(parents=True)
    (proc / "stat").write_text("cpu 1 0 0 1\n")
    (task / "stat").write_text("55 (thread) S " + " ".join(["0"]*11 + ["1", "2"]) + "\n")
    ticks = iter([1.0, 4.0])
    sampler = RuntimeSampler(proc_root=proc, pid="55", clock=lambda: next(ticks))
    sampler.sample()
    result = sampler.sample()
    assert result["threads"]["55"]["schedstat_run_ns_delta"] is None


def test_impossible_pl_activity_is_unknown_instead_of_above_100_percent():
    before={"sequence":1,"counters":dict(total_cycles=100,active_cycles=0,
        input_beats=0,output_beats=0,input_stall_cycles=0,output_stall_cycles=0)}
    after={"sequence":2,"counters":dict(total_cycles=101,active_cycles=5,
        input_beats=0,output_beats=0,input_stall_cycles=0,output_stall_cycles=0)}
    assert counter_rates(before,after,1)["fresh"] is False


def test_runtime_sampler_records_network_deltas_with_missing_files_unknown(tmp_path):
    proc=tmp_path/'proc';(proc/'net').mkdir(parents=True)
    (proc/'stat').write_text('cpu 1 0 0 1\n')
    def network(rx,tx):
        (proc/'net/dev').write_text('Inter-| Receive | Transmit\n eth0: '+
            ' '.join(map(str,[rx,2,0,0,0,0,0,0,tx,3,0,0,0,0,0,0]))+'\n')
    network(100,200);times=iter([0,3,6]);sampler=RuntimeSampler(proc_root=proc,pid='self',clock=lambda:next(times))
    assert sampler.sample() is None
    network(150,260);sample=sampler.sample()
    assert sample['network']['eth0']['rx_bytes']==50
    assert sample['network']['eth0']['tx_bytes']==60
    (proc/'net/dev').unlink()
    assert sampler.sample()['network'] is None


def test_cpu_source_breakdown_groups_named_threads_and_preserves_proc_deltas():
    sample = {
        "interval_s": 2,
        "threads": {
            "1": {"name": "gray8-camera", "utime_ticks_delta": 10, "stime_ticks_delta": 0},
            "2": {"name": "drawing", "utime_ticks_delta": 5, "stime_ticks_delta": 5},
            "3": {"name": "video-pump", "utime_ticks_delta": 2, "stime_ticks_delta": 0},
            "4": {"name": "driver-owner", "utime_ticks_delta": 1, "stime_ticks_delta": 1},
            "5": {"name": "crossing-snapshot", "utime_ticks_delta": 1, "stime_ticks_delta": 0},
            "6": {"name": "mystery", "utime_ticks_delta": 1, "stime_ticks_delta": 0},
        },
        "network": {"eth0": {"rx_bytes": 12, "tx_bytes": 8, "rx_packets": 2, "tx_packets": 1}},
        "interrupts": {"7: eth": 3}, "softirqs": {"NET_RX": 4},
    }

    result = cpu_source_breakdown(sample, hertz=100, cpu_count=2)

    assert result["camera"] == 2.5
    assert result["drawing"] == 2.5
    assert result["preview_pump"] == 0.5
    assert result["hardware_owner"] == 0.5
    assert result["snapshots"] == 0.25
    assert result["other"] == 0.25
    assert result["process_total"] == 6.5
    assert result["network"] == sample["network"]
    assert result["interrupts"] == {"interrupts": sample["interrupts"], "softirqs": sample["softirqs"]}


def test_cpu_source_breakdown_reports_external_processes_separately_from_app_threads():
    sample = {
        "interval_s": 2,
        "threads": {
            "1": {"name": "gray8-camera", "utime_ticks_delta": 10, "stime_ticks_delta": 0},
        },
        "external_processes": {
            "10": {"name": "sshd", "cpu_ticks_delta": 10},
            "11": {"name": "python", "cpu_ticks_delta": 10},
        },
        "external_processes_complete": True,
        "external_interval_s": 4,
    }

    result = cpu_source_breakdown(sample, hertz=100, cpu_count=4)

    assert result["camera"] == 1.25
    assert result["process_total"] == 1.25
    assert result["external_ssh"] == 0.625
    assert result["external_other"] == 0.625
    assert result["external_process_total"] == 1.25
    assert result["cpu_capacity"] == "whole_system_all_cores_0_to_100_percent"
    assert result["external_processes_additive_with_threads"] is False


def test_cpu_source_breakdown_keeps_external_process_attribution_unknown_when_incomplete():
    sample = {
        "interval_s": 2,
        "threads": {"1": {"name": "worker", "utime_ticks_delta": 2,
                           "stime_ticks_delta": 0}},
        "external_processes": {"10": {"name": "sshd", "cpu_ticks_delta": 10}},
        "external_processes_complete": False,
        "external_interval_s": 4,
    }

    result = cpu_source_breakdown(sample, hertz=100, cpu_count=4)

    assert result["other"] == 0.25
    assert result["external_ssh"] is None
    assert result["external_other"] is None
    assert result["external_process_total"] is None


def test_external_process_sampler_is_rate_limited_and_uses_bounded_proc_scan(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    for pid in range(10, 14):
        (proc / str(pid)).mkdir()
        fields = ["0"] * 20
        fields[11:13] = ["1", "2"]
        fields[19] = "100"
        (proc / str(pid) / "stat").write_text(f"{pid} (worker) S " + " ".join(fields) + "\n")
    times = iter([0.0, 3.0, 3.0, 6.0])
    sampler = ExternalProcessSampler(proc_root=proc, interval_s=3,
                                     max_processes=3, clock=lambda: next(times))

    assert sampler.sample() is None
    first = sampler.sample()
    assert first["external_processes_complete"] is False
    assert first["external_processes"] == {}
    assert sampler.sample() is None
    second = sampler.sample()
    assert second["external_processes_complete"] is False


def test_external_process_sampler_reports_stable_pid_deltas_without_cmdline(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    app = proc / "55"
    ssh = proc / "56"
    app.mkdir(); ssh.mkdir()
    def write_stat(task, pid, name, user, system, starttime):
        fields = ["0"] * 19
        fields[10:12] = [str(user), str(system)]
        fields[18] = str(starttime)
        (task / "stat").write_text(f"{pid} ({name}) S " + " ".join(fields) + "\n")
    write_stat(app, 55, "runner", 2, 3, 7)
    write_stat(ssh, 56, "sshd", 4, 5, 8)
    times = iter([0.0, 3.0])
    sampler = ExternalProcessSampler(proc_root=proc, clock=lambda: next(times))

    assert sampler.sample(excluded_pids={55}) is None
    write_stat(app, 55, "runner", 7, 5, 7)
    write_stat(ssh, 56, "sshd", 9, 8, 8)
    sample = sampler.sample(excluded_pids={55})

    assert sample["external_processes_complete"] is True
    assert sample["external_processes"] == {"56": {"name": "sshd", "cpu_ticks_delta": 8}}
    assert "cmdline" not in json.dumps(sample)


def test_external_process_sampler_marks_pid_reuse_unknown(tmp_path):
    proc = tmp_path / "proc"
    task = proc / "56"
    task.mkdir(parents=True)
    stat = task / "stat"
    def write_stat(ticks, starttime):
        fields = ["0"] * 19
        fields[10] = str(ticks)
        fields[11] = "0"
        fields[18] = str(starttime)
        stat.write_text("56 (worker) S " + " ".join(fields) + "\n")
    write_stat(10, 100)
    times = iter([0.0, 3.0])
    sampler = ExternalProcessSampler(proc_root=proc, clock=lambda: next(times))

    assert sampler.sample() is None
    write_stat(11, 101)
    sample = sampler.sample()

    assert sample["external_processes_complete"] is False
    assert sample["external_processes"] == {}


def test_external_process_sampler_caps_pid_read_attempts(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    proc.mkdir()
    for pid in (10, 11, 12):
        task = proc / str(pid)
        task.mkdir()
        (task / "stat").write_text("invalid" if pid < 12 else "not read")
    attempts = []
    read_text = Path.read_text
    def tracking_read_text(path, *args, **kwargs):
        if path.parent.parent == proc:
            attempts.append(path.parent.name)
        return read_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", tracking_read_text)
    sampler = ExternalProcessSampler(proc_root=proc, max_processes=2)

    processes, complete = sampler._read_processes(set())

    assert processes == {}
    assert complete is False
    assert len(attempts) == 2


def test_cpu_source_breakdown_keeps_missing_and_rolled_back_values_unknown():
    assert cpu_source_breakdown(None) is None
    assert cpu_source_breakdown({"interval_s": 1, "threads": {}})["process_total"] is None
    sample = {"interval_s": 1, "threads": {
        "1": {"name": "gray8-camera", "utime_ticks_delta": None, "stime_ticks_delta": 0}}}
    result = cpu_source_breakdown(sample, hertz=100, cpu_count=2)
    assert result["camera"] is None
    assert result["drawing"] is None
    assert result["network"] is None
    assert result["interrupts"] is None
