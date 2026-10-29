import pytest

try:
    from scripts.standalone.controls import ButtonDecoder, ControlState
except ModuleNotFoundError:
    ButtonDecoder = ControlState = None


def decoder():
    if ButtonDecoder is None:
        pytest.fail("board button decoding is not implemented")
    return ButtonDecoder()


def test_bounce_and_held_buttons_do_not_repeat_toggle():
    d = decoder()
    assert d.update(1, 0) == ()
    assert d.update(0, .01) == ()
    assert d.update(1, .02) == ()
    assert d.update(1, .06) == ("pause",)
    assert d.update(1, 3) == ()


def test_clear_requires_two_seconds_after_debounce_and_only_fires_once():
    d = decoder()
    d.update(2, 0)
    assert d.update(2, .04) == ()
    assert d.update(2, 2) == ()
    assert d.update(2, 2.05) == ("clear",)
    assert d.update(2, 4) == ()
    d.update(0, 4.1)
    d.update(0, 4.2)
    d.update(2, 5)
    d.update(2, 5.04)
    assert d.update(2, 7.1) == ("clear",)


def test_mode_page_and_pause_are_applied_at_the_explicit_frame_boundary():
    d = decoder()
    d.update(13, 0)
    commands = d.update(13, .04)
    assert commands == ("pause", "background", "page")
    s = ControlState()
    assert not s.paused and s.binary_background and s.page == 0
    s.apply(commands)
    assert s.paused and not s.binary_background and s.page == 1
    s.apply(("clear",))
    assert s.clear_requests == 1 and s.paused


def test_invalid_clock_and_gpio_values_are_rejected():
    d = decoder()
    with pytest.raises(ValueError):
        d.update(16, 0)
    d.update(0, 2)
    with pytest.raises(ValueError):
        d.update(0, 1)
