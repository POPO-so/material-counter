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


def test_btn2_exposure_direction_follows_sw0():
    d = ButtonDecoder()
    assert d.update(4, 0, sw0_state=True) == ()
    assert d.advance(.031, sw0_state=True) == ("exposure_up",)
    assert d.update(0, .1, sw0_state=True) == ()
    assert d.advance(.131, sw0_state=True) == ()
    assert d.update(4, .2, sw0_state=False) == ()
    assert d.advance(.231, sw0_state=False) == ("exposure_down",)
    assert d.update(0, .3, sw0_state=None) == ()
    assert d.advance(.331, sw0_state=None) == ()
    assert d.update(4, .4, sw0_state=None) == ()
    assert d.advance(.431, sw0_state=None) == ()


def test_btn3_cycles_pages():
    d = ButtonDecoder()
    assert d.update(8, 0) == ()
    assert d.advance(.03) == ("page",)
    assert d.update(0, .1) == ()
    assert d.advance(.13) == ()


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


def test_invalid_clock_and_gpio_values_are_rejected():
    d = ButtonDecoder()
    with pytest.raises(ValueError):
        d.update(16, 0)
    d.update(0, 2)
    with pytest.raises(ValueError):
        d.update(0, 1)
