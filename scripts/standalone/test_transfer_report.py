from scripts.standalone.integrated_run import (
    summarize_transfer_timing, timing_report_flag, timing_sample_fields,
)


def test_timing_flag_is_absent_when_disabled():
    assert timing_report_flag(False) == {}


def test_timing_flag_is_present_when_enabled():
    assert timing_report_flag(True) == {"transfer_timing_enabled": True}


def test_sample_field_is_absent_when_disabled():
    assert timing_sample_fields(False, {"enter": 1.0}) == {}


def test_sample_field_is_present_when_enabled():
    last = {"enter": 1.0}
    assert timing_sample_fields(True, last) == {"transfer_timing": last}


def _part(offset, polls):
    return {
        "enter": 1.0 + offset,
        "encoded": 1.01 + offset,
        "configured": 1.02 + offset,
        "started": 1.03 + offset,
        "waited": 1.04 + offset,
        "synced": 1.05 + offset,
        "decoded": 1.06 + offset,
        "poll_count": polls,
        "poll_max_gap_ms": 1.2,
        "poll_total_ms": 10.0,
    }


def test_summary_has_the_expected_schema():
    stats = summarize_transfer_timing([_part(0.0, 5), _part(1.0, 7)])
    for key in ("poll_count", "poll_max_gap_ms", "poll_total_ms"):
        assert key in stats
        assert set(stats[key]) == {"p50", "p95", "p99"}
    for first, second in (
        ("enter", "encoded"), ("encoded", "configured"),
        ("configured", "started"), ("started", "waited"),
        ("waited", "synced"), ("synced", "decoded"),
    ):
        name = "{}_to_{}".format(first, second)
        assert name in stats
        assert set(stats[name]) == {"p50", "p95", "p99"}
        assert abs(stats[name]["p50"] - 10.0) < 1.0


def test_summary_of_empty_parts_is_well_formed():
    stats = summarize_transfer_timing([])
    for key in ("poll_count", "poll_max_gap_ms", "poll_total_ms"):
        assert stats[key] == {"p50": None, "p95": None, "p99": None}
    assert "enter_to_encoded" not in stats

def _part_with_substages(offset):
    part = _part(offset, 4)
    part.update({
        "preflighted": 1.005 + offset,
        "packed": 1.007 + offset,
        "dma_done": 1.045 + offset,
    })
    return part


def test_summary_adds_substage_keys_when_the_marks_are_present():
    stats = summarize_transfer_timing([_part_with_substages(0.0), _part_with_substages(1.0)])
    expected = ("preflight_ms", "encode_ms", "sync_to_device_ms", "configure_ms",
                "dma_start_ms", "dma_wait_ms", "dma_reap_ms",
                "sync_from_device_ms", "decode_ms")
    for name in expected:
        assert name in stats
        assert set(stats[name]) == {"p50", "p95", "p99"}
    for first, second in (("enter", "encoded"), ("encoded", "configured"),
                          ("configured", "started"), ("started", "waited"),
                          ("waited", "synced"), ("synced", "decoded")):
        assert "{}_to_{}".format(first, second) in stats
    assert abs(stats["encode_ms"]["p50"] - 2.0) < 1.5
    assert abs(stats["sync_to_device_ms"]["p50"] - 3.0) < 1.5


def test_summary_omits_substage_keys_for_samples_without_the_marks():
    stats = summarize_transfer_timing([_part(0.0, 3)])
    for name in ("preflight_ms", "encode_ms", "sync_to_device_ms", "decode_ms"):
        assert name not in stats
