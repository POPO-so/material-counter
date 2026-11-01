import pytest

from scripts.standalone.controls import ButtonDecoder, ControlState


def test_new_button_mapping_and_mode_control_state():
    d = ButtonDecoder()
    assert d.update(1, 0) == ()
    assert d.update(1, .031) == ("clear",)
    assert d.update(2, .1) == ()
    assert d.update(2, .131) == ("mode",)
    assert d.update(4, .2) == ()
    assert d.update(4, .231) == ()
    assert d.update(4, .5) == ()
    s = ControlState()
    assert s.diagnostic is False
    s.apply(("mode",))
    assert s.diagnostic is True
    s.apply(("mode",))
    assert s.diagnostic is False


def test_btn2_short_and_long_press_have_distinct_single_events():
    d = ButtonDecoder(long_press=.8)
    assert d.update(4, 0) == ()
    assert d.update(4, .031) == ()
    assert d.next_deadline == pytest.approx(.831)
    assert d.advance(.830) == ()
    assert d.advance(.831) == ("exposure_down",)
    assert d.advance(2) == ()
    assert d.update(0, 2.1) == ()
    assert d.update(0, 2.131) == ()

    d = ButtonDecoder(long_press=.8)
    d.update(4, 0)
    d.update(4, .031)
    assert d.update(0, .5) == ()
    assert d.update(0, .531) == ("exposure_up",)


def test_deadline_drives_debounce_and_simultaneous_new_buttons():
    d = ButtonDecoder()
    assert d.update(3, 1) == ()
    assert d.next_deadline == pytest.approx(1.03)
    assert d.advance(1.03) == ("clear", "mode")
    assert d.next_deadline is None


def test_legacy_commands_remain_available_for_offline_replay():
    s = ControlState()
    s.apply(("pause", "background", "page", "clear"))
    assert s.paused and not s.binary_background and s.page == 1
    assert s.clear_requests == 1 and not s.diagnostic


def test_page_cycles_through_three_pages():
    s = ControlState()
    for expected in (1, 2, 0):
        s.apply(("page",))
        assert s.page == expected


def test_invalid_clock_and_gpio_values_are_rejected():
    d = ButtonDecoder()
    with pytest.raises(ValueError):
        d.update(16, 0)
    d.update(0, 2)
    with pytest.raises(ValueError):
        d.update(0, 1)
