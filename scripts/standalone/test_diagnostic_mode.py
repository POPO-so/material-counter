from scripts.standalone.diagnostic_mode import DiagnosticPolicy


def test_normal_is_lightweight_and_diagnostic_is_full_trace():
    policy = DiagnosticPolicy()
    assert policy.mode == "normal"
    assert policy.trace_enabled is False
    assert policy.set_mode("diagnostic")["to"] == "diagnostic"
    assert policy.trace_enabled is True
    assert policy.transitions[0]["from"] == "normal"


def test_repeated_mode_does_not_create_fake_transition():
    policy = DiagnosticPolicy("diagnostic")
    assert policy.set_mode("diagnostic") is None
    assert policy.transitions == []
