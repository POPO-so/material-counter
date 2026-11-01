from scripts.standalone.display_health import DisplayHealthMonitor


def _pump(submitted, *, state="RUNNING", alive=True, error=None, stage=None):
    return {"state": state, "alive": alive, "submitted": submitted,
            "error": error, "failed_stage": stage, "failed_at": None,
            "last_frame_id": submitted}


def test_normal_progress_has_no_event():
    monitor = DisplayHealthMonitor()
    for step in range(10):
        event = monitor.observe(100.0 + step * 2, 100 + step * 30, False, True,
                                _pump(10 + step), display_fps=20.0,
                                fps_sample_time=100.0 + step * 2)
        assert event is None
    assert monitor.events == 0


def test_no_processed_progress_is_not_a_display_failure():
    monitor = DisplayHealthMonitor()
    for step in range(10):
        event = monitor.observe(100.0 + step * 5, 100, False, True,
                                _pump(10), display_fps=0.0,
                                fps_sample_time=100.0 + step * 5)
        assert event is None


def test_pause_resets_the_window():
    monitor = DisplayHealthMonitor()
    monitor.observe(100.0, 100, False, True, _pump(10))
    event = monitor.observe(140.0, 400, True, True, _pump(10), display_fps=0.0,
                            fps_sample_time=140.0)
    assert event is None
    event = monitor.observe(142.0, 430, False, True, _pump(10))
    assert event is None


def test_submitted_stall_requires_window_and_processed_progress():
    monitor = DisplayHealthMonitor()
    monitor.observe(100.0, 100, False, True, _pump(10))
    assert monitor.observe(110.0, 120, False, True, _pump(10)) is None
    event = monitor.observe(116.0, 140, False, True, _pump(10))
    assert event is not None
    assert event["reason"] == "submitted_stalled"
    assert event["submitted"] == 10
    assert event["processed"] == 140
    assert event["action"] == "record_only"
    # Reported once until a new submission proves recovery.
    assert monitor.observe(118.0, 160, False, True, _pump(10)) is None
    assert monitor.observe(120.0, 180, False, True, _pump(11)) is None


def test_display_fail_hold_is_reported_with_evidence():
    monitor = DisplayHealthMonitor()
    event = monitor.observe(100.0, 500, False, True,
                            _pump(7, state="DISPLAY_FAIL_HOLD", alive=False,
                                  error="VDMA stalled", stage="writeframe"))
    assert event is not None
    assert event["reason"] == "display_fail_hold"
    assert event["video_pump_error"] == "VDMA stalled"
    assert event["video_pump_failed_stage"] == "writeframe"
    assert event["last_frame_id"] == 7


def test_pump_thread_exit_is_reported():
    monitor = DisplayHealthMonitor()
    event = monitor.observe(100.0, 500, False, True,
                            _pump(7, state="RUNNING", alive=False))
    assert event is not None
    assert event["reason"] == "pump_thread_exited"


def test_fps_zero_needs_consecutive_fresh_samples():
    monitor = DisplayHealthMonitor()
    monitor.observe(100.0, 100, False, True, _pump(10))
    assert monitor.observe(102.0, 120, False, True, _pump(10),
                           display_fps=0.0, fps_sample_time=102.0) is None
    assert monitor.observe(104.0, 140, False, True, _pump(10),
                           display_fps=0.0, fps_sample_time=104.0) is None
    event = monitor.observe(106.0, 160, False, True, _pump(10),
                            display_fps=0.0, fps_sample_time=106.0)
    assert event is not None
    assert event["reason"] == "display_fps_zero"


def test_stale_fps_samples_do_not_count():
    monitor = DisplayHealthMonitor()
    for step in range(5):
        event = monitor.observe(100.0 + step * 2, 100 + step * 10, False, True,
                                _pump(10), display_fps=0.0,
                                fps_sample_time=None)
        assert event is None


def test_startup_grace_does_not_flag_zero_submissions():
    monitor = DisplayHealthMonitor(startup_grace_s=30.0)
    for step in range(5):
        event = monitor.observe(100.0 + step * 5, 100 + step * 50, False, True,
                                _pump(0))
        assert event is None
    event = monitor.observe(140.0, 500, False, True, _pump(0))
    assert event is not None
    assert event["reason"] == "submitted_stalled"