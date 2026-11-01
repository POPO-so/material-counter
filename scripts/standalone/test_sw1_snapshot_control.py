"""SW1 and diagnostic controls gate crossing snapshot capture."""

import pytest

import scripts.standalone.g1_run as g1_run
import scripts.standalone.integrated_run as integrated_run


@pytest.mark.parametrize(
    "cli_enabled,diagnostic_enabled,sw1_status",
    [
        (True, True, "off"),
        (True, False, "on"),
        (False, True, "on"),
        (None, True, "on"),
        ("unknown", True, "on"),
        ("unavailable", True, "on"),
        (True, True, "unknown"),
        (True, True, "unavailable"),
    ],
)
def test_snapshot_capture_is_disabled_when_any_control_is_not_enabled(
    cli_enabled, diagnostic_enabled, sw1_status
):
    assert not integrated_run.snapshot_capture_allowed(
        cli_enabled, diagnostic_enabled, sw1_status
    )


def test_snapshot_capture_requires_cli_diagnostic_mode_and_sw1_on():
    assert integrated_run.snapshot_capture_allowed(True, True, "on")


@pytest.mark.parametrize(
    "width,expected",
    [
        ("4", (4, False)),
        ("0x4", (4, False)),
        (4, (4, False)),
        ("6", (6, True)),
        ("0x6", (6, True)),
        (6, (6, True)),
    ],
)
def test_gpio_channel1_config_reports_width_and_sw1_availability(width, expected):
    ip_dict = {
        "btns_gpio": {"parameters": {"C_GPIO_WIDTH": width}},
    }

    assert g1_run.gpio_channel1_config(ip_dict) == expected


def test_legacy_four_bit_hwh_disables_snapshot_capture():
    ip_dict = {
        "btns_gpio": {"parameters": {"C_GPIO_WIDTH": "0x4"}},
    }
    _, sw1_available = g1_run.gpio_channel1_config(ip_dict)
    sw1_status = "on" if sw1_available else "unavailable"

    assert not integrated_run.snapshot_capture_allowed(True, True, sw1_status)


def test_gpio_channel1_config_rejects_width_below_four():
    ip_dict = {
        "btns_gpio": {"parameters": {"C_GPIO_WIDTH": "3"}},
    }

    with pytest.raises((ValueError, RuntimeError)):
        g1_run.gpio_channel1_config(ip_dict)
