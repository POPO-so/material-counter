import json

from scripts.standalone.runtime_metrics import counter_rates, RuntimeSampler


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
