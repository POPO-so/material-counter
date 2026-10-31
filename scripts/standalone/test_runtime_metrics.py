import json

from scripts.standalone.runtime_metrics import counter_rates, RuntimeSampler, cpu_source_breakdown


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
