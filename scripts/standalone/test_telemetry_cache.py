import math

import pytest

from scripts.standalone.telemetry_cache import TelemetryCache,parse_process_stat


def test_process_cpu_and_rss_parse_handles_spaces_and_parentheses_in_comm():
    fields=['R']+['0']*21
    fields[11]='70';fields[12]='30';fields[21]='123'
    result=parse_process_stat('123 (drawing worker (test)) '+' '.join(fields),page_size=4096)
    assert result=={'cpu_ticks':100,'rss_kib':492}


def test_truncated_process_stat_is_not_reported_as_zero_cpu():
    with pytest.raises(ValueError):parse_process_stat('123 (worker) R 1 2',page_size=4096)


@pytest.mark.parametrize("period,boundary_s", [(0.5, 2), (1, 3), (2, 5)])
def test_staleness_boundary_is_strictly_greater_than_two_periods_plus_one(
        period, boundary_s):
    cache = TelemetryCache()
    cache.record("temperature", 12.5, period_s=period, now=10)

    assert cache.snapshot(now=10 + boundary_s)["temperature"]["stale"] is False
    assert cache.snapshot(now=10 + boundary_s + 0.0001)["temperature"]["stale"] is True


def test_stale_sample_becomes_fresh_after_recording_new_value():
    cache = TelemetryCache()
    cache.record("status", {"count": 4}, period_s=1, now=3)
    assert cache.snapshot(now=6.1)["status"]["stale"] is True

    cache.record("status", {"count": 5}, period_s=1, now=7)
    item = cache.snapshot(now=7)["status"]
    assert item == {
        "value": {"count": 5},
        "sampled_monotonic": 7.0,
        "period_s": 1.0,
        "age_s": 0.0,
        "stale": False,
    }


@pytest.mark.parametrize("period", [0, -1, math.nan, math.inf, -math.inf, True])
def test_record_rejects_invalid_period(period):
    cache = TelemetryCache()
    with pytest.raises(ValueError):
        cache.record("x", 1, period_s=period, now=0)


@pytest.mark.parametrize("now", [-1, math.nan, math.inf, -math.inf])
def test_record_rejects_invalid_sampling_time(now):
    cache = TelemetryCache()
    with pytest.raises(ValueError):
        cache.record("x", 1, period_s=1, now=now)


def test_same_key_sampling_time_cannot_go_backwards():
    cache = TelemetryCache()
    cache.record("x", 1, period_s=1, now=2)
    with pytest.raises(ValueError):
        cache.record("x", 2, period_s=1, now=1.999)


def test_snapshot_time_cannot_precede_any_recorded_sample():
    cache = TelemetryCache()
    cache.record("first", 1, period_s=1, now=3)
    cache.record("second", 2, period_s=2, now=5)
    with pytest.raises(ValueError):
        cache.snapshot(now=4.999)


@pytest.mark.parametrize("now", [-1, math.nan, math.inf, -math.inf])
def test_snapshot_rejects_invalid_time(now):
    cache = TelemetryCache()
    with pytest.raises(ValueError):
        cache.snapshot(now=now)


def test_snapshot_is_read_only_and_does_not_refresh_sample_time():
    cache = TelemetryCache()
    value = {"nested": [1, 2]}
    cache.record("x", value, period_s=2, now=8)

    first = cache.snapshot(now=10)
    second = cache.snapshot(now=14)

    assert first["x"]["sampled_monotonic"] == 8.0
    assert first["x"]["age_s"] == 2.0
    assert second["x"]["sampled_monotonic"] == 8.0
    assert second["x"]["age_s"] == 6.0
    assert second["x"]["stale"] is True
    assert cache.snapshot(now=8)["x"]["sampled_monotonic"] == 8.0
